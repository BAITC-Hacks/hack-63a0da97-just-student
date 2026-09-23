"""Local accounts for optional saved history; independent of EKT identity."""
import hashlib
import hmac
import json
import secrets
import sqlite3
from contextlib import contextmanager
from app.core.config import settings

@contextmanager
def database():
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.data_dir / "accounts.sqlite3", timeout=5)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, username TEXT UNIQUE, salt TEXT, password TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS history (user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, body TEXT NOT NULL)")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def digest(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()

def register(username, password):
    user_id, salt = secrets.token_urlsafe(24), secrets.token_hex(16)
    hashed = digest(password, salt)
    try:
        with database() as db:
            db.execute("INSERT INTO users VALUES (?, ?, ?, ?)", (user_id, username.casefold(), salt, hashed))
    except sqlite3.IntegrityError:
        return None
    return user_id

def login(username, password):
    with database() as db:
        row = db.execute("SELECT id,salt,password FROM users WHERE username=?", (username.casefold(),)).fetchone()
    # Perform the same expensive computation for unknown names.
    computed = digest(password, row[1] if row else '00' * 16)
    return row[0] if row and hmac.compare_digest(computed, row[2]) else None

def save_history(user_id, history):
    if user_id:
        with database() as db:
            db.execute("INSERT OR REPLACE INTO history VALUES (?,?)", (user_id, json.dumps(history[-100:], ensure_ascii=False)))

def read_history(user_id):
    if not user_id:
        return []
    with database() as db:
        row = db.execute("SELECT body FROM history WHERE user_id=?", (user_id,)).fetchone()
    return json.loads(row[0]) if row else []

def delete_account(user_id):
    with database() as db:
        db.execute("DELETE FROM users WHERE id=?", (user_id,))
