"""PAE HTTP surface (FastAPI)."""
import base64
import binascii
import hashlib
import json
import sqlite3
import time
from datetime import date as calendar_date
from pathlib import Path
from typing import Optional

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, StrictInt, model_validator

from . import capsurface
from . import llm as llm_mod
from . import user_controls
from . import persona as persona_mod
from . import paths
from .daily_sheet import build_daily_sheet, local_day_bounds, render_daily_sheet
from .engine import LearningWritePaused, close_dictionary_on_shutdown, get_engine

app = FastAPI(title="PAE")

# 引擎配置：pae/config.json（相对本文件 ../../config.json）
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.json"
EXTENSION_MANIFEST_PATH = Path(__file__).resolve().parents[1] / "extension" / "manifest.json"


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def safe_config(cfg: dict) -> dict:
    """Retain existing diagnostics without returning a legacy provider key."""
    out = dict(cfg)
    if isinstance(out.get("llm"), dict):
        out["llm"] = {k: ("[configured]" if v else "") if k == "key" else v
                      for k, v in out["llm"].items()}
        if "base" in out["llm"]:
            out["llm"]["base"] = llm_mod.safe_base(out["llm"]["base"])
    return out


def _extension_origin() -> Optional[str]:
    """Derive this package's Chromium origin from its public manifest key."""
    try:
        manifest = json.loads(EXTENSION_MANIFEST_PATH.read_text(encoding="utf-8"))
        key = manifest.get("key") if isinstance(manifest, dict) else None
        if not isinstance(key, str):
            return None
        public = base64.b64decode(key, validate=True)
        if not public:
            return None
        digest = hashlib.sha256(public).hexdigest()[:32]
        identity = "".join(chr(ord("a") + int(c, 16)) for c in digest)
        return "chrome-extension://" + identity
    except (OSError, ValueError, binascii.Error):
        return None


def _local_write(request: Request, *, allow_extension=False):
    """Reject website writes; retain same-origin UI and native loopback callers.

    Extension writes require this package's public identity. This source check
    is not strong authentication and does not replace loopback binding. Missing
    Origin is allowed only without cross-site Fetch metadata.
    """
    from urllib.parse import urlsplit
    try:
        host = request.url.hostname
        if host not in {"127.0.0.1", "localhost", "::1"}:
            return False
        origin = request.headers.get("origin", "")
        site = request.headers.get("sec-fetch-site", "")
        if not origin:
            return site in ("", "none", "same-origin")
        o = urlsplit(origin)
        if (o.username is not None or o.password is not None
                or o.path or o.query or o.fragment):
            return False
        if (allow_extension and o.scheme == "chrome-extension"
                and origin == _extension_origin()):
            return True
        port = o.port if o.port is not None else {"http": 80, "https": 443}.get(o.scheme)
        target_port = request.url.port
        if target_port is None:
            target_port = {"http": 80, "https": 443}.get(request.url.scheme)
        return (site in ("", "none", "same-origin") and o.hostname == host
                and port == target_port and o.scheme == request.url.scheme)
    except ValueError:
        return False


def _require_client_write(request: Request):
    if not _local_write(request, allow_extension=True):
        raise HTTPException(status_code=403, detail="local_origin_required")


class AnnotateBody(BaseModel):
    text: str = ""
    page_id: str = "default"
    session_id: str = ""
    max_annotations: Optional[StrictInt] = Field(default=None, ge=0)


