"""Reading-data storage controls and detached output projection.

page_key is an instance-specific HMAC for grouping, not anonymization. URL storage
and event idempotency are independent. Existing event history is never rewritten.
"""
import hashlib
import hmac
import json
import logging
import re
import secrets
from urllib.parse import urlsplit
from .paths import config_path
from .secrets_store import load_or_create

_SETTINGS_WARNING = False


def settings():
    global _SETTINGS_WARNING
    invalid = False
    try:
        document = json.loads(config_path().read_text(encoding="utf-8"))
        if not isinstance(document, dict): raise ValueError("invalid_config")
        cfg = document.get("privacy", {})
        if not isinstance(cfg, dict): raise ValueError("invalid_privacy")
    except (OSError, ValueError, TypeError):
        cfg = {"url_storage": "off", "context_storage": False, "shadow_model": False}
        invalid = True
    mode = cfg.get("url_storage", "full")
    if mode not in ("full", "host_only", "hash", "off"):
        mode = "off"; invalid = True
    if invalid and not _SETTINGS_WARNING:
        logging.getLogger(__name__).warning("隐私配置无效：可读网址保存已关闭；请检查本实例 config.json 的 privacy 配置。")
    _SETTINGS_WARNING = invalid
    return {"url_storage": mode,
            "context_storage": cfg.get("context_storage", True) is True,
            "shadow_model": cfg.get("shadow_model", False) is True}


def _instance_key_bytes():
    key = load_or_create(config_path().parent / "privacy-key.json", "key",
                         lambda: secrets.token_hex(32),
                         lambda value: isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value)))
    return bytes.fromhex(key)


def _page_hmac(raw, key_bytes):
    return "hmac:" + hmac.new(key_bytes, str(raw).encode("utf-8"), hashlib.sha256).hexdigest()


def page_key(raw):
    return _page_hmac(raw, _instance_key_bytes())


def page_identity(payload):
    return payload.get("page_key") or page_key(payload.get("page_id") or payload.get("url") or "")


def page_identity_resolver():
    """Resolve legacy page IDs with one instance-key read per query, never across queries."""
    key_bytes = None

    def resolve(payload):
        nonlocal key_bytes
        existing = payload.get("page_key")
        if existing:
            return existing
        if key_bytes is None:
            key_bytes = _instance_key_bytes()
        return _page_hmac(payload.get("page_id") or payload.get("url") or "", key_bytes)

    return resolve


def url_value(raw, mode):
    raw = str(raw or "")
    if mode == "off":
        return ""
    if not raw or raw.startswith("hmac:"):
        return raw
    if mode == "full":
        return raw
    if mode == "hash":
        return page_key(raw)
    try:
        parsed = urlsplit(raw)
        if parsed.scheme in ("http", "https") and parsed.hostname:
            host = parsed.hostname
            if ":" in host:
                host = "[" + host + "]"
            return parsed.scheme + "://" + host + (":" + str(parsed.port) if parsed.port else "")
    except ValueError:
        pass
    return ""  # opaque page identifiers are not assumed safe to reveal


def stored_payload(payload):
    out = dict(payload)
    cfg = settings()
    raw = out.get("page_id", out.get("url", ""))
    if "page_id" in out or "url" in out:
        out["page_key"] = page_key(raw)
    for field in ("page_id", "url"):
        if field in out:
            out[field] = url_value(out[field], cfg["url_storage"])
    if not cfg["context_storage"]:
        out.pop("sentence_context", None)
        out.pop("sentence", None)
    return out


def output_projection(value):
    """Redact URL paths/queries even when compatibility storage remains full."""
    mode = settings()["url_storage"]
    mode = "host_only" if mode == "full" else mode
    if isinstance(value, dict):
        return {key: (url_value(item, mode) if key in ("page_id", "url") else output_projection(item))
                for key, item in value.items()}
    if isinstance(value, list):
        return [output_projection(item) for item in value]
    return value

