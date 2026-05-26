"""
Calendar sync — Google Calendar (OAuth2) + Apple iCloud (CalDAV).

Google flow:
  1. User visits /auth/google  → redirects to Google consent page
  2. Google redirects back to /auth/google/callback?code=...
  3. Tokens stored in DB settings table
  4. Subsequent syncs use stored refresh token

Apple flow:
  - Credentials (Apple ID + app-specific password) stored in .env
  - caldav library connects directly to caldav.icloud.com
  - No redirect flow needed
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone, timedelta

import database

# Allow Google to return extra scopes (e.g. openid) without raising an error
os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

logger = logging.getLogger(__name__)

# ── Helpers ───────────────────────────────────────────────────────────────────

MEMBERS = ["ale", "miguel", "noa", "oli", "family"]
ALL_MEMBERS = MEMBERS + ["birthday"]

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
]

GOOGLE_CAL_IDS: dict[str, str] = {
    m: os.getenv(f"GOOGLE_CALENDAR_{m.upper()}", "")
    for m in ALL_MEMBERS
}

APPLE_CALENDAR_NAMES: dict[str, list[str]] = {
    m: [n.strip() for n in os.getenv(f"APPLE_CALENDAR_{m.upper()}", "").split(",") if n.strip()]
    for m in MEMBERS
}

ICS_URLS: dict[str, list[str]] = {
    m: [u.strip() for u in os.getenv(f"ICS_URL_{m.upper()}", "").split(",") if u.strip()]
    for m in MEMBERS
}


def week_range(ref: datetime) -> tuple[str, str]:
    """Returns (monday_iso, next_monday_iso) for the week containing ref."""
    day = ref.weekday()  # 0=Mon
    monday = ref - timedelta(days=day)
    monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    next_monday = monday + timedelta(days=7)
    return monday.isoformat(), next_monday.isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# ── Google Calendar ────────────────────────────────────────────────────────────

def build_google_credentials(account_id: str):
    """Load credentials for a given google_accounts row. Returns None if not found."""
    from google.oauth2.credentials import Credentials

    accounts = database.get_google_accounts()
    account = next((a for a in accounts if a["id"] == account_id), None)
    if not account:
        return None

    info = json.loads(account["token_json"])
    # Don't pass scopes here: google-auth includes them in the refresh request,
    # which causes invalid_scope when Google originally granted extra scopes
    # (openid, email, profile). The refresh token already encodes the real scopes.
    creds = Credentials(
        token=info.get("token"),
        refresh_token=info.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.getenv("GOOGLE_CLIENT_ID"),
        client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    )
    return creds


def refresh_google_token(creds, account_id: str | None = None):
    """Refresh token if expired and persist the updated token."""
    from google.auth.transport.requests import Request

    if (creds.expired or creds.expiry is None) and creds.refresh_token:
        creds.refresh(Request())
        token_json = json.dumps({"token": creds.token, "refresh_token": creds.refresh_token})
        if account_id:
            accounts = database.get_google_accounts()
            account = next((a for a in accounts if a["id"] == account_id), None)
            if account:
                database.upsert_google_account(account_id, account["email"], token_json)
        else:
            database.set_setting("google_token", token_json)
    return creds


def _client_config():
    return {
        "web": {
            "client_id": os.getenv("GOOGLE_CLIENT_ID"),
            "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


def google_oauth_url(redirect_uri: str) -> str:
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(
        _client_config(),
        scopes=GOOGLE_SCOPES,
        redirect_uri=redirect_uri,
    )
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="select_account consent",
        include_granted_scopes="true",
    )
    return auth_url


def google_exchange_code(code: str, redirect_uri: str) -> str:
    """Exchange auth code for tokens, fetch user email, persist account. Returns account_id."""
    import httpx
    import uuid as _uuid
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(
        _client_config(),
        scopes=GOOGLE_SCOPES,
        redirect_uri=redirect_uri,
    )
    flow.fetch_token(code=code)
    creds = flow.credentials

    try:
        resp = httpx.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {creds.token}"},
            timeout=10,
        )
        email = resp.json().get("email", "Google")
    except Exception:
        email = "Google"

    account_id = str(_uuid.uuid4())
    token_json = json.dumps({"token": creds.token, "refresh_token": creds.refresh_token})
    database.upsert_google_account(account_id, email, token_json)
    return account_id


def sync_google_member(creds, member_id: str, cal_id: str, week_start: str, week_end: str):
    from googleapiclient.discovery import build

    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    try:
        result = service.events().list(
            calendarId=cal_id,
            timeMin=week_start[:10] + "T00:00:00Z",
            timeMax=week_end[:10] + "T00:00:00Z",
            singleEvents=True,
            orderBy="startTime",
            maxResults=250,
        ).execute()
    except Exception as exc:
        logger.warning("Google Calendar error for %s: %s", member_id, exc)
        return 0

    items = result.get("items", [])
    database.delete_synced_events_for_member(member_id, "google", week_start, week_end)

    count = 0
    for item in items:
        if item.get("status") == "cancelled":
            continue
        all_day = "date" in item.get("start", {})
        start = item["start"].get("dateTime") or item["start"].get("date", "")
        end = item["end"].get("dateTime") or item["end"].get("date", "")
        # Normalise to ISO without timezone suffix for consistent DB storage
        start = start[:19] if len(start) > 10 else start
        end = end[:19] if len(end) > 10 else end
        recurring_id = item.get("recurringEventId")
        database.upsert_event(
            {
                "id": f"gcal-{item['id']}",
                "title": item.get("summary", "(sin título)"),
                "start_dt": start,
                "end_dt": end,
                "all_day": int(all_day),
                "member_id": member_id,
                "source": "google",
                "description": item.get("description"),
                "location": item.get("location"),
                "external_id": item["id"],
                "series_id": f"gcal-series-{recurring_id}" if recurring_id else None,
            }
        )
        count += 1

    return count


def _build_valid_google_creds() -> list:
    """Refresh tokens once per sync cycle. Returns [(account, creds), ...] for valid accounts only."""
    accounts = database.get_google_accounts()
    if not accounts:
        return []
    valid = []
    for account in accounts:
        creds = build_google_credentials(account["id"])
        if not creds:
            continue
        try:
            creds = refresh_google_token(creds, account["id"])
            valid.append((account, creds))
        except Exception as exc:
            if "invalid_grant" in str(exc):
                logger.warning("Google token revoked for %s — reconnect in settings", account.get("email", account["id"]))
            else:
                logger.error("Token refresh failed for account %s: %s", account["id"], exc)
    return valid


def sync_google(week_start: str, week_end: str, valid_creds: list | None = None) -> dict:
    if valid_creds is None:
        # Legacy .env-based token fallback (no accounts in DB)
        legacy_token = database.get_setting("google_token")
        if not legacy_token:
            return {"status": "not_connected"}
        from google.oauth2.credentials import Credentials
        info = json.loads(legacy_token)
        creds = Credentials(
            token=info.get("token"),
            refresh_token=info.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.getenv("GOOGLE_CLIENT_ID"),
            client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
        )
        try:
            creds = refresh_google_token(creds)
        except Exception as exc:
            logger.error("Token refresh failed (legacy): %s", exc)
            return {"status": "token_error", "error": str(exc)}
        total = 0
        for member_id, cal_id in GOOGLE_CAL_IDS.items():
            if not cal_id:
                continue
            total += sync_google_member(creds, member_id, cal_id, week_start, week_end)
        return {"status": "ok", "events_synced": total}

    total = 0
    for account, creds in valid_creds:
        for cal in database.get_google_calendars(account["id"]):
            total += sync_google_member(creds, cal["member_id"], cal["calendar_id"], week_start, week_end)
    return {"status": "ok", "events_synced": total}


# ── Apple iCloud CalDAV ────────────────────────────────────────────────────────

def sync_apple(week_start: str, week_end: str) -> dict:
    apple_id = os.getenv("APPLE_ID")
    apple_password = os.getenv("APPLE_APP_PASSWORD")

    if not apple_id or not apple_password:
        return {"status": "not_configured"}

    try:
        import caldav
        from caldav.lib.error import AuthorizationError
    except ImportError:
        return {"status": "caldav_not_installed"}

    try:
        client = caldav.DAVClient(
            url="https://caldav.icloud.com",
            username=apple_id,
            password=apple_password,
        )
        principal = client.principal()
        calendars = principal.calendars()
    except Exception as exc:
        logger.error("Apple CalDAV connection error: %s", exc)
        return {"status": "connection_error", "error": str(exc)}

    # Build a name→calendar map
    cal_map = {cal.name: cal for cal in calendars if cal.name}

    start_dt = datetime.fromisoformat(week_start).replace(tzinfo=timezone.utc)
    end_dt = datetime.fromisoformat(week_end).replace(tzinfo=timezone.utc)

    total = 0
    for member_id, cal_names in APPLE_CALENDAR_NAMES.items():
        for cal_name in cal_names:
            cal = cal_map.get(cal_name)
            if not cal:
                logger.debug("Apple calendar '%s' not found for member %s", cal_name, member_id)
                continue

            try:
                components = cal.search(
                    start=start_dt,
                    end=end_dt,
                    event=True,
                    expand=True,
                )
            except Exception as exc:
                logger.warning("Apple CalDAV search error for %s/%s: %s", member_id, cal_name, exc)
                continue

            database.delete_synced_events_for_member(member_id, "apple", week_start, week_end)

            for comp in components:
                try:
                    vevent = comp.vobject_instance.vevent
                    uid = str(vevent.uid.value) if hasattr(vevent, "uid") else _new_id()
                    summary = str(vevent.summary.value) if hasattr(vevent, "summary") else "(sin título)"

                    dtstart = vevent.dtstart.value
                    dtend = getattr(vevent, "dtend", None)
                    dtend = dtend.value if dtend else dtstart

                    all_day = not isinstance(dtstart, datetime)
                    start_str = dtstart.isoformat()[:10] if all_day else dtstart.isoformat()[:19]
                    end_str = dtend.isoformat()[:10] if all_day else dtend.isoformat()[:19]

                    location = str(vevent.location.value) if hasattr(vevent, "location") else None
                    description = str(vevent.description.value) if hasattr(vevent, "description") else None

                    has_rrule = hasattr(vevent, "rrule")
                    occurrence_key = f"{uid}_{start_str[:10]}" if has_rrule else uid
                    database.upsert_event(
                        {
                            "id": f"apple-{occurrence_key}",
                            "title": summary,
                            "start_dt": start_str,
                            "end_dt": end_str,
                            "all_day": int(all_day),
                            "member_id": member_id,
                            "source": "apple",
                            "description": description,
                            "location": location,
                            "external_id": occurrence_key,
                            "series_id": f"apple-series-{uid}" if has_rrule else None,
                        }
                    )
                    total += 1
                except Exception as exc:
                    logger.debug("Skipping malformed event: %s", exc)

    return {"status": "ok", "events_synced": total}


# ── ICS / webcal URLs ─────────────────────────────────────────────────────────

def sync_ics(week_start: str, week_end: str) -> dict:
    try:
        from icalendar import Calendar as ICalendar
        import httpx
    except ImportError:
        return {"status": "icalendar_not_installed"}

    try:
        import recurring_ical_events
        _has_recur = True
    except ImportError:
        _has_recur = False
        logger.warning("recurring-ical-events not installed — recurring ICS events won't expand")

    feeds = database.get_ics_calendars()
    if not feeds:
        return {"status": "no_calendars"}

    start_dt = datetime.fromisoformat(week_start)
    end_dt = datetime.fromisoformat(week_end)

    total = 0
    for feed in feeds:
        member_id = feed["member_id"]
        url = feed["url"]
        fetch_url = url.replace("webcal://", "https://").replace("webcal:", "https:")
        try:
            response = httpx.get(fetch_url, follow_redirects=True, timeout=30)
            response.raise_for_status()
            cal = ICalendar.from_ical(response.content)
        except Exception as exc:
            logger.warning("ICS error for %s (%s): %s", member_id, url, exc)
            continue

        database.delete_synced_events_for_member(member_id, "ics", week_start, week_end)

        # Expand recurring events if library is available, otherwise fall back to raw walk
        if _has_recur:
            try:
                components = recurring_ical_events.of(cal).between(start_dt, end_dt)
            except Exception as exc:
                logger.warning("ICS expand error for %s: %s", member_id, exc)
                components = [c for c in cal.walk() if c.name == "VEVENT"]
        else:
            components = [c for c in cal.walk() if c.name == "VEVENT"]

        for component in components:
            if component.name != "VEVENT":
                continue
            try:
                dtstart = component.get("DTSTART").dt
                dtend_prop = component.get("DTEND")
                dtend = dtend_prop.dt if dtend_prop else dtstart

                all_day = not isinstance(dtstart, datetime)
                if all_day:
                    ev_start = datetime(dtstart.year, dtstart.month, dtstart.day)
                    ev_end = datetime(dtend.year, dtend.month, dtend.day)
                else:
                    ev_start = dtstart.replace(tzinfo=None) if dtstart.tzinfo else dtstart
                    ev_end = dtend.replace(tzinfo=None) if dtend.tzinfo else dtend

                # Without expansion, filter manually
                if not _has_recur and (ev_end < start_dt or ev_start >= end_dt):
                    continue

                uid = str(component.get("UID") or _new_id())
                summary = str(component.get("SUMMARY") or "(sin título)")
                location = str(component.get("LOCATION") or "") or None
                description = str(component.get("DESCRIPTION") or "") or None
                start_str = ev_start.isoformat()[:10] if all_day else ev_start.isoformat()[:19]
                end_str = ev_end.isoformat()[:10] if all_day else ev_end.isoformat()[:19]

                # For recurring events, each occurrence needs a unique id/external_id
                # We use uid + start date so the same occurrence is stable across syncs
                occurrence_key = f"{uid}_{start_str[:10]}"

                has_rrule = bool(component.get("RRULE"))
                database.upsert_event({
                    "id": f"ics-{occurrence_key}",
                    "title": summary,
                    "start_dt": start_str,
                    "end_dt": end_str,
                    "all_day": int(all_day),
                    "member_id": member_id,
                    "source": "ics",
                    "description": description,
                    "location": location,
                    "external_id": occurrence_key,
                    "series_id": f"ics-series-{uid}" if has_rrule else None,
                })
                total += 1
            except Exception as exc:
                logger.debug("Skipping malformed ICS event: %s", exc)

    return {"status": "ok", "events_synced": total}


# ── Main sync entry point ─────────────────────────────────────────────────────

async def sync_all(weeks_ahead: int = 3) -> dict:
    """
    Sync Google Calendar and Apple Calendar for the current week
    plus `weeks_ahead` additional weeks.
    """
    now = datetime.now()
    results = []

    # Refresh tokens once — skip invalid accounts for the whole cycle
    accounts = database.get_google_accounts()
    valid_google_creds = _build_valid_google_creds() if accounts else None

    for offset in range(weeks_ahead):
        ref = now + timedelta(weeks=offset)
        week_start, week_end = week_range(ref)
        g = sync_google(week_start, week_end, valid_google_creds)
        a = sync_apple(week_start, week_end)
        i = sync_ics(week_start, week_end)
        results.append({"week": week_start[:10], "google": g, "apple": a, "ics": i})

    database.set_setting("last_sync", datetime.utcnow().isoformat() + "Z")
    return {"synced_weeks": results}
