import csv, io, json, os, re, sys, urllib.request
from datetime import datetime, timezone, timedelta
from googleapiclient.errors import HttpError
import assign  # nutzt dieselbe Anmeldung und dieselben Funktionen

SHEET_ID = os.environ["SHEET_ID"]
STATE = "sheet_state.json"
URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?tqx=out:csv&headers=1&sheet=Form%20Responses%201"
MAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$")
PKG = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")
KEEP = timedelta(hours=48)


def now():
    return datetime.now(timezone.utc)


def process(email, pkg):
    """True = erledigt, False = spaeter nochmal versuchen (App existiert noch nicht)."""
    if pkg and pkg not in assign.PROTECTED and not assign.app_exists(pkg):
        return False
    try:
        assign.cmd_request(email, pkg)
    except SystemExit:
        pass  # fail() hat die Ablehnung schon als Annotation ausgegeben
    except HttpError as e:
        print("::error title=poll::" + email + " " + assign.err_text(e))
    return True


def main():
    raw = urllib.request.urlopen(URL, timeout=30).read().decode("utf-8")
    rows = list(csv.reader(io.StringIO(raw)))
    head, data = rows[0], rows[1:]
    low = [h.lower() for h in head]
    # Paketspalte zuerst suchen: ihr Titel enthaelt auch "Gmail" ("... associated with your Gmail Account")
    cp = next((i for i, h in enumerate(low) if "package name" in h), None)
    ci = next((i for i, h in enumerate(low) if i != cp and ("gmail" in h or "e-mail" in h or "email" in h)), None)
    kopf = " | ".join(h[:50] for h in head)
    if ci is None:
        print("::error title=sheet::keine E-Mail-Spalte gefunden: " + kopf)
        sys.exit(1)
    if cp is None:
        print("::warning title=sheet::keine Paketname-Spalte gefunden, nur Einladungen moeglich: " + kopf)
    try:
        state = json.load(open(STATE))
    except FileNotFoundError:
        state = {"done": len(data), "pending": []}
        print(f"::notice title=sheet::Erster Lauf, {len(data)} alte Zeilen uebersprungen")

    todo = []
    for item in state["pending"]:
        if now() - datetime.fromisoformat(item["since"]) > KEEP:
            print(f"::notice title=sheet::{item['email']} {item['pkg']}: nach 48 h aufgegeben")
        else:
            todo.append((item["email"], item["pkg"], item["since"]))
    for r in data[state["done"]:]:
        email = (r[ci] if ci < len(r) else "").strip().lower()
        pkg = (r[cp] if cp is not None and cp < len(r) else "").strip()
        if not MAIL.match(email):
            print(f"::notice title=sheet::ungueltige E-Mail uebersprungen: {email[:40]}")
            continue
        if pkg and not PKG.match(pkg):
            print(f"::notice title=sheet::ungueltiger Paketname uebersprungen: {pkg[:60]}")
            continue
        todo.append((email, pkg, now().isoformat()))

    pending = []
    for email, pkg, since in todo:
        if not process(email, pkg):
            if not any(p["email"] == email and p["pkg"] == pkg for p in pending):
                pending.append({"email": email, "pkg": pkg, "since": since})
    state = {"done": len(data), "pending": pending}
    json.dump(state, open(STATE, "w"), indent=2)
    print(f"::notice title=sheet::Zeilen: {len(data)}, bearbeitet: {len(todo)}, wartend: {len(pending)}")


if __name__ == "__main__":
    main()
