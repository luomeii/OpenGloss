# -*- coding: utf-8 -*-
"""T13: 规格一致性 —— 防止「机制/算法偏离最初要求」。

四道钉：
1. 冻结文件哈希（state/fold/lemmatize 一行不许变）
2. 26 号算法规格的常数逐个对上
3. 旧 encounter 初始 S=0.195842；新版自动选词只记接触事实，主动互动沿用原增量
4. 在参数不变时，实时状态与同一批已提交事件的完整重放一致

工件 tests/artifacts/t13_conformance.json
"""
import hashlib
import json
from contextlib import ExitStack, closing
import pathlib
import sqlite3
import sys
import tempfile
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ART = pathlib.Path(__file__).parent / "artifacts"
ART.mkdir(exist_ok=True)

from pae_core import engine as eng_mod          # noqa: E402
from pae_core.daily_sheet import build_daily_sheet, local_day_bounds  # noqa: E402
from pae_core.db import append_event, get_db, init_schema, load_events  # noqa: E402
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

# ---- 3) 旧历史、新接触和主动互动 ----
with tempfile.TemporaryDirectory(prefix="pae-t13-") as temp, ExitStack() as stack:
    fixture = pathlib.Path(temp)
    db = fixture / "events.db"
    dictionary = fixture / "dictionary.sqlite"
    lemma_map = fixture / "lemma.en.txt"
    lemma_map.write_text("", encoding="utf-8")
    words = ("contention", "scheduler", "intensity", "quartz")
    with closing(sqlite3.connect(dictionary)) as conn:
        conn.execute("CREATE TABLE dict_words(word TEXT PRIMARY KEY, translation TEXT, bnc INTEGER)")
        conn.execute("CREATE TABLE exchange_map(inflected TEXT, base TEXT)")
        conn.executemany("INSERT INTO dict_words VALUES (?, '测试释义', 0)", ((word,) for word in words))
        conn.commit()
    start, end = local_day_bounds("2026-06-03")
    now = start + 12 * 3600
    legacy_key = ("scheduler", 0)
    new_key = ("contention", 0)
    legacy_ts = now - 86400
    with closing(get_db(db)) as conn, patch("pae_core.db._now", return_value=legacy_ts):
        init_schema(conn)
        append_event(conn, "encounter", {"lemma_id": legacy_key[0], "session_id": "legacy"},
                     idem_key="t13-legacy")

    stack.enter_context(patch.object(eng_mod, "DICT_SQLITE_PATH", str(dictionary)))
    stack.enter_context(patch.object(eng_mod.time, "time", return_value=now))
    engine = eng_mod.Engine(db, lemma_map)
    stack.enter_context(closing(engine.conn))
    stack.enter_context(closing(engine.learning_conn))
    engine._proactive_dispatch_ts = now  # 禁止启动与本验收无关的后台任务。

    legacy = engine.states.get(legacy_key)
    res["legacy_first_encounter_s"] = round(legacy.s_value(engine.params), 6) if legacy else None
    res["legacy_first_encounter_matches_spec"] = bool(
        legacy and legacy.E == 1.0 and legacy.n_enc == 1 and legacy.t_E == legacy_ts
        and res["legacy_first_encounter_s"] == 0.195842
        and engine.contacts.get(legacy_key) and engine.contacts[legacy_key].count == 1)

    out = engine.annotate(new_key[0], page_id="t13-first")
    engine.annotate(new_key[0], page_id="t13-first")
    events = load_events(engine.conn)
    today = build_daily_sheet(db, dictionary, start, end)["rows"]
    res["first_selected_s"] = out[0]["s_value"] if out else None
    res["first_selected_is_contact_only"] = (
        len(out) == 1 and out[0]["lemma"] == new_key[0] and out[0]["counted"]
        and res["first_selected_s"] == 0.0 and new_key not in engine.states
        and new_key in engine.contacts and engine.contacts[new_key].count == 1
        and [(event["type"], event["payload"].get("lemma_id")) for event in events]
        == [("encounter", legacy_key[0]), ("contact_selected_v1", new_key[0])]
        and [(row["lemma"], row["encounters"]) for row in today] == [(new_key[0], 1)])

    for i in range(12):
        engine.annotate("contention intensity quartz", page_id=f"t13-p{i}", session_id=f"s{i % 3}")
    hover = engine.record_event("hover", "quartz", metas={"session_id": "s1"})
    known = engine.record_event("known_click", "intensity")
    hovered = engine.states.get(("quartz", 0))
    recognized = engine.states.get(("intensity", 0))
    res["active_evidence_matches_spec"] = bool(
        hover["ok"] and hover["counted"] and hovered and hovered.E == 0.3 and hovered.n_hover == 1
        and known["ok"] and known["counted"] and recognized
        and recognized.E == 4.5 and recognized.n_known == 1)

    # ---- 4) 相同参数下，增量 ≡ 全量 ----
    replay = eng_mod.Engine(db, lemma_map)
    stack.enter_context(closing(replay.conn))
    stack.enter_context(closing(replay.learning_conn))
    res["live_count"] = len(engine.states)
    res["replay_count"] = len(replay.states)
    res["incremental_equals_full"] = (
        engine.states == replay.states and engine.contacts == replay.contacts
        and engine.seen_idem == replay.seen_idem and new_key not in replay.states)

ok = (all(res["frozen_unchanged"].values()) and not res["spec_constants_mismatch"]
      and res["annotate_max_s"] == 0.75 and res["budget"] == 8 and res["ranges_cover_tunables"]
      and res["legacy_first_encounter_matches_spec"] and res["first_selected_is_contact_only"]
      and res["active_evidence_matches_spec"] and res["incremental_equals_full"])
res["T13"] = "PASS" if ok else "FAIL"
(ART / "t13_conformance.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(res, ensure_ascii=False, indent=1))
print("T13 CONFORMANCE:", res["T13"])
if not ok:
    sys.exit(1)
