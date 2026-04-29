import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx
import msal
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

app = FastAPI(title="cal-bridge")

DATA_DIR = Path("/data/tokens")
DATA_DIR.mkdir(parents=True, exist_ok=True)

GOOGLE_CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
API_KEY = os.environ["CAL_API_KEY"]
BASE_URL = os.environ["CAL_BASE_URL"].rstrip("/")

MS_CLIENT_ID = os.environ.get("MS_CLIENT_ID", "")
MS_CLIENT_SECRET = os.environ.get("MS_CLIENT_SECRET", "")
MS_AUTHORITY = "https://login.microsoftonline.com/common"
MS_GRAPH_SCOPES = ["https://graph.microsoft.com/Calendars.Read", "offline_access"]

GOOGLE_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
GOOGLE_CLIENT_CONFIG = {
    "web": {
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
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


# ── Google ────────────────────────────────────────────────────────────────────

def _google_token_path(account: str) -> Path:
    return DATA_DIR / f"{account}.json"


def _load_google_creds(account: str) -> Optional[Credentials]:
    path = _google_token_path(account)
    if not path.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(path), GOOGLE_SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        path.write_text(creds.to_json())
    return creds


def _fetch_google_calendar_events(service, calendar_id: str, calendar_name: str, time_min: datetime, time_max: datetime) -> list:
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
            "account": None,
            "provider": "google",
        }
        for e in result.get("items", [])
    ]


def _get_google_events(account: str, time_min: datetime, time_max: datetime) -> list:
    creds = _load_google_creds(account)
    if not creds:
        raise HTTPException(
            status_code=404,
            detail=f"Google account '{account}' not authorized. Visit {BASE_URL}/auth/{account}",
        )
    service = build("calendar", "v3", credentials=creds)
    cal_list = service.calendarList().list().execute()
    events = []
    for cal in cal_list.get("items", []):
        try:
            cal_events = _fetch_google_calendar_events(service, cal["id"], cal["summary"], time_min, time_max)
            for e in cal_events:
                e["account"] = account
            events.extend(cal_events)
        except Exception:
            pass
    return events


# ── Microsoft ─────────────────────────────────────────────────────────────────

def _ms_token_path(account: str) -> Path:
    return DATA_DIR / f"{account}_ms.json"


def _get_ms_app(account: str):
    cache = msal.SerializableTokenCache()
    path = _ms_token_path(account)
    if path.exists():
        cache.deserialize(path.read_text())
    ms_app = msal.ConfidentialClientApplication(
        MS_CLIENT_ID,
        authority=MS_AUTHORITY,
        client_credential=MS_CLIENT_SECRET,
        token_cache=cache,
    )
    return ms_app, cache


def _save_ms_cache(account: str, cache):
    if cache.has_state_changed:
        _ms_token_path(account).write_text(cache.serialize())


def _get_ms_access_token(account: str) -> Optional[str]:
    ms_app, cache = _get_ms_app(account)
    ms_accounts = ms_app.get_accounts()
    if not ms_accounts:
        return None
    result = ms_app.acquire_token_silent(MS_GRAPH_SCOPES, account=ms_accounts[0])
    _save_ms_cache(account, cache)
    return result.get("access_token") if result else None


def _get_ms_events(account: str, time_min: datetime, time_max: datetime) -> list:
    token = _get_ms_access_token(account)
    if not token:
        raise HTTPException(
            status_code=404,
            detail=f"Microsoft account '{account}' not authorized. Visit {BASE_URL}/auth/microsoft/{account}",
        )
    headers = {"Authorization": f"Bearer {token}"}
    cal_resp = httpx.get("https://graph.microsoft.com/v1.0/me/calendars", headers=headers)
    cal_resp.raise_for_status()

    events = []
    for cal in cal_resp.json().get("value", []):
        try:
            ev_resp = httpx.get(
                f"https://graph.microsoft.com/v1.0/me/calendars/{cal['id']}/calendarView",
                headers=headers,
                params={
                    "startDateTime": time_min.isoformat(),
                    "endDateTime": time_max.isoformat(),
                    "$top": 50,
                    "$select": "id,subject,start,end,isAllDay,location",
                },
            )
            ev_resp.raise_for_status()
            for e in ev_resp.json().get("value", []):
                events.append({
                    "id": e["id"],
                    "title": e.get("subject", ""),
                    "start": e["start"].get("dateTime") or e["start"].get("date", ""),
                    "end": e["end"].get("dateTime") or e["end"].get("date", ""),
                    "all_day": e.get("isAllDay", False),
                    "location": e.get("location", {}).get("displayName"),
                    "calendar_id": cal["id"],
                    "calendar_name": cal["name"],
                    "account": account,
                    "provider": "microsoft",
                })
        except Exception:
            pass
    return events


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/accounts")
def list_accounts(_: None = Depends(_verify_key)):
    google = [p.stem for p in DATA_DIR.glob("*.json") if not p.stem.endswith("_ms")]
    microsoft = [p.stem[:-3] for p in DATA_DIR.glob("*_ms.json")]
    return {"google": google, "microsoft": microsoft}


