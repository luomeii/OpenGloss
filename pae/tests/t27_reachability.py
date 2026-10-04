# -*- coding: utf-8 -*-
"""T27: 全能力可达性 —— 41 项能力必须能通过 **HTTP 与 MCP 两条传输**到达。

起因（2026-10-02 审计抓到两个真 bug）：
- MCP 工具名正反转换不对称 → 15/40 项能力经 MCP 调用必 404
- EventBody 未声明 page_active_ms/sentence → pydantic v2 静默丢弃 → 两条链路失效
这说明「模块级测试通过」不等于「接线通」。本测试专门盯接线。

工件 tests/artifacts/t27_reachability.json"""
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
PORT = 4836
BASE = "http://127.0.0.1:%d" % PORT
ART = Path(__file__).parent / "artifacts"
ART.mkdir(exist_ok=True)

# 每项能力的最小可用入参（缺的会自动用空对象；404/unknown_capability 才算失败）
ARGS = {
    "pae.annotate": {"text": "quartz contention", "page_id": "t27"},
    "pae.event": {"type": "annotation_shown", "lemma": "quartz", "page_id": "t27", "page_active_ms": 3000},
    "pae.word": {"lemma": "quartz"},
    "pae.events": {"days": 7, "n": 5},
    "pae.decisions": {"n": 5},
    "pae.audit": {"n": 5},
    "pae.memory.search": {"query": "x", "k": 3},
    "pae.memory.recent": {"n": 3},
    "pae.remember": {"kind": "goal", "text": "t27 测试事实", "evidence": ["t27"]},
    "pae.pin": {"lemma": "quartz"},
    "pae.say": {"text": "t27"},
    "pae.hint": {"lemma": "quartz", "level": "up"},
    "pae.persona.preview": {"params": {}},
    "pae.persona.say": {"stimulus": "t27"},
    "pae.propose": {"patch": [{"key": "annotate_max_s", "to": 0.7}], "rationale": "t27", "ttl_days": 1},
    "pae.context": {"top_n": 3},
    "pae.outcomes": {"evaluate_decisions": False},
    "pae.familiarity": {"recompute": False},
    "pae.familiarity.restore": {"lemma": "quartz"},
    "pae.sensejudge": {"stats_only": True},
    "pae.agent.log": {"n": 3},
    "pae.agent.schedule": {},
    "pae.agent.schedule.set": {"enabled": False},
    "pae.key.issue": {"name": "ext:t27"},
    "pae.session.open": {"actor": "companion"},
    "pae.session.turn": {"session_id": "nope", "role": "user", "text": "x"},
    "pae.session.close": {"session_id": "nope"},
}


def kill_port(port):
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=20).stdout
    pids = {p.split()[4] for p in out.splitlines()
            if len(p.split()) >= 5 and p.split()[1].endswith(":" + str(port)) and p.split()[3] == "LISTENING"}
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
    time.sleep(1)


