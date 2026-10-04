"""PAE engine tests. Temporary databases only; the production db is never touched."""
import pytest

from pae_core.engine import Engine

from pathlib import Path
LEMMA_PATH = str(Path(__file__).resolve().parents[2] / "resources" / "ECDICT" / "lemma.en.txt")
TEXT = "The contention intensified as worker threads multiplied"
CAP_WORDS = ("apple bridge candle danger engine forest garden harbor island "
             "jungle kettle ladder market needle orange pencil quartz river "
             "saddle temple urchin village window yellow zebra anchor basket "
             "camera dragon fox").split()


@pytest.fixture
def engine(tmp_path):
    return Engine(str(tmp_path / "t.db"), LEMMA_PATH)


def test_annotate_returns_schema(engine):
    out = engine.annotate(TEXT, page_id="t1")
    assert isinstance(out, list)
    assert out
    for item in out:
        assert set(item) >= {"surface", "lemma", "gloss", "s_value", "counted"}
        assert isinstance(item["counted"], bool)
        assert 0.0 <= item["s_value"] <= 1.0


def test_idempotent_same_day(engine):
    first = engine.annotate(TEXT, page_id="t1")
    assert any(i["counted"] for i in first)
    second = engine.annotate(TEXT, page_id="t1")
    assert second
    assert all(i["counted"] is False for i in second)


def test_new_page_new_day(engine):
    engine.annotate(TEXT, page_id="t1")
    other = engine.annotate(TEXT, page_id="t2")
    assert any(i["counted"] for i in other)


def test_persistence(tmp_path):
    path = str(tmp_path / "p.db")
    e1 = Engine(path, LEMMA_PATH)
    e1.annotate(TEXT, page_id="t1")
    e2 = Engine(path, LEMMA_PATH)
    st = e2.status()
    assert st["lexicon"] > 0
    assert st["events"] >= 1
    assert e2.states


def test_budget_cap(engine):
    text = " ".join(CAP_WORDS * 3)
    out = engine.annotate(text, page_id="big")
    assert isinstance(out, list)
    assert 0 < len(out) <= 8
