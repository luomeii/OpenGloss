"""Read-only daily list of persisted encounter words. No learning-state writes."""

from datetime import date, timedelta
from html import escape
from pathlib import Path
import json
import re
import sqlite3
from string import Template
import time
from .contacts import SELECTED_TYPES


MISSING = "未提供"
_DAY_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_TEMPLATE = Path(__file__).resolve().parents[1] / "daily_sheet.html"


def local_day_bounds(day_text: str) -> tuple[int, int]:
    """Bounds for one system-local calendar day; adjacent midnights handle DST."""
    if not _DAY_RE.fullmatch(day_text or ""):
        raise ValueError("日期应为 YYYY-MM-DD")
    try:
        selected = date.fromisoformat(day_text)
        following = selected + timedelta(days=1)
        start = int(time.mktime((*selected.timetuple()[:3], 0, 0, 0, 0, 0, -1)))
        end = int(time.mktime((*following.timetuple()[:3], 0, 0, 0, 0, 0, -1)))
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError("日期超出可用范围") from exc
    if not 20 * 3600 <= end - start <= 28 * 3600:
        raise ValueError("本地日期边界异常")
    return start, end


def _read_only(path):
    # The live event database must NOT use immutable=1: that would miss WAL writes.
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=2.0)
    con.execute("PRAGMA query_only=ON")
    return con


def _lemma(payload):
    try:
        obj = json.loads(payload)
    except (TypeError, ValueError):
        return ""
    if not isinstance(obj, dict):
        return ""
    value = obj.get("lemma") or obj.get("lemma_id")
    return value.strip().lower() if isinstance(value, str) else ""


def build_daily_sheet(events_path, dictionary_path, start_ts: int, end_ts: int) -> dict:
    """Read a committed snapshot and include every valid encounter lemma in the day."""
    if not 20 * 3600 <= end_ts - start_ts <= 28 * 3600:
        raise ValueError("日期范围异常")
    events = _read_only(events_path)
    encountered = {}
    interactions = {}
    diagnostics = {"valid_encounters": 0, "unusable_encounters": 0,
                   "interaction_only_words": 0}
    try:
        events.execute("BEGIN")
        cursor = events.execute(
            "SELECT ts,type,payload FROM events WHERE ts>=? AND ts<? "
            "AND type IN (?,?,'hover','known_click') ORDER BY ts",
            (start_ts, end_ts, *SELECTED_TYPES),
        )
        for ts, kind, payload in cursor:
            lemma = _lemma(payload)
            if not lemma:
                if kind in SELECTED_TYPES:
                    diagnostics["unusable_encounters"] += 1
                continue
            if kind in SELECTED_TYPES:
                diagnostics["valid_encounters"] += 1
                item = encountered.setdefault(lemma, {"lemma": lemma, "encounters": 0,
                                                       "hovers": 0, "known": 0,
                                                       "last_ts": 0})
                item["encounters"] += 1
                item["last_ts"] = max(item["last_ts"], ts)
            else:
                counts = interactions.setdefault(lemma, {"hover": 0, "known_click": 0})
                counts[kind] += 1
        # Releasing the short read transaction leaves all learning state untouched.
        events.rollback()
    finally:
        events.close()

    diagnostics["interaction_only_words"] = sum(
        1 for lemma in interactions if lemma not in encountered
    )
    dictionary = _read_only(dictionary_path)
    try:
        for lemma, item in encountered.items():
            counts = interactions.get(lemma, {})
            item["hovers"] = counts.get("hover", 0)
            item["known"] = counts.get("known_click", 0)
            row = dictionary.execute("SELECT translation FROM dict_words WHERE word=?",
                                     (lemma,)).fetchone()
            translation = row[0].strip() if row and isinstance(row[0], str) else ""
            item["translation"] = translation.replace("\\n", "；").replace("\n", "；") or MISSING
            item["english_definition"] = MISSING
            item["pos"] = MISSING
            item["dictionary_status"] = "公开发布词典" if row else "词典无词条"
            item["review_hint"] = bool(not item["known"] and
                                        (item["hovers"] or item["encounters"] >= 2))
    finally:
        dictionary.close()

    rows = sorted(encountered.values(), key=lambda r: (
        not r["review_hint"], not bool(r["hovers"]), -r["last_ts"], r["lemma"]
    ))
    return {"rows": rows, "diagnostics": diagnostics}


def render_daily_sheet(day_text: str, sheet: dict) -> str:
    """Render escaped cards; optional local script handles paging/printing only."""
    cards = []
    focus = []
    suggested = 0
    for index, row in enumerate(sheet["rows"]):
        lemma = escape(row["lemma"])
        translation = escape(row["translation"])
        status = escape(row["dictionary_status"])
        seen_at = time.strftime("%H:%M", time.localtime(row["last_ts"]))
        hint = '<span class="hint">建议先回顾（展示提示）</span>' if row["review_hint"] else ""
        known = "今日记录过“认识了”反馈" if row["known"] else "今日未记录该反馈"
        basis = ("今日记录过认识反馈" if row["known"] else
                 "主动悬停，今日未记录认识反馈" if row["hovers"] else
                 "多次系统选中，未记录悬停" if row["encounters"] >= 2 else
                 "仅系统选中，未记录悬停")
        initial = bool(row["review_hint"] and suggested < 10)
        if initial:
            suggested += 1
            focus.append(f'<article class="focus-card"><h3>{lemma}</h3><p>{translation}</p>'
                         f'<small>{basis}</small> '
                         f'<a href="#word-{index}" data-jump="{index}">查看并选择</a></article>')
        checked = " checked" if initial else ""
        cards.append(
            f'<article class="word-card" id="word-{index}" data-suggested="{str(initial).lower()}">'
            f'<label class="selection"><input type="checkbox"{checked}>加入打印：{lemma}</label>'
            f'<h2>{lemma} {hint}</h2><p><b>中译：</b>{translation}</p>'
            f'<p><b>英解：</b>未提供　<b>词性：</b>未提供</p>'
            f'<p class="basis">回顾依据：{basis}</p>'
            f'<small>系统记录 {row["encounters"]} 次 · 主动悬停 {row["hovers"]} 次 · '
            f'{known} · 最后选中 {seen_at} · {status}</small></article>'
        )
    summary = (f'已记录词 {len(sheet["rows"])} · '
               f'建议先回顾 {sum(r["review_hint"] for r in sheet["rows"])}')
    diag = sheet["diagnostics"]
    diagnostics = (f'另有仅互动、无当日选词的词 {diag["interaction_only_words"]} 个；'
                   f'无可用词头的选词记录 {diag["unusable_encounters"]} 条。')
    return Template(_TEMPLATE.read_text(encoding="utf-8")).substitute(
        date=escape(day_text), summary=summary,
        cards="\n".join(cards) or '<p class="empty">这一天没有已记录的选词。</p>',
        focus="\n".join(focus) or '<p class="empty">今日没有符合现有回顾提示的词，可在完整清单自行选择。</p>',
        diagnostics=diagnostics,
    )