@app.get("/v1/health")
def health(resp: Response):
    """存活 + 就绪。**必须真的触碰引擎**，否则会说谎。

    旧实现直接返回 ok:true，于是「词典缺失/损坏、库只读」时 health 一律绿，
    而 status/annotate/event 全 500 —— 而 README 与 start_engine.bat 恰恰拿它当验收标准。
    现在：引擎能构造 -> 200 ok:true；构造失败 -> 503 ok:false + 原因。
    """
    from .engine import DB_PATH as _DB
    try:
        eng = get_engine()               # 已构造过时是缓存命中，几乎零成本
        if getattr(eng, '_learning_write_fault', False):
            resp.status_code = 503
            return {'ok': False, 'error': 'learning_write_paused', 'restart_required': True}
    except Exception as e:
        resp.status_code = 503
        msg = "%s: %s" % (type(e).__name__, e)
        low = msg.lower()
        # 把最常见的失败翻译成新人能照做的提示：词典是第 1 步，且它不在 git 仓库里
        if (isinstance(e, FileNotFoundError) or "no such table" in low
                or "file is not a database" in low or "unable to open database" in low
                or "no such file" in low):
            hint = ("词典缺失或损坏：请按 README 第 1 步，把 dict.sqlite 放到 resources/ECDICT/ 下"
                    "（约 65 MB，不在 git 仓库里，需从 Releases 下载）")
        else:
            hint = ""
        return {"ok": False, "db": _DB, "error": msg, "hint": hint}
    return {"ok": True, "db": _DB}   # 带上库路径：测试能识别「我在跟哪个引擎说话」


@app.post("/v1/annotate", dependencies=[Depends(_require_client_write)])
def annotate(body: AnnotateBody):
    try:
        eng = get_engine()
        if body.max_annotations is None:
            return eng.annotate(body.text, body.page_id, body.session_id or None)
        return eng.annotate(body.text, body.page_id, body.session_id or None,
                            max_annotations=body.max_annotations)
    except LearningWritePaused:
        return JSONResponse(status_code=503, content={
            'ok': False, 'error': 'learning_write_paused', 'restart_required': True})


@app.get("/v1/status")
def status():
    try:
        return get_engine().status()
    except LearningWritePaused:
        return JSONResponse(status_code=503, content={
            'ok': False, 'error': 'learning_write_paused', 'restart_required': True})


PANEL = Path(__file__).resolve().parents[1] / "panel.html"


class EventBody(BaseModel):
    # ⚠️ pydantic v2 默认**丢弃未声明字段**——少声明一个字段就等于静默切断一条链路。
    # 2026-10-02 审计抓到：page_active_ms 与 sentence 没声明 → P4 的视口过滤与 P5 的悬停触发双双失效。
    type: str = ""
    lemma: str = ""
    surface: str = ""
    page_id: str = ""
    session_id: str = ""
    s_value: float = None
    page_active_ms: int = None
    sentence: str = ""
    visible_ms: Optional[StrictInt] = None
    observation_version: Optional[StrictInt] = None

    @model_validator(mode='before')
    @classmethod
    def observation_fields(cls, value):
        if isinstance(value, dict) and value.get('type') == 'visible_opportunity':
            allowed = {'type', 'lemma', 'page_id', 'visible_ms', 'observation_version'}
            if set(value) - allowed:
                raise ValueError('unexpected observation fields')
        return value


@app.get('/v1/feedback')
def feedback_state(lemma: str = ''):
    result = get_engine().feedback_state(lemma)
    if result.get('error') == 'learning_write_paused':
        return JSONResponse(status_code=503, content=result)
    return result


@app.post("/v1/event", dependencies=[Depends(_require_client_write)])
def record_event(body: EventBody):
    """扩展上报的观察/交互事件（annotation_shown / hover / known_click）。
    注：这是扩展的内部通道，第 1 批会并入 /v1/cap/* 能力面（同一套守门与配额）。"""
    if body.type == 'visible_opportunity':
        result = get_engine().record_event(body.type, body.lemma, metas={
            'page_id': body.page_id, 'visible_ms': body.visible_ms,
            'observation_version': body.observation_version})
        if not result.get('ok'):
            status = (503 if result.get('error') == 'learning_write_paused' else
                      429 if result.get('error') == 'visible_opportunity_daily_limit' else 400)
            return JSONResponse(status_code=status, content=result)
        return result
    metas = {"page_id": body.page_id, "session_id": body.session_id}
    if body.s_value is not None:
        metas["s_value"] = body.s_value
    if body.page_active_ms is not None:
        metas["page_active_ms"] = body.page_active_ms
    if body.sentence:
        metas["sentence"] = body.sentence
    return get_engine().record_event(body.type, body.lemma, body.surface, metas)


class PersonaBody(BaseModel):
    params: dict = {}


