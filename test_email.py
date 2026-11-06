import os
import smtplib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import datetime as dt
import email_monitor
import monitor

class EmailTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "SMTP_USERNAME": "sender@web.de", "SMTP_PASSWORD": "app-secret",
            "MAIL_TO": "parent@example.org,child@example.org,parent@example.org"}, clear=True)
        self.env.start()
        self.tls = patch("email_monitor.ssl.create_default_context")
        self.context = self.tls.start().return_value

    def tearDown(self):
        self.tls.stop()
        self.env.stop()

    def test_both_recipients_and_tls_before_login(self):
        with patch("email_monitor.smtplib.SMTP") as smtp:
            connection = smtp.return_value.__enter__.return_value
            connection.send_message.return_value = {}
            email_monitor.notify("Stundenplan geaendert")
            smtp.assert_called_once_with("smtp.web.de", 587, timeout=30)
            names = [call[0] for call in connection.method_calls]
            self.assertLess(names.index("starttls"), names.index("login"))
            connection.starttls.assert_called_once_with(context=self.context)
            args = connection.send_message.call_args
            self.assertEqual(args.kwargs["to_addrs"], ["parent@example.org", "child@example.org"])
            self.assertEqual(str(args.args[0]["From"]), "sender@web.de")

    def test_rejected_recipient_is_error(self):
        with patch("email_monitor.smtplib.SMTP") as smtp:
            smtp.return_value.__enter__.return_value.send_message.return_value = {
                "child@example.org": (550, b"rejected")}
            with self.assertRaises(monitor.MonitorError):
                email_monitor.notify("test")

    def test_auth_failure_does_not_reveal_server_response(self):
        with patch("email_monitor.smtplib.SMTP") as smtp:
            smtp.return_value.__enter__.return_value.login.side_effect = smtplib.SMTPAuthenticationError(
                535, b"private server response app-secret")
            with self.assertRaises(monitor.MonitorError) as error:
                email_monitor.notify("test")
            self.assertIn("Anmeldung abgelehnt", str(error.exception))
            self.assertNotIn("app-secret", str(error.exception))
            self.assertNotIn("private server", str(error.exception))

    def test_header_injection_and_empty_recipients_rejected(self):
        for value in ("x@example.org\r\nBcc:other@example.org", "   ", "bad-address"):
            with patch.dict(os.environ, {"MAIL_TO": value}):
                with self.assertRaises(monitor.MonitorError):
                    email_monitor.settings()

    def test_test_mail_does_not_query_untis(self):
        with patch("email_monitor.notify") as notify, patch("monitor.fetch_entries") as fetch:
            email_monitor.main(test=True)
            notify.assert_called_once()
            fetch.assert_not_called()

    def test_email_run_requires_no_ntfy(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"WEBUNTIS_SCHOOL":"test","WEBUNTIS_USERNAME":"test",
                             "WEBUNTIS_PASSWORD":"test"}):
                with patch("monitor.fetch_entries", return_value=[]), patch("email_monitor.notify") as notify:
                    path = Path(tmp) / "state.json"
                    monitor.run(path, dt.date(2026,11,6), notifier=email_monitor.notify,
                                extra_required=email_monitor.REQUIRED)
                    self.assertEqual(notify.call_count, 1)
                    monitor.run(path, dt.date(2026,11,6), notifier=email_monitor.notify,
                                extra_required=email_monitor.REQUIRED)
                    self.assertEqual(notify.call_count, 1)

if __name__ == "__main__":
    unittest.main()
