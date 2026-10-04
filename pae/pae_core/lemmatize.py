"""PAE lemma cascade: lemma_map -> rule fallback. Spec: 27号 R2."""
import re

# 允许双写还原的辅音组（固定 8 组，不含 ll / cc）
_DOUBLES = ("pp", "tt", "gg", "dd", "mm", "nn", "rr", "bb")

_ALPHA_RE = re.compile(r"^[a-z]+$")


def load_lemma_map(path):
    """解析 lemma.en.txt。返回 dict: 变形(小写) -> lemma(小写)。

    数据行格式 "lemma/freq -> inflection1,inflection2,..."
    变形和 lemma 都转小写；变形==lemma 的也保留（查询快）；撇号原样保留。
    """
    lemma_map = {}
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith(";"):
                continue
            if "->" not in line:
                continue
            left, right = line.split("->", 1)
            lemma = left.split("/", 1)[0].strip().lower()
            if not lemma:
                continue
            # 自身也进表，命中即原样返回；不覆盖已有的真实变形映射
            if lemma not in lemma_map:
                lemma_map[lemma] = lemma
            for inf in right.split(","):
                inf = inf.strip().lower()
                if not inf or inf == lemma:
                    continue
                # 后出现的组覆盖先出现的组（同频由语料顺序决定）
                lemma_map[inf] = lemma
    return lemma_map


def _undouble(stem):
    """双写还原：stopp -> [stop, stopp]，还原形优先。"""
    if len(stem) >= 2 and stem[-1] == stem[-2] and stem[-2:] in _DOUBLES:
        return [stem[:-1], stem]
    return [stem]


def _strip_suffix(w):
    """规则剥缀层。只处理纯字母小写词：
    ies -> y；ed/ied -> 剥 ed 或 y（双写还原优先）；ing（>=5 字母）；
    es -> s/x/z/o/ch/sh 结尾去 es，其他只去 s；s（>=4 字母且非 ss/us/is）。
    返回候选列表，调用方再查词典。
    """
    cands = []
    if not _ALPHA_RE.match(w):
        return cands

    def add(cand):
        if cand and cand != w and cand not in cands:
            cands.append(cand)

    n = len(w)

    # 1) ies -> y（优先于 s）
    if n >= 4 and w.endswith("ies"):
        add(w[:-3] + "y")

    # 2) ed / ied
    if n >= 4 and w.endswith("ed"):
        for cand in _undouble(w[:-2]):
            add(cand)
        if w.endswith("ied"):
            add(w[:-3] + "y")

    # 3) ing
    if n >= 5 and w.endswith("ing"):
        for cand in _undouble(w[:-3]):
            add(cand)

    # 4) es
    if n >= 3 and w.endswith("es"):
        if w[-3] in "sxzo" or w.endswith(("ch", "sh")):
            add(w[:-2])
        else:
            add(w[:-1])

    # 5) s
    if n >= 4 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        add(w[:-1])

    return cands


def candidates(word, lemma_map):
    """主入口。小写化后按 原词/lemma_map/规则 三层级联返回候选 lemma 列表。"""
    if not word:
        return [word]
    w = word.lower()

    mapped = lemma_map.get(w)
    if mapped is not None:
        if mapped == w:
            return [w]
        return [mapped]

    rule_cands = _strip_suffix(w)
    if rule_cands:
        return rule_cands
    return [w]
