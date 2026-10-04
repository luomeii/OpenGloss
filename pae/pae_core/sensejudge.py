# -*- coding: utf-8 -*-
"""义项判定接入（P5 第一步：纯影子）。两轮独立设计交叉评审后确定：
- 永不同步等待：上屏/悬停走确定性释义；LLM 只在后台影子跑，只写缓存与决策日志
- 免费规则先行：单义项 / 首行即技术行 / 命中缓存 → 不调用（预估省四到六成）
- 两级缓存：L2 = (lemma, 整句 hash) 权威；L1 = (lemma, 搭配 bigram) 泛化，泛化只供预取不覆盖 L2
- 失败零可见：超时/坏 JSON/越界 → 保持确定性释义，不重试不提示
- 候选义项从 dict.sqlite 现场取全文（内存里的精简释义不够判）
"""
import hashlib
import json
import re
import sqlite3
import time
import unicodedata

PROMPT_VERSION = "sj_v1"
MODEL_HINT = "flash"
TIMEOUT_S = 5
DAILY_CALL_CAP = 150
PER_PAGE_CAP = 8

DDL = """
CREATE TABLE IF NOT EXISTS sense_cache (
    lemma          TEXT NOT NULL,
    ctx_hash       TEXT NOT NULL,
    key_level      TEXT NOT NULL,
    chosen_idx     INTEGER,
    baseline_idx   INTEGER,
    candidates_fp  TEXT,
    model          TEXT,
    prompt_version TEXT,
    latency_ms     INTEGER,
    fallback       INTEGER NOT NULL DEFAULT 0,
    created_at     INTEGER NOT NULL,
    PRIMARY KEY (lemma, ctx_hash, key_level)
);
CREATE TABLE IF NOT EXISTS sense_shadow_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             INTEGER NOT NULL,
    lemma          TEXT NOT NULL,
    ctx_hash       TEXT,
    key_level      TEXT,
    chosen_idx     INTEGER,
    baseline_idx   INTEGER,
    n_candidates   INTEGER,
    latency_ms     INTEGER,
    fallback       INTEGER NOT NULL DEFAULT 0,
    reason         TEXT,
    prompt_version TEXT
);
CREATE INDEX IF NOT EXISTS idx_sense_shadow_ts ON sense_shadow_log(ts);
"""


def ensure_tables(conn):
    conn.executescript(DDL)
    conn.commit()


def _norm_sentence(s):
    s = unicodedata.normalize("NFKC", str(s or "")).lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def ctx_hash_sentence(lemma, sentence):
    h = hashlib.sha1((str(lemma) + "||" + _norm_sentence(sentence)).encode("utf-8")).hexdigest()
    return h[:20]


def ctx_hash_collocation(lemma, sentence):
    toks = re.findall(r"[A-Za-z][A-Za-z\x27-]+", _norm_sentence(sentence))
    key = str(lemma).lower()
    if key not in toks:
        return None
    i = toks.index(key)
    left = toks[i - 1] if i > 0 else ""
    right = toks[i + 1] if i + 1 < len(toks) else ""
    if not left and not right:
        return None
    return hashlib.sha1(("%s|%s|%s" % (key, left, right)).encode("utf-8")).hexdigest()[:20]


def full_candidates(dict_path, lemma):
    try:
        c = sqlite3.connect(str(dict_path))
        row = c.execute("SELECT translation FROM dict_words WHERE word = ?", (str(lemma).lower(),)).fetchone()
        c.close()
    except Exception:
        return []
    if not row or not row[0]:
        return []
    raw = str(row[0]).replace(chr(92) + "n", chr(10))
    return [l.strip() for l in raw.split(chr(10)) if l.strip()]


def prefilter(lemma, candidates, cached=None):
    if cached:
        return False, "cache_hit"
    if not candidates:
        return False, "no_candidates"
    if len(candidates) == 1:
        return False, "single_sense"
    if candidates[0].startswith("[") and len(candidates) >= 2:
        return False, "first_is_technical"
    return True, "worth_llm"


def _calls_today(conn):
    day0 = int(time.time()) // 86400 * 86400
    return conn.execute("SELECT COUNT(*) FROM sense_shadow_log WHERE ts >= ? AND fallback = 0",
                        (day0,)).fetchone()[0]


def _judge_prompt(lemma, sentence, candidates):
    cand_lines = chr(10).join("%d) %s" % (i, c) for i, c in enumerate(candidates))
    sys = ("你是词义判定器。给定英文句子和该词在词典里的编号义项，选出句中该词实际使用的义项。"
           "只输出 JSON，形如 {sense_index: 整数, evidence_span: 句子中该词的原文子串}。"
           "sense_index 必须在候选编号范围内；evidence_span 必须是句子的子串并包含该词。")
    user = "单词：%s" % lemma + chr(10) + "句子：%s" % sentence + chr(10) + "候选义项：" + chr(10) + cand_lines
    return sys, user


def _log(conn, out, key_level):
    conn.execute(
        "INSERT INTO sense_shadow_log(ts, lemma, ctx_hash, key_level, chosen_idx, baseline_idx, n_candidates,"
        " latency_ms, fallback, reason, prompt_version) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (out["ts"], out["lemma"], out.get("ctx_hash"), key_level, out.get("chosen_idx"), 0,
         out.get("n_candidates"), out.get("latency_ms"), out.get("fallback"), out.get("reason") or "judged",
         PROMPT_VERSION))


