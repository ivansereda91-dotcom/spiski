"""Хранилище списков: SQLite, один файл."""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

DB_PATH = os.getenv("DB_PATH", "spiski.db")
LISTS = ("buy", "todo")


@contextmanager
def connect():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    with connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS items(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            list TEXT NOT NULL,
            text TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0,
            added_by INTEGER,
            added_at TEXT,
            done_by INTEGER,
            done_at TEXT
        );
        CREATE INDEX IF NOT EXISTS items_list ON items(list, done);
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY,
            name TEXT,
            notify INTEGER NOT NULL DEFAULT 1
        );
        """)


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def add(lst: str, texts: list[str], uid: int) -> list[str]:
    assert lst in LISTS
    added = []
    with connect() as c:
        existing = {r["text"].lower() for r in c.execute(
            "SELECT text FROM items WHERE list=? AND done=0", (lst,))}
        for t in texts:
            t = " ".join(t.split())[:100]
            if not t or t.lower() in existing:
                continue
            c.execute("INSERT INTO items(list,text,added_by,added_at) VALUES(?,?,?,?)", (lst, t, uid, now()))
            existing.add(t.lower())
            added.append(t)
    return added


def items(lst: str) -> list[sqlite3.Row]:
    with connect() as c:
        return list(c.execute(
            "SELECT * FROM items WHERE list=? ORDER BY done, id", (lst,)))


def get(item_id: int):
    with connect() as c:
        return c.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()


def toggle(item_id: int, uid: int):
    """Переключает галочку. Возвращает (list, новое состояние) или None, если пункта уже нет."""
    with connect() as c:
        r = c.execute("SELECT list, done FROM items WHERE id=?", (item_id,)).fetchone()
        if r is None:
            return None
        new = 0 if r["done"] else 1
        c.execute("UPDATE items SET done=?, done_by=?, done_at=? WHERE id=?",
                  (new, uid if new else None, now() if new else None, item_id))
        return r["list"], new


def delete(item_id: int):
    with connect() as c:
        r = c.execute("SELECT list FROM items WHERE id=?", (item_id,)).fetchone()
        c.execute("DELETE FROM items WHERE id=?", (item_id,))
        return r["list"] if r else None


def clear_done(lst: str) -> int:
    with connect() as c:
        return c.execute("DELETE FROM items WHERE list=? AND done=1", (lst,)).rowcount


def counts(lst: str) -> tuple[int, int]:
    with connect() as c:
        r = c.execute("SELECT COUNT(*) n, COALESCE(SUM(done),0) d FROM items WHERE list=?", (lst,)).fetchone()
        return r["n"] - r["d"], r["d"]


def touch_user(uid: int, name: str):
    with connect() as c:
        c.execute("INSERT INTO users(id,name) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name",
                  (uid, name))


def name(uid) -> str:
    if uid is None:
        return ""
    with connect() as c:
        r = c.execute("SELECT name FROM users WHERE id=?", (uid,)).fetchone()
        return r["name"] if r else ""


def notify_on(uid: int) -> bool:
    with connect() as c:
        r = c.execute("SELECT notify FROM users WHERE id=?", (uid,)).fetchone()
        return bool(r["notify"]) if r else True


def toggle_notify(uid: int) -> bool:
    with connect() as c:
        c.execute("UPDATE users SET notify = 1 - notify WHERE id=?", (uid,))
        return bool(c.execute("SELECT notify FROM users WHERE id=?", (uid,)).fetchone()["notify"])
