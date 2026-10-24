# -*- coding: utf-8 -*-
"""PAE key 管理：actor ←→ key（只存 sha256，明文只在签发时打印一次）。

keys.json 在 pae/ 下，已 gitignore（含 sha256，仍属敏感）。
"""
import hashlib
import hmac
import json
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEYFILE = Path(__import__("os").environ.get("PAE_KEYS_PATH") or (ROOT / "keys.json"))

DEFAULT_ACTORS = {
    "local": {"scopes": ["read", "write", "admin"], "note": "仅进程内无 key 兼容；HTTP 需真实配对 Bearer"},
    "companion": {"scopes": ["read", "write"], "note": "内置角色"},
}


RESERVED_ACTORS = frozenset({"local", "user", "companion"})


def _load() -> dict:
    try:
        return json.loads(KEYFILE.read_text(encoding="utf-8"))
    except Exception:
        return {"actors": {}}


def _save(d: dict) -> None:
    KEYFILE.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def issue(actor: str, scopes=None) -> str:
    """签发一个 key，返回明文（只此一次）。"""
    if actor in RESERVED_ACTORS:
        raise ValueError("reserved_actor")
    d = _load()
    d.setdefault("actors", {})
    plain = "pae_" + secrets.token_urlsafe(24)
    d["actors"][actor] = {
        "key_sha256": sha(plain),
        "scopes": scopes or ["read", "write"],
        "issued_at": __import__("time").time(),
    }
    _save(d)
    return plain


def resolve(key: str):
    """返回 (actor, scopes)；无 key → ('local', admin 全权，向后兼容)；未知 key → (None, None)。"""
    if not key:
        return "local", DEFAULT_ACTORS["local"]["scopes"]
    h = sha(key)
    document = _load()
    actors = document.get("actors", {}) if isinstance(document, dict) else {}
    if not isinstance(actors, dict): return None, None
    for actor, rec in actors.items():
        if not isinstance(rec, dict): continue
        digest = rec.get("key_sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest): continue
        if hmac.compare_digest(digest, h):
            if actor in RESERVED_ACTORS:
                return None, None
            scopes = rec.get("scopes") or ["read"]
            if not isinstance(scopes, list) or any(s not in ("read", "write", "admin") for s in scopes): return None, None
            return actor, scopes
    return None, None


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2 and sys.argv[1] == "issue":
        who = sys.argv[2] if len(sys.argv) > 2 else "ext:unnamed"
        print("actor:", who)
        print("key:", issue(who))
    else:
        print("usage: python -m pae_core.keys issue <actor-name>")
