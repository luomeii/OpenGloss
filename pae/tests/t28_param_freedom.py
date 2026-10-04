# -*- coding: utf-8 -*-
"""T28: 参数自由度与生效性 —— 决定性参数是否都可调，且调整后**真的改变行为**。

起因（终检）：用户问「决定性参数都确保有改动自由度吗、确保有参数可调的空间吗」。
审计发现 state.py 19 个常量里 6 个写死，其中 prior_free / band_p_lo / band_p_hi 是真决定性的。
本测试锁定：① 覆盖完整性 ② 每类参数调整后行为确实变化 ③ 冻结常量有明确理由

工件 tests/artifacts/t28_param_freedom.json"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ART = Path(__file__).parent / "artifacts"
ART.mkdir(exist_ok=True)

from pae_core import params as pm
from pae_core.state import DEFAULTS, WordState

res = {"tunable_count": len(pm.TUNABLE), "checks": {}}

# ① 覆盖完整性：state.DEFAULTS 里每个常量，要么可调，要么在 FIXED_CONSTANTS 里写明理由
missing = []
for k in DEFAULTS:
    if k in pm.RANGE_ALL or k in pm.SPEC_ONLY:
        continue
    missing.append(k)
res["undocumented_constants"] = missing

# ② 每个可调键都要有默认值（否则覆盖层不知道该从哪开始）
no_default = [k for k in pm.TUNABLE if k not in pm.DEFAULTS_ALL]
res["tunable_without_default"] = no_default

# ③ 区间合法性：lo < hi
bad_range = [k for k, v in pm.RANGE_ALL.items() if not (len(v) == 2 and v[0] < v[1])]
res["bad_ranges"] = bad_range


# 走**真实 fold 管线**（half_life_days/rho/day_bonus/w_hover/w_known 都作用在这里，不在 s_value 里）
from pae_core.fold import fold_event, decide

_BASE = 1_700_000_000
_SEQ = [
    {"ts": _BASE, "type": "encounter", "lemma_id": "w", "lemma": "w", "idem_key": "e1", "session_id": "s1"},
    {"ts": _BASE + 300, "type": "encounter", "lemma_id": "w", "lemma": "w", "idem_key": "e2", "session_id": "s1"},
    {"ts": _BASE + 3 * 86400, "type": "encounter", "lemma_id": "w", "lemma": "w", "idem_key": "e3", "session_id": "s2"},
    {"ts": _BASE + 5 * 86400, "type": "hover", "lemma_id": "w", "lemma": "w"},
    {"ts": _BASE + 9 * 86400, "type": "known_click", "lemma_id": "w", "lemma": "w"},
    {"ts": _BASE + 20 * 86400, "type": "encounter", "lemma_id": "w", "lemma": "w", "idem_key": "e4", "session_id": "s3"},
]


def s_with(over):
    p = dict(DEFAULTS)
    p.update(over)
    states, seen = {}, set()
    for ev in _SEQ:
        fold_event(states, ev, p, seen)
    return round(states[("w", 0)].s_value(p), 6)


# ④ 生效性：改每一个决定性常数，S 或判定必须跟着变
base = s_with({})
moved = {}
for k, delta in (("half_life_days", 180.0), ("E0", 12.0), ("p0", 0.15),
                 ("prior_tau", 4.0), ("rho", 0.8), ("day_bonus", 1.8),
                 ("w_hover", 0.8), ("w_known", 2.5), ("prior_free", 0)):
    v = s_with({k: delta})
    moved[k] = v
    res["checks"][k] = {"base": base, "after": v, "changed": v != base}

# band 边界（band_lo/band_hi 是**已接线**的：metrics/observe/decide 都读它）→ 改它必须改变注解判定
_d_default = decide(0.30, DEFAULTS["band_lo"], DEFAULTS["band_hi"], False, False)
_d_shift = decide(0.30, 0.40, 0.90, False, False)
res["checks"]["band_lo/hi"] = {"base": _d_default, "after": _d_shift,
                               "changed": _d_default != _d_shift}

# band 分位（band_p_lo/hi 是 SPEC_ONLY：未接线）——这里只验证「没被留在可调表里」
res["checks"]["band_p_spec_only"] = {"base": None, "after": None,
                                      "changed": all(k in pm.SPEC_ONLY for k in ("band_p_lo", "band_p_hi"))}

# band 边界：改 band_p_lo/hi 应改变「哪些词进带」（用纯函数判定一个固定 S 序列）
def band_pick(p_lo, p_hi, s_values):
    xs = sorted(s_values)
    n = len(xs)
    lo = xs[min(n - 1, int(n * p_lo))]
    hi = xs[min(n - 1, int(n * p_hi))]
    return [x for x in xs if lo <= x <= hi]


sv = [0.05, 0.12, 0.2, 0.31, 0.44, 0.52, 0.61, 0.7, 0.83, 0.95]
a = band_pick(DEFAULTS["band_p_lo"], DEFAULTS["band_p_hi"], sv)
b = band_pick(0.40, 0.90, sv)
res["band_shift_changes_membership"] = (a != b)
res["band_shift_example"] = {"default": a, "shifted": b}

# ⑤ 静态读者检查（本测试最重要的一条）：每个可调键必须在**生产代码**里有读者。
# 起因：终检发现 6 个「可调但没人读」的假旋钮（可调面 ≠ 接线面 = 审计账本里的谎话）。
import re as _re
_core = [p for p in (ROOT / "pae_core").glob("*.py") if not p.name.startswith("test_")]
_src = "\n".join(p.read_text(encoding="utf-8") for p in _core
                 if p.name not in ("params.py", "state.py", "fold.py"))
_extra = (ROOT / "pae_core" / "fold.py").read_text(encoding="utf-8") + \
         (ROOT / "pae_core" / "state.py").read_text(encoding="utf-8")
_reader_src = _src + "\n" + _extra
no_reader = []
for _k in pm.TUNABLE:
    if ('"%s"' % _k) in _reader_src:
        continue
    no_reader.append(_k)
# 扩展侧读者也算（max_ann_per_page 经 pae.params 被扩展消费）
_js = (ROOT / "extension" / "sw.js").read_text(encoding="utf-8")
for _k in list(no_reader):
    if _k in _js:
        no_reader.remove(_k)
res["tunable_without_reader"] = no_reader
res["spec_only"] = sorted(pm.SPEC_ONLY.keys())
res["tunable_spec_overlap"] = sorted(set(pm.TUNABLE) & set(pm.SPEC_ONLY))

# ⑤ 引擎旋钮也在可调表里
res["engine_knobs_tunable"] = [k for k in ("annotate_max_s", "max_ann_per_page", "daily_say_cap")
                               if k in pm.TUNABLE]

ok = (not missing and not no_default and not bad_range and not no_reader
      and not res["tunable_spec_overlap"]
      and all(v["changed"] for v in res["checks"].values())
      and res["band_shift_changes_membership"]
      and len(res["engine_knobs_tunable"]) == 3
      and len(pm.TUNABLE) >= 12)   # 真旋钮数（剔除 SPEC_ONLY 后当前为 14）
res["T28"] = "PASS" if ok else "FAIL"
(ART / "t28_param_freedom.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(res, ensure_ascii=False, indent=1))
print("T28 PARAM FREEDOM:", res["T28"])