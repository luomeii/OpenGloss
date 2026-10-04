# -*- coding: utf-8 -*-
"""T5: persona end-to-end — 渲染提示词 → 调 flash 模型 → 校验角色感与红线。

工件：tests/artifacts/t5_persona.json
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pae_core import llm
from pae_core.persona import load_params, render, validate

ART = Path(__file__).parent / "artifacts"
ART.mkdir(exist_ok=True)

CTX = """- 最近 7 天：遭遇 128 次，新增词 21 个，学习带内 47 词
- 他刚在页面里连续悬停 contention（S=0.31，在带内），还悬停了 2 次就不看了
- 今天你已经发过 1 条主动消息，他没回
- 他昨天把 arbitration 点了「认识了」（S 已推到 0.93）"""

SCENARIOS = [
    ("主动提点", "（系统事件：用户连着悬停 contention 三次又走开）你现在要说一句话。只输出这句话本身。"),
    ("用户提问", "用户问：contention 和 conflict 有啥区别？只输出你的回答。"),
    ("用户问进度", "用户问：我最近学得咋样？只输出你的回答。"),
]

FORBIDDEN = ["你学会了吗", "学会了没", "需要我详细解释", "要不要我", "想不想我", "需要我展开", "要我帮你复习"]
META_OPEN = ("作为", "我是", "我是一个", "根据设定", "按设定", "好的，", "嗯，")


def main():
    p = load_params()
    issues = validate(p)
    system = render(p, context_block=CTX)

    # 参数口验证：改 humor 应该改变渲染结果（证明「改数字就能改人格」）
    p2 = json.loads(json.dumps(p))
    p2["traits"]["humor"] = 0.9
    rendered_hi = render(p2, context_block=CTX)
    slot_ok = ("爱损人" in rendered_hi) and ("爱损人" not in system)

    results = []
    for name, user_msg in SCENARIOS:
        rec = {"scenario": name, "user": user_msg}
        try:
            r = llm.chat([{"role": "system", "content": system},
                          {"role": "user", "content": user_msg}],
                         temperature=0.7, timeout=120)
            c = r["content"]
            rec["reply"] = c
            rec["len"] = len(c)
            rec["usage"] = r.get("usage")
            rec["violations"] = [w for w in FORBIDDEN if w in c]
            rec["meta_open"] = c.strip().startswith(META_OPEN)
            rec["has_chinese"] = bool(re.search(r"[\u4e00-\u9fff]", c))
            rec["ok"] = (0 < len(c) <= 200) and not rec["violations"] and not rec["meta_open"] and rec["has_chinese"]
        except Exception as e:
            rec["error"] = "%s: %s" % (type(e).__name__, str(e)[:200])
            rec["ok"] = False
        results.append(rec)

    ok = slot_ok and all(x["ok"] for x in results)
    out = {"persona_params": p, "validate": issues or "PASS", "system_prompt": system,
           "slot_test_ok": slot_ok, "scenarios": results, "T5": "PASS" if ok else "FAIL"}
    (ART / "t5_persona.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print("参数口(改humor→爱损人):", "PASS" if slot_ok else "FAIL")
    for r in results:
        print("[%s] %s | %s" % ("OK" if r["ok"] else "NG", r["scenario"], (r.get("reply") or r.get("error", ""))[:110]))
    print("T5 PERSONA:", out["T5"])


if __name__ == "__main__":
    main()
