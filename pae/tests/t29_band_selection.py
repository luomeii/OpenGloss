# -*- coding: utf-8 -*-
"""T29: 带优先选择层 —— 学习带真正参与选词（算法复审 D2 修复的验收）。

旧行为：按文本顺序取前 8 个有释义的词（学习状态只剩 S<max_s 二元闸，带只做报表）。
新行为（band_priority，默认）：pinned 最优先 → 带内词按 |S-带中点| 升序 → 带外垫后。

工件 tests/artifacts/t29_band_selection.json"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ART = Path(__file__).parent / "artifacts"
ART.mkdir(exist_ok=True)

from pae_core import engine as eng_mod
from pae_core.engine import Engine, LEMMA_PATH

res = {}
tmpd = Path(tempfile.mkdtemp())
eng = Engine(str(tmpd / "t29.db"), LEMMA_PATH)

# 播种走**自然路径**（record_event 有类型白名单，encounter 只能由 annotate 产生——这是引擎的正确设计）
# A（contention）：1 次遭遇 → S≈0.196，进带 [0.10,0.55]
eng.annotate("contention", page_id="seed-a")
res["contention_s_after_seed"] = round(eng.states[("contention", 0)].s_value(eng.params), 3)
# K（quartz）：9 次跨页遭遇 → E=9、S≈0.777≥0.75（8 次只有 0.737——刀刃之下），然后钉住
for i in range(9):
    eng.annotate("quartz", page_id="seed-k-%d" % i)
eng.record_event("user_pin", "quartz", "quartz", {})
st_q = eng.states.get(("quartz", 0))
res["quartz_s"] = round(st_q.s_value(eng.params), 3)
res["quartz_pinned"] = st_q.pinned

FRESH = ("scheduler resilient meticulous ubiquitous redundant perfunctory "
         "tenacious ephemeral daemon intensified")
TEXT = FRESH + " contention quartz"   # 带内词与钉住词都放在**文本最末**

# ① band_priority（默认）：文本末尾的带内词/钉住词必须被选中
out = eng.annotate(TEXT, page_id="t29-p1")
lemmas1 = [a["lemma"] for a in out]
res["band_priority_lemmas"] = lemmas1
res["band_word_selected"] = "contention" in lemmas1
res["pinned_word_selected"] = "quartz" in lemmas1

# ② text_order（旧模式）：按文本顺序，末尾两词不该被选中（对照组，证明行为真的变了）
eng_mod.SELECTION_MODE = "text_order"
out2 = eng.annotate(TEXT, page_id="t29-p2")
lemmas2 = [a["lemma"] for a in out2]
res["text_order_lemmas"] = lemmas2
res["text_order_band_word_excluded"] = "contention" not in lemmas2
res["text_order_pinned_excluded"] = "quartz" not in lemmas2
eng_mod.SELECTION_MODE = "band_priority"

# ③ 每请求预算仍然生效（8 词）
res["budget_respected"] = (len(lemmas1) <= 8 and len(lemmas2) <= 8)

# ④ 带外新词（S=0）不挤掉带内词：再注一次同文本（新页面），带内词仍在
out3 = eng.annotate(TEXT, page_id="t29-p3")
lemmas3 = [a["lemma"] for a in out3]
res["band_word_selected_again"] = "contention" in lemmas3

ok = (res["band_word_selected"] and res["pinned_word_selected"]
      and res["text_order_band_word_excluded"] and res["text_order_pinned_excluded"]
      and res["budget_respected"] and res["band_word_selected_again"]
      and res["quartz_s"] >= 0.75 and res["quartz_pinned"])
res["T29"] = "PASS" if ok else "FAIL"
(ART / "t29_band_selection.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(res, ensure_ascii=False, indent=1))
print("T29 BAND SELECTION:", res["T29"])