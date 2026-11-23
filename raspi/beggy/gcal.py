"""Google Calendar for Beggy (Gemini tools + upcoming-deadline feed for the backend).

One-time setup (see README "Google Calendar"):
  1. Google Cloud console, same project as the Gemini key: enable "Google Calendar API"
  2. OAuth consent screen: External, add your Google account as a test user
     (then "Publish app" -> In production, otherwise the login expires every 7 days)
  3. Credentials -> Create OAuth client ID -> Desktop app -> download JSON as raspi/secrets/google_oauth.json
  4. On the Pi desktop: python -m beggy.gcal login   (opens a browser, you allow access once)
     -> raspi/secrets/google_token.json (gitignored), refreshed automatically after that
"""
from __future__ import annotations

import datetime as dt
import logging
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from beggy.settings import ROOT

log = logging.getLogger("gcal")

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
SECRETS = ROOT / "secrets"
CLIENT_FILE = SECRETS / "google_oauth.json"
TOKEN_FILE = SECRETS / "google_token.json"
TZ = ZoneInfo("Asia/Bangkok")


class NotConnected(Exception):
    pass


def _credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    if not TOKEN_FILE.exists():
        raise NotConnected("Google Calendar is not connected yet (run: python -m beggy.gcal login)")
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json())
        else:
            raise NotConnected("Google Calendar login expired (run: python -m beggy.gcal login)")
    return creds


def simplify(ev: dict) -> dict:
    """Keep only what Gemini / the dashboard needs from a Calendar API event."""
    start, end = ev.get("start", {}), ev.get("end", {})
    return {
        "id": ev.get("id"),
        "title": ev.get("summary", "(no title)"),
        "start": start.get("dateTime") or start.get("date"),
        "end": end.get("dateTime") or end.get("date"),
        "all_day": "date" in start and "dateTime" not in start,
        "location": ev.get("location"),
    }


class Calendar:
    def __init__(self, service=None, calendar_id: str = "primary"):
        self._service = service      # injected in tests
        self.calendar_id = calendar_id

    @property
    def service(self):
        if self._service is None:
            from googleapiclient.discovery import build
            self._service = build("calendar", "v3", credentials=_credentials(), cache_discovery=False)
        return self._service

    def list_events(self, days_ahead: float = 1, now: dt.datetime | None = None, limit: int = 20) -> list[dict]:
        now = now or dt.datetime.now(TZ)
        res = self.service.events().list(
            calendarId=self.calendar_id, timeMin=now.isoformat(),
            timeMax=(now + dt.timedelta(days=days_ahead)).isoformat(),
            singleEvents=True, orderBy="startTime", maxResults=limit,
        ).execute()
        return [simplify(e) for e in res.get("items", [])]

    def create_event(self, title: str, start_iso: str, end_iso: str) -> dict:
        start, end = dt.datetime.fromisoformat(start_iso), dt.datetime.fromisoformat(end_iso)
        if start.tzinfo is None:
            start, end = start.replace(tzinfo=TZ), end.replace(tzinfo=TZ)
        if end <= start:
            raise ValueError("end must be after start")
        body = {"summary": title,
                "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Bangkok"},
                "end": {"dateTime": end.isoformat(), "timeZone": "Asia/Bangkok"}}
        return simplify(self.service.events().insert(calendarId=self.calendar_id, body=body).execute())

    def delete_event(self, event_id: str) -> dict:
        self.service.events().delete(calendarId=self.calendar_id, eventId=event_id).execute()
        return {"deleted": event_id}


def login():
    """Interactive one-time login (needs a browser on this machine)."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    if not CLIENT_FILE.exists():
        sys.exit(f"put the OAuth client JSON at {CLIENT_FILE} first (see the top of beggy/gcal.py)")
    SECRETS.mkdir(exist_ok=True)
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    TOKEN_FILE.write_text(creds.to_json())
    TOKEN_FILE.chmod(0o600)
    print(f"saved {TOKEN_FILE}")
    for e in Calendar().list_events(days_ahead=7):
        print(" ", e["start"], e["title"])


if __name__ == "__main__":
    if sys.argv[1:] == ["login"]:
        login()
    else:
        print("usage: python -m beggy.gcal login")
