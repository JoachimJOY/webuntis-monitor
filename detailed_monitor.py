"""Detailed timetable comparison; private snapshots are encrypted before caching."""
import base64
from collections import Counter
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo
from cryptography.fernet import Fernet, InvalidToken
import monitor
import email_monitor

STATE = Path(".state-details/snapshot.enc")
WEEKDAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")

def clean(value):
    text = str(value or "")
    return "".join(c for c in text if c == "\n" or ord(c) >= 32)[:1500]

def fetch_data(start):
    school = os.environ["WEBUNTIS_SCHOOL"]
    login = monitor.rpc("authenticate", {"user": os.environ["WEBUNTIS_USERNAME"],
        "password": os.environ["WEBUNTIS_PASSWORD"], "client": "webuntis-details"}, school)
    session = login.get("sessionId")
    if not session:
        raise monitor.MonitorError("Keine WebUntis-Sitzung erhalten.")
    try:
        person = int(os.environ.get("WEBUNTIS_PERSON_ID") or login.get("personId", 0))
        kind = int(os.environ.get("WEBUNTIS_PERSON_TYPE") or login.get("personType", 0))
        if person <= 0 or kind not in (1, 2, 5):
            raise monitor.MonitorError("Schuelerzuordnung fehlt.")
        options = {"element": {"id": person, "type": kind},
            "startDate": int(start.strftime("%Y%m%d")),
            "endDate": int((start + dt.timedelta(days=13)).strftime("%Y%m%d")),
            "onlyBaseTimetable": False, "showInfo": True, "showSubstText": True,
            "showLsText": True, "showStudentgroup": True}
        entries = monitor.rpc("getTimetable", {"options": options}, school, session)
        subjects = monitor.rpc("getSubjects", {}, school, session)
        return entries, subjects
    finally:
        try:
            monitor.rpc("logout", {}, school, session)
        except Exception:
            pass

def normalize(entries, subjects, start):
    monitor.snapshot(entries, start)  # Validate required date/time fields before comparison.
    if not isinstance(subjects, list):
        raise monitor.MonitorError("Fachnamen konnten nicht gelesen werden.")
    names = {str(s["id"]): clean(s.get("longName") or s.get("name") or s["id"]) for s in subjects}
    days = {(start + dt.timedelta(days=i)).strftime("%Y%m%d"): [] for i in range(14)}
    for entry in entries:
        day = str(entry["date"])
        if day not in days:
            continue
        subject_names = sorted({names.get(str(s.get("id")), "Fach-ID " + str(s.get("id")))
                                for s in entry.get("su", [])})
        record = {
            "id": str(entry.get("id", "")),
            "start": int(entry["startTime"]), "end": int(entry["endTime"]),
            "subjects": subject_names, "code": clean(entry.get("code")).casefold(),
            "teachers": sorted(str(x.get("id")) for x in entry.get("te", [])),
            "rooms": sorted(str(x.get("id")) for x in entry.get("ro", [])),
            "groups": monitor.canonical(entry.get("sg", "")),
            "notes": {k: monitor.canonical(entry[k]) for k in
                      ("statustext", "substText", "lstext", "info", "activityType", "lsnumber")
                      if entry.get(k)},
        }
        days[day].append(record)
    return days

def content(record):
    return monitor.digest({k: v for k, v in record.items() if k != "id"})

def time_text(record):
    def fmt(value):
        return f"{value//100:02d}:{value%100:02d}"
    return fmt(record["start"]) + "–" + fmt(record["end"])

def subject_text(record):
    return ", ".join(record["subjects"]) or "Unterricht"

def status_text(code):
    return {"": "regulär", "cancelled": "entfällt", "irregular": "abweichender Unterricht / Vertretung"}.get(
        code, code or "regulär")

def describe_pair(old, new):
    lines = []
    if old["subjects"] != new["subjects"]:
        lines.append("Fach gewechselt: " + subject_text(old) + " → " + subject_text(new) + ".")
    if (old["start"], old["end"]) != (new["start"], new["end"]):
        lines.append("Zeit verschoben: " + time_text(old) + " → " + time_text(new) + ".")
    if old["code"] != new["code"]:
        if new["code"] == "cancelled":
            lines.append("Die Stunde entfällt.")
        elif old["code"] == "cancelled" and new["code"] == "":
            lines.append("Die Stunde findet wieder statt.")
        else:
            lines.append("Status: " + status_text(old["code"]) + " → " + status_text(new["code"]) + ".")
    if old["teachers"] != new["teachers"]:
        lines.append("Lehrkraft geändert.")
    if old["rooms"] != new["rooms"]:
        lines.append("Raum geändert.")
    if old["groups"] != new["groups"]:
        lines.append("Unterrichtsgruppe geändert.")
    if old["notes"] != new["notes"]:
        texts = [clean(value) for value in new["notes"].values() if isinstance(value, str)]
        lines.append("Unterrichtshinweis geändert" + (": " + "; ".join(texts) if texts else "."))
    return subject_text(new) + " (" + time_text(new) + "):\n  " + "\n  ".join(lines)

