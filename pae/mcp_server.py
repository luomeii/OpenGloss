#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""PAE MCP server（stdio）——把能力面清单包成 MCP 工具，供外部智能体挂载。

用法（Claude Code）：
    claude mcp add pae -- python pae/mcp_server.py
用法（其他支持 MCP 的客户端）：把上面的命令写进它的 MCP 配置即可。

设计：本进程是**薄客户端**——所有调用转发到 http://127.0.0.1:4815 的能力面，
因此鉴权、守门、配额、审计与内置角色完全一致（同一套能力，没有第二条路）。

环境变量：PAE_BASE（默认 http://127.0.0.1:4815）、PAE_KEY（可选；不带=本机用户，最高写权）
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("PAE_BASE", "http://127.0.0.1:4815").rstrip("/")
KEY = os.environ.get("PAE_KEY", "")
PROTOCOL = "2024-11-05"
SERVER = {"name": "pae", "version": "0.3.0"}


def _http(path, body=None, method=None):
    hdr = {"Content-Type": "application/json"}
    if KEY:
        hdr["X-PAE-Key"] = KEY
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=hdr, method=method)
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


def manifest():
    return _http("/v1/cap/manifest")


def _schema(cap):
    props = {}
    required = []
    for k, v in (cap.get("params") or {}).items():
        t = v.get("type") or "string"
        if t == "object":
            props[k] = {"type": "object", "description": v.get("description", "")}
        elif t == "array":
            props[k] = {"type": "array", "items": {}, "description": v.get("description", "")}
        else:
            props[k] = {"type": t, "description": v.get("description", "")}
        if v.get("enum"):
            props[k]["enum"] = v["enum"]
        if v.get("required"):
            required.append(k)
    return {"type": "object", "properties": props, "required": required}


def tools():
    out = []
    for c in manifest().get("capabilities") or []:
        if c.get("status") != "implemented":
            continue
        desc = c.get("description", "")
        if c.get("kind") == "observe":
            desc = "[只读] " + desc
        out.append({"name": c["name"].replace(".", "_"), "description": desc + ("｜原能力名：" + c["name"]),
                    "inputSchema": _schema(c)})
    return out


_TOOL2CAP = None


def tool_to_cap(name):
    """工具名 → 能力名。**用清单建表反查**，不再做字符串猜测。

    2026-10-02 审计抓到的真 bug：原实现 name.replace("_", ".", 1) 只还原第一个下划线，
    pae_memory_search → pae.memory_search（不是合法能力名）→ 15/40 项能力调用必 404。
    """
    global _TOOL2CAP
    if _TOOL2CAP is None:
        try:
            m = {}
            for c in manifest().get("capabilities") or []:
                m[c["name"].replace(".", "_")] = c["name"]
            if m:
                _TOOL2CAP = m      # 只有成功拿到清单才缓存；失败下次重试（别永久降级）
        except Exception:
            pass
    if _TOOL2CAP and name in _TOOL2CAP:   # 引擎不可用时优雅退化为兜底，不抛异常
        return _TOOL2CAP[name]
    if "." in name:                      # 调用方直接用了能力名（也接受）
        return name
    return name.replace("_", ".", 1)     # 兜底：老行为（单点名可用）


def call_tool(name, arguments):
    cap_name = tool_to_cap(name)
    try:
        body = _http("/v1/cap/" + cap_name, arguments or {})
        return {"content": [{"type": "text", "text": json.dumps(body, ensure_ascii=False)}],
                "isError": not body.get("ok", False)}, None
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8")
        except Exception:
            detail = ""
        return {"content": [{"type": "text", "text": "HTTP %d %s" % (e.code, detail[:500])}],
                "isError": True}, None
    except Exception as e:
        return {"content": [{"type": "text", "text": "%s: %s" % (type(e).__name__, str(e)[:200])}],
                "isError": True}, None


def handle(msg):
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER,
            "instructions": "PAE：本地被动英语习得引擎。只读能力随便用（看趋势/词/指标/记忆/人格）；"
                            "写能力有守门与配额，被拒时看返回的 gate_result 再调整。"}}
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": tools()}}
    if method == "tools/call":
        p = msg.get("params") or {}
        result, _ = call_tool(p.get("name") or "", p.get("arguments") or {})
        return {"jsonrpc": "2.0", "id": mid, "result": result}
    if method in ("ping",):
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": "Method not found: %s" % method}}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": None,
                                         "error": {"code": -32700, "message": "Parse error"}}) + "\n")
            sys.stdout.flush()
            continue
        resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()