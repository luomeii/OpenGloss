"""Loopback HTTP identity boundary.

The instance pairing Bearer is full local/user authority. It is never a scoped
external credential. Issued actor keys may instead be used directly as scoped
Bearers; an optional legacy X-PAE-Key must match that key. A root Bearer plus
legacy X-PAE-Key narrows this request only: possessing root still grants full
user authority by omitting that header. Never give root to an untrusted tool.
"""
import hmac
import os
import re
import secrets
from urllib.parse import urlsplit, urlunsplit
from .paths import config_path
from .secrets_store import load_or_create


def engine_token():
    token = os.environ.get("PAE_HTTP_TOKEN")
    if token:
        if len(token) < 32:
            raise ValueError("PAE_HTTP_TOKEN must contain at least 32 characters")
        return token
    return load_or_create(config_path().parent / "http-auth.json", "token",
                          lambda: secrets.token_urlsafe(32),
                          lambda value: isinstance(value, str) and len(value) >= 32)


def request_identity(request):
    """Return the validated (actor, scopes), or None for an anonymous request.

    Malformed, duplicate, unknown or conflicting credentials raise ValueError.
    The empty-key local compatibility rule is never used for HTTP credentials.
    """
    from . import keys
    for header in ("authorization", "x-pae-key"):
        if len(request.headers.getlist(header)) > 1:
            raise ValueError("invalid_credentials")
    supplied = request.headers.get("authorization")
    legacy = request.headers.get("x-pae-key")
    if supplied is None:
        if legacy is not None:
            raise ValueError("invalid_credentials")
        return None
    prefix, separator, bearer = supplied.partition(" ")
    if (prefix.lower() != "bearer" or not separator or not bearer
            or bearer != bearer.strip() or any(c.isspace() for c in bearer)):
        raise ValueError("invalid_credentials")
    if legacy is not None and not legacy:
        raise ValueError("invalid_credentials")
    root = hmac.compare_digest(bearer.encode(), engine_token().encode())
    if root and legacy is None:
        return "local", ("read", "write", "admin")
    if not root and legacy is not None and not hmac.compare_digest(bearer.encode(), legacy.encode()):
        raise ValueError("invalid_credentials")
    actor, scopes = keys.resolve(legacy if root else bearer)
    if actor is None:
        raise ValueError("invalid_credentials")
    return actor, tuple(scopes or ())


def authenticated(request):
    """Compatibility predicate; routes must consume request_identity once."""
    try:
        return request_identity(request) is not None
    except ValueError:
        return False


def trusted_request(request):
    server = request.scope.get("server")
    port = server[1] if server and server[1] else int(os.environ.get("PAE_ENGINE_PORT", "4815"))
    authority = "127.0.0.1:%d" % port
    for header in ("host", "origin"):
        if len(request.headers.getlist(header)) > 1:
            return False
    if request.headers.get("host") != authority:
        return False
    origin = request.headers.get("origin")
    return (origin is None or origin == "http://" + authority
            or bool(re.fullmatch(r"chrome-extension://[a-p]{32}", origin)))


def config_projection(value):
    """Recursive public config, including credentials embedded in URL values."""
    if isinstance(value, dict):
        return {key: config_projection(item) for key, item in value.items()
                if not any(part in str(key).lower() for part in
                           ("key", "token", "password", "secret", "authorization", "credential", "passwd"))}
    if isinstance(value, list):
        return [config_projection(item) for item in value]
    if isinstance(value, str) and "://" in value:
        try:
            parsed = urlsplit(value)
            if not parsed.netloc:
                return "[redacted_url]"
            if "@" in parsed.netloc or parsed.query or parsed.fragment:
                return urlunsplit((parsed.scheme, parsed.netloc.rsplit("@", 1)[-1], parsed.path, "", ""))
        except ValueError:
            return "[redacted_url]"
    return value
