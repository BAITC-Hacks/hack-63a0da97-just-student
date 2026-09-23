"""Durable, expiring sessions for the supported single-worker deployment."""
import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager

from app.core.config import settings


@contextmanager
def database():
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(settings.data_dir / "sessions.sqlite3", timeout=10)
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, expires REAL NOT NULL, user_id TEXT, mode INTEGER NOT NULL, body TEXT NOT NULL)")
        with connection:
            yield connection
    finally:
        connection.close()


def identifier(key):
    return hashlib.sha256(key.encode()).hexdigest()


def load(key):
    if not key:
        return None
    with database() as db:
        row = db.execute("SELECT body FROM sessions WHERE id=? AND expires>? AND mode=?", (identifier(key), time.time(), int(settings.demo_mode))).fetchone()
    return json.loads(row[0]) if row else None


def save(key, body, *, create=False):
    values = (body["expires"], body.get("user_id"), int(settings.demo_mode), json.dumps(body, ensure_ascii=False), identifier(key))
    with database() as db:
        if create:
            db.execute("INSERT INTO sessions (expires,user_id,mode,body,id) VALUES (?,?,?,?,?)", values)
        else:
            # UPDATE cannot resurrect a session revoked by a concurrent request.
            db.execute("UPDATE sessions SET expires=?,user_id=?,mode=?,body=? WHERE id=?", values)


def delete(key):
    with database() as db:
        db.execute("DELETE FROM sessions WHERE id=?", (identifier(key),))


def delete_user(user_id):
    with database() as db:
        db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))


def cleanup():
    with database() as db:
        db.execute("DELETE FROM sessions WHERE expires<=?", (time.time(),))
