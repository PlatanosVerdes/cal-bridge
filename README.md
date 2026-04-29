# cal-bridge

A lightweight FastAPI service that bridges Google Calendar (and optionally Microsoft Outlook) to a simple JSON API. Designed to run on a Raspberry Pi homelab so that resource-constrained clients like an ESP32 can fetch calendar events with a plain HTTP GET — no OAuth complexity on the device.

---

## Why `CAL_API_KEY`?

The service is intentionally exposed on a local network (and optionally via Tailscale). Without an API key, any device on your WiFi could read your calendar data. `CAL_API_KEY` is a shared secret that every caller must include as `?key=<value>`. Generate one with:

```bash
openssl rand -hex 16
```

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `GOOGLE_CLIENT_ID` | Yes | OAuth2 client ID from Google Cloud Console |
| `GOOGLE_CLIENT_SECRET` | Yes | OAuth2 client secret from Google Cloud Console |
| `CAL_API_KEY` | Yes | Shared secret for API authentication |
| `CAL_BASE_URL` | Yes | Public base URL (e.g. `https://cal.example.com`) — used for OAuth redirect URIs |
| `MS_CLIENT_ID` | No | Azure app client ID (only needed for Microsoft/Outlook) |
| `MS_CLIENT_SECRET` | No | Azure app client secret (only needed for Microsoft/Outlook) |

---

## Google Calendar setup

1. Go to [Google Cloud Console](https://console.cloud.google.com) → create or select a project
2. **APIs & Services → Enable APIs** → enable **Google Calendar API**
3. **APIs & Services → OAuth consent screen**
   - User type: External
   - Fill in app name, support email
   - Add scope: `https://www.googleapis.com/auth/calendar.readonly`
   - Add your Google account as a **test user** (no need to publish the app)
4. **APIs & Services → Credentials → Create Credentials → OAuth client ID**
   - Application type: **Web application**
   - Authorized redirect URIs: `https://<your-domain>/callback`
5. Copy the client ID and secret to your `.env`

## Microsoft / Outlook setup (optional)

1. Go to [Azure Portal](https://portal.azure.com) → **App registrations → New registration**
   - Name: anything (e.g. `cal-bridge`)
   - Supported account types: **Accounts in any organizational directory and personal Microsoft accounts**
   - Redirect URI: Web → `https://<your-domain>/callback/microsoft`
2. Copy the **Application (client) ID** → `MS_CLIENT_ID`
3. **Certificates & secrets → New client secret** → copy the value → `MS_CLIENT_SECRET`
4. **API permissions → Add permission → Microsoft Graph → Delegated**
   - Add `Calendars.Read`

---

## Running with Docker Compose

```yaml
cal-bridge:
  image: ghcr.io/platanosverdes/cal-bridge:latest  # or build: .
  environment:
    GOOGLE_CLIENT_ID: ${GOOGLE_CLIENT_ID}
    GOOGLE_CLIENT_SECRET: ${GOOGLE_CLIENT_SECRET}
    CAL_API_KEY: ${CAL_API_KEY}
    CAL_BASE_URL: https://cal.example.com
  volumes:
    - ./appdata/cal-bridge:/data/tokens
  ports:
    - "8091:8000"
```

Tokens are stored in `/data/tokens` — mount a persistent volume so re-deploys don't invalidate sessions.

---

## Authorizing an account

Visit the auth URL in a browser (once per account):

```
# Google
https://cal.example.com/auth/personal

# Microsoft
https://cal.example.com/auth/microsoft/trabajo
```

After authorizing, a token is saved server-side. You never need to repeat this unless the token is revoked.

---

## API reference

All endpoints except `/health` and the OAuth callbacks require `?key=<CAL_API_KEY>`.

### `GET /health`
```json
{"status": "ok"}
```

### `GET /accounts?key=`
Lists all authorized accounts.
```json
{"google": ["personal", "family"], "microsoft": ["trabajo"]}
```

### `GET /calendars?account=personal&key=`
Lists all calendars for a Google account. Add `&provider=microsoft` for Outlook.

### `GET /events?key=`
Fetch events. Supports multiple accounts in one call.

| Param | Default | Description |
|---|---|---|
| `account` | — | Google account(s), comma-separated |
| `ms_account` | — | Microsoft account(s), comma-separated |
| `days` | `7` | Look-ahead window (1–30) |

At least one of `account` or `ms_account` is required.

```bash
# Single Google account
curl "https://cal.example.com/events?account=personal&days=7&key=SECRET"

# Merge Google + Microsoft
curl "https://cal.example.com/events?account=personal&ms_account=trabajo&days=7&key=SECRET"

# Two Google accounts merged
curl "https://cal.example.com/events?account=personal,family&key=SECRET"
```

Response:
```json
{
  "days": 7,
  "count": 3,
  "events": [
    {
      "id": "...",
      "title": "Team meeting",
      "start": "2025-05-01T10:00:00+02:00",
      "end": "2025-05-01T11:00:00+02:00",
      "all_day": false,
      "location": null,
      "calendar_id": "primary",
      "calendar_name": "Personal",
      "account": "personal",
      "provider": "google"
    }
  ]
}
```

### `GET /today?account=personal&key=`
Shorthand for `/events?days=1`. Accepts same `account` / `ms_account` params.

### `GET /week?account=personal&key=`
Shorthand for `/events?days=7`. Accepts same `account` / `ms_account` params.
