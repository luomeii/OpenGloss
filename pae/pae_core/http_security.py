"""Single-user loopback boundary. Ordinary local use needs no pairing."""
import os
import re
from urllib.parse import urlsplit, urlunsplit

def instance_identity():
    """Public ownership marker, never a credential or a filesystem path."""
    explicit=os.environ.get('PAE_INSTANCE_ID')
    if explicit:return explicit
    from .paths import db_path
    from pathlib import Path
    import hashlib
    return 'source-'+hashlib.sha256(str(Path(db_path()).resolve()).casefold().encode('utf-8')).hexdigest()


def local_identity(request):
    """Keep existing optional actor scopes; native/local browser use is local.

    This is a local single-user API, not an isolation boundary against processes
    or other extensions running as that same user. No root secret is generated.
    """
    from . import keys
    if len(request.headers.getlist("x-pae-key")) > 1:raise ValueError("invalid_actor_key")
    legacy = request.headers.get("x-pae-key")
    if legacy is None:return "local", ("read", "write", "admin")
    if not legacy:raise ValueError("invalid_actor_key")
    actor, scopes = keys.resolve(legacy)
    if actor is None:raise ValueError("invalid_actor_key")
    return actor, tuple(scopes or ())


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
    if origin is None and request.headers.get("sec-fetch-site") == "cross-site":return False
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
