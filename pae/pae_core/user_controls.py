# -*- coding: utf-8 -*-
"""User-owned controls. Ship this module; exclude its runtime settings JSON."""
import json
import os
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit


_ROOT = Path(__file__).resolve().parents[1]
_LOCK = threading.RLock()
DEFAULTS = {
    "ai_enabled": False,
    "shadow_ai_enabled": False,
    "proactive_enabled": False,
    "proactive_ai_enabled": False,
    "scheduled_ai_enabled": False,
    "proactive_gap_minutes": 30,
    "base": "",
    "model": "",
    "key": "",
    "key_overridden": False,
}
_BOOL_FIELDS = {"ai_enabled", "shadow_ai_enabled", "proactive_enabled",
                "proactive_ai_enabled", "scheduled_ai_enabled", "key_overridden"}


def path():
    return Path(os.environ.get("PAE_CONTROLS_PATH") or (_ROOT / "user_controls.json"))


def load():
    """Missing or damaged user settings fail closed for AI permissions."""
    with _LOCK:
        try:
            saved = json.loads(path().read_text(encoding="utf-8"))
            if not isinstance(saved, dict):
                raise ValueError("invalid controls")
        except (FileNotFoundError, OSError, ValueError, TypeError):
            saved = {}
        try:
            if not isinstance(saved, dict):
                raise ValueError("invalid controls object")
            _validate({k: v for k, v in saved.items() if k in DEFAULTS and k != "key_overridden"} or {"ai_enabled": False})
            if "key_overridden" in saved and type(saved["key_overridden"]) is not bool:
                raise ValueError("invalid key override flag")
        except (ValueError, TypeError):
            return dict(DEFAULTS)
        out = dict(DEFAULTS)
        out.update({k: v for k, v in saved.items() if k in DEFAULTS})
        for key in _BOOL_FIELDS:
            if type(out[key]) is not bool:
                out[key] = False
        return out


def _validate(patch):
    if not isinstance(patch, dict) or not patch:
        raise ValueError("settings must be a nonempty object")
    if set(patch) - ((set(DEFAULTS) - {"key_overridden"}) | {"clear_key"}):
        raise ValueError("unknown setting")
    for key in _BOOL_FIELDS:
        if key in patch and type(patch[key]) is not bool:
            raise ValueError("setting must be boolean: " + key)
    if "clear_key" in patch and type(patch["clear_key"]) is not bool:
        raise ValueError("clear_key must be boolean")
    if patch.get("clear_key") and "key" in patch:
        raise ValueError("cannot set and clear key together")
    if "proactive_gap_minutes" in patch:
        v = patch["proactive_gap_minutes"]
        if type(v) is not int or not 30 <= v <= 1440:
            raise ValueError("proactive gap must be 30–1440 minutes")
    for key in ("base", "model", "key"):
        if key in patch and (not isinstance(patch[key], str) or len(patch[key]) > 2048):
            raise ValueError("invalid setting: " + key)
    if "base" in patch and patch["base"]:
        u = urlsplit(patch["base"])
        _ = u.port  # Reject an invalid port before persisting the URL.
        if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ValueError("AI URL must be an http(s) base URL without credentials or query")
    if "model" in patch and patch["model"] and not patch["model"].strip():
        raise ValueError("AI model cannot be whitespace")


def save(patch):
    _validate(patch)
    with _LOCK:
        target = path()
        if target.exists():
            try:
                raw = json.loads(target.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError()
            except (OSError, ValueError, TypeError):
                raise ValueError("existing settings cannot be read; no changes saved")
        data = load()
        data.update({k: v for k, v in patch.items() if k in DEFAULTS})
        if patch.get("clear_key"):
            data["key"] = ""
        if "key" in patch or patch.get("clear_key"):
            data["key_overridden"] = True
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".controls-", suffix=".tmp", dir=str(target.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_name, target)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return public_state()


def public_state():
    data = load()
    return {**{k: v for k, v in data.items() if k not in ("key", "key_overridden")},
            "key_configured": bool(data["key"])}


def allowed(purpose="manual"):
    data = load()
    if not data["ai_enabled"]:
        return False
    return {
        "manual": True,
        "shadow": data["shadow_ai_enabled"],
        "proactive": data["proactive_enabled"] and data["proactive_ai_enabled"],
        "scheduled": data["scheduled_ai_enabled"],
    }.get(purpose, False)