class SayBody(BaseModel):
    stimulus: str = ""
    params: dict = {}
    context_block: str = ""


@app.on_event("startup")
def _warm_engine():
    """启动即预热：后台线程立刻加载词典。

    起因（2026-10-02 观察到的真问题）：/v1/health 不加载词典就返回 OK，
    健康检查通过 ≠ 引擎就绪；冷启动的第一次请求要背 12–15 秒词典加载，
    机器繁忙时会到分钟级 —— 测试与真实使用都会撞上「看起来起了其实没就绪」。
    """
    def _w():
        try:
            from .engine import get_engine
            get_engine()
        except Exception:
            pass
    import threading as _t
    _t.Thread(target=_w, daemon=True, name="pae-warmup").start()


@app.on_event("startup")
def _start_scheduler():
    """仅当 config 的 agent_schedule.enabled 打开时启动低频调度；默认不开。"""
    try:
        from . import scheduler as sched_mod
        from .engine import DB_PATH
        cfg = sched_mod.config()
        if cfg.get("enabled"):
            sched_mod.start(DB_PATH)
    except Exception:
        pass


@app.on_event("shutdown")
def _close_dictionary():
    close_dictionary_on_shutdown()


@app.get("/v1/ext/stream")
def ext_stream(key: str = ""):
    """SSE 推送：say / hint / param_changed / decision_reverted。

    没有这条通道，角色就不可能主动说话——proactivity 参数也就没有意义。
    """
    from fastapi.responses import StreamingResponse
    from . import push as push_mod

    q = push_mod.BUS.subscribe()

    def gen():
        try:
            yield "event: hello\ndata: {}\n\n"
            while True:
                try:
                    ev = q.get(timeout=15)
                    yield "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
                except Exception:
                    yield ": keepalive\n\n"
        finally:
            push_mod.BUS.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/v1/push/log")
def push_log(n: int = 20, kind: str = ""):
    """角色对我说过什么（审计视图用）。"""
    from . import push as push_mod
    return push_mod.log(get_engine().conn, n=n, kind=kind or None)


@app.get("/v1/cap/manifest")
def cap_manifest():
    """能力面清单：外部智能体读一次就知道能干什么（含实现状态）。"""
    return capsurface.manifest_doc()


def _cap_response(status: int, body: dict):
    return JSONResponse(status_code=status, content=body)


@app.post("/v1/cap/{name}", dependencies=[Depends(_require_client_write)])
def cap_call_post(name: str, body: dict = Body(default={}), x_pae_key: str = Header(default="")):
    """统一能力入口（内置 companion 与外部智能体同一条路）。"""
    status, payload = capsurface.handle(name, body or {}, x_pae_key)
    return _cap_response(status, payload)


# Keep the documented queries and the two legacy queries used by T10/T11.
# The existing params view still performs its normal expiry cleanup.
_CAP_GET_NAMES = frozenset({
    "pae.health", "pae.status", "pae.config", "pae.persona",
    "pae.decisions", "pae.push.log", "pae.params", "pae.agent.log",
    "pae.audit", "pae.permissions", "pae.agent.schedule",
    "pae.metrics", "pae.page",
})


@app.get("/v1/cap/{name}")
def cap_call_get(name: str, x_pae_key: str = Header(default="")):
    if name not in _CAP_GET_NAMES:
        if capsurface.find(name) is None:
            return _cap_response(404, {
                "ok": False, "error": "unknown_capability", "name": name,
                "hint": "GET /v1/cap/manifest",
            })
        return JSONResponse(
            status_code=405,
            content={"ok": False, "error": "method_not_allowed"},
            headers={"Allow": "POST"},
        )
    status, payload = capsurface.handle(name, {}, x_pae_key)
    return _cap_response(status, payload)


