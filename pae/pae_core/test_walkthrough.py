"""PAE core walkthrough acceptance tests.

Spec-true values are dual-verified: an independent JS reimplementation and the
Python module agree to 6 decimals on every step (e1-e6), idempotency (e7),
family aggregation (e8), prior-only S, and the 180-day forgetting trajectory.

Note on the design doc walkthrough table: the original table has ~4.3e-4 drift
at e5/e6 (the table skipped the 30-minute decay before known_click; spec 2.4
requires decay-then-add for evidence events). The spec formulas are authoritative;
values below follow the spec exactly.
"""
import math

import pytest

from pae_core.state import DEFAULTS, WordState
from pae_core import fold


def D(k):
    return k * 86400


def ev(t, typ, **kw):
    return dict(ts=t, type=typ, lemma_id="abandon", **kw)


def run_walkthrough():
    states, seen, p = {}, set(), dict(DEFAULTS)
    events = [
        ev(D(0) + 32400, "encounter", session_id="A", ctx=1.0, idem_key="k1"),
        ev(D(0) + 32700, "encounter", session_id="A", ctx=1.0, idem_key="k2"),
        ev(D(0) + 33000, "hover"),
        ev(D(1) + 28800, "encounter", session_id="B", ctx=1.0, idem_key="k3"),
        ev(D(1) + 30600, "known_click"),
        ev(D(11) + 28800, "encounter", session_id="C", ctx=1.0, idem_key="k4"),
    ]
    snaps = []
    for e in events:
        fold.fold_event(states, e, p, seen)
        st = states[("abandon", 0)]
        snaps.append((st.E, st.s_value()))
    return states, seen, p, events, snaps


# (E, S) after each event — dual-verified spec-true values
EXPECTED = [
    (1.000000, 0.195842),  # e1 first encounter, sess A
    (1.399973, 0.247702),  # e2 same session, rho-damped
    (1.699936, 0.284387),  # e3 hover +0.3
    (2.937525, 0.417765),  # e4 new session, cross-day bonus x1.25
    (7.437054, 0.724950),  # e5 known_click +4.5 (M12-C, after 30min decay)
    (8.136885, 0.750162),  # e6 cross-day x1.25 (after 10d decay)
]


@pytest.mark.parametrize("i", range(len(EXPECTED)))
def test_walkthrough_step(i):
    *_, snaps = run_walkthrough()
    got_e, got_s = snaps[i]
    exp_e, exp_s = EXPECTED[i]
    assert got_e == pytest.approx(exp_e, abs=1e-5)
    assert got_s == pytest.approx(exp_s, abs=1e-5)


def test_e7_idempotent_duplicate_event():
    states, seen, p, events, _ = run_walkthrough()
    before = states[("abandon", 0)]
    dup = dict(events[-1])
    dup["ts"] = D(11) + 28900  # same idem_key k4, later timestamp
    fold.fold_event(states, dup, p, seen)
    after = states[("abandon", 0)]
    assert (after.E, after.n_enc, after.s_value()) == (
        before.E, before.n_enc, before.s_value())


def test_e8_family_noisy_or():
    fam = fold.family_noisy_or([0.603045, 0.195842])
    assert fam == pytest.approx(0.680785, abs=1e-5)


def test_prior_only_s():
    st = WordState(lemma_id="x")
    assert st.s_value() == pytest.approx(0.05, abs=1e-9)


def test_long_forget_reenters_band():
    """180 days of decay from the e6 state: S falls back into the learning band.

    S = 1 - (1 - p0*w) * exp(-E_decayed/E0)   [noisy-or closed form]
    """
    states, _, _, _, _ = run_walkthrough()
    st = states[("abandon", 0)]
    e_decayed = st.E * math.exp(-math.log(2) * (180 * 86400) / (90 * 86400))
    w = math.exp(-(st.n_enc - DEFAULTS["prior_free"]) / DEFAULTS["prior_tau"])
    s = 1.0 - (1.0 - DEFAULTS["p0"] * w) * math.exp(-e_decayed / DEFAULTS["E0"])
    assert s == pytest.approx(0.30915, abs=1e-4)   # M12-C：w_known 4.5 后 abandon 的 E 更高


def test_decide_bands():
    assert fold.decide(0.05, 0.10, 0.55, False, False) == "SCAFFOLD"
    assert fold.decide(0.30, 0.10, 0.55, False, False) == "ANNOTATE"
    assert fold.decide(0.80, 0.10, 0.55, False, False) == "NONE"
    assert fold.decide(0.01, 0.10, 0.55, True, False) == "ANNOTATE"   # pinned
    assert fold.decide(0.99, 0.10, 0.55, False, True) == "NONE"       # mastered


def test_quantile_r7():
    xs = [0.1, 0.2, 0.3, 0.4, 0.5]
    assert fold.quantile_r7(xs, 0.5) == pytest.approx(0.3, abs=1e-9)
    assert fold.quantile_r7(xs, 0.25) == pytest.approx(0.2, abs=1e-9)
    assert fold.quantile_r7(xs, 0.0) == pytest.approx(0.1, abs=1e-9)
    assert fold.quantile_r7(xs, 1.0) == pytest.approx(0.5, abs=1e-9)


def test_same_session_spam_is_bounded():
    states, seen, p = {}, set(), dict(DEFAULTS)
    t = D(0) + 32400
    for i in range(40):
        fold.fold_event(states, dict(ts=t, type="encounter", lemma_id="w",
                                     session_id="S", ctx=1.0, idem_key=f"s{i}"), p, seen)
        t += 60
    st = states[("w", 0)]
    assert st.E < 1.0 / (1.0 - DEFAULTS["rho"]) + 1.0  # bounded, no explosion


def test_observed_events_do_not_touch_state():
    states, _, p, _, _ = run_walkthrough()
    before = states[("abandon", 0)]
    for typ in ["gloss_cached", "annotation_shown", "chat_turn", "compose",
                "grammar_shown", "decision_applied", "param_changed"]:
        fold.fold_event(states, dict(ts=D(12), type=typ, lemma_id="abandon"), p, set())
    after = states[("abandon", 0)]
    assert (after.E, after.n_enc, after.t_E) == (before.E, before.n_enc, before.t_E)