# -*- coding: utf-8 -*-
"""T20: 内嵌角色形态 —— 胶囊渲染 / 对话回环 / 推送上屏。需要 LLM 与代理。

工件 tests/artifacts/t20_character.json + character.png
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
EXT = str(ROOT / "extension")
PORT = 4815   # 扩展里的引擎地址是写死的 4815，故本测试必须用 4815（临时库）
BASE = "http://127.0.0.1:%d" % PORT
FIX = Path(__file__).parent / "fixture_m2.html"
ART = Path(__file__).parent / "artifacts"
ART.mkdir(exist_ok=True)


def kill_port(port):
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=20).stdout
    pids = {p.split()[4] for p in out.splitlines()
            if len(p.split()) >= 5 and p.split()[1].endswith(":" + str(port)) and p.split()[3] == "LISTENING"}
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
    time.sleep(1)


def wait_engine_verified(port, db_path, eng=None, tries=3, timeout=90):
    """等引擎就绪并**验证身份**：/v1/health 返回的 db 必须等于本测试的临时库路径。
    端口 4815 由 11 个测试共用，任何一个崩溃残留的僵尸引擎都会让后续测试静默拿到 0 条注解
    （2026-10-02 实锤：清僵尸后 e2e 立即恢复）。这里撞上僵尸就杀掉重试，把静默级联失败变成自愈。"""
    import urllib.request, json, time, subprocess
    for attempt in range(tries):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/v1/health" % port, timeout=3) as r:
                    h = json.loads(r.read().decode("utf-8"))
                if str(h.get("db")).replace(chr(92), "/") == str(db_path).replace(chr(92), "/"):
                    return True                      # 身份正确：就是我们的引擎
                # 身份不符 = 撞上僵尸：杀掉后重起
                break
            except Exception:
                time.sleep(0.5)
        # 端口上有别人的引擎（或我们的没起来）：清理后由调用方重起
        _kill_port_4815(port)
        if eng is not None and attempt < tries - 1:
            try:
                eng.terminate()
            except Exception:
                pass
            return False   # 返回 False 让调用方重新 Popen 并再次调用本函数
    raise RuntimeError("engine identity check failed on port %d (expect db=%s)" % (port, db_path))


_kill_port_4815 = kill_port


res = {}
tmpd = Path(tempfile.mkdtemp())
kill_port(PORT)
env = dict(os.environ)
env["PAE_DB_PATH"] = str(tmpd / "t20.db")
eng = subprocess.Popen([sys.executable, "-m", "uvicorn", "pae_core.api:app", "--port", str(PORT)],
                       cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# 扩展里的引擎地址是写死的 4815；为测试把它指到 PORT（注入一个补丁版 manifest 太麻烦），
# 因此这里用 4815 的写法：改测 4815，但用临时库 -> 需要引擎在 4815。改为直接跑 4815。
try:
    for _attempt in range(3):
        if wait_engine_verified(PORT, tmpd / "t20.db", eng):
            break
        eng = subprocess.Popen([sys.executable, "-m", "uvicorn", "pae_core.api:app", "--port", str(PORT)],
                               cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        raise RuntimeError("engine start failed after 3 tries (port %d, db %s)" % (PORT, tmpd / "t20.db"))
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            tempfile.mkdtemp(prefix="pae-t20-"), headless=True, channel="chromium", timeout=60000,
            args=["--disable-extensions-except=" + EXT, "--load-extension=" + EXT])
        page = ctx.new_page()
        page.goto(FIX.as_uri())
        for _ in range(60):
            page.wait_for_timeout(1000)
            if page.evaluate("!!document.getElementById('pae-char-host')"):
                break
        res["host_present"] = page.evaluate("!!document.getElementById('pae-char-host')")
        res["shadow_ok"] = page.evaluate("(function(){var h=document.getElementById('pae-char-host');return !!(h && h.shadowRoot);})()")
        res["capsule_text"] = page.evaluate("(function(){var h=document.getElementById('pae-char-host');return h&&h.shadowRoot?h.shadowRoot.getElementById('capname').textContent:null;})()")

        # 打开面板 → 输入一句 → 等回复
        page.evaluate("(function(){var h=document.getElementById('pae-char-host');h.shadowRoot.getElementById('cap').click();})()")
        page.wait_for_timeout(300)
        page.evaluate("(function(){var h=document.getElementById('pae-char-host');var i=h.shadowRoot.getElementById('in');i.value='我最近学得咋样';h.shadowRoot.getElementById('send').click();})()")
        reply = ""
        for _ in range(45):
            page.wait_for_timeout(1000)
            reply = page.evaluate("(function(){var h=document.getElementById('pae-char-host');return h.shadowRoot.getElementById('btext').textContent;})()")
            if reply and reply != "我最近学得咋样":
                break
        res["reply_seen"] = reply[:160]
        res["reply_is_reply_not_echo"] = bool(reply and reply != "我最近学得咋样")
        page.screenshot(path=str(ART / "character.png"))

        # 推送上屏：让引擎 say 一句 → 应出现在气泡里
        def say(text):
            req = urllib.request.Request(BASE + "/v1/cap/pae.say",
                                         data=json.dumps({"text": text, "actor": "companion"}).encode(),
                                         headers={"Content-Type": "application/json"})
            return urllib.request.urlopen(req, timeout=30).read().decode()
        try:
            say("推送测试：这条应该出现在胶囊气泡里")
            pushed = ""
            for _ in range(20):
                page.wait_for_timeout(1000)
                pushed = page.evaluate("(function(){var h=document.getElementById('pae-char-host');return h.shadowRoot.getElementById('btext').textContent;})()")
                if "推送测试" in pushed:
                    break
            res["push_seen"] = pushed[:160]
            res["push_ok"] = "推送测试" in pushed
        except urllib.error.HTTPError as e:
            res["push_ok"] = False
            res["push_error"] = e.code
        ctx.close()
finally:
    eng.terminate()
    kill_port(PORT)

ok = (res.get("host_present") and res.get("shadow_ok") and res.get("capsule_text")
      and res.get("reply_is_reply_not_echo") and res.get("push_ok"))
res["T20"] = "PASS" if ok else "FAIL"
(ART / "t20_character.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(res, ensure_ascii=False, indent=1))
print("T20 CHARACTER:", res["T20"])