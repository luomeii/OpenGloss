"""PAE core state model. Spec: 26号算法规格 (authoritative)."""
from dataclasses import dataclass
from typing import Optional
import math

DEFAULTS = dict(
    # w_known 1.5→4.5（2026-10-02 用户拍板 M12-C）：「点认识了」是最强显式信号，
    # 旧值只顶 1.2 次曝光，点完还要被标 4-5 天。新值：点一次 S 0.32→0.55，点两次出带。
    half_life_days=90.0, E0=6.0, p0=0.05, prior_tau=2.0, prior_free=3,
    rho=0.4, day_bonus=1.25, w_hover=0.3, w_known=4.5,
    eps_active=0.05, N_min=30, band_lo=0.10, band_hi=0.55,
    band_p_lo=0.25, band_p_hi=0.75, drift_delta=0.01,
    late_amortize_K=500, w_quiz=1.0,
)
RANGES = dict(
    half_life_days=(14, 365), E0=(2, 20), p0=(0, 0.2), prior_tau=(1, 5),
    rho=(0.1, 0.9), day_bonus=(1.0, 2.0), w_hover=(0, 1),
    w_known=(0.5, 5.0),   # M12-C（2026-10-02 用户拍板）：1.5→4.5，上限 3→5
    eps_active=(0, 0.5), N_min=(10, 100), band_lo=(0.01, 0.99), band_hi=(0.01, 0.99),
)

@dataclass
class WordState:
    lemma_id: str
    sense_id: int = 0
    E: float = 0.0
    t_E: Optional[int] = None
    n_enc: int = 0
    n_hover: int = 0
    n_known: int = 0
    t_known: Optional[int] = None   # 最近一次「认识了」的时间戳（M12-C：3 天抑制用）
    session_id: Optional[str] = None
    sess_k: int = 0
    day_idx: Optional[int] = None
    pinned: bool = False
    mastered: bool = False

    def s_value(self, p=DEFAULTS) -> float:
        w = 1.0 if self.n_enc <= p["prior_free"] else math.exp(-(self.n_enc - p["prior_free"]) / p["prior_tau"])
        s_prior = p["p0"] * w
        s_ev = 1.0 - math.exp(-self.E / p["E0"])
        return 1.0 - (1.0 - s_prior) * (1.0 - s_ev)
