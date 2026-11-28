"""Daily-sheet candidate contract, using only synthetic databases."""
import asyncio
import json
from html.parser import HTMLParser
import inspect
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from pae_core import api
from pae_core.daily_sheet import build_daily_sheet, render_daily_sheet, local_day_bounds


class DailySheetTests(unittest.TestCase):
    def test_paginated_page_retains_all_cards_and_only_ten_suggestions(self):
        class Cards(HTMLParser):
            def __init__(self):
                super().__init__()
                self.words, self.suggested, self.focus, self.hidden_words = 0, 0, 0, 0
            def handle_starttag(self, tag, attrs):
                a = dict(attrs)
                if tag == 'article' and a.get('class') == 'word-card':
                    self.words += 1
                    self.suggested += a.get('data-suggested') == 'true'
                    self.hidden_words += 'hidden' in a
                if tag == 'a' and 'data-jump' in a: self.focus += 1
        for count in (0, 1, 10, 11, 85, 300):
            with self.subTest(count=count):
                rows = [{'lemma': 'word'+str(i), 'translation': '合成释义',
                         'dictionary_status': '合成词典', 'last_ts': self.start,
                         'encounters': 2, 'hovers': 0, 'known': 0, 'review_hint': True}
                        for i in range(count)]
                page = render_daily_sheet('2026-11-08', {'rows': rows, 'diagnostics': {
                    'interaction_only_words': 0, 'unusable_encounters': 0}})
                parser = Cards(); parser.feed(page)
                self.assertEqual((parser.words, parser.suggested, parser.focus),
                                 (count, min(10, count), min(10, count)))
                self.assertEqual(parser.hidden_words, 0, 'no-script fallback must retain every word')
                self.assertIn('src="/daily-sheet.js"', page)
                self.assertIn('id="pagination"', page)
                self.assertIn('id="print-selected"', page)

    def test_passive_repeats_are_explained_without_claiming_attention(self):
        self.add(self.start + 1, 'encounter', 'reflect')
        self.add(self.start + 2, 'encounter', 'reflect')
        self.writer.commit()
        page = render_daily_sheet('2026-11-08', self.sheet())
        self.assertIn('多次系统选中，未记录悬停', page)
        self.assertNotIn('最不会', page)

    def test_single_passive_contact_does_not_fill_suggestions(self):
        self.add(self.start + 1, 'encounter', 'reflect')
        self.writer.commit()
        page = render_daily_sheet('2026-11-08', self.sheet())
        self.assertIn('今日没有符合现有回顾提示的词', page)
        self.assertNotIn('data-suggested="true"', page)

    def test_fixed_script_endpoint_and_daily_csp(self):
        self.assertTrue(hasattr(api, 'daily_sheet_script'), 'fixed script endpoint missing')
        self.assertEqual(len(inspect.signature(api.daily_sheet_script).parameters), 0)
        response = api.daily_sheet_script()
        self.assertEqual(response.status_code, 200)
        self.assertIn('javascript', response.headers['content-type'])
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
        self.assertEqual(response.body, (Path(__file__).resolve().parents[1] / 'daily_sheet.js').read_bytes())
        with patch.object(api, 'DAILY_SCRIPT', self.events.parent / 'missing.js'):
            failed = api.daily_sheet_script()
        self.assertEqual(failed.status_code, 503)
        self.assertNotIn(str(self.events.parent), failed.body.decode())
        with patch.object(api.paths, 'db_path', return_value=str(self.events)), \
             patch.object(api.paths, 'dict_path', return_value=str(self.dictionary)):
            page = api.daily_sheet_page('2026-11-08')
        self.assertIn("script-src 'self'", page.headers['content-security-policy'])
        self.assertNotIn("script-src 'unsafe-inline'", page.headers['content-security-policy'])

    def test_script_is_delivered_through_registered_app_without_engine(self):
        async def request(path, method='GET', query=b''):
            sent = []
            async def receive():
                return {'type': 'http.request', 'body': b'', 'more_body': False}
            async def send(message):
                sent.append(message)
            scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
                     'method': method, 'scheme': 'http', 'path': path, 'query_string': query,
                     'headers': [(b'host', b'127.0.0.1:41815')],
                     'server': ('127.0.0.1', 41815), 'client': ('127.0.0.1', 40101)}
            await api.app(scope, receive, send)
            return (sent[0]['status'], dict(sent[0]['headers']),
                    b''.join(x.get('body', b'') for x in sent[1:]))

        self.add(self.start + 1, 'encounter', 'reflect')
        self.writer.commit()
        before = tuple(self.writer.iterdump())
        with patch.object(api, 'get_engine', side_effect=AssertionError('engine touched')), \
             patch.object(api.paths, 'db_path', return_value=str(self.events)), \
             patch.object(api.paths, 'dict_path', return_value=str(self.dictionary)):
            status, headers, body = asyncio.run(request('/daily-sheet.js'))
            self.assertEqual(status, 200)
            self.assertIn(b'javascript', headers[b'content-type'])
            self.assertEqual(headers[b'cache-control'], b'no-store')
            self.assertEqual(headers[b'x-content-type-options'], b'nosniff')
            self.assertEqual(body, api.DAILY_SCRIPT.read_bytes())
            # Extra query arguments never turn this fixed route into file access.
            self.assertEqual(asyncio.run(request('/daily-sheet.js', query=b'path=elsewhere'))[2], body)
            self.assertEqual(asyncio.run(request('/daily-sheet.js/elsewhere'))[0], 404)
            self.assertEqual(asyncio.run(request('/daily-sheet.js', method='POST'))[0], 405)
            status, headers, body = asyncio.run(request('/daily', query=b'date=2026-11-08'))
            self.assertEqual(status, 200)
            self.assertIn(b"script-src 'self'", headers[b'content-security-policy'])
            self.assertIn(b'src="/daily-sheet.js"', body)
            status, _, body = asyncio.run(request('/v1/daily-sheet', query=b'date=2026-11-08'))
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)['rows'][0]['lemma'], 'reflect')
        self.assertEqual(tuple(self.writer.iterdump()), before)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ["OG_DAILY_TEST_ROOT"])
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.events = root / "events.sqlite"
        self.dictionary = root / "dict.sqlite"
        self.writer = sqlite3.connect(self.events)
        self.addCleanup(self.writer.close)
        self.writer.executescript("""
            CREATE TABLE events(event_id TEXT PRIMARY KEY, idem_key TEXT, ts INTEGER NOT NULL,
                                type TEXT NOT NULL, payload TEXT NOT NULL, source TEXT NOT NULL);
            CREATE INDEX idx_events_ts ON events(ts);
        """)
        con = sqlite3.connect(self.dictionary)
        try:
            con.execute("CREATE TABLE dict_words(word TEXT PRIMARY KEY, translation TEXT)")
            con.executemany("INSERT INTO dict_words VALUES(?,?)", [
                ("reflect", "vt. 反映\\nvi. 反思"),
                ("clarify", "vt. 澄清"),
                ("approach", "n. 方法"),
            ])
            con.commit()
        finally:
            con.close()
        self.start, self.end = local_day_bounds("2026-11-08")
        self.seq = 0

    def add(self, ts, kind, lemma=None, raw=None, **extra):
        self.seq += 1
        payload = raw if raw is not None else json.dumps({"lemma": lemma, **extra})
        self.writer.execute("INSERT INTO events VALUES(?,?,?,?,?,?)",
                            (str(self.seq), None, ts, kind, payload, "synthetic"))

    def sheet(self):
        return build_daily_sheet(self.events, self.dictionary, self.start, self.end)

    def test_empty_day_and_no_database_creation(self):
        self.assertEqual(self.sheet()["rows"], [])
        self.assertEqual(self.sheet()["diagnostics"]["valid_encounters"], 0)
        missing = self.events.parent / "missing.sqlite"
        with self.assertRaises(sqlite3.OperationalError):
            build_daily_sheet(missing, self.dictionary, self.start, self.end)
        self.assertFalse(missing.exists())

    def test_only_committed_events_are_visible(self):
        self.add(self.start + 1, "encounter", "reflect")
        self.assertEqual(self.sheet()["rows"], [])
        self.writer.commit()
        self.assertEqual([r["lemma"] for r in self.sheet()["rows"]], ["reflect"])

    def test_wal_committed_rows_visible_without_checkpoint(self):
        self.assertEqual(self.writer.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
        self.add(self.start + 1, "encounter", "reflect")
        self.writer.commit()
        self.assertTrue(Path(str(self.events) + "-wal").exists())
        self.assertEqual([r["lemma"] for r in self.sheet()["rows"]], ["reflect"])
        self.add(self.start + 2, "encounter", "clarify")
        self.assertEqual([r["lemma"] for r in self.sheet()["rows"]], ["reflect"])

    def test_local_midnights_aggregation_and_missing_fields(self):
        self.add(self.start - 1, "encounter", "before_day")
        self.add(self.start, "encounter", "reflect", sentence="SECRET_CONTEXT", page_id="SECRET_PAGE")
        self.add(self.end - 1, "encounter", "reflect")
        self.add(self.start + 2, "hover", "reflect")
        self.add(self.start + 3, "encounter", "clarify")
        self.add(self.start + 4, "known_click", "clarify")
        self.add(self.start + 5, "encounter", "missing_entry")
        self.add(self.start + 6, "hover", "orphan_hover")
        self.add(self.start + 7, "encounter", raw="{bad json")
        self.add(self.start + 8, "encounter", "")
        self.add(self.end, "encounter", "after_day")
        self.writer.commit()
        sheet = self.sheet()
        self.assertEqual([r["lemma"] for r in sheet["rows"]],
                         ["reflect", "missing_entry", "clarify"])
        self.assertEqual(sheet["diagnostics"], {
            "valid_encounters": 4, "unusable_encounters": 2,
            "interaction_only_words": 1,
        })
        self.assertEqual(sheet["rows"][0]["encounters"], 2)
        self.assertEqual(sheet["rows"][0]["hovers"], 1)
        self.assertEqual(sheet["rows"][0]["review_hint"], True)
        self.assertEqual(sheet["rows"][1]["translation"], "未提供")
        self.assertEqual(sheet["rows"][2]["known"], 1)
        self.assertTrue(all(r["english_definition"] == "未提供" and
                            r["pos"] == "未提供" for r in sheet["rows"]))
        self.assertNotIn("SECRET_", json.dumps(sheet, ensure_ascii=False))

    def test_full_list_is_not_eight_or_forty(self):
        for i in range(121):
            self.add(self.start + i, "encounter", f"word{i}")
        self.writer.commit()
        self.assertEqual(len(self.sheet()["rows"]), 121)

    def test_render_escapes_dictionary_and_word(self):
        self.add(self.start + 1, "encounter", "<script>alert(1)</script>")
        self.writer.commit()
        sheet = self.sheet()
        html = render_daily_sheet("2026-11-08", sheet)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn('系统记录词 · 2026-11-08</p>', html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("SECRET_", html)

    def test_invalid_date_rejected(self):
        with self.assertRaises(ValueError):
            local_day_bounds("2026-02-30")
        with self.assertRaises(ValueError):
            local_day_bounds("2026-11-08T00:00")

    def test_api_and_print_page_use_only_synthetic_stores(self):
        self.add(self.start + 1, "encounter", "reflect", page_id="SECRET_PAGE")
        self.writer.commit()
        with patch.object(api.paths, "db_path", return_value=str(self.events)), \
             patch.object(api.paths, "dict_path", return_value=str(self.dictionary)), \
             patch.object(api, "get_engine", side_effect=AssertionError("engine touched")):
            data = api.daily_sheet_json("2026-11-08")
            page = api.daily_sheet_page("2026-11-08")
        self.assertEqual(data.status_code, 200)
        self.assertEqual(json.loads(data.body)["rows"][0]["lemma"], "reflect")
        self.assertEqual(data.headers["cache-control"], "no-store")
        self.assertEqual(page.status_code, 200)
        self.assertIn("今日识单", page.body.decode("utf-8"))
        self.assertIn("default-src 'none'", page.headers["content-security-policy"])
        self.assertNotIn("SECRET_PAGE", data.body.decode("utf-8") + page.body.decode("utf-8"))

    def test_api_failures_do_not_disclose_paths(self):
        self.assertEqual(api.daily_sheet_json("bad-date").status_code, 400)
        self.assertEqual(api.daily_sheet_page("2026-02-30").status_code, 400)
        with patch.object(api.paths, "db_path", return_value=str(self.events.parent / "absent.db")), \
             patch.object(api.paths, "dict_path", return_value=str(self.dictionary)):
            response = api.daily_sheet_json("2026-11-08")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(str(self.events.parent), response.body.decode("utf-8"))
        routes = {route.path for route in api.app.routes}
        self.assertTrue({"/v1/daily-sheet", "/daily"}.issubset(routes))


if __name__ == "__main__":
    unittest.main()