def cap(name, body=None, timeout=120):
    req = urllib.request.Request(BASE + "/v1/cap/" + name, data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return -1, str(e)


res = {"http": {}, "mcp": {}, "problems": []}
tmpd = Path(tempfile.mkdtemp())
kill_port(PORT)
env = dict(os.environ)
env["PAE_DB_PATH"] = str(tmpd / "t27.db")
# 夹具隔离（2026-10-03）：persona 参数也写到临时文件（用仓库默认值做种子），
# 跑测试不写仓库里的 persona/default.json
try:
    (tmpd / "persona.json").write_text((ROOT / "persona" / "default.json").read_text(encoding="utf-8"),
                                       encoding="utf-8")
    env["PAE_PERSONA_PATH"] = str(tmpd / "persona.json")
except Exception:
    pass
eng = subprocess.Popen([sys.executable, "-m", "uvicorn", "pae_core.api:app", "--port", str(PORT)],
                       cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
mcp = None
try:
    end = time.time() + 90
    while time.time() < end:
        try:
            urllib.request.urlopen(BASE + "/v1/health", timeout=3)
            break
        except Exception:
            time.sleep(0.5)
    cap("pae.status")   # 预热（等词典加载完）

    with urllib.request.urlopen(BASE + "/v1/cap/manifest", timeout=30) as r:
        man = json.loads(r.read().decode("utf-8"))
    names = [c["name"] for c in (man.get("capabilities") or []) if c.get("status") == "implemented"]
    res["capability_count"] = len(names)

    # ---- 前置夹具（2026-10-03 评审收尾）：ARGS 表从没给 forget / session.turn / persona.update
    # 合法入参（发空对象、假 session_id），门禁一收紧成「必须 200」，三个历史 500 立刻现形。
    # 处置是**让用例合法**（先造夹具），不是退回「只判 404」。夹具落在临时库/临时 persona 文件上。
    def _call(name, body=None):
        st, txt = cap(name, body or {})
        try:
            return st, json.loads(txt)
        except Exception:
            return st, {"raw": (txt or "")[:200]}

    def _result(b):
        return (b.get("result") or {}) if isinstance(b, dict) else {}

    fixtures = {}
    st_r, b_r = _call("pae.remember", {"kind": "event", "text": "t27 临时事实",
                                       "evidence": ["t27-fixture"]})
    fid = _result(b_r).get("fact_id") if st_r == 200 else None
    if fid:
        ARGS["pae.forget"] = {"fact_id": fid}
    fixtures["remember"] = {"status": st_r, "fact_id": fid}

    st_s, b_s = _call("pae.session.open", {"actor": "companion"})
    sid = _result(b_s).get("session_id") if st_s == 200 else None
    if sid:
        ARGS["pae.session.turn"] = {"session_id": sid, "role": "user", "text": "t27 探针"}
    fixtures["session_open"] = {"status": st_s, "session_id": sid}

    # persona.update：读现值 → 完整回传六个身份字段（M2 修复的判据正是「缺字段即拒」），
    # 顺带成为 M2 的一次真实验证
    st_p, b_p = _call("pae.persona")
    cur = _result(b_p).get("params") if st_p == 200 else None
    if cur:
        ARGS["pae.persona.update"] = {"params": cur}
    IDENTITY = ("name", "address_user", "backstory_seed", "style_notes", "quirks", "forbidden")
    fixtures["persona_read"] = {"status": st_p,
                                "identity_fields": [k for k in IDENTITY if cur and k in cur]}
    res["fixtures"] = fixtures

    # ---- 环境门控（本仓库为公开发布仓库，**调用前**判定，不是失败后补救）：
    # 本仓库不带 LLM key，而 pae.persona.say 是真实 LLM 调用。判据只看「key 有没有配置」
    # （与 llm.chat 里那个 if not p.get(key): raise 同源，用同一个 load_provider），
    # 无 key → 这一项**根本不调用**，显式记为 skip 且不计入 problems；有 key → 必须 200。
    def _llm_key_configured():
        try:
            from pae_core import llm as _llm
            return bool((_llm.load_provider() or {}).get("key"))
        except Exception:
            return False

    if not _llm_key_configured():
        res["skipped"] = {"pae.persona.say": "no_llm_key_configured"}

    # ---- HTTP：每项都要能到达（杜绝 404 / unknown_capability）
    for n in names:
        _skip = (res.get("skipped") or {}).get(n)
        if _skip:
            res["http"][n] = "skipped:" + _skip
            continue
        st, body = cap(n, ARGS.get(n, {}))
        # H1 教训（评审 2026-10-03）：500 也算不可达——pae.mute 坏了半年，
        # 工件里躺着 500 却因「只判 404」全绿。实现项必须 200。
        bad = (st != 200) or ("unknown_capability" in body) or (st == -1)
        res["http"][n] = st
        if bad:
            res["problems"].append("HTTP %s -> %s %s" % (n, st, body[:80]))

    # ---- MCP：每个工具都要能到达（这是原 bug 所在）
    menv = dict(env)
    menv["PAE_BASE"] = BASE
    mcp = subprocess.Popen([sys.executable, "-X", "utf8", "mcp_server.py"], cwd=str(ROOT), env=menv,
                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, encoding="utf-8", bufsize=1)

    def rpc(msg, expect=True):
        mcp.stdin.write(json.dumps(msg) + "\n")
        mcp.stdin.flush()
        if not expect:
            return None
        return json.loads(mcp.stdout.readline())

    rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "t27", "version": "0"}}})
    rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}, expect=False)
    tl = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = [t["name"] for t in ((tl.get("result") or {}).get("tools") or [])]
    res["mcp_tool_count"] = len(tools)

    mid = 10
    for tn in tools:
        # 工具名 pae_x_y → 能力名 pae.x.y（本测试自己按清单反查，避免与实现同错）
        cand = [n for n in names if n.replace(".", "_") == tn]
        args = ARGS.get(cand[0], {}) if cand else {}
        mid += 1
        resp = rpc({"jsonrpc": "2.0", "id": mid, "method": "tools/call",
                    "params": {"name": tn, "arguments": args}})
        text = ((resp.get("result") or {}).get("content") or [{}])[0].get("text", "")
        bad = ("unknown_capability" in text) or ("HTTP 404" in text)
        res["mcp"][tn] = ("404-ish" if bad else "reachable")
        if bad:
            res["problems"].append("MCP %s (%s) -> %s" % (tn, cand[0] if cand else "?", text[:90]))
finally:
    if mcp:
        mcp.terminate()
    eng.terminate()
    kill_port(PORT)

ok = (res.get("capability_count", 0) >= 40 and not res.get("problems")
      and res.get("mcp_tool_count", 0) >= 40)
res["T27"] = "PASS" if ok else "FAIL"
(ART / "t27_reachability.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({k: v for k, v in res.items() if k != "http" and k != "mcp"}, ensure_ascii=False, indent=1))
print("HTTP 可达:", sum(1 for v in res["http"].values() if v not in (404, -1)), "/", len(res["http"]),
      " 跳过:", json.dumps(res.get("skipped") or {}, ensure_ascii=False))
print("MCP 可达:", sum(1 for v in res["mcp"].values() if v == "reachable"), "/", len(res["mcp"]))
print("T27 REACHABILITY:", res["T27"])