# -*- coding: utf-8 -*-
"""T13: 规格一致性 —— 防止「机制/算法偏离最初要求」。

四道钉：
1. 冻结文件哈希（state/fold/lemmatize 一行不许变）
2. 26 号算法规格的常数逐个对上
3. 规格文档里写死的数值行为（首遭遇 S=0.195842）实测复现
4. 事件溯源的结构不变量：**增量路径 ≡ 全量重放**（同一份事件，实时折叠 vs 重启重放，状态逐项相等）

工件 tests/artifacts/t13_conformance.json
"""
import hashlib
import json
import os
import pathlib
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ART = pathlib.Path(__file__).parent / "artifacts"
ART.mkdir(exist_ok=True)

from pae_core import engine as eng_mod          # noqa: E402
from pae_core.state import DEFAULTS, RANGES      # noqa: E402

res = {}
# ---- 1) 冻结文件哈希 ----
baseline = json.loads((pathlib.Path(__file__).parent / "frozen_hashes.json").read_text(encoding="utf-8"))
now = {}
for f in ("state.py", "fold.py", "lemmatize.py"):
    now[f] = hashlib.sha256((ROOT / "pae_core" / f).read_bytes()).hexdigest()
res["frozen_unchanged"] = {f: (now[f] == baseline.get(f)) for f in now}

# ---- 2) 26 号算法规格常数 ----
SPEC = {
    "half_life_days": 90.0, "E0": 6.0, "p0": 0.05, "prior_tau": 2.0, "prior_free": 3,
    "rho": 0.4, "day_bonus": 1.25, "w_hover": 0.3, "w_known": 4.5,   # M12-C（2026-10-02 用户拍板）：1.5→4.5
    "eps_active": 0.05, "N_min": 30, "band_lo": 0.10, "band_hi": 0.55,
    "band_p_lo": 0.25, "band_p_hi": 0.75,
}
bad = {k: {"spec": v, "actual": DEFAULTS.get(k)} for k, v in SPEC.items() if DEFAULTS.get(k) != v}
res["spec_constants_mismatch"] = bad
res["annotate_max_s"] = eng_mod.ANNOTATE_MAX_S
res["budget"] = eng_mod.BUDGET
res["ranges_cover_tunables"] = all(k in RANGES for k in
                                   ("half_life_days", "rho", "w_hover", "w_known", "band_lo", "band_hi"))

# ---- 3) 规格写死的行为：首遭遇 S ----
d = pathlib.Path(tempfile.mkdtemp())
LEMMA = str(ROOT.parent / "resources" / "ECDICT" / "lemma.en.txt")
e = eng_mod.Engine(str(d / "t13.db"), LEMMA)
out = e.annotate("contention", page_id="t13-first")
first_s = out[0]["s_value"] if out else None
res["first_encounter_s"] = first_s
res["first_encounter_matches_spec"] = (first_s is not None and abs(first_s - 0.196) <= 0.002)

# ---- 4) 增量 ≡ 全量 ----
for i in range(12):
    e.annotate("contention scheduler intensity quartz", page_id="t13-p%d" % i,
               session_id="s%d" % (i % 3))
e.record_event("hover", "quartz", metas={"session_id": "s1"})
e.record_event("known_click", "intensity")
live = {k: round(st.s_value(e.params), 6) for k, st in e.states.items()}

e2 = eng_mod.Engine(str(d / "t13.db"), LEMMA)
replay = {k: round(st.s_value(e2.params), 6) for k, st in e2.states.items()}
res["live_count"] = len(live)
res["replay_count"] = len(replay)
diff = {str(k): {"live": live.get(k), "replay": replay.get(k)} for k in set(live) | set(replay)
        if live.get(k) != replay.get(k)}
res["incremental_equals_full"] = (not diff)
res["diff_sample"] = dict(list(diff.items())[:5])

ok = (all(res["frozen_unchanged"].values()) and not res["spec_constants_mismatch"]
      and res["annotate_max_s"] == 0.75 and res["budget"] == 8 and res["ranges_cover_tunables"]
      and res["first_encounter_matches_spec"] and res["incremental_equals_full"])
res["T13"] = "PASS" if ok else "FAIL"
(ART / "t13_conformance.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(res, ensure_ascii=False, indent=1))
print("T13 CONFORMANCE:", res["T13"])
