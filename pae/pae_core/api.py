"""PAE HTTP surface (FastAPI)."""
import json
import time
from pathlib import Path

from fastapi import Body, FastAPI, Header, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from . import capsurface
from . import llm as llm_mod
from . import persona as persona_mod
from .engine import get_engine

app = FastAPI(title="PAE")

# 引擎配置：pae/config.json（相对本文件 ../../config.json）
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.json"


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


class AnnotateBody(BaseModel):
    text: str = ""
    page_id: str = "default"
    session_id: str = ""


@app.get("/v1/health")
def health(resp: Response):
    """存活 + 就绪。**必须真的触碰引擎**，否则会说谎。

    旧实现直接返回 ok:true，于是「词典缺失/损坏、库只读」时 health 一律绿，
    而 status/annotate/event 全 500 —— 而 README 与 start_engine.bat 恰恰拿它当验收标准。
    现在：引擎能构造 -> 200 ok:true；构造失败 -> 503 ok:false + 原因。
    """
    from .engine import DB_PATH as _DB
    try:
        get_engine()                     # 已构造过时是缓存命中，几乎零成本
    except Exception as e:
        resp.status_code = 503
        return {"ok": False, "db": _DB, "error": "%s: %s" % (type(e).__name__, e)}
    return {"ok": True, "db": _DB}   # 带上库路径：测试能识别「我在跟哪个引擎说话」


@app.post("/v1/annotate")
def annotate(body: AnnotateBody):
    return get_engine().annotate(body.text, body.page_id, body.session_id or None)


@app.get("/v1/status")
def status():
    return get_engine().status()


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


@app.post("/v1/event")
def record_event(body: EventBody):
    """扩展上报的观察/交互事件（annotation_shown / hover / known_click）。
    注：这是扩展的内部通道，第 1 批会并入 /v1/cap/* 能力面（同一套守门与配额）。"""
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


@app.post("/v1/cap/{name}")
def cap_call_post(name: str, body: dict = Body(default={}), x_pae_key: str = Header(default="")):
    """统一能力入口（内置 companion 与外部智能体同一条路）。"""
    status, payload = capsurface.handle(name, body or {}, x_pae_key)
    return _cap_response(status, payload)


@app.get("/v1/cap/{name}")
def cap_call_get(name: str, x_pae_key: str = Header(default="")):
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
<a href="/docs">API 文档</a>
<a href="/v1/health">健康检查</a>
<a href="/v1/status">学习状态</a>
<a href="/v1/persona">当前人格参数</a>
<a href="/v1/config">当前配置</a>
</body></html>"""


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
def persona_save(body: PersonaBody):
    ok, issues, path = persona_mod.save_params(body.params)
    return {"ok": ok, "issues": issues, "saved_to": path}


@app.post("/v1/persona/say")
def persona_say(body: SayBody):
    """用当前人格试说一句（面板的即时反馈）。"""
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
    return load_config()

# 启动: uvicorn pae_core.api:app --port 4815