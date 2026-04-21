import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

import database
import calendar_sync

# ── App lifespan ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    # Seed with demo tasks if DB is fresh
    _seed_demo_tasks()
    # Background periodic sync every 30 min
    sync_task = asyncio.create_task(_periodic_sync())
    yield
    sync_task.cancel()


app = FastAPI(title="Family Dashboard", lifespan=lifespan)


def _seed_demo_tasks():
    existing = database.get_tasks()
    if existing:
        return
    demo = [
        {"id": str(uuid.uuid4()), "title": "Comprar material escolar de Noa",
         "member_id": "ale", "due_date": None, "priority": "medium", "notes": None},
        {"id": str(uuid.uuid4()), "title": "Revisar seguro del coche",
         "member_id": "miguel", "due_date": None, "priority": "high", "notes": None},
        {"id": str(uuid.uuid4()), "title": "Recoger a Oli del colegio",
         "member_id": "family", "due_date": (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d"),
         "priority": "high", "notes": None},
    ]
    for t in demo:
        database.create_task(t)


async def _periodic_sync():
    interval = int(os.getenv("SYNC_INTERVAL_MINUTES", "30")) * 60
    while True:
        try:
            await calendar_sync.sync_all()
        except Exception as exc:
            print(f"[sync] Error: {exc}")
        await asyncio.sleep(interval)


# ── Static frontend ───────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory="frontend"), name="static")


@app.get("/", response_class=HTMLResponse)
async def root():
    with open("frontend/index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


# ── Google OAuth ──────────────────────────────────────────────────────────────

def _redirect_uri(request: Request) -> str:
    base = os.getenv("APP_URL", str(request.base_url).rstrip("/"))
    return f"{base}/auth/google/callback"


@app.get("/auth/google")
async def auth_google(request: Request):
    if not os.getenv("GOOGLE_CLIENT_ID") or not os.getenv("GOOGLE_CLIENT_SECRET"):
        raise HTTPException(400, "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET not set in .env")
    url = calendar_sync.google_oauth_url(_redirect_uri(request))
    return RedirectResponse(url)


@app.get("/auth/google/callback")
async def auth_google_callback(request: Request, code: str | None = None, error: str | None = None):
    if error or not code:
        return RedirectResponse("/?error=google_auth_failed")
    try:
        calendar_sync.google_exchange_code(code, _redirect_uri(request))
        asyncio.create_task(calendar_sync.sync_all())
    except Exception as exc:
        print(f"[auth] Google exchange error: {exc}")
        return RedirectResponse("/?error=google_token_failed")
    return RedirectResponse("/?connected=1")


@app.get("/auth/google/disconnect/{account_id}")
async def auth_google_disconnect_account(account_id: str):
    database.delete_google_account(account_id)
    return RedirectResponse("/")


@app.get("/auth/google/disconnect")
async def auth_google_disconnect():
    for account in database.get_google_accounts():
        database.delete_google_account(account["id"])
    database.set_setting("google_token", "")
    database.set_setting("google_connected_at", "")
    return RedirectResponse("/")


# ── API: Status ───────────────────────────────────────────────────────────────

@app.get("/api/status")
async def api_status():
    google_accounts = database.get_google_accounts()
    apple_configured = bool(os.getenv("APPLE_ID") and os.getenv("APPLE_APP_PASSWORD"))
    return {
        "google_connected": bool(google_accounts),
        "apple_configured": apple_configured,
        "last_sync": database.get_setting("last_sync"),
        "members": [
            {"id": "ale",      "name": "Ale",        "color": "#7C3AED", "emoji": "👩", "adult": True, "image": "/static/assets/ale.png", "category": False},
            {"id": "miguel",   "name": "Miguel",     "color": "#2563EB", "emoji": "👨", "adult": True, "image": "/static/assets/miguel.png", "category": False},
            {"id": "noa",      "name": "Noa",        "color": "#059669", "emoji": "🧒", "adult": False, "image": "/static/assets/noa.png", "category": False},
            {"id": "oli",      "name": "Oli",        "color": "#D97706", "emoji": "👧", "adult": False, "image": "/static/assets/oli.png", "category": False},
            {"id": "family",   "name": "Familia",    "color": "#DB2777", "emoji": "🏠", "adult": False, "image": "/static/assets/family.png", "category": False},
            {"id": "birthday", "name": "Cumpleaños", "color": "#B45309", "emoji": "🎂", "adult": False, "image": "/static/assets/birthday.png", "category": True},
        ],
    }


# ── API: Calendar sync ────────────────────────────────────────────────────────

@app.post("/api/sync")
async def api_sync(background_tasks: BackgroundTasks):
    background_tasks.add_task(calendar_sync.sync_all)
    return {"status": "sync_started"}


# ── API: Events ───────────────────────────────────────────────────────────────

def _week_bounds(week_param: str | None) -> tuple[str, str]:
    """Given any date string (YYYY-MM-DD), return ISO Mon and next-Mon for that week."""
    try:
        ref = datetime.fromisoformat(week_param) if week_param else datetime.now()
    except ValueError:
        ref = datetime.now()
    day = ref.weekday()
    monday = ref - timedelta(days=day)
    monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    next_monday = monday + timedelta(days=7)
    return monday.isoformat(), next_monday.isoformat()


@app.get("/api/events")
async def api_get_events(week: str | None = None, member: str | None = None):
    week_start, week_end = _week_bounds(week)
    events = database.get_events(week_start, week_end, member)
    return {"events": events, "week_start": week_start[:10], "week_end": week_end[:10]}


class EventCreate(BaseModel):
    title: str
    start_dt: str
    end_dt: str | None = None
    all_day: bool = False
    member_id: str
    description: str | None = None
    location: str | None = None


@app.post("/api/events", status_code=201)
async def api_create_event(body: EventCreate):
    event = {
        "id": f"manual-{uuid.uuid4()}",
        "title": body.title,
        "start_dt": body.start_dt,
        "end_dt": body.end_dt or body.start_dt,
        "all_day": int(body.all_day),
        "member_id": body.member_id,
        "description": body.description,
        "location": body.location,
    }
    database.create_manual_event(event)
    return {"event": event}


@app.delete("/api/events/{event_id}")
async def api_delete_event(event_id: str):
    database.delete_event(event_id)
    return {"ok": True}


class EventAssign(BaseModel):
    member_ids: list[str]


@app.put("/api/events/{event_id}/assignments")
async def api_assign_event(event_id: str, body: EventAssign):
    database.assign_event_members(event_id, body.member_ids)
    return {"ok": True}


# ── API: Tasks ────────────────────────────────────────────────────────────────

@app.get("/api/tasks")
async def api_get_tasks(member: str | None = None):
    tasks = database.get_tasks(member)
    return {"tasks": tasks}


class TaskCreate(BaseModel):
    title: str
    member_id: str = "family"
    priority: str = "medium"
    due_date: str | None = None
    notes: str | None = None


@app.post("/api/tasks", status_code=201)
async def api_create_task(body: TaskCreate):
    task = {
        "id": str(uuid.uuid4()),
        "title": body.title,
        "member_id": body.member_id,
        "priority": body.priority,
        "due_date": body.due_date,
        "notes": body.notes,
    }
    database.create_task(task)
    return {"task": {**task, "completed": 0, "created_at": datetime.now().isoformat()}}


class TaskUpdate(BaseModel):
    title: str | None = None
    member_id: str | None = None
    completed: bool | None = None
    due_date: str | None = None
    priority: str | None = None
    notes: str | None = None


@app.patch("/api/tasks/{task_id}")
async def api_update_task(task_id: str, body: TaskUpdate):
    updates = body.model_dump(exclude_none=True)
    if "completed" in updates:
        updates["completed"] = int(updates["completed"])
        updates["completed_at"] = datetime.now().isoformat() if updates["completed"] else None
    database.update_task(task_id, updates)
    return {"ok": True}


@app.delete("/api/tasks/{task_id}")
async def api_delete_task(task_id: str):
    database.delete_task(task_id)
    return {"ok": True}


# ── API: Google Accounts ─────────────────────────────────────────────────────

@app.get("/api/google/accounts")
async def api_list_google_accounts():
    accounts = database.get_google_accounts()
    return {"accounts": [{"id": a["id"], "email": a["email"], "connected_at": a["connected_at"]} for a in accounts]}


# ── API: Google Calendars (discovery + DB) ───────────────────────────────────

@app.get("/api/google/calendars")
async def api_list_google_calendars(account_id: str | None = None):
    from googleapiclient.discovery import build
    if not account_id:
        accounts = database.get_google_accounts()
        if not accounts:
            raise HTTPException(401, "Google no conectado")
        account_id = accounts[0]["id"]
    creds = calendar_sync.build_google_credentials(account_id)
    if not creds:
        raise HTTPException(401, "Cuenta no encontrada")
    try:
        creds = calendar_sync.refresh_google_token(creds, account_id)
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        result = service.calendarList().list().execute()
        calendars = [
            {"id": c["id"], "name": c.get("summary", c["id"])}
            for c in result.get("items", [])
        ]
        return {"calendars": calendars}
    except Exception as exc:
        raise HTTPException(500, str(exc))


class GoogleCalendarSave(BaseModel):
    calendar_id: str
    name: str
    member_id: str
    account_id: str | None = None


@app.get("/api/google/saved-calendars")
async def api_get_saved_google_calendars(account_id: str | None = None):
    return {"calendars": database.get_google_calendars(account_id)}


@app.post("/api/google/saved-calendars", status_code=201)
async def api_save_google_calendar(body: GoogleCalendarSave, background_tasks: BackgroundTasks):
    cal = {
        "id": str(uuid.uuid4()),
        "calendar_id": body.calendar_id,
        "name": body.name,
        "member_id": body.member_id,
        "account_id": body.account_id,
    }
    database.create_google_calendar(cal)
    background_tasks.add_task(calendar_sync.sync_all)
    return {"calendar": cal}


@app.delete("/api/google/saved-calendars/{cal_id}")
async def api_delete_saved_google_calendar(cal_id: str):
    database.delete_google_calendar(cal_id)
    return {"ok": True}


# ── API: ICS Calendars ────────────────────────────────────────────────────────

class IcsCalendarCreate(BaseModel):
    name: str
    url: str
    member_id: str


@app.get("/api/ics-calendars")
async def api_get_ics_calendars():
    return {"calendars": database.get_ics_calendars()}


@app.post("/api/ics-calendars", status_code=201)
async def api_create_ics_calendar(body: IcsCalendarCreate, background_tasks: BackgroundTasks):
    cal = {"id": str(uuid.uuid4()), "name": body.name, "url": body.url, "member_id": body.member_id}
    database.create_ics_calendar(cal)
    background_tasks.add_task(calendar_sync.sync_all)
    return {"calendar": cal}


@app.delete("/api/ics-calendars/{cal_id}")
async def api_delete_ics_calendar(cal_id: str):
    database.delete_ics_calendar(cal_id)
    return {"ok": True}
