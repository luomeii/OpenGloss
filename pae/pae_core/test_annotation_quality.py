# -*- coding: utf-8 -*-
"""P0-3 注解质量回归测试：引擎选择层四修复的行为级验证。

T3a  _make_gloss 保留技术标记行（[计] 等）——旧实现只取首行，丢 ~37% 技术义项。
T3b  无释义不注：词典里查不到的词（用户名/拼写错误）不进返回，也不进事件账本。
T3c  BNC 已知词过滤：高频常见词跳过，生僻词保留。
T3d  session_id 落库：写入 events.payload，供 fold 的会话阻尼 ρ 使用。
"""
import json

import pytest

from pae_core.dict_loader import _make_gloss
from pae_core.engine import BNC_KNOWN_RANK, Engine

from pathlib import Path
LEMMA_PATH = str(Path(__file__).resolve().parents[2] / "resources" / "ECDICT" / "lemma.en.txt")


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    return Engine(str(tmp_path_factory.mktemp("quality") / "t.db"), LEMMA_PATH)


def _payloads(engine):
    rows = engine.conn.execute("SELECT payload FROM events").fetchall()
    return [json.loads(r["payload"]) for r in rows]


def test_t3a_gloss_keeps_tech_line():
    """T3a: [计] 技术义项必须出现在增强释义中（不再被首行截断丢掉）。"""
    # ECDICT 把换行存成字面量 \n（反斜杠+n），这里用 raw 字符串还原真实存储形态。
    raw = r"n. 进程, 程序\n[计] 进程, 线程\n[化] 硫化"
    gloss = _make_gloss(raw)
    assert "[计]" in gloss, gloss
    assert "线程" in gloss, gloss
    assert gloss.startswith("n. 进程")
    assert len(gloss) <= 80


def test_t3b_unknown_word_not_annotated(engine):
    """T3b: 词典里没有的假词（zzqqxx）不出现在返回里，也不写进事件账本。"""
    out = engine.annotate("The contention zzqqxx intensified quartz", page_id="t3b")
    assert out
    assert "zzqqxx" not in {i["surface"] for i in out}
    assert "zzqqxx" not in {i["lemma"] for i in out}
    assert all("zzqqxx" not in (p.get("lemma") or "") for p in _payloads(engine))


def test_t3c_no_frequency_assumption_by_default(engine):
    """T3c（2026-10-01 用户策略变更）：默认【不预设已知词】——先全部当不会，慢慢积累。

    所以 comment 这类常见词默认也会被注解；但过滤机制本身仍可用（bnc_known_rank > 0 时生效）。
    """
    import pae_core.engine as eng
    assert BNC_KNOWN_RANK == 0, "默认必须为 0：不做词频假设"

    out = engine.annotate("The comment mentioned quartz", page_id="t3c-off")
    lemmas = {i["lemma"] for i in out}
    assert out
    assert "comment" in lemmas, "默认不该因为词频高就跳过"
    assert "quartz" in lemmas

    # 机制仍在：显式开启阈值后，常见词被过滤、生僻词保留
    old = eng.BNC_KNOWN_RANK
    try:
        eng.BNC_KNOWN_RANK = 3000
        out2 = engine.annotate("The comment mentioned quartz", page_id="t3c-on")
        lemmas2 = {i["lemma"] for i in out2}
        assert "comment" not in lemmas2
        assert "quartz" in lemmas2
    finally:
        eng.BNC_KNOWN_RANK = old


def test_t3d_session_id_persisted(engine):
    """T3d: 传 session_id 调用 annotate，事件 payload 里必须存住 session_id。"""
    out = engine.annotate("The contention intensified", page_id="t3d",
                          session_id="SESSION-42")
    assert out
    payloads = _payloads(engine)
    assert any(p.get("session_id") == "SESSION-42" for p in payloads)
