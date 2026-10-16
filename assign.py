import json, os, sys

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

# App-Admin-Recht (CAN_MANAGE_PERMISSIONS) bewusst NICHT enthalten.
PERMS = [
    "CAN_VIEW_NON_FINANCIAL_DATA", "CAN_VIEW_APP_QUALITY",
    "CAN_MANAGE_DRAFT_APPS", "CAN_MANAGE_APP_CONTENT",
    "CAN_MANAGE_PUBLIC_LISTING", "CAN_MANAGE_TRACK_APKS",
    "CAN_MANAGE_TRACK_USERS", "CAN_MANAGE_PUBLIC_APKS",
    "CAN_REPLY_TO_REVIEWS",
]
POOL = "pool.json"


def load_pool():
    try:
        return json.load(open(POOL))
    except FileNotFoundError:
        return {"free": [], "used": {}}


def save_pool(p):
    json.dump(p, open(POOL, "w"), indent=2)


def all_users():
    out, token = [], None
    while True:
        r = svc.users().list(parent=f"developers/{DEV}", pageToken=token).execute()
        out += r.get("users", [])
        token = r.get("nextPageToken")
        if not token:
            return out


def cmd_users():
    for u in all_users():
        apps = [g["name"].split("/grants/")[-1] for g in u.get("grants", [])]
        print(u["email"], "|", u.get("accessState"), "|",
              u.get("developerAccountPermissions", []), "|", apps)


def grant(email, app):
    parent = f"developers/{DEV}/users/{email}"
    body = {"name": f"{parent}/grants/{app}", "appLevelPermissions": PERMS}
    if "." in app:  # Paketname; App-IDs von Entwuerfen sind rein numerisch
        body["packageName"] = app
    return svc.grants().create(parent=parent, body=body).execute()


def check_user(email):
    if email.lower() not in {u["email"].lower() for u in all_users()}:
        sys.exit(f"{email} ist kein Nutzer in diesem Konto")


def cmd_grant(email, app):
    check_user(email)
    print("OK:", grant(email, app))


def cmd_assign(email):
    check_user(email)
    pool = load_pool()
    if not pool["free"]:
        sys.exit("Pool leer - bitte neue Entwurfs-Apps anlegen und per pool_add eintragen")
    app_id = pool["free"][0]
    print("OK:", grant(email, app_id))
    pool["free"].pop(0)  # erst nach erfolgreichem Grant entfernen
    pool["used"].setdefault(email, []).append(app_id)
    save_pool(pool)
    print(f"{email} -> {app_id}; im Pool noch frei: {len(pool['free'])}")


def cmd_pool_add(ids):
    pool = load_pool()
    known = set(pool["free"]) | {a for v in pool["used"].values() for a in v}
    new = [i.strip() for i in ids.split(",") if i.strip() and i.strip() not in known]
    pool["free"] += new
    save_pool(pool)
    print(f"{len(new)} hinzugefuegt; frei: {len(pool['free'])}")


if __name__ == "__main__":
    action, email, app = (sys.argv + ["", "", ""])[1:4]
    try:
        if action == "users":
            cmd_users()
        elif action == "assign":
            cmd_assign(email)
        elif action == "grant":
            cmd_grant(email, app)
        elif action == "pool_add":
            cmd_pool_add(app)
        else:
            sys.exit("unbekannte Aktion")
    except HttpError as e:
        print("::error title=API::" + f"{e.resp.status} " + e.content.decode().replace("\n", " ")[:800]); sys.exit(1)