def judge(conn, dict_path, lemma, sentence, llm_chat=None):
    """影子判定一次（不抛异常）。llm_chat 可注入替身便于测试。"""
    ensure_tables(conn)
    out = {"lemma": lemma, "ts": int(time.time()), "fallback": 0, "reason": None,
           "chosen_idx": None, "baseline_idx": 0, "n_candidates": 0, "latency_ms": 0,
           "ctx_hash": None, "prompt_version": PROMPT_VERSION}
    cands = full_candidates(dict_path, lemma)
    out["n_candidates"] = len(cands)
    fp = hashlib.sha1(("|".join(cands)).encode("utf-8")).hexdigest()[:12] if cands else ""
    h2 = ctx_hash_sentence(lemma, sentence)
    h1 = ctx_hash_collocation(lemma, sentence)
    out["ctx_hash"] = h2
    cached = conn.execute("SELECT chosen_idx FROM sense_cache WHERE lemma=? AND ctx_hash=? AND key_level='L2'",
                          (lemma, h2)).fetchone()
    cached1 = None
    if h1:
        cached1 = conn.execute("SELECT chosen_idx FROM sense_cache WHERE lemma=? AND ctx_hash=? AND key_level='L1'",
                               (lemma, h1)).fetchone()
    should, why = prefilter(lemma, cands, cached=cached)
    out["reason"] = why
    if not should:
        if cached:
            out["chosen_idx"] = cached[0]
            out["reason"] = "cache_hit_L2"
        out["fallback"] = 1
        _log(conn, out, "L2")
        conn.commit()
        return out
    if _calls_today(conn) >= DAILY_CALL_CAP:
        out["reason"] = "daily_cap"
        out["fallback"] = 1
        _log(conn, out, "L2")
        conn.commit()
        return out
    if cached1 is not None:
        out["reason"] = "L1_generalized_hit_shadow_only"
    sys_p, user_p = _judge_prompt(lemma, sentence, cands)
    t0 = time.time()
    chosen = None
    try:
        if llm_chat is None:
            from . import llm as llm_mod
            r = llm_mod.chat([{"role": "system", "content": sys_p}, {"role": "user", "content": user_p}],
                             temperature=0, timeout=TIMEOUT_S, thinking=False)
            content = r.get("content") or ""
        else:
            content = llm_chat(sys_p, user_p)
        m = re.search(r"\{.*\}", content, re.S)
        obj = json.loads(m.group(0)) if m else None
        if obj is not None:
            idx = obj.get("sense_index")
            span = str(obj.get("evidence_span") or "")
            if isinstance(idx, int) and 0 <= idx < len(cands) and span and span in sentence:
                chosen = idx
    except Exception as e:
        out["reason"] = "llm_error:" + type(e).__name__
    out["latency_ms"] = int((time.time() - t0) * 1000)
    if chosen is None:
        out["fallback"] = 1
        if not out["reason"] or out["reason"] == "worth_llm":
            out["reason"] = "invalid_or_timeout"
    else:
        out["chosen_idx"] = chosen
        conn.execute(
            "INSERT INTO sense_cache(lemma, ctx_hash, key_level, chosen_idx, baseline_idx, candidates_fp, model,"
            " prompt_version, latency_ms, fallback, created_at) VALUES(?,?,?,?,?,?,?,?,?,0,?)"
            " ON CONFLICT(lemma, ctx_hash, key_level) DO UPDATE SET chosen_idx=excluded.chosen_idx,"
            " latency_ms=excluded.latency_ms, created_at=excluded.created_at",
            (lemma, h2, "L2", chosen, 0, fp, MODEL_HINT, PROMPT_VERSION, out["latency_ms"], out["ts"]))
        if h1:
            conn.execute(
                "INSERT INTO sense_cache(lemma, ctx_hash, key_level, chosen_idx, baseline_idx, candidates_fp, model,"
                " prompt_version, latency_ms, fallback, created_at) VALUES(?,?,?,?,?,?,?,?,?,0,?)"
                " ON CONFLICT(lemma, ctx_hash, key_level) DO NOTHING",
                (lemma, h1, "L1", chosen, 0, fp, MODEL_HINT, PROMPT_VERSION, out["latency_ms"], out["ts"]))
        out["reason"] = out["reason"] or "judged"
    _log(conn, out, "L2")
    conn.commit()
    return out


def stats(conn):
    ensure_tables(conn)
    day0 = int(time.time()) // 86400 * 86400
    calls = conn.execute("SELECT COUNT(*) FROM sense_shadow_log WHERE ts >= ? AND fallback = 0", (day0,)).fetchone()[0]
    fb = conn.execute("SELECT COUNT(*) FROM sense_shadow_log WHERE ts >= ? AND fallback = 1", (day0,)).fetchone()[0]
    reasons = conn.execute("SELECT reason, COUNT(*) FROM sense_shadow_log WHERE ts >= ? AND fallback = 1 GROUP BY reason",
                           (day0,)).fetchall()
    cache = conn.execute("SELECT key_level, COUNT(*) FROM sense_cache GROUP BY key_level").fetchall()
    return {"prompt_version": PROMPT_VERSION, "daily_call_cap": DAILY_CALL_CAP,
            "calls_today": calls, "fallback_today": fb,
            "fallback_rate": (round(fb / (calls + fb), 3) if (calls + fb) else None),
            "skip_reasons": dict(reasons), "cache": dict(cache),
            "note": "影子期：只记录不上屏。fallback=1 表示没调 LLM（免费规则拦下）或调用失败。"}