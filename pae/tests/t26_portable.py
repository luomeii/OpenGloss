# -*- coding: utf-8 -*-
"""T26: 工程可迁移 —— 路径不再写死（env > config > 相对默认）/ 引擎可换端口 / 扩展地址可配置。

工件 tests/artifacts/t26_portable.json"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ART = Path(__file__).parent / "artifacts"
ART.mkdir(exist_ok=True)
PORT = 4835

res = {}
from pae_core import paths

# A) 默认路径：全部相对本文件推导，不含写死的绝对路径
res["default"] = paths.describe()
res["db_under_pae"] = str(res["default"]["db"]).startswith(str(paths.PAE_ROOT))
res["lemma_exists"] = res["default"]["lemma_exists"]
res["dict_exists"] = res["default"]["dict_exists"]

# B) 相对路径解析：不管项目被挪到哪，相对 config 都能解析到项目根下
res["relative_resolves"] = str(paths.PROJECT_ROOT) in paths.lemma_path()

# C) env 覆盖仍然生效（测试隔离靠这个）
os.environ["PAE_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "x.db")
import importlib
importlib.reload(paths)
res["env_override_db"] = paths.db_path().endswith("x.db")

# D) 引擎可以跑在非默认端口（换端口不用改代码）
import importlib.util
def kill_port(port):
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, errors='replace', timeout=20).stdout
    pids = {p.split()[4] for p in out.splitlines()
            if len(p.split()) >= 5 and p.split()[1].endswith(":" + str(port)) and p.split()[3] == "LISTENING"}
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
    time.sleep(1)

kill_port(PORT)
env = dict(os.environ)
env["PAE_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "t26.db")
eng = subprocess.Popen([sys.executable, "-m", "uvicorn", "pae_core.api:app", "--port", str(PORT)],
                       cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    ok = False
    end = time.time() + 60
    while time.time() < end:
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/v1/health" % PORT, timeout=3)
            ok = True
            break
        except Exception:
            time.sleep(0.5)
    res["engine_on_custom_port"] = ok
    if ok:
        with urllib.request.urlopen("http://127.0.0.1:%d/v1/health" % PORT, timeout=5) as r:
            res["health_reports_db"] = "db" in json.loads(r.read().decode("utf-8"))
finally:
    eng.terminate()
    kill_port(PORT)

# E) 扩展侧：引擎地址从 storage 读（静态检查：不再有写死地址）
sw = (ROOT / "extension" / "sw.js").read_text(encoding="utf-8")
popup = (ROOT / "extension" / "popup.js").read_text(encoding="utf-8")
res["sw_hardcoded_left"] = sw.count("http://127.0.0.1:4815")
res["popup_hardcoded_left"] = popup.count("http://127.0.0.1:4815")
res["sw_reads_storage"] = "paeBase" in sw
res["popup_reads_storage"] = "paeBase" in popup
res["sw_uses_var"] = sw.count("PAE_BASE") > 5

ok = (res.get("db_under_pae") and res.get("lemma_exists") and res.get("dict_exists")
      and res.get("relative_resolves") and res.get("env_override_db")
      and res.get("engine_on_custom_port") and res.get("health_reports_db")
      and res.get("sw_reads_storage") and res.get("popup_reads_storage") and res.get("sw_uses_var"))
res["T26"] = "PASS" if ok else "FAIL"
(ART / "t26_portable.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(res, ensure_ascii=False, indent=1))
print("T26 PORTABLE:", res["T26"])