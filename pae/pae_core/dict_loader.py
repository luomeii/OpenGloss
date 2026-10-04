# -*- coding: utf-8 -*-
"""PAE dictionary loaders: full ECDICT SQLite -> in-memory maps.

Spec: R2 full import. Only sqlite3 from the standard library is used.
"""
import sqlite3

from . import paths as _paths

DEFAULT_DB_PATH = _paths.dict_path()


def _make_gloss(translation):
    """ECDICT 的 translation 用字面 \\n 分行。首行 + 技术标记行（[xx]开头的）合并。"""
    if not translation:
        return ''
    lines = [l.strip() for l in translation.replace('\\n', '\n').split('\n') if l.strip()]
    if not lines:
        return ''
    primary = lines[0]
    tech = [l for l in lines[1:] if l.startswith('[')]
    if tech and not primary.startswith('['):
        out = primary + ' ／ ' + ' ／ '.join(tech[:2])
    else:
        out = primary
    return out[:80]


def load_full_dict(db_path=DEFAULT_DB_PATH) -> dict:
    """dict_words -> {word(小写): (_make_gloss(translation), bnc)}。

    释义 = 首行 + 最多两条技术标记行（[计]/[医] 等），上限 80 字符；
    仍然每词只存一个字符串（内存不爆）。
    """
    out = {}
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute('SELECT word, translation, bnc FROM dict_words')
        for word, translation, bnc in cur:
            if not word:
                continue
            # ECDICT 把换行存成字面量 \\n（反斜杠+n），_make_gloss 还原后再取首行+技术行
            out[word.lower()] = (_make_gloss(translation), bnc)
    finally:
        conn.close()
    return out


def load_exchange_map(db_path=DEFAULT_DB_PATH) -> dict:
    """exchange_map -> {变形(小写): base(小写)}."""
    out = {}
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute('SELECT inflected, base FROM exchange_map')
        for inflected, base in cur:
            if not inflected:
                continue
            out[inflected.lower()] = (base or '').lower()
    finally:
        conn.close()
    return out
