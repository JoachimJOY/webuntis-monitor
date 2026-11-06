"""WEB.DE SMTP transport. Addresses and passwords must stay in GitHub Secrets."""
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
import monitor

REQUIRED = ("SMTP_USERNAME", "SMTP_PASSWORD", "MAIL_TO")

def settings():
    missing = [key for key in REQUIRED if not os.environ.get(key)]
    if missing:
        raise monitor.MonitorError("E-Mail-Einrichtung fehlt: " + ", ".join(missing))
    sender = os.environ["SMTP_USERNAME"].strip()
    recipients = list(dict.fromkeys(s.strip() for s in os.environ["MAIL_TO"].split(",") if s.strip()))
    pattern = r"[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+"
    if not re.fullmatch(pattern, sender) or not recipients or any(
            not re.fullmatch(pattern, address) for address in recipients):
        raise monitor.MonitorError("E-Mail-Adressen ungueltig. MAIL_TO muss durch Kommas getrennte Adressen enthalten.")
    return sender, recipients, os.environ["SMTP_PASSWORD"]

def notify(text):
    sender, recipients, password = settings()
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Subject"] = "WebUntis: Stundenplan-Mitteilung"
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[1])
    message.set_content(text + "\n\nStundenplan ansehen:\n" + monitor.SERVER + "/WebUntis/\n")
    try:
        with smtplib.SMTP("smtp.web.de", 587, timeout=30) as connection:
            connection.ehlo()
            connection.starttls(context=ssl.create_default_context())
            connection.ehlo()
            connection.login(sender, password)
            refused = connection.send_message(message, from_addr=sender, to_addrs=recipients)
            if refused:
                raise monitor.MonitorError("WEB.DE hat nicht alle Empfaenger angenommen. Vergleichsstand bleibt erhalten.")
    except smtplib.SMTPAuthenticationError:
        raise monitor.MonitorError("WEB.DE-Anmeldung abgelehnt. SMTP_USERNAME, App-Passwort und Freigabe fuer externe Programme pruefen.") from None
    except (smtplib.SMTPException, OSError):
        raise monitor.MonitorError("E-Mail-Versand fehlgeschlagen. Vergleichsstand bleibt erhalten.") from None

def main(test=False):
    settings()
    if test:
        notify("Testmail: Der E-Mail-Versand des WebUntis-Monitors funktioniert. Diese Nachricht meldet keine Stundenplanaenderung.")
        print("WEB.DE hat die Testmail fuer alle Empfaenger angenommen. Posteingang und Spamordner pruefen.")
    else:
        monitor.run(notifier=notify, extra_required=REQUIRED)

if __name__ == "__main__":
    import sys
    try:
        main(test="--test-email" in sys.argv[1:])
    except monitor.MonitorError as error:
        print("::error::" + str(error))
        raise SystemExit(1)
    except Exception as error:
        import traceback
        print("::error::Fehlertyp: " + type(error).__name__)
        for frame in traceback.extract_tb(error.__traceback__):
            print(f"Fehlerstelle: {frame.name}, Zeile {frame.lineno}")
        raise SystemExit(1)
