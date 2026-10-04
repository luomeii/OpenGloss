# -*- coding: utf-8 -*-
"""T9: 能力面验收 —— 清单/别名/鉴权/未实现/审计。工件 tests/artifacts/t9_capsurface.json"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PORT = 4821  # 独立端口：不与浏览器测试抢 4815，避免端口竞争
ART = Path(__file__).parent / "artifacts"
ART.mkdir(exist_ok=True)
BASE = "http://127.0.0.1:%d" % PORT


def kill_port(port):
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, errors='replace', timeout=20).stdout
    pids = {p.split()[4] for p in out.splitlines()
            if len(p.split()) >= 5 and p.split()[1].endswith(":" + str(port)) and p.split()[3] == "LISTENING"}
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
    time.sleep(1)


def start_engine(db):
    env = dict(os.environ); env["PAE_DB_PATH"] = str(db)
    return subprocess.Popen([sys.executable, "-m", "uvicorn", "pae_core.api:app", "--port", str(PORT)],
                            cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_health(t=60):
    end = time.time() + t
    while time.time() < end:
        try:
            with urllib.request.urlopen(BASE + "/v1/health", timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def call(path, body=None, key=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    hdr = {"Content-Type": "application/json"}
    if key:
        hdr["X-PAE-Key"] = key
    req = urllib.request.Request(BASE + path, data=data, headers=hdr, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


res = {}
db = Path(tempfile.mkdtemp()) / "t9.db"
kill_port(PORT)
eng = start_engine(db)
try:
    assert wait_health(), "engine down"
    st, man = call("/v1/cap/manifest")
    caps = man.get("capabilities") or []
    s = man.get("summary") or {}
    res["manifest_status"] = st
    res["manifest_total"] = s.get("total")
    res["implemented_count"] = s.get("implemented_count")
    res["planned_count"] = s.get("planned_count")
    required = ("name", "kind", "status", "scope", "description")
    res["caps_missing_fields"] = [c.get("name") for c in caps
                                  if any(k not in c for k in required)]

    # 别名等价：/v1/annotate 与 /v1/cap/pae.annotate 都返回列表
    a = call("/v1/annotate", {"text": "contention scheduler", "page_id": "t9-a"})
    b = call("/v1/cap/pae.annotate", {"text": "contention scheduler", "page_id": "t9-b"})
    res["direct_annotate_status"] = a[0]
    res["cap_annotate_status"] = b[0]
    res["cap_annotate_is_list"] = isinstance((b[1].get("result") if b[0] == 200 else None), list)

    # 鉴权：无 key = local；错 key = 401
    res["no_key_actor"] = call("/v1/cap/pae.health")[1].get("actor")
    res["bad_key_status"] = call("/v1/cap/pae.health", key="pae_bogus")[0]

    # 未实现 → 501；不存在 → 404（动态取「规划中」的能力，避免清单推进后假失败）
    # R6 修复：没有 planned 能力时不写死 501 冒充测过 —— 该判据置 N/A，明确不参与判定，
    # 且不发任何请求（planned_probe 保持 null）。
    planned = (man.get("summary") or {}).get("planned") or []
    planned_name = planned[0] if planned else None
    res["planned_probe"] = planned_name
    if planned_name:
        res["planned_status"] = call("/v1/cap/" + planned_name, {})[0]
        res["planned_check"] = "PASS" if res["planned_status"] == 501 else "FAIL"
        res["planned_note"] = "已对 planned(%s) 真发请求并断言 501" % planned_name
    else:
        res["planned_status"] = None
        res["planned_check"] = "N/A"
        res["planned_note"] = ("清单 planned_count=%s 无 planned 能力 → 「未实现 501」判据本轮 N/A，"
                               "未发请求、不参与 T9 判定" % res.get("planned_count"))
    res["unknown_status"] = call("/v1/cap/pae.nope", {})[0]

    # 审计落库
    conn = sqlite3.connect(str(db))
    rows = conn.execute("SELECT actor, capability, ok FROM cap_calls ORDER BY call_id").fetchall()
    conn.close()
    res["audit_rows"] = len(rows)
    from pae_core import capsurface
    res["audit_error"] = capsurface.LAST_AUDIT_ERROR
    res["audit_sample"] = [{"actor": r[0], "cap": r[1], "ok": r[2]} for r in rows[:4]]
finally:
    eng.terminate(); kill_port(PORT)

# planned 判据：有 planned 能力必须真测出 501；没有则 N/A（不计入判定）。
planned_check = res.get("planned_check")
assert planned_check in ("PASS", "N/A", "FAIL"), planned_check
planned_ok = planned_check in ("PASS", "N/A")
res["planned_counts_toward_verdict"] = bool(res.get("planned_probe"))
ok = (res.get("manifest_status") == 200 and res.get("manifest_total", 0) >= 25
      and not res.get("caps_missing_fields") and res.get("cap_annotate_status") == 200
      and res.get("cap_annotate_is_list") and res.get("no_key_actor") == "local"
      and res.get("bad_key_status") == 401 and planned_ok
      and res.get("unknown_status") == 404 and res.get("audit_rows", 0) >= 4
      and not res.get("audit_error"))
res["T9"] = "PASS" if ok else "FAIL"
(ART / "t9_capsurface.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(res, ensure_ascii=False, indent=1))
print("T9 CAPSURFACE:", res["T9"])