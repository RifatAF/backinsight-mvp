"""Append-only trial journal. The API has no update or delete; SQLite triggers block them too."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "journal.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trials(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    config_json TEXT NOT NULL,
    data_range TEXT NOT NULL,
    dataset_sha256 TEXT NOT NULL,
    metrics_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
"""
_GUARDS = "".join(
    f"CREATE TRIGGER IF NOT EXISTS no_{op.lower()}_{t} BEFORE {op} ON {t} "
    f"BEGIN SELECT RAISE(ABORT, 'journal is append-only'); END;\n"
    for t in ("trials", "events")
    for op in ("UPDATE", "DELETE")
)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _dumps(obj):
    return json.dumps(obj, sort_keys=True, default=str)


def connect(path=None):
    conn = sqlite3.connect(str(DEFAULT_PATH if path is None else path))
    conn.executescript(_SCHEMA + _GUARDS)
    return conn


def log_event(conn, kind, payload):
    with conn:
        cur = conn.execute(
            "INSERT INTO events(ts, kind, payload_json) VALUES (?, ?, ?)",
            (_now(), kind, _dumps(payload)),
        )
    return cur.lastrowid


def start_trial(conn, config, data_range, dataset_sha256):
    """Writes the trial with status 'started' before any computation."""
    with conn:
        cur = conn.execute(
            "INSERT INTO trials(ts, config_json, data_range, dataset_sha256, metrics_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (_now(), _dumps(config), _dumps(data_range), dataset_sha256, _dumps({"status": "started"})),
        )
    return cur.lastrowid


def finish_trial(conn, trial_id, metrics):
    """Metrics go to events as a second INSERT, never an UPDATE of trials."""
    return log_event(conn, "trial_metrics", {"trial_id": trial_id, "metrics": metrics})


def trials(conn):
    rows = conn.execute(
        "SELECT id, ts, config_json, data_range, dataset_sha256, metrics_json FROM trials ORDER BY id"
    ).fetchall()
    return [
        {"id": r[0], "ts": r[1], "config": json.loads(r[2]), "data_range": json.loads(r[3]),
         "dataset_sha256": r[4], "metrics": json.loads(r[5])}
        for r in rows
    ]


def events(conn, kind=None):
    q = "SELECT id, ts, kind, payload_json FROM events"
    args = ()
    if kind is not None:
        q += " WHERE kind = ?"
        args = (kind,)
    rows = conn.execute(q + " ORDER BY id", args).fetchall()
    return [{"id": r[0], "ts": r[1], "kind": r[2], "payload": json.loads(r[3])} for r in rows]
