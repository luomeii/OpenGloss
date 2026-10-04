# -*- coding: utf-8 -*-
"""PAE persona renderer: 参数 + 模板 → 一份发给 LLM 的系统提示词。

设计要点（用户 2026-10-01 拍板）：人格的本质就是归一成一份提示词。
所以这里只有两件东西：一个可改的模板 + 一组参数口。改人格 = 改参数，不动代码。
"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TPL = ROOT / "persona" / "template.md"
DEFAULT_PARAMS = ROOT / "persona" / "default.json"

# 5 个轴 → 三档白话（参数口的意义：用户改数字，模板自己翻译成人话）
BANDS = {
    "warmth": ("客气疏离，公事公办", "平和自然", "热络亲近，像熟人"),
    "verbosity": ("能一句话就一句话", "两三句讲清", "愿意多说几句，但仍有节制"),
    "humor": ("正经，不调侃", "偶尔调侃一句", "爱损人，但不下作"),
    "formality": ("随性口语，可以省主语", "自然，正常说话", "偏正式书面"),
    "proactivity": ("只在被召唤时说话", "偶尔主动提一句", "常主动提点，但看场合"),
}


def _band(axis, v):
    lo, mid, hi = BANDS[axis]
    try:
        x = float(v)
    except Exception:
        return mid
    return lo if x < 0.34 else (mid if x < 0.67 else hi)


def params_path(path=None):
    """参数文件位置：显式 path > 环境变量 PAE_PERSONA_PATH > 默认。"""
    if path:
        return Path(path)
    return Path(os.environ.get("PAE_PERSONA_PATH") or DEFAULT_PARAMS)


def save_params(params, path=None):
    """保存参数口（面板/提案都走这里）。返回 (ok, issues, saved_to)。"""
    issues = validate(params)
    if issues:
        return False, issues, None
    p = params_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(params, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True, [], str(p)


def load_params(path=None):
    p = params_path(path)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def validate(params):
    """返回问题列表（空 = 合法）。"""
    issues = []
    t = params.get("traits") or {}
    for k in BANDS:
        if k not in t:
            issues.append("missing trait: " + k)
        else:
            try:
                v = float(t[k])
                if not (0.0 <= v <= 1.0):
                    issues.append("out of range [0,1]: " + k)
            except Exception:
                issues.append("not a number: " + k)
    q = params.get("quirks") or []
    if len(q) > 8:
        issues.append("quirks > 8 (spec cap)")
    if not (params.get("backstory_seed") or "").strip():
        issues.append("backstory_seed is empty")
    return issues


def bands(params):
    """5 轴数值 → 白话（面板显示用，与渲染同源）。"""
    t = (params or {}).get("traits") or {}
    return {k: _band(k, t.get(k, 0.5)) for k in BANDS}


def render(params=None, context_block="", template_path=None):
    """参数 + 模板 → 系统提示词字符串。context_block 由引擎实时注入。"""
    p = params or load_params()
    tpl = Path(template_path or TPL).read_text(encoding="utf-8")
    traits = p.get("traits") or {}
    quirks = p.get("quirks") or []
    forbidden = p.get("forbidden") or []
    style = (p.get("style_notes") or "").strip()

    table = {
        "{{name}}": str(p.get("name") or "小P"),
        "{{address_user}}": str(p.get("address_user") or "你"),
        "{{backstory_seed}}": str(p.get("backstory_seed") or ""),
        "{{language}}": str(p.get("language") or "中文"),
        "{{quirks_list}}": "\n".join("- " + str(x) for x in quirks) if quirks else "- （暂无）",
        "{{forbidden_list}}": "\n".join("- " + str(x) for x in forbidden) if forbidden else "- （暂无）",
        "{{style_notes}}": style if style else "（用户未填写；以模板默认与参数口为准）",
        "{{context_block}}": (context_block or "（本次没有额外状态）"),
    }
    for k in BANDS:
        table["{{traits.%s}}" % k] = str(traits.get(k, 0.5))
        table["{{trait_%s}}" % k] = _band(k, traits.get(k, 0.5))
    for slot, val in table.items():
        tpl = tpl.replace(slot, val)
    return tpl.strip() + "\n"
