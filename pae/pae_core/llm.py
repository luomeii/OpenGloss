# -*- coding: utf-8 -*-
"""PAE companion LLM provider (OpenAI-compatible chat completions).

这是「换大脑」的唯一接缝：内置 companion、你的 LLM、外部智能体，都从这一层走。
配置解析优先级：环境变量 > 用户本地控制文件 > config.json llm 段 > 内置缺省。
管理页密钥保存在未跟踪的 user_controls.json；公开模板不得包含真实密钥。
"""
import json
import os
import urllib.request
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from . import user_controls

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config.json"

DEFAULTS = {"base": "https://api.deepseek.com", "model": "deepseek-chat", "key": "", "thinking": "disabled"}
_PURPOSE = ContextVar("pae_llm_purpose", default="manual")


def current_purpose():
    return _PURPOSE.get()


@contextmanager
def call_purpose(purpose):
    token = _PURPOSE.set(purpose)
    try:
        yield
    finally:
        _PURPOSE.reset(token)


def load_provider():
    """返回 {base, key, model}。环境变量最优先（便于测试与换脑）。"""
    cfg = {}
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8")).get("llm", {}) or {}
    except Exception:
        cfg = {}
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in cfg.items() if v})
    user = user_controls.load()
    merged.update({k: user[k] for k in ("base", "model") if user[k]})
    # An explicitly cleared key masks a legacy config key after the first save.
    if user["key_overridden"]:
        merged["key"] = user["key"]
    if os.environ.get("PAE_LLM_BASE"):
        merged["base"] = os.environ["PAE_LLM_BASE"]
    if os.environ.get("PAE_LLM_KEY"):
        merged["key"] = os.environ["PAE_LLM_KEY"]
    if os.environ.get("PAE_LLM_MODEL"):
        merged["model"] = os.environ["PAE_LLM_MODEL"]
    return merged


def safe_base(value):
    """Display a provider URL without embedded credentials or query values."""
    if not isinstance(value, str):
        return ""
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if ":" in host:
            host = "[" + host + "]"
        if parsed.port is not None:
            host += ":" + str(parsed.port)
        safe_base = urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    except (ValueError, TypeError):
        safe_base = ""
    return safe_base


def public_provider():
    p = load_provider()
    user = user_controls.load()
    return {
        "base": safe_base(p["base"]), "model": p["model"],
        "key_configured": bool(p.get("key")),
        "base_source": "environment" if os.environ.get("PAE_LLM_BASE") else
                       ("user" if user["base"] else "config"),
        "model_source": "environment" if os.environ.get("PAE_LLM_MODEL") else
                        ("user" if user["model"] else "config"),
        "key_source": "environment" if os.environ.get("PAE_LLM_KEY") else
                      ("user" if user["key_overridden"] else "config"),
    }


def _chat_endpoint(base):
    """Accept an HTTP(S) root, /v1 base or complete chat endpoint without retries."""
    base = base.rstrip("/")
    try:
        parsed = urlsplit(base)
        compatible = (parsed.scheme in ("http", "https") and parsed.hostname
                      and parsed.username is None and parsed.password is None
                      and not parsed.query and not parsed.fragment)
        # Invalid legacy ports retain the previous construction/failure behavior.
        parsed.port
    except ValueError:
        compatible = False
    if compatible:
        if parsed.path.endswith("/chat/completions"):
            return base
        if parsed.path.endswith("/v1"):
            return base + "/chat/completions"
    return base + "/v1/chat/completions"


class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep AI credentials within the configured origin; never change global state."""

    @staticmethod
    def _origin(url):
        try:
            parsed = urlsplit(url)
            if (parsed.scheme not in ("http", "https") or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None):
                return None
            port = parsed.port
            if port is None:
                port = 443 if parsed.scheme == "https" else 80
            return parsed.scheme, parsed.hostname, port
        except ValueError:
            return None

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        source, target = self._origin(req.full_url), self._origin(newurl)
        if source is None or target is None or source != target:
            raise RuntimeError("AI endpoint redirect blocked; configure the final API URL")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def chat(messages, temperature=0.7, timeout=90, max_tokens=None, thinking=None, purpose=None):
    """OpenAI 兼容 chat。thinking: None=读配置 / True=开 / False=关（实测关掉快 3 倍，见
    tests/artifacts/thinking_probe.json：1.29s→0.44s，reasoning_tokens 33→0）。
    短句（companion say/hint）建议关；需要判断的（义项判定/参数提案）建议开。"""
    if not user_controls.allowed(purpose or current_purpose()):
        raise RuntimeError("AI call disabled for this action")
    p = load_provider()
    if not p.get("key"):
        raise RuntimeError("no LLM key configured")
    payload = {"model": p["model"], "messages": messages, "temperature": temperature}
    if max_tokens:
        payload["max_tokens"] = max_tokens
    mode = thinking if thinking is not None else p.get("thinking")
    if mode is True or mode == "enabled":
        payload["thinking"] = {"type": "enabled"}
    elif mode is False or mode == "disabled":
        payload["thinking"] = {"type": "disabled"}
    req = urllib.request.Request(
        _chat_endpoint(p["base"]),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + p["key"]},
    )
    if not user_controls.allowed(purpose or current_purpose()):
        raise RuntimeError("AI call disabled for this action")
    with urllib.request.build_opener(_SameOriginRedirectHandler()).open(req, timeout=timeout) as r:
        j = json.loads(r.read().decode("utf-8"))
    msg = (j.get("choices") or [{}])[0].get("message") or {}
    return {
        "content": (msg.get("content") or "").strip(),
        "reasoning": (msg.get("reasoning_content") or "").strip(),
        "usage": j.get("usage") or {},
        "model": j.get("model"),
    }
