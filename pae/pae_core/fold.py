"""PAE fold engine. Spec: 26号算法规格. events -> state (pure function)."""
import math
from .state import DEFAULTS, WordState

LN2 = math.log(2)
_OBS_ONLY = {"gloss_cached", "annotation_shown", "chat_turn", "compose",
             "grammar_shown", "decision_applied", "param_changed"}

def _clamp(x, lo, hi):
    return lo if x < lo else (hi if x > hi else x)

def decay(E: float, t_E, t_now: int, half_life_days: float) -> float:
    if E == 0.0 or t_E is None:
        return E
    return E * math.exp(-LN2 * (t_now - t_E) / (half_life_days * 86400.0))

def _get_state(states, key) -> WordState:
    if key not in states:
        states[key] = WordState(lemma_id=key[0], sense_id=key[1])
    return states[key]

def fold_event(states: dict, ev: dict, params: dict, seen_idem: set) -> None:
    t = ev["ts"]
    key = (ev["lemma_id"], ev.get("sense_id", 0))
    etype = ev["type"]
    if etype in _OBS_ONLY:
        return
    st = _get_state(states, key)
    p = params
    if etype == "encounter":
        idem = ev.get("idem_key")
        if idem is not None and idem in seen_idem:
            return
        if idem is not None:
            seen_idem.add(idem)
        st.E = decay(st.E, st.t_E, t, p["half_life_days"]); st.t_E = t
        q = 1.0 * _clamp(ev.get("ctx", 1.0), 0.5, 1.5)
        d = t // 86400
        if st.session_id is not None and ev.get("session_id") == st.session_id:
            st.sess_k += 1
            gain = q * p["rho"] ** (st.sess_k - 1)
        else:
            st.sess_k = 1
            bonus = p["day_bonus"] if (st.day_idx is not None and d > st.day_idx) else 1.0
            gain = q * bonus
        st.session_id = ev.get("session_id")
        st.day_idx = d
        st.E += gain
        st.n_enc += 1
    elif etype == "hover":
        st.E = decay(st.E, st.t_E, t, p["half_life_days"]); st.t_E = t
        st.E += p["w_hover"]; st.n_hover += 1
    elif etype == "known_click":
        st.E = decay(st.E, st.t_E, t, p["half_life_days"]); st.t_E = t
        st.E += p["w_known"]; st.n_known += 1
        st.t_known = t   # M12-C：选择层 3 天抑制的依据（抑制期被动曝光照记，S 继续真实上涨）
    elif etype == "quiz_result":
        if ev.get("correct"):
            st.E = decay(st.E, st.t_E, t, p["half_life_days"]); st.t_E = t
            st.E += p.get("w_quiz", 1.0)
    elif etype == "user_pin":
        st.pinned = True
    elif etype == "user_mastered":
        st.mastered = True

def quantile_r7(sorted_xs, p: float) -> float:
    h = (len(sorted_xs) - 1) * p
    lo, hi = math.floor(h), math.ceil(h)
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (h - lo)

def decide(S_family: float, band_lo: float, band_hi: float, pinned: bool, mastered: bool) -> str:
    if pinned:
        return "ANNOTATE"
    if mastered:
        return "NONE"
    if S_family < band_lo:
        return "SCAFFOLD"
    if S_family <= band_hi:
        return "ANNOTATE"
    return "NONE"

def family_noisy_or(senses) -> float:
    prod = 1.0
    for s in senses:
        prod *= (1.0 - s)
    return 1.0 - prod
