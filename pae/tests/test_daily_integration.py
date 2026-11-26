"""Integration contract for the cumulative learning candidate plus read-only daily sheet."""

from contextlib import closing
from datetime import date
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from pae_core import api
from pae_core.engine import LearningWritePaused


class DailyIntegrationTests(unittest.TestCase):
    def test_existing_routes_and_budget_survive_daily_integration(self):
        routes = {route.path for route in api.app.routes}
        self.assertTrue({"/v1/feedback", "/v1/annotate", "/v1/event",
                         "/v1/daily-sheet", "/daily"}.issubset(routes))
        self.assertIn('href="/daily"', api.index())
        self.assertIn('href="/panel"', api.index())

        class FakeEngine:
            def annotate(self, text, page_id, session_id, max_annotations=None):
                return {"text": text, "page_id": page_id,
                        "max_annotations": max_annotations}

        with patch.object(api, "get_engine", return_value=FakeEngine()):
            result = api.annotate(api.AnnotateBody(
                text="hello", page_id="synthetic", max_annotations=2))
        self.assertEqual(result["max_annotations"], 2)

    def test_write_pause_does_not_make_daily_read_touch_engine(self):
        with tempfile.TemporaryDirectory(dir=os.environ["OG_DAILY_TEST_ROOT"]) as temp:
            root = Path(temp)
            db = root / "events.sqlite"
            dictionary = root / "dict.sqlite"
            with closing(sqlite3.connect(db)) as con:
                con.execute("CREATE TABLE events(event_id TEXT PRIMARY KEY, idem_key TEXT, "
                            "ts INTEGER NOT NULL, type TEXT NOT NULL, payload TEXT NOT NULL, "
                            "source TEXT NOT NULL)")
                con.execute("INSERT INTO events VALUES(?,?,?,?,?,?)", (
                    "1", None, int(time.time()), "encounter",
                    json.dumps({"lemma": "reflect", "page_id": "SECRET_SYNTHETIC_PAGE"}),
                    "synthetic"))
                con.commit()
            with closing(sqlite3.connect(dictionary)) as con:
                con.execute("CREATE TABLE dict_words(word TEXT PRIMARY KEY, translation TEXT)")
                con.execute("INSERT INTO dict_words VALUES(?,?)", ("reflect", "反思"))
                con.commit()

            with patch.object(api, "get_engine", side_effect=LearningWritePaused("synthetic")):
                paused = api.annotate(api.AnnotateBody(text="hello", page_id="synthetic"))
                self.assertEqual(paused.status_code, 503)
                with patch.object(api.paths, "db_path", return_value=str(db)), \
                     patch.object(api.paths, "dict_path", return_value=str(dictionary)):
                    daily = api.daily_sheet_json(date.today().isoformat())
                    page = api.daily_sheet_page(date.today().isoformat())
            self.assertEqual(daily.status_code, 200)
            self.assertEqual(page.status_code, 200)
            self.assertEqual([row["lemma"] for row in json.loads(daily.body)["rows"]],
                             ["reflect"])
            self.assertNotIn("SECRET_SYNTHETIC_PAGE", daily.body.decode("utf-8"))
            self.assertNotIn("SECRET_SYNTHETIC_PAGE", page.body.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
