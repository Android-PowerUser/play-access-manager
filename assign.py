import json, os, sys, time

def _hook(t, v, tb):
    msg = (t.__name__ + ": " + str(v)).replace("\r", " ").replace("\n", " ")[:900]
    print("::error title=assign.py::" + msg)
sys.excepthook = _hook

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

DEV = os.environ["DEV_ID"]
creds = service_account.Credentials.from_service_account_info(
    json.loads(os.environ["SERVICE_ACCOUNT_JSON"]),
    scopes=["https://www.googleapis.com/auth/androidpublisher"])
svc = build("androidpublisher", "v3", credentials=creds, cache_discovery=False)

# Rechte pro App (inkl. Admin und Finanzdaten der eigenen App)
PERMS = [
    "CAN_MANAGE_PERMISSIONS", "CAN_VIEW_FINANCIAL_DATA", "CAN_MANAGE_ORDERS",
    "CAN_VIEW_NON_FINANCIAL_DATA", "CAN_VIEW_APP_QUALITY",
    "CAN_MANAGE_DRAFT_APPS", "CAN_MANAGE_APP_CONTENT",
    "CAN_MANAGE_PUBLIC_LISTING", "CAN_MANAGE_TRACK_APKS",
    "CAN_MANAGE_TRACK_USERS", "CAN_MANAGE_PUBLIC_APKS",
    "CAN_REPLY_TO_REVIEWS", "CAN_MANAGE_DEEPLINKS",
]
# "Ruhezustand": die API kann globale Rechte nicht auf leer setzen (500), daher dieses schmale Recht
SAFE_GLOBAL = ["CAN_CREATE_MANAGED_PLAY_APPS_GLOBAL"]
# Zum Anlegen einer App noetig (laut Play-Hilfe beide zusammen)
GLOBAL_CREATE = ["CAN_MANAGE_DRAFT_APPS_GLOBAL", "CAN_VIEW_NON_FINANCIAL_DATA_GLOBAL"]


def all_users():
    # users.list erlaubt keine Seitenaufteilung: pageSize muss -1 sein
    return svc.users().list(parent=f"developers/{DEV}", pageSize=-1).execute().get("users", [])


def find_user(email):
    for u in all_users():
        if u["email"].lower() == email.lower():
            return u
    sys.exit(f"{email} ist kein Nutzer in diesem Konto")


def err_text(e):
    return f"{e.resp.status} " + e.content.decode().replace("\n", " ")[:500]


def grant(email, app):
    """Setzt App-Rechte: erst aktualisieren, falls vorhanden, sonst neu anlegen."""
    parent = f"developers/{DEV}/users/{email}"
    body = {"name": f"{parent}/grants/{app}", "packageName": app,
            "appLevelPermissions": PERMS}
    try:
        return svc.grants().patch(name=body["name"], updateMask="appLevelPermissions",
                                  body=body).execute()
    except HttpError:
        return svc.grants().create(parent=parent, body=body).execute()


def set_global(email, perms):
    """Globale Rechte setzen. Leere Liste: mehrere Varianten probieren (API macht Probleme)."""
    name = f"developers/{DEV}/users/{email}"
    if perms:
        return svc.users().patch(name=name, updateMask="developerAccountPermissions",
                                 body={"developerAccountPermissions": perms}).execute()
    tries = [
        ("mask+leer", dict(updateMask="developerAccountPermissions",
                           body={"developerAccountPermissions": []})),
        ("ohne mask+leer", dict(body={"developerAccountPermissions": []})),
        ("mask+leerer body", dict(updateMask="developerAccountPermissions", body={})),
    ]
    errs = []
    for label, kw in tries:
        try:
            r = svc.users().patch(name=name, **kw).execute()
            print(f"::notice title=strip-variante::{label} hat funktioniert")
            return r
        except HttpError as e:
            errs.append(label + ": " + err_text(e)[:120])
    raise RuntimeError("Entziehen fehlgeschlagen: " + " | ".join(errs))


def fail(msg):
    print("::error title=abgelehnt::" + msg)
    sys.exit(1)


def app_exists(package):
    """True, wenn die App im Konto existiert (404 = gibt es nicht)."""
    try:
        e = svc.edits().insert(packageName=package, body={}).execute()
    except HttpError as ex:
        if ex.resp.status == 404:
            return False
        raise
    try:
        svc.edits().delete(packageName=package, editId=e["id"]).execute()
    except HttpError:
        pass
    return True


def holders(package):
    """Nutzer, die bereits ein App-Recht auf dieses Paket haben."""
    return [u["email"] for u in all_users()
            if any(g["name"].endswith("/grants/" + package) for g in u.get("grants", []))]


def invite(email, perms=None):
    # Die API lehnt Einladungen ganz ohne Rechte ab ("No permissions set")
    return svc.users().create(parent=f"developers/{DEV}",
                              body={"email": email,
                                    "developerAccountPermissions": perms or SAFE_GLOBAL}).execute()


def cmd_check(package):
    print(f"::notice title=check::{package} | existiert={app_exists(package)} | Rechteinhaber={holders(package)}")


