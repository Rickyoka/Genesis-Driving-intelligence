"""SQLite access: one connection per request, always closed on teardown."""
from __future__ import annotations

import logging
import sqlite3

from flask import Flask, current_app, g

logger = logging.getLogger(__name__)

SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS vehicles (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    nickname      TEXT    NOT NULL,
    maker         TEXT    NOT NULL,
    model         TEXT    NOT NULL,
    engine        TEXT    NOT NULL DEFAULT '',
    power_ps      INTEGER NOT NULL CHECK (power_ps BETWEEN 40 AND 2000),
    torque_nm     INTEGER NOT NULL CHECK (torque_nm BETWEEN 50 AND 3000),
    drivetrain    TEXT    NOT NULL CHECK (drivetrain IN ('FF','FR','MR','RR','AWD')),
    transmission  TEXT    NOT NULL CHECK (transmission IN ('MT','AT','DCT','EV')),
    weight_kg     INTEGER NOT NULL CHECK (weight_kg BETWEEN 500 AND 4000),
    size_class    TEXT    NOT NULL CHECK (size_class IN ('compact','mid','large')),
    created_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    photo         TEXT
);

CREATE TABLE IF NOT EXISTS reports (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id    INTEGER REFERENCES vehicles(id) ON DELETE SET NULL,
    source        TEXT    NOT NULL,
    overall       INTEGER NOT NULL,
    braking       INTEGER,
    distance_km   REAL    NOT NULL,
    result_json   TEXT    NOT NULL,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_reports_created ON reports(created_at);

CREATE TABLE IF NOT EXISTS favorites (
    course_id     TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS settings (
    key           TEXT PRIMARY KEY,
    value         TEXT NOT NULL,
    updated_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS stamps (
    course_id     TEXT PRIMARY KEY,
    source        TEXT NOT NULL CHECK (source IN ('gps','vehicle','demo')),
    first_at      TEXT NOT NULL,
    last_at       TEXT NOT NULL,
    visits        INTEGER NOT NULL DEFAULT 1 CHECK (visits >= 1)
);

-- Visit log keeps no coordinates: only which checkpoint, how it was verified, and when.
CREATE TABLE IF NOT EXISTS visits (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id     TEXT NOT NULL,
    source        TEXT NOT NULL CHECK (source IN ('gps','vehicle','demo')),
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_visits_id_source ON visits(source, id);

-- ref is UNIQUE so a stamp / collection / milestone can never be paid twice.
CREATE TABLE IF NOT EXISTS points_ledger (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ref           TEXT    NOT NULL UNIQUE,
    amount        INTEGER NOT NULL,
    reason        TEXT    NOT NULL,
    source        TEXT    NOT NULL,
    created_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS education (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id    TEXT    NOT NULL,
    school        TEXT    NOT NULL,
    completed_on  TEXT    NOT NULL,
    verified      INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0,1)),
    created_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
"""


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        conn = sqlite3.connect(current_app.config["DATABASE"], timeout=10,
                               detect_types=sqlite3.PARSE_DECLTYPES)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


def get_setting(key: str, default: str = "") -> str:
    row = get_db().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    db = get_db()
    db.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET "
               "value = excluded.value, updated_at = datetime('now','localtime')", (key, value))
    db.commit()


def close_db(_exc: BaseException | None = None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:  # pragma: no cover - defensive
            logger.exception("Failed to close DB connection")


SCHEMA_VERSION = 4

_VEHICLES_V2 = SCHEMA.split("CREATE TABLE IF NOT EXISTS vehicles (", 1)[1].split(");", 1)[0]


def _migrate(conn: sqlite3.Connection) -> None:
    """v1 -> v2: allow 'EV' powertrain (SQLite CHECK constraints need a table rebuild)."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= SCHEMA_VERSION:
        return
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='vehicles'").fetchone()
    if row and "'EV'" not in row[0]:
        logger.info("Migrating vehicles table to schema v2 (EV support)")
        conn.execute("PRAGMA foreign_keys = OFF")
        with conn:  # single transaction: either fully migrated or untouched
            conn.execute(f"CREATE TABLE vehicles_v2 ({_VEHICLES_V2})")
            cols = ("id, nickname, maker, model, engine, power_ps, torque_nm, drivetrain, transmission, "
                    "weight_kg, size_class, created_at")
            conn.execute(f"INSERT INTO vehicles_v2 ({cols}) SELECT {cols} FROM vehicles")
            conn.execute("DROP TABLE vehicles")
            conn.execute("ALTER TABLE vehicles_v2 RENAME TO vehicles")
    # v3 -> v4: optional vehicle photo
    if "photo" not in {r[1] for r in conn.execute("PRAGMA table_info(vehicles)")}:
        conn.execute("ALTER TABLE vehicles ADD COLUMN photo TEXT")
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def init_db(app: Flask) -> None:
    conn = sqlite3.connect(app.config["DATABASE"])
    try:
        conn.executescript(SCHEMA)
        conn.commit()
        _migrate(conn)
    finally:
        conn.close()
    app.teardown_appcontext(close_db)
    logger.info("Database ready: %s", app.config["DATABASE"])
