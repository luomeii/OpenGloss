# -*- coding: utf-8 -*-
"""ECDICT csv -> dict.sqlite 构建脚本（PAE 词典数据源）。

用法（在仓库根目录运行，用相对路径避免中文绝对路径的 Unicode 坑）：
    python scripts/build_dict.py --csv resources/ECDICT/ecdict.csv --out resources/ECDICT/dict.sqlite

源数据：skywind3000/ECDICT 的 csv（MIT 许可），列：
    word,phonetic,definition,translation,pos,collins,oxford,tag,bnc,frq,exchange,detail,audio

目标 schema（与生产 dict.sqlite 一致，已实测验证）：
    dict_words(word TEXT PRIMARY KEY, translation TEXT, exchange TEXT,
               bnc INTEGER, frq INTEGER, tag TEXT)
    exchange_map(inflected TEXT PRIMARY KEY, base TEXT)   -- 主键自带 inflected 索引

exchange 列格式形如 "d:did:p:done/i:doing/s:does/..."（类型:变形，斜杠分隔）。
拆分规则：
    - 前缀 '0'：冒号后是本条词的 lemma -> 写 (本条词, lemma)
    - 其他前缀：冒号后是本条词的变形 -> 写 (变形, 本条词)
    - 冒号后为关系码（p/d/i/3/s/r/t/j/0/1）或空、或与 base 相同 -> 跳过
translation 为空的行也保留（词典里本来就有）。
流式逐行读 csv（62.9MB 不整体进内存）；重复词按 PRIMARY KEY 冲突 INSERT OR IGNORE
（先到先得，与生产库构建行为一致）。
"""
import argparse
import csv
import os
import sqlite3
import sys
import time

BATCH = 10000
PROGRESS_EVERY = 200000

# exchange 段冒号后可能是关系码而非词形，跳过
_CODE_VALUES = {"0", "1", "p", "d", "i", "3", "s", "r", "t", "j"}

DDL = """
CREATE TABLE dict_words (
    word TEXT PRIMARY KEY,
    translation TEXT,
    exchange TEXT,
    bnc INTEGER,
    frq INTEGER,
    tag TEXT
);
CREATE TABLE exchange_map (
    inflected TEXT PRIMARY KEY,
    base TEXT
);
"""


def _to_int(v):
    v = (v or "").strip()
    if not v:
        return None
    try:
        return int(v)
    except ValueError:
        return None


def parse_exchange(word_lower, exchange):
    """返回 [(inflected, base), ...]，规则见模块 docstring。"""
    out = []
    if not exchange:
        return out
    for seg in exchange.split("/"):
        seg = seg.strip()
        if not seg or ":" not in seg:
            continue
        prefix, val = seg.split(":", 1)
        prefix = prefix.strip().lower()
        val = val.strip().lower()
        if not val or val in _CODE_VALUES:
            continue
        if prefix == "0":
            inflected, base = word_lower, val
        else:
            inflected, base = val, word_lower
        if not inflected or inflected == base:
            continue
        out.append((inflected, base))
    return out


def main():
    ap = argparse.ArgumentParser(description="Build PAE dict.sqlite from ECDICT csv")
    ap.add_argument("--csv", required=True, help="ECDICT csv 路径（如 resources/ECDICT/ecdict.csv）")
    ap.add_argument("--out", required=True, help="输出 sqlite 路径（如 resources/ECDICT/dict.sqlite）")
    args = ap.parse_args()

    if not os.path.exists(args.csv):
        print("csv not found: %s" % args.csv, file=sys.stderr)
        return 2

    t0 = time.time()
    if os.path.exists(args.out):
        os.remove(args.out)
    conn = sqlite3.connect(args.out)
    conn.execute("PRAGMA journal_mode=MEMORY")
    conn.execute("PRAGMA synchronous=OFF")
    conn.executescript(DDL)

    n_rows = 0
    word_buf, ex_buf = [], []

    def flush():
        if word_buf:
            conn.executemany(
                "INSERT OR IGNORE INTO dict_words "
                "(word, translation, exchange, bnc, frq, tag) VALUES (?,?,?,?,?,?)",
                word_buf)
            word_buf.clear()
        if ex_buf:
            conn.executemany(
                "INSERT OR IGNORE INTO exchange_map (inflected, base) VALUES (?,?)",
                ex_buf)
            ex_buf.clear()
        conn.commit()

    csv.field_size_limit(10 ** 9)
    with open(args.csv, encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        idx = {name: i for i, name in enumerate(header)}
        for col in ("word", "translation", "exchange", "tag", "bnc", "frq"):
            if col not in idx:
                print("csv missing column: %s (header=%s)" % (col, header), file=sys.stderr)
                return 2
        iw, it = idx["word"], idx["translation"]
        ie, itag = idx["exchange"], idx["tag"]
        ib, ifr = idx["bnc"], idx["frq"]

        for row in reader:
            if not row:
                continue
            n_rows += 1
            w = (row[iw] or "").strip().lower()
            if not w:
                continue
            translation = row[it] if it < len(row) else ""
            exchange = row[ie] if ie < len(row) else ""
            tag = row[itag] if itag < len(row) else ""
            bnc = _to_int(row[ib] if ib < len(row) else "")
            frq = _to_int(row[ifr] if ifr < len(row) else "")
            word_buf.append((w, translation, exchange, bnc, frq, tag))
            ex_buf.extend(parse_exchange(w, exchange))

            if n_rows % BATCH == 0:
                flush()
            if n_rows % PROGRESS_EVERY == 0:
                print("rows=%d elapsed=%.1fs" % (n_rows, time.time() - t0), flush=True)

    flush()
    conn.commit()
    conn.execute("PRAGMA optimize")
    conn.commit()

    n_words = conn.execute("SELECT COUNT(*) FROM dict_words").fetchone()[0]
    n_map = conn.execute("SELECT COUNT(*) FROM exchange_map").fetchone()[0]
    conn.close()
    print("---")
    print("csv rows     : %d" % n_rows)
    print("dict_words   : %d" % n_words)
    print("exchange_map : %d" % n_map)
    print("elapsed      : %.1fs" % (time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