def cmd_request(email, package):
    """Anfrage aus dem Formular."""
    if email.lower() not in {u["email"].lower() for u in all_users()}:
        invite(email, SAFE_GLOBAL)
        print(f"::notice title=invite::{email} eingeladen (Ruhezustand ohne App-Zugriff)")
        if package:
            print("::notice title=request::Einladung annehmen, dann Formular mit Paketname erneut senden")
        return
    if not package:
        print("::notice title=request::kein Paketname angegeben -> keine Rechte vergeben")
        return
    cmd_window(email, package)


def cmd_migrate(apply):
    """Bestehende Apps: mapping.json (E-Mail -> Pakete) auf App-Rechte umsetzen. Standard: Probelauf."""
    mp = json.load(open("mapping.json"))
    users = {u["email"].lower(): u for u in all_users()}
    lines = []
    for email, pkgs in mp.items():
        u = users.get(email.lower())
        if not u:
            lines.append(f"{email}: NICHT im Konto")
            continue
        for p in pkgs:
            if not app_exists(p):
                lines.append(f"{email} -> {p}: App existiert nicht")
                continue
            if any(g["name"].endswith("/grants/" + p) for g in u.get("grants", [])):
                lines.append(f"{email} -> {p}: Recht schon vorhanden")
                continue
            if not apply:
                lines.append(f"{email} -> {p}: wuerde vergeben")
                continue
            try:
                grant(u["email"], p)
                lines.append(f"{email} -> {p}: VERGEBEN")
            except HttpError as e:
                lines.append(f"{email} -> {p}: FEHLER {err_text(e)[:120]}")
    print("\n".join(lines))
    print(f"::notice title=migrate {'APPLY' if apply else 'PROBELAUF'}::" + "%0A".join(lines))


def cmd_users():
    lines = []
    for u in all_users():
        apps = [g["name"].split("/grants/")[-1] for g in u.get("grants", [])]
        lines.append(f'{u["email"]} | {u.get("accessState")} | '
                     f'{len(u.get("developerAccountPermissions", []))} globale Rechte | {apps}')
    print("\n".join(lines))
    print(f"::notice title=users ({len(lines)})::" + "%0A".join(lines))


def cmd_user(email):
    u = find_user(email)
    apps = [g["name"].split("/grants/")[-1] + " :: " + ",".join(g.get("appLevelPermissions", []))
            for g in u.get("grants", [])]
    print("::notice title=user::" + f'{u["email"]} | {u.get("accessState")} | '
          f'global={u.get("developerAccountPermissions", [])} | ' + " ;; ".join(apps))


def cmd_grant(email, app):
    find_user(email)
    grant(email, app)
    print(f"::notice title=grant::OK {email} -> {app}")


def cmd_window(email, package, minutes=20):
    """Oeffnet ein kurzes Zeitfenster zum Anlegen der App, vergibt dann App-Rechte
    auf das Paket und entzieht die globalen Rechte wieder (immer, auch bei Timeout)."""
    u = find_user(email)
    if u.get("accessState") != "ACCESS_GRANTED":
        fail("Einladung noch nicht angenommen - bitte erst annehmen, dann Formular erneut senden")
    if set(u.get("developerAccountPermissions", [])) - set(SAFE_GLOBAL):
        fail("Nutzer hat bereits globale Rechte - zuerst migrieren")
    if app_exists(package):
        fail(f"{package} existiert bereits - keine Rechte vergeben")
    if holders(package):
        fail(f"{package}: es gibt bereits Rechteinhaber - keine Rechte vergeben")
    set_global(email, GLOBAL_CREATE)
    print(f"::notice title=window::Fenster offen fuer {email}, Paket {package}, {minutes} Min.")
    done, last = False, ""
    try:
        deadline = time.time() + minutes * 60
        while time.time() < deadline:
            try:
                grant(email, package)
                done = True
                break
            except HttpError as e:
                last = err_text(e)
                time.sleep(20)
    finally:
        set_global(email, SAFE_GLOBAL)
    if not done:
        print("::error title=window::Timeout, App nicht gefunden. Letzte Antwort: " + last)
        sys.exit(1)
    print(f"::notice title=window::FERTIG {email} -> {package}; globale Rechte entzogen")


if __name__ == "__main__":
    action, email, app = (sys.argv + ["", "", ""])[1:4]
    try:
        if action == "users":
            cmd_users()
        elif action == "user":
            cmd_user(email)
        elif action == "grant":
            cmd_grant(email, app)
        elif action == "window":
            cmd_window(email, app)
        elif action == "check":
            cmd_check(app)
        elif action == "invite":
            invite(email)
            print(f"::notice title=invite::{email} eingeladen mit {SAFE_GLOBAL}")
        elif action == "setglobal":
            set_global(email, [p.strip() for p in app.split(",") if p.strip()])
            print(f"::notice title=setglobal::{email} -> {app}")
        elif action == "strip":
            set_global(email, SAFE_GLOBAL)
            print(f"::notice title=strip::{email} auf Ruhezustand {SAFE_GLOBAL} gesetzt")
        elif action == "request":
            cmd_request(email, app)
        elif action == "migrate":
            cmd_migrate(app == "apply")
        else:
            sys.exit("unbekannte Aktion")
    except HttpError as e:
        print("::error title=API::" + err_text(e))
        sys.exit(1)
