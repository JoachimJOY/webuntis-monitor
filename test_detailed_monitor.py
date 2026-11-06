import copy
import datetime as dt
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import detailed_monitor as detail
import monitor

DAY = dt.date(2026,11,5)
SUBJECTS = [{"id": 1, "name": "Diff", "longName": "Differenzierungsstunde"},
            {"id": 2, "name": "D", "longName": "Deutsch"}]
BASE = {"id": 11, "date": 20261105, "startTime": 1005, "endTime": 1050,
        "su": [{"id": 1}], "code": "", "te": [{"id": 3}], "ro": [{"id": 4}]}

def plan(entries):
    return detail.normalize(entries, SUBJECTS, DAY)

class DetailTests(unittest.TestCase):
    def test_cancel_plus_german_added(self):
        new = [{**BASE, "code":"cancelled"},
               {**BASE, "id":12, "su":[{"id":2}], "code":"irregular"}]
        text = detail.report(plan([BASE]), plan(new))
        self.assertIn("Differenzierungsstunde", text)
        self.assertIn("Die Stunde entfällt", text)
        self.assertIn("Neu im Stundenplan: Deutsch", text)
        self.assertIn("10:05–10:50", text)

    def test_subject_changed(self):
        text = detail.report(plan([BASE]),plan([{**BASE,"su":[{"id":2}]}]))
        self.assertIn("Fach gewechselt: Differenzierungsstunde → Deutsch",text)

    def test_new_id_same_slot_replacement(self):
        text = detail.report(plan([BASE]),plan([{**BASE,"id":99,"su":[{"id":2}]}]))
        self.assertIn("Fach gewechselt",text)

    def test_time_shift(self):
        text = detail.report(plan([BASE]),plan([{**BASE,"startTime":1100,"endTime":1145}]))
        self.assertIn("10:05–10:50 → 11:00–11:45",text)

    def test_reinstatement(self):
        text = detail.report(plan([{**BASE,"code":"cancelled"}]),plan([BASE]))
        self.assertIn("findet wieder statt",text)

    def test_removed_is_not_claimed_cancelled(self):
        text = detail.report(plan([BASE]),plan([]))
        self.assertIn("Nicht mehr im Stundenplan",text)
        self.assertNotIn("Die Stunde entfällt",text)

    def test_volatile_ids_and_order_ignored(self):
        second = {**BASE,"id":12,"startTime":1100,"endTime":1145}
        self.assertIsNone(detail.report(plan([BASE,second]),
            plan([{**second,"id":98},{**BASE,"id":99}])))

    def test_no_false_match_for_multiple_parallel_replacements(self):
        before=plan([BASE,{**BASE,"id":12,"su":[{"id":2}]}])
        after=plan([{**BASE,"id":20,"su":[{"id":2}],"code":"irregular"},
                    {**BASE,"id":21,"code":"cancelled"}])
        text=detail.report(before,after)
        self.assertNotIn("Fach gewechselt",text)

    def test_only_overlap_days_compared(self):
        before=plan([BASE])
        after=detail.normalize([],SUBJECTS,DAY+dt.timedelta(days=1))
        self.assertIsNone(detail.report(before,after))

    def test_note_room_teacher_changes(self):
        text=detail.report(plan([BASE]),plan([{**BASE,"te":[{"id":5}],
            "ro":[{"id":8}],"substText":"Deutsch statt Differenzierung"}]))
        self.assertIn("Lehrkraft geändert",text)
        self.assertIn("Raum geändert",text)
        self.assertIn("Deutsch statt Differenzierung",text)

    def test_encrypted_roundtrip_and_wrong_key(self):
        data={"version":2,"days":plan([BASE])}
        encrypted=detail.encrypt_state(data,"test-password")
        self.assertNotIn("Differenzierungsstunde".encode(),encrypted)
        self.assertEqual(detail.decrypt_state(encrypted,"test-password"),data)
        with self.assertRaises(monitor.MonitorError):
            detail.decrypt_state(encrypted,"other-password")

class DetailPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/"state.enc"
        self.env=patch.dict(os.environ,{"WEBUNTIS_SCHOOL":"test","WEBUNTIS_USERNAME":"test",
            "WEBUNTIS_PASSWORD":"test","SMTP_USERNAME":"test@web.de","SMTP_PASSWORD":"test-app",
            "MAIL_TO":"parent@example.org,child@example.org"},clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def invoke(self,entries,sender):
        with patch("detailed_monitor.fetch_data",return_value=(entries,SUBJECTS)):
            detail.run(self.path,DAY,sender=sender)

    def test_initialize_change_then_no_repeat(self):
        messages=[]
        self.invoke([BASE],messages.append)
        self.assertEqual(len(messages),1)
        changed=[{**BASE,"su":[{"id":2}]}]
        self.invoke(changed,messages.append)
        self.assertEqual(len(messages),2)
        self.assertIn("Fach gewechselt",messages[-1])
        self.invoke(changed,messages.append)
        self.assertEqual(len(messages),2)

    def test_send_failure_keeps_state(self):
        self.invoke([BASE],lambda text:None)
        saved=self.path.read_bytes()
        def fail(text):
            raise RuntimeError("simulated delivery failure")
        with self.assertRaises(RuntimeError):
            self.invoke([{**BASE,"code":"cancelled"}],fail)
        self.assertEqual(self.path.read_bytes(),saved)

    def test_api_failure_keeps_state(self):
        self.invoke([BASE],lambda text:None)
        saved=self.path.read_bytes()
        with patch("detailed_monitor.fetch_data",side_effect=RuntimeError("API down")):
            with self.assertRaises(RuntimeError):
                detail.run(self.path,DAY,sender=lambda text:None)
        self.assertEqual(self.path.read_bytes(),saved)

if __name__=="__main__":
    unittest.main()
