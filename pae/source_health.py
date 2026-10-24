"""Authenticated source-script health probe. Never prints pairing credentials.

Default probe is read-only. --prepare creates/validates the same local pairing
secret used by raw uvicorn, before start_engine.bat launches its own process.
"""
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request
from pae_core.paths import config_path, db_path


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError("", code, "redirect refused", headers, fp)


def health_token():
    token = os.environ.get("PAE_HTTP_TOKEN")
    if not token:
        try:
            token = json.loads((config_path().parent / "http-auth.json").read_text(encoding="utf-8"))["token"]
        except (OSError, ValueError, TypeError, KeyError):
            raise ValueError("http_auth_unavailable") from None
    if not isinstance(token, str) or len(token) < 32 or any(c.isspace() for c in token):
        raise ValueError("http_auth_unavailable")
    return token


def probe():
    expected = str(Path(db_path()).resolve())
    try:
        token = health_token()
        port = int(os.environ.get("PAE_ENGINE_PORT", "4815"))
        if not 1 <= port <= 65535:
            raise ValueError("invalid engine port")
        req = urllib.request.Request("http://127.0.0.1:%d/v1/health" % port,
                                     headers={"Authorization": "Bearer " + token})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(req, timeout=60) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError("invalid health response")
        body = json.loads(raw.decode("utf-8"))
        if (not isinstance(body, dict) or not isinstance(body.get("db"), str)
                or not Path(body["db"]).is_absolute()):
            raise ValueError("invalid health response")
        return {"ok": body.get("ok") is True, "db": body["db"], "expected_db": expected, "status": 200}
    except urllib.error.HTTPError as exc:
        exc.close()
        error = "engine_authentication_failed" if exc.code in (401, 403) else "engine_unavailable"
        return {"ok": False, "error": error, "status": exc.code, "expected_db": expected}
    except (urllib.error.URLError, OSError):
        return {"ok": False, "error": "engine_connection_error", "status": 0, "expected_db": expected}
    except (ValueError, TypeError, KeyError):
        return {"ok": False, "error": "health_credentials_or_response_invalid", "status": 0, "expected_db": expected}


def main():
    if sys.argv[1:] == ["--prepare"]:
        try:
            from pae_core.http_security import engine_token
            engine_token()
            health_token()
        except (OSError, ValueError):
            return 1
        return 0
    print(json.dumps(probe(), ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
