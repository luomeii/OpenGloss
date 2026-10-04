# -*- coding: utf-8 -*-
"""R2 full cascade coverage test. Bar: 85% on real engineering English.

Cascade layers:
  1) lemma.en.txt lookup       (pae_core.lemmatize.candidates)
  2) rule suffix stripping     (pae_core.lemmatize.candidates)
  3) ECDICT exchange reverse lookup (this test adds it on top of candidates)
"""
import pytest

from pae_core.dict_loader import load_full_dict, load_exchange_map
from pae_core.lemmatize import load_lemma_map, candidates
from pae_core.test_corpus_coverage import CORPUS, STOP, content_words

from pathlib import Path
_RES = Path(__file__).resolve().parents[2] / "resources" / "ECDICT"
DB_PATH = str(_RES / "dict.sqlite")
LEMMA_PATH = str(_RES / "lemma.en.txt")

CASCADE_HOPS = 3


def cascade(word, lemma_map, exchange_map):
    """candidates() 输出后再用 exchange_map 逐跳反查（第三层）。"""
    seen = set(candidates(word, lemma_map))
    frontier = list(seen)
    for _ in range(CASCADE_HOPS):
        nxt = []
        for cand in frontier:
            base = exchange_map.get(cand)
            if base and base not in seen:
                seen.add(base)
                nxt.append(base)
        if not nxt:
            break
        frontier = nxt
    return seen


def test_full_cascade_coverage():
    lemma_map = load_lemma_map(LEMMA_PATH)
    exchange_map = load_exchange_map(DB_PATH)
    known = set(load_full_dict(DB_PATH).keys())

    total, hit = 0, 0
    misses = []
    for text in CORPUS:
        for w in content_words(text):
            if not w or not w.isalpha() or w in STOP or len(w) < 3:
                continue
            total += 1
            cands = cascade(w, lemma_map, exchange_map)
            if any(c in known for c in cands):
                hit += 1
            else:
                misses.append(w)

    cov = hit / total if total else 0.0
    print('full cascade coverage: %.3f (%d/%d), exchange_map=%d, dict_words=%d'
          % (cov, hit, total, len(exchange_map), len(known)))
    print('misses(%d): %s' % (len(set(misses)), sorted(set(misses))[:20]))
    assert cov >= 0.85, 'full cascade coverage %.3f < 0.85' % cov
