"""Hourly WebUntis comparison. Never log credentials or raw timetable data."""
import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

STATE = Path(".state/snapshot.json")
SERVER = "https://ohg-tuttlingen.webuntis.com"
FIELDS = ("date", "startTime", "endTime", "su", "te", "ro", "kl",
          "code", "statustext", "substText", "lstext", "info",
          "activityType", "sg", "lsnumber")

class MonitorError(Exception):
    pass

def canonical(value):
    if isinstance(value, dict):
        return {k: canonical(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return sorted((canonical(v) for v in value),
                      key=lambda v: json.dumps(v, sort_keys=True))
    return value

def digest(value):
    return hashlib.sha256(json.dumps(canonical(value), sort_keys=True,
                                    ensure_ascii=False).encode()).hexdigest()

def snapshot(entries, start, days=14):
    if not isinstance(entries, list):
        raise MonitorError("Ungueltige Stundenplan-Antwort; Vergleichsstand bleibt erhalten.")
    result = {(start + dt.timedelta(days=i)).strftime("%Y%m%d"): []
              for i in range(days)}
    for entry in entries:
        if not isinstance(entry, dict) or not all(k in entry for k in ("date", "startTime", "endTime")):
            raise MonitorError("Unvollstaendige Stundenplandaten; Vergleich abgebrochen.")
        day = str(entry["date"])
        if day in result:
            result[day].append(digest({k: entry[k] for k in FIELDS if k in entry}))
    return {day: sorted(items) for day, items in result.items()}

def changed_days(previous, current):
    # Compare overlap only: neither yesterday disappearing nor a new horizon day is a change.
    return sorted(day for day in current if day in previous and previous[day] != current[day])

def rpc(method, params, school, session=None):
    endpoint = SERVER + "/WebUntis/jsonrpc.do?" + urlencode({"school": school})
    headers = {"Content-Type": "application/json", "User-Agent": "webuntis-monitor/1.0"}
    if session:
        if re.search(r"[\r\n;]", session):
            raise MonitorError("Ungueltige Sitzungskennung.")
        headers["Cookie"] = "JSESSIONID=" + session
    body = json.dumps({"id": "monitor", "jsonrpc": "2.0", "method": method,
                       "params": params}).encode()
    with urlopen(Request(endpoint, data=body, headers=headers), timeout=30) as response:
        payload = json.load(response)
    if not isinstance(payload, dict) or "result" not in payload or "error" in payload:
        raise MonitorError("WebUntis lehnt " + method + " ab. Anmeldung, Schulkennung und API-Rechte pruefen.")
    return payload["result"]

def fetch_entries(start):
    school = os.environ["WEBUNTIS_SCHOOL"]
    login = rpc("authenticate", {"user": os.environ["WEBUNTIS_USERNAME"],
                "password": os.environ["WEBUNTIS_PASSWORD"], "client": "webuntis-monitor"}, school)
    if not isinstance(login, dict) or not login.get("sessionId"):
        raise MonitorError("WebUntis-Anmeldung lieferte keine Sitzung.")
    session = login["sessionId"]
    try:
        person_id = os.environ.get("WEBUNTIS_PERSON_ID") or login.get("personId")
        person_type = os.environ.get("WEBUNTIS_PERSON_TYPE") or login.get("personType")
        if not person_id or not person_type or int(person_id) <= 0 or int(person_type) not in (1, 2, 5):
            raise MonitorError("Kein persoenlicher Stundenplan zugeordnet. Bei Elternkonto muss das Kind explizit konfiguriert werden.")
        options = {"element": {"id": int(person_id), "type": int(person_type)},
                   "startDate": int(start.strftime("%Y%m%d")),
                   "endDate": int((start + dt.timedelta(days=13)).strftime("%Y%m%d")),
                   "onlyBaseTimetable": False, "showInfo": True, "showSubstText": True,
                   "showLsText": True, "showStudentgroup": True}
        return rpc("getTimetable", {"options": options}, school, session)
    finally:
        try:
            rpc("logout", {}, school, session)
        except Exception:
            print("Hinweis: Abmeldung konnte nicht bestaetigt werden.")

def notify(message):
    topic = os.environ["NTFY_TOPIC"]
    if not re.fullmatch(r"[A-Za-z0-9_-]{24,128}", topic):
        raise MonitorError("NTFY_TOPIC: zufaelligen Namen mit 24-128 Zeichen verwenden.")
    body = json.dumps({"topic": topic, "title": "WebUntis OHG",
                       "message": message, "click": SERVER + "/WebUntis/"}).encode()
    with urlopen(Request("https://ntfy.sh", data=body,
                         headers={"Content-Type": "application/json"}), timeout=30) as response:
        if response.status != 200:
            raise MonitorError("Push-Zustellung nicht bestaetigt.")

def run(state_path=STATE, today=None):
    required = ("WEBUNTIS_SCHOOL", "WEBUNTIS_USERNAME", "WEBUNTIS_PASSWORD", "NTFY_TOPIC")
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise MonitorError("Einrichtung fehlt: " + ", ".join(missing))
    start = today or dt.datetime.now(ZoneInfo("Europe/Berlin")).date()
    current = snapshot(fetch_entries(start), start)
    identity = digest([SERVER, os.environ["WEBUNTIS_SCHOOL"], os.environ["WEBUNTIS_USERNAME"],
                       os.environ.get("WEBUNTIS_PERSON_ID"), os.environ.get("WEBUNTIS_PERSON_TYPE")])
    previous = None
    if state_path.exists():
        saved = json.loads(state_path.read_text(encoding="utf-8"))
        if saved.get("version") != 1 or not isinstance(saved.get("days"), dict):
            raise MonitorError("Vergleichsspeicher ungueltig. Cache bewusst loeschen und neu starten.")
        if saved.get("identity") == identity:
            previous = saved["days"]
    if previous is None or not set(previous).intersection(current):
        notify("Ueberwachung neu initialisiert. Die naechsten 14 Tage werden stuendlich verglichen. Dies ist zugleich der Push-Test.")
    else:
        changes = changed_days(previous, current)
        if changes:
            dates = ", ".join(dt.datetime.strptime(d, "%Y%m%d").strftime("%d.%m.") for d in changes)
            notify("Stundenplan geaendert am " + dates + ". Bitte Details in Untis ansehen.")
    # Persist only after successful delivery; a failed push is retried next run.
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"version": 1, "identity": identity, "days": current}),
                         encoding="utf-8")
    temporary.replace(state_path)
    print("Stundenplan geprueft. Vergleichsstand aktualisiert.")

if __name__ == "__main__":
    try:
        run()
    except MonitorError as exc:
        print("::error::" + str(exc))
        sys.exit(1)
    except Exception:
        # Exception URLs / server bodies can contain private information.
        print("::error::Pruefung fehlgeschlagen (Netzwerk, Antwortformat oder Konfiguration). Vergleichsstand bleibt erhalten.")
        sys.exit(1)
