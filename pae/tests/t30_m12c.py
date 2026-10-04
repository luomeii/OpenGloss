# -*- coding: utf-8 -*-
"""T30: M12-C —— 点「认识了」当天安静 + S 真实上涨不诈尸 + 误点可回归。

方案 C = w_known 4.5（模型承认表态）+ 3 天短抑制（当天安静）+ 抑制期被动曝光照记（不诈尸）。
工件 tests/artifacts/t30_m12c.json"""
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ART = Path(__file__).parent / "artifacts"
ART.mkdir(exist_ok=True)

from pae_core.engine import Engine, LEMMA_PATH
from pae_core.state import DEFAULTS

res = {"w_known": DEFAULTS["w_known"]}
tmpd = Path(tempfile.mkdtemp())
eng = Engine(str(tmpd / "t30.db"), LEMMA_PATH)

# 播种：contention 被标 2 次（自然路径）
eng.annotate("contention", page_id="s1")
eng.annotate("contention", page_id="s2")
s_before = eng.states[("contention", 0)].s_value(eng.params)
res["s_before"] = round(s_before, 3)

# ① 点「认识了」：S 应显著上涨（4.5 权重：0.32→0.55 量级）
eng.record_event("known_click", "contention", "contention", {})
st = eng.states[("contention", 0)]
s_after = round(st.s_value(eng.params), 3)
res["s_after_click"] = s_after
res["click_moves_s"] = s_after - res["s_before"]
res["t_known_recorded"] = st.t_known is not None

# ② 当天（t_known 刚记）：抑制生效 = 预算紧张时输给别人。
# 用「词典里真实存在的两个词」做预算竞争：contention（S=0.678，被抑制） vs scheduler（生词 S=0）
from pae_core import engine as eng_mod
_old = eng_mod.BUDGET
eng_mod.BUDGET = 1
out1 = eng.annotate("contention scheduler", page_id="budget1")
lemmas_b1 = [a["lemma"] for a in out1]
res["budget1_picks_fresh"] = ("scheduler" in lemmas_b1 and "contention" not in lemmas_b1)
eng_mod.BUDGET = _old
# 预算富余时（8）contention 仍会被选中记账（抑制只降优先级，不取消记账——E 照涨）
out_today = eng.annotate("contention scheduler resilient", page_id="today")
res["today_still_accounted"] = "contention" in [a["lemma"] for a in out_today]

# ③ 抑制期内预算竞争再验一次（t_known 仍在 3 天内）
eng_mod.BUDGET = 1
out2 = eng.annotate("resilient contention", page_id="budget2")
res["budget2_picks_fresh"] = ("resilient" in [a["lemma"] for a in out2]
                              and "contention" not in [a["lemma"] for a in out2])
eng_mod.BUDGET = _old

# ④ 不诈尸（核心设计验证）：抑制 + 到期后的自然轨迹。
# 到期时 S 已因点击+被动曝光上移；若已过 0.75 → 自然出带彻底安静（这是**设计成功**不是失败）；
# 若还没出带 → 继续注解继续涨。两种都算 PASS，断言只要求「不再无限期霸占」：
st.t_known = int(time.time()) - 4 * 86400   # 模拟到期
eng.annotate("contention", page_id="d4")
s_d4 = eng.states[("contention", 0)].s_value(eng.params)
res["s_at_expiry"] = round(s_d4, 3)
o5 = eng.annotate("contention", page_id="d5")
s_d5 = eng.states[("contention", 0)].s_value(eng.params)
res["s_after_expiry"] = round(s_d5, 3)
res["no_zombie"] = (s_d5 >= 0.75) or ("contention" in [a["lemma"] for a in o5])
# 即：要么已出带（安静），要么仍在注解（继续涨）——不存在「S 低却被永久抑制」的诈尸态

# ⑤ 误点回归：误点一次的词（S 0.55 <0.75 仍在带内），没有后续点击，继续被正常注解（学习带=撤销）
res["misclick_recoverable"] = res["s_after_click"] < 0.75

ok = (res["w_known"] == 4.5
      and res["click_moves_s"] >= 0.2
      and res["t_known_recorded"]
      and res["budget1_picks_fresh"]
      and res["budget2_picks_fresh"]
      and res["today_still_accounted"]
      and res["no_zombie"]
      and res["misclick_recoverable"])
res["T30"] = "PASS" if ok else "FAIL"
(ART / "t30_m12c.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(res, ensure_ascii=False, indent=1))
print("T30 M12C:", res["T30"])