# -*- coding: utf-8 -*-
"""PAE companion LLM provider (OpenAI-compatible chat completions).

这是「换大脑」的唯一接缝：内置 companion、你的 LLM、外部智能体，都从这一层走。
配置解析优先级：环境变量（PAE_LLM_BASE/KEY/MODEL） > config.json llm 段 > 内置缺省。
API key 只从环境变量或本机 config.json 读取——config.json 里的 key 值已被 .gitignore
防泄漏的惯例约束：请勿把真实 key 写进仓库内文件，用环境变量或本地未跟踪文件。
"""
import json
import os
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config.json"

DEFAULTS = {"base": "https://api.deepseek.com", "model": "deepseek-chat", "key": "", "thinking": "disabled"}


def load_provider():
    """返回 {base, key, model}。环境变量最优先（便于测试与换脑）。"""
    cfg = {}
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8")).get("llm", {}) or {}
    except Exception:
        cfg = {}
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in cfg.items() if v})
    if os.environ.get("PAE_LLM_BASE"):
        merged["base"] = os.environ["PAE_LLM_BASE"]
    if os.environ.get("PAE_LLM_KEY"):
        merged["key"] = os.environ["PAE_LLM_KEY"]
    if os.environ.get("PAE_LLM_MODEL"):
        merged["model"] = os.environ["PAE_LLM_MODEL"]
    return merged


def chat(messages, temperature=0.7, timeout=90, max_tokens=None, thinking=None):
    """OpenAI 兼容 chat。thinking: None=读配置 / True=开 / False=关（实测关掉快 3 倍，见
    tests/artifacts/thinking_probe.json：1.29s→0.44s，reasoning_tokens 33→0）。
    短句（companion say/hint）建议关；需要判断的（义项判定/参数提案）建议开。"""
    p = load_provider()
    if not p.get("key"):
        raise RuntimeError("no LLM key configured")
    payload = {"model": p["model"], "messages": messages, "temperature": temperature}
    if max_tokens:
        payload["max_tokens"] = max_tokens
    mode = thinking if thinking is not None else p.get("thinking")
    if mode is True or mode == "enabled":
        payload["thinking"] = {"type": "enabled"}
    elif mode is False or mode == "disabled":
        payload["thinking"] = {"type": "disabled"}
    req = urllib.request.Request(
        p["base"].rstrip("/") + "/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + p["key"]},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        j = json.loads(r.read().decode("utf-8"))
    msg = (j.get("choices") or [{}])[0].get("message") or {}
    return {
        "content": (msg.get("content") or "").strip(),
        "reasoning": (msg.get("reasoning_content") or "").strip(),
        "usage": j.get("usage") or {},
        "model": j.get("model"),
    }
