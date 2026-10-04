# -*- coding: utf-8 -*-
"""T18: MCP 通道 —— 外部智能体能否通过标准 MCP 协议用上全部能力。

模拟 MCP 客户端的行为：initialize → tools/list → tools/call（读 + 写 + 被守门拒绝）。
工件 tests/artifacts/t18_mcp.json
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = 4828
ART = Path(__file__).parent / "artifacts"
ART.mkdir(exist_ok=True)


def kill_port(port):
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=20).stdout
    pids = {p.split()[4] for p in out.splitlines()
            if len(p.split()) >= 5 and p.split()[1].endswith(":" + str(port)) and p.split()[3] == "LISTENING"}
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
    time.sleep(1)


res = {}
tmpd = Path(tempfile.mkdtemp())
db = tmpd / "t18.db"
kill_port(PORT)
env = dict(os.environ)
env["PAE_DB_PATH"] = str(db)
eng = subprocess.Popen([sys.executable, "-m", "uvicorn", "pae_core.api:app", "--port", str(PORT)],
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


try:
    assert wait_health(), "engine down"
    # 以 stdio 方式启动 MCP server（就像 Claude Code 那样）
    menv = dict(env)
    menv["PAE_BASE"] = "http://127.0.0.1:%d" % PORT
    mcp = subprocess.Popen([sys.executable, "-X", "utf8", "mcp_server.py"], cwd=str(ROOT), env=menv,
                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, encoding="utf-8", bufsize=1)

    def rpc(msg, expect=True):
        mcp.stdin.write(json.dumps(msg) + "\n")
        mcp.stdin.flush()
        if not expect:
            return None
        line = mcp.stdout.readline()
        try:
            return json.loads(line)
        except Exception:
            return {"_raw": line[:200]}

    init = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "test", "version": "0"}}})
    res["initialize"] = {"serverInfo": (init.get("result") or {}).get("serverInfo"),
                         "has_tools": "tools" in ((init.get("result") or {}).get("capabilities") or {})}
    rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}, expect=False)

    tl = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tl_tools = ((tl.get("result") or {}).get("tools") or [])
    res["tools_count"] = len(tl_tools)
    names = [t["name"] for t in tl_tools]
    res["has_key_tools"] = [n for n in ("pae_context", "pae_metrics", "pae_propose", "pae_say",
                                        "pae_memory_search", "pae_agent_tick") if n in names]
    res["schema_ok"] = all(("inputSchema" in t and t["inputSchema"].get("type") == "object") for t in tl_tools)

    # 只读调用
    c1 = rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
              "params": {"name": "pae_metrics", "arguments": {}}})
    t1 = ((c1.get("result") or {}).get("content") or [{}])[0].get("text", "")
    res["read_call_ok"] = "annotation_exposure" in t1

    # 写调用（带 ttl，AI 身份：用一个外部 key 更真实，这里先用本机通道）
    c2 = rpc({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
              "params": {"name": "pae_remember", "arguments": {
                  "actor": "ext:claude-code", "kind": "goal",
                  "text": "外部智能体通过 MCP 接入测试", "evidence": ["mcp_test"]}}})
    t2 = ((c2.get("result") or {}).get("content") or [{}])[0].get("text", "")
    res["write_call_ok"] = ("fact_id" in t2) or ("ok" in t2)

    # 被守门拒（不带 ttl 的 AI 提案）→ 应回传 gate_result 而不是崩
    c3 = rpc({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
              "params": {"name": "pae_propose", "arguments": {
                  "actor": "ext:claude-code", "patch": [{"key": "annotate_max_s", "to": 0.6}],
                  "rationale": "测试守门"}}})
    r3 = c3.get("result") or {}
    t3 = (r3.get("content") or [{}])[0].get("text", "")
    res["gate_rejection_surfaced"] = ("ttl" in t3)
    res["gate_isError"] = r3.get("isError")

    # 未知方法 → 标准错误码
    bad = rpc({"jsonrpc": "2.0", "id": 6, "method": "nope"})
    res["unknown_method_code"] = (bad.get("error") or {}).get("code")
    mcp.terminate()
finally:
    eng.terminate()
    kill_port(PORT)

ok = (res.get("initialize", {}).get("serverInfo", {}).get("name") == "pae"
      and res.get("tools_count", 0) >= 25
      and len(res.get("has_key_tools") or []) == 6
      and res.get("schema_ok")
      and res.get("read_call_ok")
      and res.get("write_call_ok")
      and res.get("gate_rejection_surfaced") and res.get("gate_isError") is True
      and res.get("unknown_method_code") == -32601)
res["T18"] = "PASS" if ok else "FAIL"
(ART / "t18_mcp.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(res, ensure_ascii=False, indent=1))
print("T18 MCP:", res["T18"])