# -*- coding: utf-8 -*-
"""T6: persona panel e2e — 面板加载 / 滑块实时改提示词 / 保存 / 试说一句。工件 tests/artifacts/t6_panel.json + panel.png"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PORT = 4815
ART = Path(__file__).parent / "artifacts"
ART.mkdir(exist_ok=True)


def kill_port(port):
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, errors='replace', timeout=20).stdout
    pids = {p.split()[4] for p in out.splitlines()
            if len(p.split()) >= 5 and p.split()[1].endswith(":" + str(port)) and p.split()[3] == "LISTENING"}
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
    time.sleep(1)


def start_engine(persona_path):
    env = dict(os.environ)
    env["PAE_PERSONA_PATH"] = str(persona_path)
    # 必须把数据库也指到临时目录：否则引擎会用默认的 pae/pae.db（= 生产库），
    # 而这个测试会保存人格并真调 LLM，可能改到真实数据。
    env["PAE_DB_PATH"] = str(Path(persona_path).parent / "t6_panel.db")
    import shutil
    shutil.copy(ROOT / "persona" / "default.json", persona_path)
    return subprocess.Popen([sys.executable, "-m", "uvicorn", "pae_core.api:app", "--port", str(PORT)],
                            cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_health(t=60):
    end = time.time() + t
    while time.time() < end:
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/v1/health" % PORT, timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


res = {}
tmpd = Path(tempfile.mkdtemp())
kill_port(PORT)
eng = start_engine(tmpd / "persona.json")
try:
    assert wait_health(), "engine down"
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chromium")
        pg = b.new_page(viewport={"width": 1600, "height": 1000})
        pg.goto("http://127.0.0.1:%d/panel" % PORT, wait_until="domcontentloaded")
        pg.wait_for_timeout(1500)
        prompt0 = pg.inner_text("#prompt")
        res["panel_ok"] = "角色装配模板" in prompt0
        res["prompt_len_initial"] = len(prompt0)
        res["band_warmth"] = pg.inner_text("#b_warmth")
        res["band_humor_before"] = pg.inner_text("#b_humor")

        # 拖幽默滑块到 95 → 提示词应出现「爱损人」
        pg.eval_on_selector("#s_humor", "el => { el.value = 95; el.dispatchEvent(new Event('input', {bubbles:true})); }")
        pg.wait_for_timeout(900)
        prompt1 = pg.inner_text("#prompt")
        res["band_humor_after"] = pg.inner_text("#b_humor")
        res["prompt_has_爱损人"] = "爱损人" in prompt1
        res["prompt_changed"] = prompt1 != prompt0

        # 口癖加一条 + 保存
        pg.click("#addQuirk")
        pg.wait_for_timeout(300)
        pg.eval_on_selector("#quirks .li:last-child input", "el => { el.value = '测试口癖：说话喜欢用「属于是」'; el.dispatchEvent(new Event('input', {bubbles:true})); }")
        pg.wait_for_timeout(600)
        pg.click("#save")
        pg.wait_for_timeout(1200)
        res["save_status"] = pg.inner_text("#saveStatus")
        saved = json.loads((tmpd / "persona.json").read_text(encoding="utf-8"))
        res["saved_humor"] = saved["traits"]["humor"]
        res["saved_quirk_added"] = any("属于是" in q for q in saved.get("quirks", []))

        # 试说一句（真调 LLM）
        pg.fill("#stimulus", "用户刚连续悬停 contention 三次又走开，你要说什么？只输出这句话。")
        pg.fill("#ctx", "近7天：遭遇128次，新增21词；contention S=0.31 在带内")
        pg.click("#say")
        for _ in range(40):
            pg.wait_for_timeout(1000)
            if pg.inner_text("#reply") not in ("—", "…"):
                break
        res["say_reply"] = pg.inner_text("#reply")[:200]
        res["say_status"] = pg.inner_text("#sayStatus")
        pg.screenshot(path=str(ART / "panel.png"), full_page=False)
        b.close()
finally:
    eng.terminate()
    kill_port(PORT)
    subprocess.Popen([sys.executable, "-m", "uvicorn", "pae_core.api:app", "--port", str(PORT)],
                     cwd=str(ROOT),
                     env=dict(os.environ, PAE_PERSONA_PATH=str(tmpd / "persona.json"),
                              PAE_DB_PATH=str(tmpd / "t6_panel.db")),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

ok = (res.get("panel_ok") and res.get("prompt_has_爱损人") and res.get("prompt_changed")
      and res.get("save_status", "").startswith("已保存") and res.get("saved_quirk_added")
      and len(res.get("say_reply", "")) > 4)
res["T6"] = "PASS" if ok else "FAIL"
(ART / "t6_panel.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(res, ensure_ascii=False, indent=1))
print("T6 PANEL:", res["T6"])
