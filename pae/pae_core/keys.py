# -*- coding: utf-8 -*-
"""PAE key 管理：actor ←→ key（只存 sha256，明文只在签发时打印一次）。

keys.json 在 pae/ 下，已 gitignore（含 sha256，仍属敏感）。
"""
import hashlib
import json
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEYFILE = Path(__import__("os").environ.get("PAE_KEYS_PATH") or (ROOT / "keys.json"))

DEFAULT_ACTORS = {
    "local": {"scopes": ["read", "write", "admin"], "note": "本机扩展（无 key 时默认）"},
    "companion": {"scopes": ["read", "write"], "note": "内置角色"},
}


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
    for actor, rec in (_load().get("actors") or {}).items():
        if rec.get("key_sha256") == h:
            return actor, rec.get("scopes") or ["read"]
    return None, None


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2 and sys.argv[1] == "issue":
        who = sys.argv[2] if len(sys.argv) > 2 else "ext:unnamed"
        print("actor:", who)
        print("key:", issue(who))
    else:
        print("usage: python -m pae_core.keys issue <actor-name>")
