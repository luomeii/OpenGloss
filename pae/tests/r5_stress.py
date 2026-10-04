
"""R5 事件增长压测：PAE fold 引擎的全量重放成本。

规模由 --n 决定：不带参数跑 100k + 1M 两档；nightly 只跑 --n 100000，1M 档不在
nightly 执行。每条判据只用「它自己那个规模」实测出来的数字，未跑的规模输出 N/A
并明确不参与判定（禁止用 100k 的数字冒充 1M）。结果写
tests/artifacts/r5_stress.json，实测 FAIL 时进程退出码 1。

只读本地样本，临时库 (tempfile.mkdtemp)，绝不触碰 pae/pae.db。
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

ART = Path(__file__).parent / "artifacts"
ART.mkdir(exist_ok=True)

from pae_core.db import DDL                      # noqa: E402
from pae_core.fold import fold_event             # noqa: E402
from pae_core.state import DEFAULTS              # noqa: E402

BATCH = 50000
LEMMAS = ["w%d" % i for i in range(2000)]
CTX_TEXT = "语境占位语境占位语境占位语境占位语境占位语境占位语境占位语境占位"
TS_BASE = 1750000000
TS_SPAN = 90 * 86400

REPLAY_SQL = "SELECT ts, type, payload, idem_key FROM events ORDER BY ts ASC, rowid ASC"


def payload_for(i):
    lemma = LEMMAS[i % 2000]
    ctx = (lemma + " " + CTX_TEXT)[:40]
    return json.dumps({
        "lemma_id": lemma,
        "lemma": lemma,
        "surface": lemma + "·S",
        "sentence_context": ctx,
        "page_id": "p" + str(i % 500),
    }, ensure_ascii=False)


def type_for(i):
    if i % 20 == 0:
        return "hover"
    if i % 20 == 7:
        return "known_click"
    return "encounter"


def make_conn(db_path):
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(DDL)
    return conn


def gen_events(conn, n):
    ins = ("INSERT INTO events(event_id, idem_key, ts, type, payload, source)"
           " VALUES(?,?,?,?,?,?)")
    t0 = time.perf_counter()
    for start in range(0, n, BATCH):
        stop = min(start + BATCH, n)
        rows = []
        ap = rows.append
        for i in range(start, stop):
            ts = TS_BASE + (i * 7) % TS_SPAN
            ap(("evt_r5_%d" % i, "k" + str(i), ts, type_for(i), payload_for(i), "stress"))
        conn.execute("BEGIN")
        conn.executemany(ins, rows)
        conn.execute("COMMIT")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return time.perf_counter() - t0


def replay(conn):
    states = {}
    seen_idem = set()
    fe = fold_event
    D = DEFAULTS
    loads = json.loads
    cur = conn.execute(REPLAY_SQL)
    n_rows = 0
    total_chars = 0
    t0 = time.perf_counter()
    for ts, etype, payload, idem_key in cur:
        n_rows += 1
        total_chars += len(payload)
        p = loads(payload)
        ev = {
            "ts": ts,
            "type": etype,
            "lemma_id": p["lemma_id"],
            "sense_id": p.get("sense_id", 0),
            "session_id": "s" + str(ts // 3600),
            "idem_key": idem_key,
            "ctx": 1.0,
        }
        fe(states, ev, D, seen_idem)
    dt = time.perf_counter() - t0
    return dt, n_rows, total_chars, len(states)


def measure(conn, total_chars, n):
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db_mb = os.path.getsize(DB_PATH) / (1024.0 * 1024.0)
    wal_mb = os.path.getsize(DB_PATH + "-wal") / (1024.0 * 1024.0) if os.path.exists(DB_PATH + "-wal") else 0.0
    return db_mb, wal_mb, total_chars / float(n)


def run_size(n):
    global DB_PATH
    tmp = tempfile.mkdtemp(prefix="pae_r5_")
    DB_PATH = os.path.join(tmp, "r5_%d.db" % n)
    conn = make_conn(DB_PATH)
    try:
        gen_s = gen_events(conn, n)
        replay_s, rows, total_chars, n_states = replay(conn)
        db_mb, wal_mb, avg_b = measure(conn, total_chars, n)
    finally:
        conn.close()
        shutil.rmtree(tmp, ignore_errors=True)
    rec = {
        "n": n,
        "gen_s": round(gen_s, 2),
        "replay_s": round(replay_s, 2),
        "db_mb": round(db_mb, 1),
        "avg_payload_b": round(avg_b, 1),
        "wal_after_ckpt_mb": round(wal_mb, 3),
    }
    print(json.dumps(rec, ensure_ascii=False))
    return rec, rows, n_states

def main():
    # --n 参数：只跑指定规模（nightly 用 100k 档；1M 档不在 nightly，须手动跑）
    sizes = [100000, 1000000]
    for i, a in enumerate(sys.argv[1:]):
        if a == '--n' and i + 1 < len(sys.argv):
            sizes = [int(sys.argv[i + 2])]
    print("R5 event-growth stress  (temp DB only, production pae.db untouched)")
    print("  本次实际规模: %s（nightly 只传 --n 100000；1M 档不在 nightly，须手动跑）"
          % ",".join(str(s) for s in sizes))
    recs = []
    for n in sizes:
        rec, rows, st = run_size(n)
        print("  rows_replayed=%d states=%d" % (rows, st))
        recs.append(rec)
    by_n = {r["n"]: r for r in recs}
    measured = sorted(by_n)

    # 每条判据只用它自己规模的数字；没跑的规模一律 N/A，绝不复用别的规模的数字
    checks = {}
    if 100000 in by_n:
        checks["R5-A"] = ("PASS" if by_n[100000]["replay_s"] < 3.0 else "FAIL")
        line_a = "R5-A replay(100k) < 3.0s    : %s (%.2fs, n=100000)" % (
            checks["R5-A"], by_n[100000]["replay_s"])
    else:
        checks["R5-A"] = "N/A"
        line_a = "R5-A replay(100k) < 3.0s    : N/A (本次未跑 100k；实测规模 %s)" % measured

    if 1000000 in by_n:
        checks["R5-B"] = ("PASS" if by_n[1000000]["replay_s"] < 10.0 else "FAIL")
        line_b = "R5-B replay(1M) < 10.0s     : %s (%.2fs, n=1000000)" % (
            checks["R5-B"], by_n[1000000]["replay_s"])
    else:
        checks["R5-B"] = "N/A"
        line_b = ("R5-B replay(1M) < 10.0s     : N/A (1M 本轮未跑、未度量；"
                  "nightly 只跑 100k，1M 须手动 python tests/r5_stress.py --n 1000000)")

    worst_payload = max(r["avg_payload_b"] for r in recs)
    worst_wal = max(r["wal_after_ckpt_mb"] for r in recs)
    worst_payload_n = max(recs, key=lambda r: r["avg_payload_b"])["n"]
    worst_wal_n = max(recs, key=lambda r: r["wal_after_ckpt_mb"])["n"]
    checks["R5-C"] = "PASS" if worst_payload <= 500 else "FAIL"
    checks["R5-D"] = "PASS" if worst_wal <= 1.0 else "FAIL"
    line_c = "R5-C avg_payload_b <= 500   : %s (%.1f, 最差档 n=%d)" % (
        checks["R5-C"], worst_payload, worst_payload_n)
    line_d = "R5-D wal_after_ckpt <= 1MB  : %s (%.3fMB, 最差档 n=%d)" % (
        checks["R5-D"], worst_wal, worst_wal_n)

    for ln in (line_a, line_b, line_c, line_d):
        print(ln)
    verdict = "FAIL" if any(v == "FAIL" for v in checks.values()) else "PASS"
    summary = {
        "R5": verdict,
        "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "argv": sys.argv[1:],
        "measured_sizes": measured,
        "checks": checks,
        "level_1m_run": 1000000 in by_n,
        "note_1m": ("1M 已实测" if 1000000 in by_n
                    else "1M 未在本轮运行，R5-B 不参与判定（N/A），不得用 100k 数字代替"),
        "records": recs,
        "worst_avg_payload_b": {"n": worst_payload_n, "value": worst_payload},
        "worst_wal_after_ckpt_mb": {"n": worst_wal_n, "value": worst_wal},
    }
    print("SUMMARY " + json.dumps(summary, ensure_ascii=False))
    # 保留历次运行摘要，避免「100k 的工件被当成 1M 的成绩」；夜间跑也不会抹掉手动的 1M 证据。
    art = ART / "r5_stress.json"
    history = []
    if art.exists():
        try:
            old = json.loads(art.read_text(encoding="utf-8"))
            history = [h for h in (old.get("history") or []) if isinstance(h, dict)]
            history.append({k: old.get(k) for k in ("run_at", "argv", "R5", "measured_sizes",
                                                    "checks", "level_1m_run", "records")})
        except Exception:
            history = []
    summary["history"] = history[-5:]
    art.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return 1 if verdict == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())

