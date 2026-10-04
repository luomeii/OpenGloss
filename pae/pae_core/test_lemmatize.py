"""Round-trip and rule tests for pae_core.lemmatize. Spec: 27号 R2."""
import random
from pathlib import Path

import pytest

from pae_core.lemmatize import _strip_suffix, candidates, load_lemma_map

LEMMA_FILE = Path(__file__).resolve().parents[2] / "resources" / "ECDICT" / "lemma.en.txt"


def _lemma_groups(path):
    groups = []
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith(";"):
                continue
            if "->" not in line:
                continue
            left, right = line.split("->", 1)
            lemma = left.split("/", 1)[0].strip().lower()
            infs = [x.strip().lower() for x in right.split(",") if x.strip()]
            groups.append((lemma, infs))
    return groups


@pytest.fixture(scope="module")
def lemma_map():
    return load_lemma_map(LEMMA_FILE)


@pytest.fixture(scope="module")
def groups():
    return _lemma_groups(LEMMA_FILE)


def test_roundtrip_irregular(lemma_map, groups):
    random.seed(42)
    picked = random.sample(range(len(groups)), 30)
    failures = []
    for idx in picked:
        lemma, infs = groups[idx]
        for inf in infs[:2]:
            cands = candidates(inf, lemma_map)
            if lemma not in cands:
                failures.append((lemma, inf, cands))
    assert not failures, f"roundtrip failures: {failures}"


@pytest.mark.parametrize(
    "word,expected",
    [
        ("studies", "study"),
        ("stopped", "stop"),
        ("running", "run"),
        ("goes", "go"),
        ("boxes", "box"),
        ("cats", "cat"),
    ],
)
def test_regular_rules(word, expected):
    cands = candidates(word, {})
    assert expected in cands, f"{word}: {cands}"
    assert expected in _strip_suffix(word)


def test_identity():
    assert candidates("xyzzy", {}) == ["xyzzy"]


def test_real_words(lemma_map):
    assert candidates("ran", lemma_map)[0] == "run"
    assert "good" in candidates("better", lemma_map)
    assert "mouse" in candidates("mice", lemma_map)
    assert candidates("were", lemma_map)[0] == "be"