@app.get("/", response_class=HTMLResponse)
def index():
    """入口页：给不记路径的时候用。"""
    return """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>PAE 引擎</title><style>
body{font:14px/1.7 system-ui,sans-serif;max-width:620px;margin:60px auto;color:#1f2328}
h1{font-size:18px;margin-bottom:4px}.m{color:#6b7280;font-size:12.5px;margin-bottom:20px}
a{display:block;padding:11px 14px;border:1px solid #e5e7eb;border-radius:9px;margin-bottom:9px;
text-decoration:none;color:#1f2328}a:hover{border-color:#d97706;background:#fffdf7}
b{color:#d97706}</style></head><body>
<h1>PAE 引擎在跑</h1><div class="m">本地被动英语习得引擎 · 全部入口如下</div>
<a href="/panel"><b>人格面板</b> —— 改参数，实时看渲染出的提示词</a>
<a href="/manage"><b>轻量管理</b> —— 状态、标注上限、静音与 AI 设置</a>
<a href="/docs">API 文档</a>
<a href="/v1/health">健康检查</a>
<a href="/v1/status">学习状态</a>
<a href="/v1/persona">当前人格参数</a>
<a href="/v1/config">当前配置</a>
<a href="/daily">今日识单 —— 查看并打印已记录的词</a>
</body></html>"""


_DAILY_HEADERS = {"Cache-Control": "no-store", "X-Frame-Options": "DENY",
                  "X-Content-Type-Options": "nosniff"}


def _daily_data(day_text: str):
    selected = day_text or calendar_date.today().isoformat()
    start, end = local_day_bounds(selected)
    return selected, build_daily_sheet(paths.db_path(), paths.dict_path(), start, end)


@app.get("/v1/daily-sheet")
def daily_sheet_json(date: str = ""):
    """User-requested, read-only view of persisted encounters for a local day."""
    try:
        selected, sheet = _daily_data(date)
    except ValueError:
        return JSONResponse({"error": "日期应为有效的 YYYY-MM-DD"}, status_code=400,
                            headers=_DAILY_HEADERS)
    except (sqlite3.Error, OSError):
        return JSONResponse({"error": "今日识单暂时无法读取"}, status_code=503,
                            headers=_DAILY_HEADERS)
    return JSONResponse({"date": selected, **sheet}, headers=_DAILY_HEADERS)


