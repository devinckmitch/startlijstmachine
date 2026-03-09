import sqlite3
import os
import hashlib
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


def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS rounds (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                filename    TEXT NOT NULL,
                round_date  TEXT,
                file_hash   TEXT,
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
        # Migration: add file_hash column to existing databases
        try:
            conn.execute("ALTER TABLE rounds ADD COLUMN file_hash TEXT")
        except Exception:
            pass


def find_round_by_hash(h: str) -> dict | None:
    """Return existing round info if this file was already imported."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, filename, round_date, uploaded_at FROM rounds WHERE file_hash = ?",
            (h,),
        ).fetchone()
    return dict(row) if row else None


def insert_round(filename: str, round_date: str | None, h: str | None = None) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO rounds (filename, round_date, file_hash) VALUES (?, ?, ?)",
            (filename, round_date, h),
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
            ORDER BY
                CASE WHEN g.tee_time GLOB '[0-9][0-9]:[0-9][0-9]'
                     THEN g.tee_time
                     ELSE '99:99'
                END
            """,
            (player,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_pair_frequencies(players: list[str]) -> list[dict]:
    """How often did each pair from the given player list play in the same group?"""
    if len(players) < 2:
        return []
    placeholders = ",".join("?" * len(players))
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT m1.player_name AS player1,
                   m2.player_name AS player2,
                   COUNT(*)       AS times_together
            FROM group_members m1
            JOIN group_members m2
                ON  m1.group_id    = m2.group_id
                AND m1.player_name < m2.player_name
            WHERE m1.player_name IN ({placeholders})
              AND m2.player_name IN ({placeholders})
            GROUP BY m1.player_name, m2.player_name
            ORDER BY times_together DESC
            """,
            players + players,
        ).fetchall()
    return [dict(r) for r in rows]


def get_groups_for_round(round_id: int) -> list[dict]:
    """Return all groups with their members for a given round."""
    with get_connection() as conn:
        groups = conn.execute(
            "SELECT id, tee_time, slot_label FROM groups WHERE round_id = ? ORDER BY tee_time, slot_label",
            (round_id,),
        ).fetchall()
        result = []
        for g in groups:
            members = conn.execute(
                "SELECT player_name FROM group_members WHERE group_id = ? ORDER BY player_name",
                (g["id"],),
            ).fetchall()
            result.append({
                "tee_time": g["tee_time"],
                "slot_label": g["slot_label"],
                "players": [m["player_name"] for m in members],
            })
    return result


def get_player_rounds(player: str) -> list[dict]:
    """Return all rounds a player participated in, with flight details."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                r.id          AS round_id,
                r.filename,
                r.round_date,
                g.tee_time,
                g.slot_label,
                g.id          AS group_id
            FROM group_members m
            JOIN groups g  ON m.group_id  = g.id
            JOIN rounds r  ON g.round_id  = r.id
            WHERE m.player_name = ?
            ORDER BY r.round_date DESC, r.uploaded_at DESC, g.tee_time
            """,
            (player,),
        ).fetchall()
        result = []
        for row in rows:
            co_players = conn.execute(
                "SELECT player_name FROM group_members WHERE group_id = ? AND player_name != ? ORDER BY player_name",
                (row["group_id"], player),
            ).fetchall()
            result.append({
                "round_id": row["round_id"],
                "filename": row["filename"],
                "round_date": row["round_date"],
                "tee_time": row["tee_time"],
                "slot_label": row["slot_label"],
                "co_players": [r["player_name"] for r in co_players],
            })
    return result


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
