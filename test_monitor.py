import copy
import datetime as dt
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import monitor

TODAY = dt.date(2026, 11, 6)
ENTRY = {"id": 1, "date": 20261106, "startTime": 730, "endTime": 815,
         "su": [{"id": 3}], "ro": [{"id": 4}], "code": ""}

class ComparisonTests(unittest.TestCase):
    def test_cancel_room_and_time_changes(self):
        before = monitor.snapshot([ENTRY], TODAY)
        for key, value in (("code", "cancelled"), ("ro", [{"id": 8}]), ("startTime", 900)):
            after = monitor.snapshot([{**ENTRY, key: value}], TODAY)
            self.assertEqual(monitor.changed_days(before, after), ["20261106"])

    def test_removed_lesson(self):
        self.assertEqual(monitor.changed_days(monitor.snapshot([ENTRY], TODAY),
                                             monitor.snapshot([], TODAY)), ["20261106"])

    def test_order_and_volatile_id_do_not_matter(self):
        a = {**ENTRY, "te": [{"id": 1}, {"id": 2}]}
        b = {**a, "id": 999, "te": list(reversed(a["te"]))}
        self.assertEqual(monitor.snapshot([a], TODAY), monitor.snapshot([b], TODAY))

    def test_horizon_rollover_does_not_notify(self):
        before = monitor.snapshot([ENTRY], TODAY)
        after = monitor.snapshot([], TODAY + dt.timedelta(days=1))
        self.assertEqual(monitor.changed_days(before, after), [])

    def test_invalid_response_fails(self):
        for value in ({}, None, [{"date": 20261106}]):
            with self.assertRaises(monitor.MonitorError):
                monitor.snapshot(value, TODAY)

    def test_cache_has_no_raw_timetable(self):
        value = json.dumps(monitor.snapshot([{**ENTRY, "substText": "Private details"}], TODAY))
        self.assertNotIn("Private details", value)
        self.assertNotIn("startTime", value)

class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "snapshot.json"
        self.env = patch.dict(os.environ, {"WEBUNTIS_SCHOOL": "test", "WEBUNTIS_USERNAME": "test",
                         "WEBUNTIS_PASSWORD": "test", "NTFY_TOPIC": "x" * 32}, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def invoke(self, entries, push_error=None):
        with patch("monitor.fetch_entries", return_value=entries), patch("monitor.notify", side_effect=push_error) as push:
            monitor.run(self.path, TODAY)
            return push.call_count

    def test_first_run_then_unchanged_and_change(self):
        self.assertEqual(self.invoke([ENTRY]), 1)
        self.assertEqual(self.invoke([ENTRY]), 0)
        self.assertEqual(self.invoke([{**ENTRY, "code": "cancelled"}]), 1)

    def test_failed_push_keeps_previous_state_for_retry(self):
        self.invoke([ENTRY])
        original = self.path.read_bytes()
        with self.assertRaises(RuntimeError):
            self.invoke([], RuntimeError("push down"))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.invoke([]), 1)

    def test_api_failure_preserves_state(self):
        self.invoke([ENTRY])
        original = self.path.read_bytes()
        with patch("monitor.fetch_entries", side_effect=RuntimeError("API down")):
            with self.assertRaises(RuntimeError):
                monitor.run(self.path, TODAY)
        self.assertEqual(self.path.read_bytes(), original)

    def test_new_account_gets_baseline(self):
        self.invoke([ENTRY])
        with patch.dict(os.environ, {"WEBUNTIS_USERNAME": "other"}):
            self.assertEqual(self.invoke([ENTRY]), 1)

if __name__ == "__main__":
    unittest.main()
