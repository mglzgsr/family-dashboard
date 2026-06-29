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

            CREATE TABLE IF NOT EXISTS event_assignments (
                event_id  TEXT NOT NULL,
                member_id TEXT NOT NULL,
                PRIMARY KEY (event_id, member_id)
            );

            CREATE TABLE IF NOT EXISTS hidden_events (
                event_id TEXT PRIMARY KEY
            );

            CREATE TABLE IF NOT EXISTS google_accounts (
                id           TEXT PRIMARY KEY,
                email        TEXT NOT NULL DEFAULT 'Google',
                token_json   TEXT NOT NULL,
                connected_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS google_calendars (
                id          TEXT PRIMARY KEY,
                calendar_id TEXT NOT NULL,
                name        TEXT NOT NULL,
                member_id   TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS ics_calendars (
                id         TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                url        TEXT NOT NULL,
                member_id  TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)
        try:
            conn.execute("ALTER TABLE google_calendars ADD COLUMN account_id TEXT")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE events ADD COLUMN series_id TEXT")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE events ADD COLUMN manually_edited INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE tasks ADD COLUMN series_id TEXT")
        except Exception:
            pass

        legacy_token = conn.execute(
            "SELECT value FROM settings WHERE key = 'google_token'"
        ).fetchone()
        has_accounts = conn.execute("SELECT COUNT(*) FROM google_accounts").fetchone()[0]
        if legacy_token and legacy_token["value"] and not has_accounts:
            import uuid as _uuid
            new_id = str(_uuid.uuid4())
            conn.execute(
                "INSERT INTO google_accounts (id, email, token_json) VALUES (?, ?, ?)",
                (new_id, "Google (migrado)", legacy_token["value"]),
            )


# ── Events ────────────────────────────────────────────────────────────────────

def get_events(week_start: str, week_end: str, member_id: str | None = None) -> list[dict]:
    with get_conn() as conn:
        if member_id:
            rows = conn.execute("""
                SELECT e.*, GROUP_CONCAT(ea.member_id) as assigned_members
                FROM events e
                LEFT JOIN event_assignments ea ON e.id = ea.event_id
                WHERE DATE(e.start_dt) >= DATE(?) AND DATE(e.start_dt) < DATE(?)
                AND e.id NOT IN (SELECT event_id FROM hidden_events)
                AND (
                    EXISTS (SELECT 1 FROM event_assignments WHERE event_id = e.id AND member_id = ?)
                    OR (e.member_id = ? AND NOT EXISTS (SELECT 1 FROM event_assignments WHERE event_id = e.id))
                )
                GROUP BY e.id
                ORDER BY e.start_dt
            """, (week_start, week_end, member_id, member_id)).fetchall()
        else:
            rows = conn.execute("""
                SELECT e.*, GROUP_CONCAT(ea.member_id) as assigned_members
                FROM events e
                LEFT JOIN event_assignments ea ON e.id = ea.event_id
                WHERE DATE(e.start_dt) >= DATE(?) AND DATE(e.start_dt) < DATE(?)
                AND e.id NOT IN (SELECT event_id FROM hidden_events)
                GROUP BY e.id
                ORDER BY e.start_dt
            """, (week_start, week_end)).fetchall()
        return [dict(r) for r in rows]


def upsert_event(e: dict):
    """Insert or update a synced event (Google / Apple)."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO events (id, title, start_dt, end_dt, all_day, member_id, source, description, location, external_id, series_id)
            VALUES (:id, :title, :start_dt, :end_dt, :all_day, :member_id, :source, :description, :location, :external_id, :series_id)
            ON CONFLICT(id) DO UPDATE SET
                title       = excluded.title,
                start_dt    = excluded.start_dt,
                end_dt      = excluded.end_dt,
                all_day     = excluded.all_day,
                member_id   = excluded.member_id,
                description = excluded.description,
                location    = excluded.location,
                series_id   = excluded.series_id
            WHERE events.manually_edited = 0
            """,
            {**e, "series_id": e.get("series_id")},
        )


