import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH", "./data/family.db")


@contextmanager
def get_conn():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                id          TEXT PRIMARY KEY,
                title       TEXT NOT NULL,
                start_dt    TEXT NOT NULL,
                end_dt      TEXT,
                all_day     INTEGER NOT NULL DEFAULT 0,
                member_id   TEXT NOT NULL,
                source      TEXT NOT NULL DEFAULT 'manual',
                description TEXT,
                location    TEXT,
                external_id TEXT UNIQUE
            );
            CREATE INDEX IF NOT EXISTS idx_events_start  ON events(start_dt);
            CREATE INDEX IF NOT EXISTS idx_events_member ON events(member_id);

            CREATE TABLE IF NOT EXISTS tasks (
                id           TEXT PRIMARY KEY,
                title        TEXT NOT NULL,
                member_id    TEXT NOT NULL DEFAULT 'family',
                completed    INTEGER NOT NULL DEFAULT 0,
                due_date     TEXT,
                priority     TEXT NOT NULL DEFAULT 'medium',
                notes        TEXT,
                created_at   TEXT NOT NULL DEFAULT (datetime('now')),
                completed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_member ON tasks(member_id);

            CREATE TABLE IF NOT EXISTS settings (
                key        TEXT PRIMARY KEY,
                value      TEXT,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS ics_calendars (
                id         TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                url        TEXT NOT NULL,
                member_id  TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)


# ── Events ────────────────────────────────────────────────────────────────────

def get_events(week_start: str, week_end: str, member_id: str | None = None) -> list[dict]:
    with get_conn() as conn:
        if member_id:
            rows = conn.execute(
                "SELECT * FROM events WHERE start_dt >= ? AND start_dt < ? AND member_id = ? ORDER BY start_dt",
                (week_start, week_end, member_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM events WHERE start_dt >= ? AND start_dt < ? ORDER BY start_dt",
                (week_start, week_end),
            ).fetchall()
        return [dict(r) for r in rows]


def upsert_event(e: dict):
    """Insert or update a synced event (Google / Apple)."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO events (id, title, start_dt, end_dt, all_day, member_id, source, description, location, external_id)
            VALUES (:id, :title, :start_dt, :end_dt, :all_day, :member_id, :source, :description, :location, :external_id)
            ON CONFLICT(id) DO UPDATE SET
                title       = excluded.title,
                start_dt    = excluded.start_dt,
                end_dt      = excluded.end_dt,
                all_day     = excluded.all_day,
                description = excluded.description,
                location    = excluded.location
            """,
            e,
        )


def create_manual_event(e: dict):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO events (id, title, start_dt, end_dt, all_day, member_id, source, description, location)
            VALUES (:id, :title, :start_dt, :end_dt, :all_day, :member_id, 'manual', :description, :location)
            """,
            e,
        )


def delete_event(event_id: str):
    """Only manual events can be deleted via the UI."""
    with get_conn() as conn:
        conn.execute("DELETE FROM events WHERE id = ? AND source = 'manual'", (event_id,))


def delete_synced_events_for_member(member_id: str, source: str, week_start: str, week_end: str):
    """Remove synced events for a member/source within a date range before re-inserting."""
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM events WHERE member_id = ? AND source = ? AND start_dt >= ? AND start_dt < ?",
            (member_id, source, week_start, week_end),
        )


# ── Tasks ─────────────────────────────────────────────────────────────────────

def get_tasks(member_id: str | None = None) -> list[dict]:
    with get_conn() as conn:
        if member_id:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE member_id = ? ORDER BY completed ASC, created_at DESC",
                (member_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY completed ASC, created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]


def create_task(t: dict):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO tasks (id, title, member_id, completed, due_date, priority, notes)
            VALUES (:id, :title, :member_id, 0, :due_date, :priority, :notes)
            """,
            t,
        )


def update_task(task_id: str, updates: dict):
    allowed = {"title", "member_id", "completed", "due_date", "priority", "notes", "completed_at"}
    filtered = {k: v for k, v in updates.items() if k in allowed}
    if not filtered:
        return
    set_clause = ", ".join(f"{k} = :{k}" for k in filtered)
    filtered["id"] = task_id
    with get_conn() as conn:
        conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = :id", filtered)


def delete_task(task_id: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))


# ── ICS Calendars ─────────────────────────────────────────────────────────────

def get_ics_calendars() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM ics_calendars ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]


def create_ics_calendar(c: dict):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO ics_calendars (id, name, url, member_id) VALUES (:id, :name, :url, :member_id)",
            c,
        )


def delete_ics_calendar(cal_id: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM ics_calendars WHERE id = ?", (cal_id,))


# ── Settings ──────────────────────────────────────────────────────────────────

def get_setting(key: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def set_setting(key: str, value: str):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value),
        )