@app.get("/daily", response_class=HTMLResponse)
def daily_sheet_page(date: str = ""):
    """Print-friendly local page. No script, tracking, or learning-state writes."""
    try:
        selected, sheet = _daily_data(date)
        page = render_daily_sheet(selected, sheet)
    except ValueError:
        return HTMLResponse("<h1>日期应为有效的 YYYY-MM-DD</h1>", status_code=400,
                            headers=_DAILY_HEADERS)
    except (sqlite3.Error, OSError):
        return HTMLResponse("<h1>今日识单暂时无法读取</h1>", status_code=503,
                            headers=_DAILY_HEADERS)
    return HTMLResponse(page, headers={**_DAILY_HEADERS,
                        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'"})


@app.get("/panel", response_class=HTMLResponse)
def panel():
    """人格面板（改参数 → 实时看渲染出的提示词）。"""
    try:
        return PANEL.read_text(encoding="utf-8")
    except Exception:
        return "<h1>panel.html not found</h1>"


@app.get("/v1/persona")
def persona_get():
    p = persona_mod.load_params()
    return {"params": p, "bands": persona_mod.bands(p), "prompt": persona_mod.render(p)}


@app.post("/v1/persona/preview")
def persona_preview(body: PersonaBody):
    p = body.params or persona_mod.load_params()
    return {"prompt": persona_mod.render(p), "bands": persona_mod.bands(p),
            "issues": persona_mod.validate(p)}


@app.post("/v1/persona")
def persona_save(request: Request, body: PersonaBody):
    if not _local_write(request):
        return JSONResponse({"error": "local_origin_required"}, status_code=403,
                            headers=_MANAGE_HEADERS)
    ok, issues, path = persona_mod.save_params(body.params)
    return {"ok": ok, "issues": issues, "saved_to": path}


@app.post("/v1/persona/say")
def persona_say(request: Request, body: SayBody):
    """用当前人格试说一句（面板的即时反馈）。"""
    if not _local_write(request):
        return JSONResponse({"error": "local_origin_required"}, status_code=403,
                            headers=_MANAGE_HEADERS)
    p = body.params or persona_mod.load_params()
    sys_prompt = persona_mod.render(p, context_block=body.context_block)
    t0 = time.time()
    try:
        r = llm_mod.chat(
            [{"role": "system", "content": sys_prompt},
             {"role": "user", "content": body.stimulus or "（没有输入，随便说一句）"}],
            temperature=0.7, timeout=90)
        return {"reply": r["content"], "elapsed_ms": int((time.time() - t0) * 1000),
                "model": r.get("model"), "usage": r.get("usage")}
    except Exception as e:
        return {"reply": "", "elapsed_ms": int((time.time() - t0) * 1000),
                "error": "%s: %s" % (type(e).__name__, str(e)[:200])}


@app.get("/v1/config")
def config():
    """只读配置视图（排障用；改配置编辑 config.json 后重启引擎）。"""
    return safe_config(load_config())


MANAGE = Path(__file__).resolve().parents[1] / "manage.html"
_MANAGE_HEADERS = {"Cache-Control": "no-store", "X-Frame-Options": "DENY",
                   "X-Content-Type-Options": "nosniff"}


@app.get("/manage", response_class=HTMLResponse)
def manage_page():
    return HTMLResponse(MANAGE.read_text(encoding="utf-8"), headers={
        **_MANAGE_HEADERS, "Content-Security-Policy":
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "connect-src 'self'; form-action 'self'; base-uri 'none'"})


@app.get("/v1/manage")
def manage_state():
    from . import params as params_mod
    from . import push as push_mod
    state = {"ai": user_controls.public_state(), "provider": llm_mod.public_provider(),
             "engine": "unavailable", "max_ann_per_page": None, "muted": None}
    try:
        eng = get_engine()
        with eng.lock:
            paused = getattr(eng, "_learning_write_fault", False)
            state["engine"] = "learning_write_paused" if paused else "ready"
            if not paused:
                state["max_ann_per_page"] = int(params_mod.effective(eng.conn)["max_ann_per_page"])
                state["muted"] = bool(push_mod.is_muted(eng.conn))
    except Exception:
        pass
    return JSONResponse(state, headers=_MANAGE_HEADERS)


@app.post("/v1/manage/ai")
def manage_ai(request: Request, body: dict = Body(...)):
    if not _local_write(request):
        return JSONResponse({"error": "local_origin_required"}, status_code=403,
                            headers=_MANAGE_HEADERS)
    try:
        safe = user_controls.save(body)
    except (ValueError, OSError):
        return JSONResponse({"error": "invalid_or_unwritable_settings"}, status_code=400,
                            headers=_MANAGE_HEADERS)
    return JSONResponse({"ai": safe, "provider": llm_mod.public_provider()}, headers=_MANAGE_HEADERS)


@app.post("/v1/manage/mute")
def manage_mute(request: Request, body: dict = Body(...)):
    if not _local_write(request):
        return JSONResponse({"error": "local_origin_required"}, status_code=403,
                            headers=_MANAGE_HEADERS)
    if type(body.get("muted")) is not bool:
        return JSONResponse({"error": "invalid_mute_value"}, status_code=400,
                            headers=_MANAGE_HEADERS)
    status, result = capsurface.handle("pae.mute", {"muted": body["muted"]}, actor_hint="local")
    return JSONResponse(result, status_code=status, headers=_MANAGE_HEADERS)


@app.post("/v1/manage/page-cap")
def manage_page_cap(request: Request, body: dict = Body(...)):
    if not _local_write(request):
        return JSONResponse({"error": "local_origin_required"}, status_code=403,
                            headers=_MANAGE_HEADERS)
    value = body.get("max_ann_per_page")
    if type(value) is not int or not 8 <= value <= 80:
        return JSONResponse({"error": "page_cap_must_be_8_to_80"}, status_code=400,
                            headers=_MANAGE_HEADERS)
    status, result = capsurface.handle("pae.propose", {
        "patch": [{"key": "max_ann_per_page", "to": value}],
        "actor": "user", "rationale": "用户在本地管理页设置页面标注上限"}, actor_hint="local")
    return JSONResponse(result, status_code=status, headers=_MANAGE_HEADERS)

# 启动: uvicorn pae_core.api:app --port 4815