@app.get("/calendars")
def list_calendars(
    account: str = Query(...),
    provider: str = Query("google"),
    _: None = Depends(_verify_key),
):
    account = _validate_account(account)
    if provider == "microsoft":
        token = _get_ms_access_token(account)
        if not token:
            raise HTTPException(status_code=404, detail=f"Microsoft account '{account}' not authorized.")
        resp = httpx.get("https://graph.microsoft.com/v1.0/me/calendars", headers={"Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        return {
            "account": account,
            "provider": "microsoft",
            "calendars": [
                {"id": c["id"], "name": c["name"], "primary": c.get("isDefaultCalendar", False)}
                for c in resp.json().get("value", [])
            ],
        }
    creds = _load_google_creds(account)
    if not creds:
        raise HTTPException(status_code=404, detail=f"Google account '{account}' not authorized.")
    service = build("calendar", "v3", credentials=creds)
    result = service.calendarList().list().execute()
    return {
        "account": account,
        "provider": "google",
        "calendars": [
            {"id": c["id"], "name": c["summary"], "primary": c.get("primary", False)}
            for c in result.get("items", [])
        ],
    }


@app.get("/auth/{account}")
def start_google_auth(account: str):
    _validate_account(account)
    flow = Flow.from_client_config(GOOGLE_CLIENT_CONFIG, scopes=GOOGLE_SCOPES)
    flow.redirect_uri = f"{BASE_URL}/callback"
    auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent", state=account)
    return RedirectResponse(auth_url)


@app.get("/callback")
def google_oauth_callback(code: str, state: str):
    _validate_account(state)
    flow = Flow.from_client_config(GOOGLE_CLIENT_CONFIG, scopes=GOOGLE_SCOPES, state=state)
    flow.redirect_uri = f"{BASE_URL}/callback"
    flow.fetch_token(code=code)
    _google_token_path(state).write_text(flow.credentials.to_json())
    return HTMLResponse(
        f"<h2>Cuenta Google <b>{state}</b> autorizada correctamente.</h2>"
        "<p>Ya puedes cerrar esta ventana.</p>"
    )


@app.get("/auth/microsoft/{account}")
def start_ms_auth(account: str):
    if not MS_CLIENT_ID:
        raise HTTPException(status_code=501, detail="Microsoft auth not configured (MS_CLIENT_ID missing)")
    _validate_account(account)
    ms_app, _ = _get_ms_app(account)
    auth_url = ms_app.get_authorization_request_url(
        MS_GRAPH_SCOPES,
        state=f"ms:{account}",
        redirect_uri=f"{BASE_URL}/callback/microsoft",
    )
    return RedirectResponse(auth_url)


@app.get("/callback/microsoft")
def ms_oauth_callback(code: str, state: str):
    if not state.startswith("ms:"):
        raise HTTPException(status_code=400, detail="Invalid state")
    account = state[3:]
    _validate_account(account)
    ms_app, cache = _get_ms_app(account)
    result = ms_app.acquire_token_by_authorization_code(
        code,
        scopes=MS_GRAPH_SCOPES,
        redirect_uri=f"{BASE_URL}/callback/microsoft",
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result.get("error_description", result["error"]))
    _save_ms_cache(account, cache)
    return HTMLResponse(
        f"<h2>Cuenta Microsoft <b>{account}</b> autorizada correctamente.</h2>"
        "<p>Ya puedes cerrar esta ventana.</p>"
    )


@app.get("/events")
def get_events(
    account: Optional[str] = Query(None),
    ms_account: Optional[str] = Query(None),
    days: int = Query(7, ge=1, le=30),
    _: None = Depends(_verify_key),
):
    if not account and not ms_account:
        raise HTTPException(status_code=400, detail="Provide at least one of: account, ms_account")

    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days)
    all_events = []

    if account:
        for acc in [a.strip() for a in account.split(",")]:
            all_events.extend(_get_google_events(_validate_account(acc), now, end))

    if ms_account:
        for acc in [a.strip() for a in ms_account.split(",")]:
            all_events.extend(_get_ms_events(_validate_account(acc), now, end))

    all_events.sort(key=lambda e: e["start"])
    return {"days": days, "count": len(all_events), "events": all_events}


@app.get("/today")
def get_today(
    account: Optional[str] = Query(None),
    ms_account: Optional[str] = Query(None),
    _: None = Depends(_verify_key),
):
    return get_events(account=account, ms_account=ms_account, days=1)


@app.get("/week")
def get_week(
    account: Optional[str] = Query(None),
    ms_account: Optional[str] = Query(None),
    _: None = Depends(_verify_key),
):
    return get_events(account=account, ms_account=ms_account, days=7)
