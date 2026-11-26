# -*- coding: utf-8 -*-
"""ECDICT gloss lookups and in-memory word-form maps."""
import hashlib
import os
import sqlite3
import threading
from functools import lru_cache
from pathlib import Path

from . import paths as _paths

DEFAULT_DB_PATH = _paths.dict_path()
_PUBLIC_DICTIONARY_SIZE = 68_612_096
_PUBLIC_DICTIONARY_SHA256 = 'ee73d182b3fa661f71f779dd047d9d59eaff5f9dec2108f73921f68cef18e7a8'


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


def _file_identity(stat):
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def _has_sidecars(path):
    for suffix in ('-wal', '-shm', '-journal'):
        try:
            path.with_name(path.name + suffix).lstat()
        except FileNotFoundError:
            continue
        return True
    return False


class IndexedTranslationLookup:
    """Read only the verified, unchanged publication artifact during this process."""

    def __init__(self, path, identity):
        self._lock = threading.Lock()
        conn = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True,
                               check_same_thread=False)
        try:
            self._size = conn.execute('SELECT COUNT(*) FROM dict_words').fetchone()[0]
            if _file_identity(path.stat()) != identity or _has_sidecars(path):
                raise OSError('dictionary changed while opening indexed lookup')
            self._cached_lookup = lru_cache(maxsize=1024)(self._lookup_uncached)
        except BaseException:
            conn.close()
            raise
        self._conn = conn

    def _lookup_uncached(self, key):
        row = self._conn.execute('SELECT translation, bnc FROM dict_words WHERE word = ?',
                                 (key,)).fetchone()
        return None if row is None else (_make_gloss(row[0]), row[1])

    def get(self, key, default=None):
        with self._lock:
            if self._conn is None:
                raise sqlite3.ProgrammingError('dictionary lookup is closed')
            value = self._cached_lookup(key)
        return default if value is None else value

    def __contains__(self, key):
        return self.get(key) is not None

    def __len__(self):
        return self._size

    def close(self):
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
                self._cached_lookup.cache_clear()


def load_translation_lookup(db_path=DEFAULT_DB_PATH):
    """Use the exact release dictionary's index, preserving eager reads for other files."""
    path = Path(db_path).resolve(strict=True)
    if _has_sidecars(path):
        return load_full_dict(db_path)
    with path.open('rb') as source:
        identity = _file_identity(os.fstat(source.fileno()))
        if identity[2] != _PUBLIC_DICTIONARY_SIZE:
            return load_full_dict(db_path)
        digest = hashlib.sha256()
        for chunk in iter(lambda: source.read(256 * 1024), b''):
            digest.update(chunk)
        if _file_identity(os.fstat(source.fileno())) != identity:
            raise OSError('dictionary changed while verifying its contents')
    if _file_identity(path.stat()) != identity:
        raise OSError('dictionary was replaced while verifying its contents')
    if digest.hexdigest() != _PUBLIC_DICTIONARY_SHA256:
        return load_full_dict(db_path)
    if _has_sidecars(path):
        raise OSError('dictionary gained SQLite sidecars while verifying its contents')
    return IndexedTranslationLookup(path, identity)


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
