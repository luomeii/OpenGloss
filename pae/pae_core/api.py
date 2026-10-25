"""PAE HTTP surface (FastAPI)."""
import json
import time
from pathlib import Path

from fastapi import Body, FastAPI, Response, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from . import health as runtime_health
from pydantic import BaseModel, Field, StrictInt

from . import http_security
from .http_security import config_projection
from . import capsurface
from . import llm as llm_mod
from . import persona as persona_mod
from .engine import get_engine

app = FastAPI(title="PAE", docs_url=None, redoc_url=None, openapi_url=None)


@app.exception_handler(runtime_health.EngineUnavailable)
async def unavailable_response(request, exc):
    return JSONResponse(status_code=503, content={"ok": False, "error": "engine_unavailable", "detail": str(exc)})


@app.middleware("http")
async def business_admission(request, call_next):
    # Capabilities authenticate and gate themselves; diagnostics/static stay available.
    path = request.url.path
    business = path.startswith("/v1/") and path not in ("/v1/health", "/v1/config") and not path.startswith("/v1/cap/")
    try:
        if business:
            await run_in_threadpool(lambda: get_engine().check_health())
        response = await call_next(request)
        if business:
            await run_in_threadpool(lambda: get_engine().check_health())
        return response
    except runtime_health.EngineUnavailable as exc:
        return await unavailable_response(request, exc)


@app.middleware("http")
async def http_boundary(request, call_next):
    if not http_security.trusted_request(request):
        return JSONResponse(status_code=403, content={"ok": False, "error": "untrusted_host_or_origin"})
    try:
        identity = http_security.local_identity(request)
    except (ValueError, OSError):
        return JSONResponse(status_code=401, content={"ok": False, "error": "invalid_actor_key"})
    request.state.http_identity = identity
    path = request.url.path
    if request.method in ("POST","PUT","PATCH") and path.startswith('/v1/') and request.headers.get('content-type','').split(';')[0].strip().lower()!='application/json':
        return JSONResponse(status_code=415,content={"ok":False,"error":"json_required"})
    if identity is not None and identity[0] not in ("local", "user"):
        actor, scopes = identity
        read_paths = {"/v1/health", "/v1/config", "/v1/status", "/v1/persona", "/v1/push/log", "/v1/ext/stream"}
        if path == "/v1/cap/manifest" or not path.startswith("/v1/cap/"):
            if "read" not in scopes:
                return JSONResponse(status_code=403, content={"ok": False, "error": "scope_denied"})
        if path.startswith("/v1/") and not path.startswith("/v1/cap/") and (request.method != "GET" or path not in read_paths):
            return JSONResponse(status_code=403, content={"ok": False, "error": "use_capability_endpoint"})
    return await call_next(request)


# 引擎配置：pae/config.json（相对本文件 ../../config.json）
from .paths import config_path
CONFIG_PATH = config_path()


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


class AnnotateBody(BaseModel):
    text: str = ""
    page_id: str = "default"
    session_id: str = ""
    max_annotations: StrictInt | None = Field(default=None, ge=0)


@app.get("/v1/health")
def health(resp: Response, request: Request):
    from .engine import DB_PATH
    try:
        get_engine().check_health()
    except Exception:
        resp.status_code = 503
        result = {"ok": False, "error": "engine_unavailable"}
    else:
        result = {"ok": True}
    if result['ok']:
        from .http_security import instance_identity
        result['instance_id']=instance_identity()
    return result


@app.post("/v1/annotate")
def annotate(body: AnnotateBody):
    return get_engine().annotate(body.text, body.page_id, body.session_id or None,
                                 max_annotations=body.max_annotations)


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
def record_event(body: EventBody, request: Request):
    """扩展上报的观察/交互事件（annotation_shown / hover / known_click）。
    注：这是扩展的内部通道，第 1 批会并入 /v1/cap/* 能力面（同一套守门与配额）。"""
    metas = {"page_id": body.page_id, "session_id": body.session_id}
    if body.s_value is not None:
        metas["s_value"] = body.s_value
    if body.page_active_ms is not None:
        metas["page_active_ms"] = body.page_active_ms
    if body.sentence:
        metas["sentence"] = body.sentence
    return get_engine().record_event(body.type, body.lemma, body.surface, metas,
                                     source=request.state.http_identity[0])


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
def ext_stream():
    """SSE 推送：say / hint / param_changed / decision_reverted。

    没有这条通道，角色就不可能主动说话——proactivity 参数也就没有意义。
    """
    from fastapi.responses import StreamingResponse
    from . import push as push_mod

    engine = get_engine()
    engine.check_health()
    q = push_mod.BUS.subscribe()

    def gen():
        import queue
        last_keepalive = time.monotonic()
        try:
            runtime_health.check(engine)
            yield "event: hello\ndata: {}\n\n"
            while True:
                runtime_health.check(engine)
                try:
                    ev = q.get(timeout=1)
                except queue.Empty:
                    runtime_health.check(engine)
                    if time.monotonic() - last_keepalive >= 15:
                        last_keepalive = time.monotonic()
                        yield ": keepalive\n\n"
                    continue
                runtime_health.check(engine)
                yield "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
        except runtime_health.EngineUnavailable:
            return
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
def cap_call_post(name: str, request: Request, body: dict = Body(default={})):
    """统一能力入口（内置 companion 与外部智能体同一条路）。"""
    status, payload = capsurface.handle(name, body or {},
                                         authenticated_identity=request.state.http_identity)
    return _cap_response(status, payload)


@app.get("/v1/cap/{name}")
def cap_call_get(name: str, request: Request):
    if name not in capsurface.HTTP_READ_CAPS:
        return _cap_response(405, {"ok": False, "error": "capability_requires_post"})
    status, payload = capsurface.handle(name, {},
                                         authenticated_identity=request.state.http_identity)
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
    ok, issues, path = get_engine().conn.health.publish(persona_mod.save_params, body.params)
    return {"ok": ok, "issues": issues, "saved_to": path}


@app.post("/v1/persona/say")
def persona_say(body: SayBody):
    """用当前人格试说一句（面板的即时反馈）。"""
    status, payload = capsurface.handle("pae.persona.say", body.model_dump(), actor_hint="local")
    if status != 200:
        return _cap_response(status, payload)
    return payload["result"]


@app.get("/v1/config")
def config():
    """只读配置视图（排障用；改配置编辑 config.json 后重启引擎）。"""
    return config_projection(load_config())

# 启动: uvicorn pae_core.api:app --port 4815
