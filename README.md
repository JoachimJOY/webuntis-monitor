# WebUntis Monitor – OHG Tuttlingen

Prüft die nächsten 14 Kalendertage stündlich über GitHub Actions.
Bei Änderungen kommt eine ntfy-Mitteilung aufs iPhone. Kein eigener Server erforderlich.

**Status: vorbereitet, noch nicht mit einem echten WebUntis-Konto getestet.**
Ohne die folgenden Secrets kann der Monitor nicht arbeiten.
Die JSON-RPC-Schnittstelle muss für dein Konto zugelassen sein. Ein funktionierender Browser-Login allein garantiert dies nicht.
Elternkonten benötigen möglicherweise eine explizite Zuordnung zum Kind; der Monitor wählt kein Kind automatisch.

## Einrichtung

1. Installiere **ntfy** auf dem iPhone und erlaube Mitteilungen.
2. Wähle einen zufälligen Themennamen mit mindestens 24 Zeichen (Buchstaben, Ziffern, _ oder -).
   Abonniere dieses Thema in der App auf dem Server https://ntfy.sh.
   Der Themenname ist ein Zugangsschlüssel: Wer ihn kennt, kann Nachrichten lesen und senden.
   Verwende keine Namen oder Schul-/Klassennamen als Thema.
3. Öffne [Settings → Secrets and variables → Actions](https://github.com/JoachimJOY/webuntis-monitor/settings/secrets/actions).
   Lege jeweils mit **New repository secret** an:

| Name | Wert |
| --- | --- |
| WEBUNTIS_USERNAME | Dein WebUntis-Benutzername |
| WEBUNTIS_PASSWORD | Dein WebUntis-Passwort |
| WEBUNTIS_SCHOOL | Technische Schulkennung aus dem Login-Link (Parameter school), nicht zwingend der Anzeigename |
| NTFY_TOPIC | Genau der zufällige Themenname aus der ntfy-App |

Wenn dein Login-Link keine Schulkennung enthält, muss diese noch ermittelt werden.
Keine Kennung raten und keine wiederholten Anmeldeversuche durchführen.
Der Server ist auf https://ohg-tuttlingen.webuntis.com eingestellt; die offizielle
[Schuladresse](https://webuntis.ohg-tuttlingen.de) leitet dorthin weiter.

Bei einem Elternkonto: Erst die eindeutige Schüler-ID klären, dann optional
WEBUNTIS_PERSON_ID und WEBUNTIS_PERSON_TYPE (= 5 für Schüler) als Secrets setzen.
Ohne passende Zuordnung bricht der Monitor ab.

4. Öffne [Actions → WebUntis Monitor](https://github.com/JoachimJOY/webuntis-monitor/actions/workflows/monitor.yml).
   Klicke **Run workflow → Run workflow** auf main.
5. Erfolg bedeutet: Tests und Monitor sind grün **und** die Initialisierungsnachricht ist auf dem iPhone angekommen.
   Vergleiche sind zu jeder vollen Stunde (Europe/Berlin) geplant. Sonntags ist für 09:00 Uhr zusätzlich eine Testmail nach erfolgreicher Prüfung vorgesehen.
   Die erste Nachricht bestätigt die Initialisierung, nicht eine Stundenplanänderung.

Zugangsdaten niemals in Issues, Chat, Code, Screenshots oder Commit-Dateien schreiben.
Die GitHub-Anbindung dieses Chats kann keine Repository-Secrets verwalten; diese vier Einträge müssen im GitHub-Formular gesetzt werden.

## Verhalten und Grenzen

- Erfasst Änderungen an Zeiten, Fächern, Räumen, Lehrern, Ausfallcodes und Vertretungstexten, soweit die API diese liefert.
- Push nennt nur die betroffenen Tage; Details stehen in Untis. Keine Lehrernamen oder Schülerdaten im Push.
- Beim ersten Lauf wird ein Vergleichsstand aufgebaut. Fehlender/abgelaufener Cache führt zu einer erneuten Initialisierungsnachricht.
- Verglichen werden nur Tage, die schon im letzten Prüfzeitraum lagen; ein neuer Tag am Ende des Zeitfensters löst keine Meldung aus.
- Fehlgeschlagener Abruf oder Push überschreibt den letzten Vergleich nicht. Der nächste Lauf versucht es erneut.
- Der Cache enthält nur Datum und SHA-256-Vergleichswerte, keine Rohstundenpläne und keine Zugangsdaten. Caches eines öffentlichen Repositorys sind nicht geheim.
- GitHub kann geplante Läufe verzögern; dies ist kein minutengenauer Alarmdienst.
- GitHub deaktiviert Zeitpläne öffentlicher Repositorys nach 60 Tagen ohne Repository-Aktivität. Bei deaktiviertem Zeitplan im Actions-Reiter wieder einschalten; siehe [GitHub-Dokumentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).
- Fehler sind im Actions-Reiter sichtbar. GitHub-Actions-Fehlerbenachrichtigungen in den GitHub-Kontoeinstellungen aktivieren.
- Pause: Actions → WebUntis Monitor → … → Disable workflow.
- Es wird eine neue Sitzung je Lauf geöffnet und anschließend abgemeldet. Passwortwechsel erfolgt durch Aktualisieren des Secrets.

## Tests

Python 3.12, keine zusätzlichen Pakete: `python -m unittest -v`.
Tests decken Ausfall, Raum-/Zeitänderungen, entfernte Stunden, Datumswechsel, Antwortfehler,
Kontowechsel sowie den Erhalt des Vergleichsstands bei Abruf-/Push-Fehlern ab.

API-Verhalten anhand der [python-webuntis-Implementierung](https://github.com/python-webuntis/python-webuntis)
nachvollzogen. Wenn die Schule JSON-RPC sperrt oder ein Elternkonto keine Personenzuordnung liefert,
muss die Anbindung angepasst werden. Es ist noch keine erfolgreiche reale Überwachung bestätigt.