def diff_day(old_records, new_records):
    old, new = list(old_records), list(new_records)
    # Remove equivalent entries even when Untis assigns new IDs or changes ordering.
    for previous in old[:]:
        match = next((current for current in new if content(previous) == content(current)), None)
        if match is not None:
            old.remove(previous)
            new.remove(match)
    paired = []
    # Pair unique stable IDs first, then unambiguous same-time replacements.
    for key in (lambda r: ("id", r["id"]) if r["id"] else None,
                lambda r: ("time", r["start"], r["end"])):
        old_counts = Counter(key(r) for r in old)
        new_counts = Counter(key(r) for r in new)
        for previous in old[:]:
            value = key(previous)
            if value is None or old_counts[value] != 1 or new_counts[value] != 1:
                continue
            current = next(r for r in new if key(r) == value)
            paired.append(describe_pair(previous, current))
            old.remove(previous)
            new.remove(current)
    output = paired
    for record in sorted(old, key=lambda r: (r["start"], subject_text(r))):
        output.append("Nicht mehr im Stundenplan: " + subject_text(record) + " (" + time_text(record) +
                      "). Vorheriger Status: " + status_text(record["code"]) + ".")
    for record in sorted(new, key=lambda r: (r["start"], subject_text(r))):
        if record["code"] == "cancelled":
            label = "Neu als Ausfall gemeldet: "
        else:
            label = "Neu im Stundenplan: "
        output.append(label + subject_text(record) + " (" + time_text(record) +
                      "). Status: " + status_text(record["code"]) + ".")
    return output

def report(previous, current):
    sections = []
    for day in sorted(set(previous).intersection(current)):
        changes = diff_day(previous[day], current[day])
        if changes:
            date = dt.datetime.strptime(day, "%Y%m%d").date()
            title = WEEKDAYS[date.weekday()] + ", " + date.strftime("%d.%m.%Y")
            sections.append(title + "\n" + "\n\n".join(changes))
    if not sections:
        return None
    return ("Im Stundenplan wurden folgende Änderungen erkannt:\n\n" +
            "\n\n".join(sections) +
            "\n\nVerglichen mit der letzten erfolgreichen Prüfung. Bitte den aktuellen Plan in Untis beachten.")

def encrypt_state(data, password):
    salt = os.urandom(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    encrypted = Fernet(base64.urlsafe_b64encode(key)).encrypt(
        json.dumps(data, ensure_ascii=False).encode())
    return json.dumps({"salt": base64.b64encode(salt).decode(),
                       "token": encrypted.decode()}).encode()

def decrypt_state(blob, password):
    try:
        envelope = json.loads(blob)
        salt = base64.b64decode(envelope["salt"], validate=True)
        if len(salt) != 16:
            raise ValueError("salt")
        key = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
        return json.loads(Fernet(base64.urlsafe_b64encode(key)).decrypt(envelope["token"].encode()))
    except (InvalidToken, ValueError, KeyError, TypeError):
        raise monitor.MonitorError(
            "Detail-Vergleichsstand nicht lesbar. Nach App-Passwortwechsel den Cache untis-details-v2 bewusst loeschen; danach wird neu initialisiert.") from None

def run(path=STATE, today=None, sender=None):
    email_monitor.settings()
    required = ("WEBUNTIS_SCHOOL", "WEBUNTIS_USERNAME", "WEBUNTIS_PASSWORD")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise monitor.MonitorError("Einrichtung fehlt: " + ", ".join(missing))
    start = today or dt.datetime.now(ZoneInfo("Europe/Berlin")).date()
    entries, subjects = fetch_data(start)
    current = normalize(entries, subjects, start)
    identity = monitor.digest([monitor.SERVER, os.environ["WEBUNTIS_SCHOOL"],
        os.environ["WEBUNTIS_USERNAME"], os.environ.get("WEBUNTIS_PERSON_ID"),
        os.environ.get("WEBUNTIS_PERSON_TYPE")])
    previous = None
    if path.exists():
        saved = decrypt_state(path.read_bytes(), os.environ["SMTP_PASSWORD"])
        if saved.get("version") != 2 or not isinstance(saved.get("days"), dict):
            raise monitor.MonitorError("Ungueltiger Detail-Vergleichsstand.")
        if saved.get("identity") == identity:
            previous = saved["days"]
    send = sender if sender is not None else email_monitor.notify
    if previous is None or not set(previous).intersection(current):
        send("Die ausführliche Stundenplanüberwachung ist eingerichtet. Ab jetzt werden Fach, Uhrzeit, "
             "Ausfälle und weitere Änderungen mit dem letzten Stand verglichen. "
             "Diese Bestätigung meldet keine neue Stundenplanänderung.")
    else:
        message = report(previous, current)
        if message:
            send(message)
    encrypted = encrypt_state({"version": 2, "identity": identity, "days": current},
                              os.environ["SMTP_PASSWORD"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(encrypted)
    temporary.replace(path)
    print("Detailvergleich erfolgreich; verschluesselter Vergleichsstand aktualisiert.")

if __name__ == "__main__":
    try:
        run()
    except monitor.MonitorError as error:
        print("::error::" + str(error))
        raise SystemExit(1)
    except Exception as error:
        import traceback
        print("::error::Detail-Monitor: " + type(error).__name__)
        for frame in traceback.extract_tb(error.__traceback__):
            print(f"Fehlerstelle: {frame.name}, Zeile {frame.lineno}")
        raise SystemExit(1)
