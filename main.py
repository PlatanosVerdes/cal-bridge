import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

app = FastAPI(title="cal-bridge")

DATA_DIR = Path("/data/tokens")
DATA_DIR.mkdir(parents=True, exist_ok=True)

CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
API_KEY = os.environ["CAL_API_KEY"]
BASE_URL = os.environ["CAL_BASE_URL"].rstrip("/")

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

CLIENT_CONFIG = {
    "web": {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [f"{BASE_URL}/callback"],
    }
}

_SAFE_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")


def _validate_account(name: str) -> str:
    if not _SAFE_NAME.match(name):
        raise HTTPException(status_code=400, detail="Invalid account name (a-z, 0-9, _, - max 32 chars)")
    return name


def _verify_key(key: str = Query(..., alias="key")):
    if key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


def _token_path(account: str) -> Path:
    return DATA_DIR / f"{account}.json"


def _load_creds(account: str) -> Optional[Credentials]:
    path = _token_path(account)
    if not path.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        path.write_text(creds.to_json())
    return creds


def _fetch_events(service, calendar_id: str, calendar_name: str, time_min: datetime, time_max: datetime) -> list:
    result = (
        service.events()
        .list(
            calendarId=calendar_id,
            timeMin=time_min.isoformat(),
            timeMax=time_max.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=50,
        )
        .execute()
    )
    return [
        {
            "id": e["id"],
            "title": e.get("summary", ""),
            "start": e["start"].get("dateTime", e["start"].get("date")),
            "end": e["end"].get("dateTime", e["end"].get("date")),
            "all_day": "date" in e["start"],
            "location": e.get("location"),
            "calendar_id": calendar_id,
            "calendar_name": calendar_name,
        }
        for e in result.get("items", [])
    ]


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/accounts")
def list_accounts(_: None = Depends(_verify_key)):
    accounts = [p.stem for p in DATA_DIR.glob("*.json")]
    return {"accounts": accounts}


@app.get("/calendars")
def list_calendars(account: str = Query(...), _: None = Depends(_verify_key)):
    account = _validate_account(account)
    creds = _load_creds(account)
    if not creds:
        raise HTTPException(status_code=404, detail=f"Account '{account}' not authorized.")
    service = build("calendar", "v3", credentials=creds)
    result = service.calendarList().list().execute()
    return {
        "account": account,
        "calendars": [
            {"id": c["id"], "name": c["summary"], "primary": c.get("primary", False)}
            for c in result.get("items", [])
        ],
    }


@app.get("/auth/{account}")
def start_auth(account: str):
    _validate_account(account)
    flow = Flow.from_client_config(CLIENT_CONFIG, scopes=SCOPES)
    flow.redirect_uri = f"{BASE_URL}/callback"
    auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent", state=account)
    return RedirectResponse(auth_url)


@app.get("/callback")
def oauth_callback(code: str, state: str):
    _validate_account(state)
    flow = Flow.from_client_config(CLIENT_CONFIG, scopes=SCOPES, state=state)
    flow.redirect_uri = f"{BASE_URL}/callback"
    flow.fetch_token(code=code)
    _token_path(state).write_text(flow.credentials.to_json())
    return HTMLResponse(
        f"<h2>Cuenta <b>{state}</b> autorizada correctamente.</h2>"
        "<p>Ya puedes cerrar esta ventana.</p>"
    )


@app.get("/events")
def get_events(
    account: str = Query(...),
    days: int = Query(7, ge=1, le=30),
    _: None = Depends(_verify_key),
):
    account = _validate_account(account)
    creds = _load_creds(account)
    if not creds:
        raise HTTPException(
            status_code=404,
            detail=f"Account '{account}' not authorized. Visit {BASE_URL}/auth/{account}",
        )

    service = build("calendar", "v3", credentials=creds)
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days)

    cal_list = service.calendarList().list().execute()
    all_events = []
    for cal in cal_list.get("items", []):
        try:
            all_events.extend(_fetch_events(service, cal["id"], cal["summary"], now, end))
        except Exception:
            pass

    all_events.sort(key=lambda e: e["start"])
    return {"account": account, "days": days, "count": len(all_events), "events": all_events}


@app.get("/today")
def get_today(account: str = Query(...), _: None = Depends(_verify_key)):
    return get_events(account=account, days=1)


@app.get("/week")
def get_week(account: str = Query(...), _: None = Depends(_verify_key)):
    return get_events(account=account, days=7)