def create_manual_event(e: dict):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO events (id, title, start_dt, end_dt, all_day, member_id, source, description, location, series_id)
            VALUES (:id, :title, :start_dt, :end_dt, :all_day, :member_id, 'manual', :description, :location, :series_id)
            """,
            {**e, "series_id": e.get("series_id")},
        )


def reset_data():
    with get_conn() as conn:
        conn.executescript("""
            DELETE FROM events;
            DELETE FROM tasks;
            DELETE FROM event_assignments;
            DELETE FROM hidden_events;
            DELETE FROM settings WHERE key = 'last_sync';
        """)


def delete_series(series_id: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM events WHERE series_id = ?", (series_id,))


def get_series_event_ids(series_id: str) -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM events WHERE series_id = ?", (series_id,)
        ).fetchall()
        return [r["id"] for r in rows]


def assign_series_members(series_id: str, member_ids: list[str]):
    ids = get_series_event_ids(series_id)
    with get_conn() as conn:
        for event_id in ids:
            conn.execute("DELETE FROM event_assignments WHERE event_id = ?", (event_id,))
            for mid in member_ids:
                conn.execute(
                    "INSERT INTO event_assignments (event_id, member_id) VALUES (?, ?)",
                    (event_id, mid),
                )


def update_series(series_id: str, updates: dict):
    allowed = {"title", "location", "description"}
    filtered = {k: v for k, v in updates.items() if k in allowed}
    if not filtered:
        return
    set_clause = ", ".join(f"{k} = :{k}" for k in filtered)
    filtered["series_id"] = series_id
    with get_conn() as conn:
        conn.execute(
            f"UPDATE events SET {set_clause} WHERE series_id = :series_id",
            filtered,
        )


def assign_event_members(event_id: str, member_ids: list[str]):
    with get_conn() as conn:
        conn.execute("DELETE FROM event_assignments WHERE event_id = ?", (event_id,))
        for mid in member_ids:
            conn.execute(
                "INSERT INTO event_assignments (event_id, member_id) VALUES (?, ?)",
                (event_id, mid),
            )


def update_event(event_id: str, updates: dict):
    allowed = {"title", "start_dt", "end_dt", "all_day", "location", "description", "series_id"}
    filtered = {k: v for k, v in updates.items() if k in allowed}
    if not filtered:
        return
    filtered["manually_edited"] = 1
    set_clause = ", ".join(f"{k} = :{k}" for k in filtered)
    filtered["id"] = event_id
    with get_conn() as conn:
        conn.execute(f"UPDATE events SET {set_clause} WHERE id = :id", filtered)


def delete_event(event_id: str, hide: bool = True):
    with get_conn() as conn:
        if hide:
            conn.execute("DELETE FROM events WHERE id = ? AND source = 'manual'", (event_id,))
            conn.execute("INSERT OR IGNORE INTO hidden_events (event_id) VALUES (?)", (event_id,))
        else:
            conn.execute("DELETE FROM events WHERE id = ?", (event_id,))


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
            INSERT INTO tasks (id, title, member_id, completed, due_date, priority, notes, series_id)
            VALUES (:id, :title, :member_id, 0, :due_date, :priority, :notes, :series_id)
            """,
            {**t, "series_id": t.get("series_id")},
        )


def update_task(task_id: str, updates: dict):
    allowed = {"title", "member_id", "completed", "due_date", "priority", "notes", "completed_at", "series_id"}
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


def delete_task_series(series_id: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM tasks WHERE series_id = ?", (series_id,))


# ── Google Accounts ───────────────────────────────────────────────────────────

def get_google_accounts() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM google_accounts ORDER BY connected_at").fetchall()
        return [dict(r) for r in rows]


def upsert_google_account(id: str, email: str, token_json: str):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO google_accounts (id, email, token_json, connected_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                email      = excluded.email,
                token_json = excluded.token_json,
                connected_at = excluded.connected_at
            """,
            (id, email, token_json),
        )


def delete_google_account(account_id: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM google_accounts WHERE id = ?", (account_id,))


# ── Google Calendars (DB) ─────────────────────────────────────────────────────

def get_google_calendars(account_id: str | None = None) -> list[dict]:
    with get_conn() as conn:
        if account_id:
            rows = conn.execute(
                "SELECT * FROM google_calendars WHERE account_id = ? ORDER BY created_at",
                (account_id,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM google_calendars ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]


def create_google_calendar(c: dict):
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM google_calendars WHERE calendar_id = ? AND account_id = ?",
            (c["calendar_id"], c.get("account_id")),
        ).fetchone()
        if existing:
            return
        conn.execute(
            "INSERT INTO google_calendars (id, calendar_id, name, member_id, account_id) VALUES (:id, :calendar_id, :name, :member_id, :account_id)",
            c,
        )


def delete_google_calendar(cal_id: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM google_calendars WHERE id = ?", (cal_id,))


def delete_google_events_for_member(member_id: str):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM events WHERE member_id = ? AND source = 'google'",
            (member_id,),
        )


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
