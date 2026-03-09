import sqlite3
import os
from pathlib import Path

# On Streamlit Cloud the app directory is read-only; use the home dir instead.
# Locally this resolves to ~/.startlijstmachine/startlijst.db.
DB_PATH = Path.home() / ".startlijstmachine" / "startlijst.db"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS rounds (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                filename    TEXT NOT NULL,
                round_date  TEXT,
                uploaded_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS groups (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                round_id    INTEGER NOT NULL REFERENCES rounds(id) ON DELETE CASCADE,
                tee_time    TEXT,
                slot_label  TEXT
            );

            CREATE TABLE IF NOT EXISTS group_members (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id    INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                player_name TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_member_name  ON group_members(player_name);
            CREATE INDEX IF NOT EXISTS idx_member_group ON group_members(group_id);
        """)


def insert_round(filename: str, round_date: str | None) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO rounds (filename, round_date) VALUES (?, ?)",
            (filename, round_date),
        )
        return cur.lastrowid


def insert_group(round_id: int, tee_time: str | None, slot_label: str | None) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO groups (round_id, tee_time, slot_label) VALUES (?, ?, ?)",
            (round_id, tee_time, slot_label),
        )
        return cur.lastrowid


def insert_members(group_id: int, names: list[str]) -> None:
    with get_connection() as conn:
        conn.executemany(
            "INSERT INTO group_members (group_id, player_name) VALUES (?, ?)",
            [(group_id, name) for name in names],
        )


def delete_round(round_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM rounds WHERE id = ?", (round_id,))


def get_all_players() -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT player_name FROM group_members ORDER BY player_name"
        ).fetchall()
    return [r["player_name"] for r in rows]


def get_partner_frequency(player: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT m2.player_name AS partner, COUNT(*) AS times_together
            FROM group_members m1
            JOIN group_members m2
                ON  m1.group_id    = m2.group_id
                AND m2.player_name != m1.player_name
            WHERE m1.player_name = ?
            GROUP BY m2.player_name
            ORDER BY times_together DESC
            LIMIT 30
            """,
            (player,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_tee_time_distribution(player: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT g.tee_time, COUNT(*) AS appearances
            FROM group_members m
            JOIN groups g ON m.group_id = g.id
            WHERE m.player_name = ?
              AND g.tee_time IS NOT NULL
            GROUP BY g.tee_time
            ORDER BY g.tee_time
            """,
            (player,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_rounds_summary() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                r.id,
                r.filename,
                r.round_date,
                r.uploaded_at,
                COUNT(DISTINCT g.id)  AS num_groups,
                COUNT(m.id)           AS num_members
            FROM rounds r
            LEFT JOIN groups g        ON g.round_id  = r.id
            LEFT JOIN group_members m ON m.group_id  = g.id
            GROUP BY r.id
            ORDER BY r.uploaded_at DESC
            """,
        ).fetchall()
    return [dict(r) for r in rows]
