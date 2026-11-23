"""Zero-dependency SQLite WAL engine for Convo Mesh cross-conversation coordination."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ISO_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"


def get_runtime_dir() -> Path:
    """Return the persistent runtime directory for Convo Mesh state and logs."""
    db_override = os.environ.get("CONVO_MESH_DB", "").strip()
    if db_override:
        return Path(db_override).expanduser().resolve().parent
    return Path.home() / ".gemini" / "antigravity" / "convo-mesh"


def get_db_path() -> Path:
    """Resolve the shared SQLite database path, honoring CONVO_MESH_DB for tests."""
    db_override = os.environ.get("CONVO_MESH_DB", "").strip()
    if db_override:
        path = Path(db_override).expanduser()
    else:
        path = Path.home() / ".gemini" / "antigravity" / "convo-mesh" / "mesh.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def get_error_log_path() -> Path:
    """Return the path for non-fatal hook error logs."""
    runtime_dir = get_runtime_dir()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    return runtime_dir / "hook_errors.log"


def utc_now_iso() -> str:
    """Return current UTC timestamp formatted with microsecond precision."""
    return datetime.now(timezone.utc).strftime(ISO_FMT)


def utc_offset_iso(minutes: float) -> str:
    """Return UTC timestamp offset by the specified number of minutes."""
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime(ISO_FMT)


def parse_iso(ts: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp string into a timezone-aware datetime."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def normalize_path(path_str: str | None) -> str:
    """Normalize filesystem paths for cross-format comparison on Windows."""
    if not path_str:
        return ""
    norm = path_str.strip().replace("\\", "/")
    while "//" in norm:
        norm = norm.replace("//", "/")
    if len(norm) > 1 and not (len(norm) == 3 and norm[1:] == ":/"):
        norm = norm.rstrip("/")
    return norm.lower()


def _workspace_matches(target: str | None, candidate: str | None) -> bool:
    """Return True if two workspace paths match or one contains the other."""
    t_norm = normalize_path(target)
    c_norm = normalize_path(candidate)
    if not t_norm or not c_norm:
        return False
    if t_norm == c_norm:
        return True
    if c_norm.startswith(t_norm + "/") or t_norm.startswith(c_norm + "/"):
        return True
    return t_norm in c_norm or c_norm in t_norm


def init_db(conn: sqlite3.Connection) -> None:
    """Create all Convo Mesh tables and indexes if they do not exist."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            conversation_id TEXT PRIMARY KEY,
            alias TEXT UNIQUE,
            workspace_path TEXT NOT NULL DEFAULT '',
            current_goal TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            last_user_input TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_turn_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS focus_locks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_convo_id TEXT NOT NULL,
            owner_alias TEXT NOT NULL DEFAULT '',
            owner_workspace TEXT NOT NULL DEFAULT '',
            goal TEXT NOT NULL,
            mode TEXT NOT NULL CHECK(mode IN ('soft', 'strict')),
            allowed_workspaces TEXT NOT NULL DEFAULT '[]',
            reason TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            paused_until TEXT,
            expires_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_convo_id TEXT NOT NULL,
            sender_alias TEXT NOT NULL DEFAULT '',
            sender_workspace TEXT NOT NULL DEFAULT '',
            target_type TEXT NOT NULL CHECK(target_type IN ('convo', 'alias', 'workspace', 'broadcast')),
            target_value TEXT NOT NULL DEFAULT '',
            priority TEXT NOT NULL DEFAULT 'normal' CHECK(priority IN ('info', 'normal', 'reminder', 'urgent')),
            subject TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL,
            reply_to INTEGER,
            expires_at TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS message_deliveries (
            message_id INTEGER NOT NULL,
            recipient_convo_id TEXT NOT NULL,
            delivered_at TEXT NOT NULL,
            acknowledged_at TEXT,
            PRIMARY KEY (message_id, recipient_convo_id),
            FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS focus_reminders_sent (
            lock_id INTEGER NOT NULL,
            convo_id TEXT NOT NULL,
            last_reminded_at TEXT NOT NULL,
            PRIMARY KEY (lock_id, convo_id),
            FOREIGN KEY (lock_id) REFERENCES focus_locks(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS blackboard (
            namespace TEXT NOT NULL DEFAULT 'global',
            key TEXT NOT NULL,
            value_json TEXT NOT NULL,
            author_convo_id TEXT NOT NULL DEFAULT '',
            author_alias TEXT NOT NULL DEFAULT '',
            expires_at TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (namespace, key)
        );

        CREATE TABLE IF NOT EXISTS file_touches (
            file_path TEXT NOT NULL,
            convo_id TEXT NOT NULL,
            workspace_path TEXT NOT NULL DEFAULT '',
            tool_name TEXT NOT NULL DEFAULT '',
            touched_at TEXT NOT NULL,
            PRIMARY KEY (file_path, convo_id)
        );

        CREATE TABLE IF NOT EXISTS collision_warnings_sent (
            workspace_path TEXT NOT NULL,
            convo_id TEXT NOT NULL,
            last_warned_at TEXT NOT NULL,
            PRIMARY KEY (workspace_path, convo_id)
        );

        CREATE TABLE IF NOT EXISTS handoffs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_convo_id TEXT NOT NULL,
            from_alias TEXT NOT NULL DEFAULT '',
            from_workspace TEXT NOT NULL DEFAULT '',
            target_type TEXT NOT NULL DEFAULT 'next_any' CHECK(target_type IN ('next_any', 'convo', 'alias', 'workspace')),
            target_value TEXT NOT NULL DEFAULT '*',
            summary TEXT NOT NULL,
            next_steps_json TEXT NOT NULL DEFAULT '[]',
            key_files_json TEXT NOT NULL DEFAULT '[]',
            claimed_by_convo_id TEXT,
            claimed_at TEXT,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_focus_locks_active ON focus_locks(active);
        CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);
        CREATE INDEX IF NOT EXISTS idx_file_touches_ws ON file_touches(workspace_path, touched_at);
        CREATE INDEX IF NOT EXISTS idx_handoffs_claimed ON handoffs(claimed_by_convo_id, created_at);
        """
    )


def connect() -> sqlite3.Connection:
    """Open a WAL-mode SQLite connection and ensure schema initialization."""
    db_path = get_db_path()
    conn = sqlite3.connect(str(db_path), timeout=2.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=2000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    mode_row = conn.execute("PRAGMA journal_mode;").fetchone()
    if mode_row and str(mode_row[0]).lower() != "wal":
        conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    init_db(conn)
    return conn


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def _format_lock(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    try:
        data["allowed_workspaces"] = json.loads(data.get("allowed_workspaces") or "[]")
    except json.JSONDecodeError:
        data["allowed_workspaces"] = []
    data["active"] = bool(data.get("active"))
    return data


def _format_handoff(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    for json_col, out_key in (("next_steps_json", "next_steps"), ("key_files_json", "key_files")):
        raw = data.pop(json_col, "[]")
        try:
            data[out_key] = json.loads(raw or "[]")
        except json.JSONDecodeError:
            data[out_key] = []
    return data


def resolve_convo_id(convo_id: str | None) -> str:
    """Return explicit convo_id or fall back to the most recently seen conversation."""
    if convo_id and convo_id.strip():
        return convo_id.strip()
    conn = connect()
    try:
        row = conn.execute(
            "SELECT conversation_id FROM conversations ORDER BY last_seen_at DESC LIMIT 1"
        ).fetchone()
        return str(row["conversation_id"]) if row else "unknown"
    finally:
        conn.close()


def upsert_conversation(
    convo_id: str,
    workspace_path: str = "",
    last_user_input: str | None = None,
    alias: str | None = None,
    goal: str | None = None,
    status: str | None = None,
    mark_turn: bool = False,
) -> dict[str, Any]:
    """Insert or update a conversation record while preserving omitted metadata."""
    clean_id = (convo_id or "").strip()
    if not clean_id:
        raise ValueError("conversation_id must be non-empty")

    now = utc_now_iso()
    clean_alias = alias.strip() if alias and alias.strip() else None
    clean_ws = workspace_path.strip() if workspace_path else ""
    clean_goal = goal.strip() if goal is not None else None
    clean_status = status.strip() if status is not None and status.strip() else None
    clean_input = last_user_input.strip()[:240] if last_user_input is not None else None

    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT * FROM conversations WHERE conversation_id = ?", (clean_id,)
        ).fetchone()

        if clean_alias:
            conn.execute(
                "UPDATE conversations SET alias = NULL WHERE LOWER(alias) = LOWER(?) AND conversation_id != ?",
                (clean_alias, clean_id),
            )

        if existing is None:
            conn.execute(
                """
                INSERT INTO conversations (
                    conversation_id, alias, workspace_path, current_goal, status,
                    last_user_input, first_seen_at, last_seen_at, last_turn_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    clean_id,
                    clean_alias,
                    clean_ws,
                    clean_goal or "",
                    clean_status or "active",
                    clean_input or "",
                    now,
                    now,
                    now,
                ),
            )
        else:
            final_alias = clean_alias if clean_alias is not None else existing["alias"]
            final_ws = clean_ws if clean_ws else existing["workspace_path"]
            final_goal = clean_goal if clean_goal is not None else existing["current_goal"]
            final_status = clean_status if clean_status is not None else existing["status"]
            final_input = clean_input if clean_input is not None else existing["last_user_input"]
            final_turn = now if (mark_turn or clean_input is not None) else existing["last_turn_at"]

            conn.execute(
                """
                UPDATE conversations
                SET alias = ?, workspace_path = ?, current_goal = ?, status = ?,
                    last_user_input = ?, last_seen_at = ?, last_turn_at = ?
                WHERE conversation_id = ?
                """,
                (
                    final_alias,
                    final_ws,
                    final_goal,
                    final_status,
                    final_input,
                    now,
                    final_turn,
                    clean_id,
                ),
            )

        row = conn.execute(
            "SELECT * FROM conversations WHERE conversation_id = ?", (clean_id,)
        ).fetchone()
        conn.execute("COMMIT")
        return dict(row)
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def list_conversations(
    hours_back: float = 24.0,
    status: str | None = None,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """List conversations seen within the given lookback window."""
    cutoff = utc_offset_iso(-abs(hours_back) * 60.0)
    conn = connect()
    try:
        query = "SELECT * FROM conversations WHERE last_seen_at >= ?"
        params: list[Any] = [cutoff]
        if status and status.strip():
            query += " AND status = ?"
            params.append(status.strip())
        query += " ORDER BY last_seen_at DESC"
        rows = [dict(r) for r in conn.execute(query, params).fetchall()]
        if workspace and workspace.strip():
            rows = [r for r in rows if _workspace_matches(workspace, r.get("workspace_path"))]
        return rows
    finally:
        conn.close()


def set_focus_lock(
    owner_convo_id: str,
    goal: str,
    mode: str = "soft",
    duration_minutes: float | None = None,
    allowed_workspaces: list[str] | str | None = None,
    reason: str = "",
) -> dict[str, Any]:
    """Activate a soft or strict focus lock for a conversation."""
    clean_owner = resolve_convo_id(owner_convo_id)
    clean_goal = (goal or "").strip()
    if not clean_goal:
        raise ValueError("goal must be non-empty")
    clean_mode = (mode or "soft").strip().lower()
    if clean_mode not in ("soft", "strict"):
        raise ValueError("mode must be 'soft' or 'strict'")

    if allowed_workspaces is None:
        allowed_list: list[str] = []
    elif isinstance(allowed_workspaces, str):
        allowed_list = [w.strip() for w in allowed_workspaces.split(",") if w.strip()]
    else:
        allowed_list = [str(w).strip() for w in allowed_workspaces if str(w).strip()]

    now = utc_now_iso()
    expires_at = utc_offset_iso(duration_minutes) if (duration_minutes and duration_minutes > 0) else None

    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        owner_row = conn.execute(
            "SELECT alias, workspace_path FROM conversations WHERE conversation_id = ?",
            (clean_owner,),
        ).fetchone()
        owner_alias = (owner_row["alias"] or "") if owner_row else ""
        owner_ws = (owner_row["workspace_path"] or "") if owner_row else ""

        conn.execute(
            "UPDATE focus_locks SET active = 0, updated_at = ? WHERE owner_convo_id = ? AND active = 1",
            (now, clean_owner),
        )
        cur = conn.execute(
            """
            INSERT INTO focus_locks (
                owner_convo_id, owner_alias, owner_workspace, goal, mode,
                allowed_workspaces, reason, active, paused_until, expires_at,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, NULL, ?, ?, ?)
            """,
            (
                clean_owner,
                owner_alias,
                owner_ws,
                clean_goal,
                clean_mode,
                json.dumps(allowed_list),
                (reason or "").strip(),
                expires_at,
                now,
                now,
            ),
        )
        lock_id = cur.lastrowid
        row = conn.execute("SELECT * FROM focus_locks WHERE id = ?", (lock_id,)).fetchone()
        conn.execute("COMMIT")
        return _format_lock(row)
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def get_active_focus_locks(include_paused: bool = False) -> list[dict[str, Any]]:
    """Return currently active focus locks after expiring elapsed locks."""
    now = utc_now_iso()
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE focus_locks SET active = 0, updated_at = ? WHERE active = 1 AND expires_at IS NOT NULL AND expires_at <= ?",
            (now, now),
        )
        if include_paused:
            rows = conn.execute(
                "SELECT * FROM focus_locks WHERE active = 1 ORDER BY created_at DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM focus_locks
                WHERE active = 1 AND (paused_until IS NULL OR paused_until <= ?)
                ORDER BY created_at DESC
                """,
                (now,),
            ).fetchall()
        conn.execute("COMMIT")
        return [_format_lock(r) for r in rows]
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def snooze_focus_lock(minutes: float = 15.0, lock_id: int | None = None) -> list[dict[str, Any]]:
    """Temporarily pause active focus lock enforcement for the specified minutes."""
    if minutes <= 0:
        raise ValueError("minutes must be > 0")
    now = utc_now_iso()
    paused_until = utc_offset_iso(minutes)
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if lock_id is not None:
            conn.execute(
                "UPDATE focus_locks SET paused_until = ?, updated_at = ? WHERE id = ? AND active = 1",
                (paused_until, now, int(lock_id)),
            )
            rows = conn.execute("SELECT * FROM focus_locks WHERE id = ?", (int(lock_id),)).fetchall()
        else:
            conn.execute(
                "UPDATE focus_locks SET paused_until = ?, updated_at = ? WHERE active = 1",
                (paused_until, now),
            )
            rows = conn.execute("SELECT * FROM focus_locks WHERE active = 1").fetchall()
        conn.execute("COMMIT")
        return [_format_lock(r) for r in rows]
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def clear_focus_lock(lock_id: int | None = None, owner_convo_id: str | None = None) -> int:
    """Deactivate matching focus locks (or all active locks if no filters given)."""
    now = utc_now_iso()
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if lock_id is not None:
            cur = conn.execute(
                "UPDATE focus_locks SET active = 0, updated_at = ? WHERE id = ? AND active = 1",
                (now, int(lock_id)),
            )
        elif owner_convo_id and owner_convo_id.strip():
            cur = conn.execute(
                "UPDATE focus_locks SET active = 0, updated_at = ? WHERE owner_convo_id = ? AND active = 1",
                (now, owner_convo_id.strip()),
            )
        else:
            cur = conn.execute(
                "UPDATE focus_locks SET active = 0, updated_at = ? WHERE active = 1",
                (now,),
            )
        count = cur.rowcount
        conn.execute("COMMIT")
        return count
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def check_focus_conflict(
    convo_id: str,
    workspace_path: str = "",
    invocation_num: int = 1,
    soft_cooldown_minutes: float = 3.0,
) -> list[dict[str, Any]]:
    """Evaluate whether the given conversation conflicts with another conversation's focus lock."""
    if invocation_num != 1:
        return []

    active_locks = get_active_focus_locks(include_paused=False)
    candidates = [lock for lock in active_locks if lock["owner_convo_id"] != convo_id]
    if not candidates:
        return []

    now = utc_now_iso()
    cooldown_cutoff = utc_offset_iso(-abs(soft_cooldown_minutes))
    triggered: list[dict[str, Any]] = []

    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        for lock in candidates:
            allowed = lock.get("allowed_workspaces") or []
            if workspace_path and any(_workspace_matches(w, workspace_path) for w in allowed):
                continue

            should_trigger = False
            if lock["mode"] == "strict":
                should_trigger = True
            else:
                rem = conn.execute(
                    "SELECT last_reminded_at FROM focus_reminders_sent WHERE lock_id = ? AND convo_id = ?",
                    (lock["id"], convo_id),
                ).fetchone()
                if rem is None or rem["last_reminded_at"] <= cooldown_cutoff:
                    should_trigger = True

            if should_trigger:
                conn.execute(
                    """
                    INSERT INTO focus_reminders_sent (lock_id, convo_id, last_reminded_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(lock_id, convo_id) DO UPDATE SET last_reminded_at = excluded.last_reminded_at
                    """,
                    (lock["id"], convo_id, now),
                )
                triggered.append(lock)
        conn.execute("COMMIT")
        return triggered
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def send_message(
    sender_convo_id: str,
    target_type: str,
    target_value: str,
    body: str,
    priority: str = "normal",
    subject: str = "",
    reply_to: int | None = None,
    ttl_minutes: float | None = None,
) -> dict[str, Any]:
    """Send a direct, alias, workspace-scoped, or broadcast message."""
    clean_sender = resolve_convo_id(sender_convo_id)
    clean_type = (target_type or "").strip().lower()
    if clean_type not in ("convo", "alias", "workspace", "broadcast"):
        raise ValueError("target_type must be one of: convo, alias, workspace, broadcast")

    clean_priority = (priority or "normal").strip().lower()
    if clean_priority not in ("info", "normal", "reminder", "urgent"):
        raise ValueError("priority must be one of: info, normal, reminder, urgent")

    clean_body = (body or "").strip()
    if not clean_body:
        raise ValueError("body must be non-empty")

    clean_target = (target_value or "").strip()
    if clean_type == "broadcast" and not clean_target:
        clean_target = "*"
    elif clean_type != "broadcast" and not clean_target:
        raise ValueError("target_value is required for non-broadcast messages")

    now = utc_now_iso()
    expires_at = utc_offset_iso(ttl_minutes) if (ttl_minutes and ttl_minutes > 0) else None

    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        sender_row = conn.execute(
            "SELECT alias, workspace_path FROM conversations WHERE conversation_id = ?",
            (clean_sender,),
        ).fetchone()
        sender_alias = (sender_row["alias"] or "") if sender_row else ""
        sender_ws = (sender_row["workspace_path"] or "") if sender_row else ""

        cur = conn.execute(
            """
            INSERT INTO messages (
                sender_convo_id, sender_alias, sender_workspace, target_type,
                target_value, priority, subject, body, reply_to, expires_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clean_sender,
                sender_alias,
                sender_ws,
                clean_type,
                clean_target,
                clean_priority,
                (subject or "").strip(),
                clean_body,
                int(reply_to) if reply_to is not None else None,
                expires_at,
                now,
            ),
        )
        msg_id = cur.lastrowid
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone()
        conn.execute("COMMIT")
        return dict(row)
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def _message_matches_recipient(
    msg_row: sqlite3.Row,
    convo_id: str,
    convo_alias: str,
    workspace_path: str,
) -> bool:
    ttype = msg_row["target_type"]
    tval = (msg_row["target_value"] or "").strip()
    if ttype == "broadcast":
        return True
    if ttype == "convo":
        return tval == convo_id
    if ttype == "alias":
        return bool(convo_alias) and tval.lower() == convo_alias.strip().lower()
    if ttype == "workspace":
        return bool(workspace_path) and _workspace_matches(tval, workspace_path)
    return False


def pop_undelivered_messages(convo_id: str, workspace_path: str = "") -> list[dict[str, Any]]:
    """Atomically fetch and mark delivered any unread/undelivered messages for convo_id."""
    now = utc_now_iso()
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        convo_row = conn.execute(
            "SELECT alias, workspace_path FROM conversations WHERE conversation_id = ?",
            (convo_id,),
        ).fetchone()
        convo_alias = (convo_row["alias"] or "") if convo_row else ""
        effective_ws = workspace_path.strip() if workspace_path.strip() else (
            (convo_row["workspace_path"] or "") if convo_row else ""
        )

        rows = conn.execute(
            """
            SELECT m.*
            FROM messages m
            WHERE m.sender_convo_id != ?
              AND (m.expires_at IS NULL OR m.expires_at > ?)
              AND NOT EXISTS (
                  SELECT 1 FROM message_deliveries d
                  WHERE d.message_id = m.id AND d.recipient_convo_id = ?
              )
            ORDER BY m.created_at ASC, m.id ASC
            """,
            (convo_id, now, convo_id),
        ).fetchall()

        matched: list[dict[str, Any]] = []
        for r in rows:
            if _message_matches_recipient(r, convo_id, convo_alias, effective_ws):
                conn.execute(
                    """
                    INSERT OR IGNORE INTO message_deliveries (message_id, recipient_convo_id, delivered_at)
                    VALUES (?, ?, ?)
                    """,
                    (r["id"], convo_id, now),
                )
                matched.append(dict(r))

        conn.execute("COMMIT")
        return matched
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def read_inbox(
    convo_id: str,
    include_acknowledged: bool = False,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """List messages targeted to convo_id along with delivery/ack state."""
    clean_id = resolve_convo_id(convo_id)
    now = utc_now_iso()
    conn = connect()
    try:
        convo_row = conn.execute(
            "SELECT alias, workspace_path FROM conversations WHERE conversation_id = ?",
            (clean_id,),
        ).fetchone()
        convo_alias = (convo_row["alias"] or "") if convo_row else ""
        effective_ws = (convo_row["workspace_path"] or "") if convo_row else ""

        rows = conn.execute(
            """
            SELECT m.*, d.delivered_at, d.acknowledged_at
            FROM messages m
            LEFT JOIN message_deliveries d
              ON d.message_id = m.id AND d.recipient_convo_id = ?
            WHERE m.sender_convo_id != ?
              AND (m.expires_at IS NULL OR m.expires_at > ?)
            ORDER BY m.created_at DESC, m.id DESC
            """,
            (clean_id, clean_id, now),
        ).fetchall()

        results: list[dict[str, Any]] = []
        for r in rows:
            if not _message_matches_recipient(r, clean_id, convo_alias, effective_ws):
                continue
            if not include_acknowledged and r["acknowledged_at"] is not None:
                continue
            results.append(dict(r))
            if len(results) >= max(1, int(limit)):
                break
        return results
    finally:
        conn.close()


def ack_messages(convo_id: str, message_ids: list[int] | None = None) -> int:
    """Mark specific or all matching inbox messages as acknowledged for convo_id."""
    clean_id = resolve_convo_id(convo_id)
    now = utc_now_iso()
    if message_ids:
        target_ids = [int(mid) for mid in message_ids]
    else:
        inbox = read_inbox(clean_id, include_acknowledged=False, limit=500)
        target_ids = [int(m["id"]) for m in inbox]

    if not target_ids:
        return 0

    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        count = 0
        for mid in target_ids:
            conn.execute(
                """
                INSERT INTO message_deliveries (message_id, recipient_convo_id, delivered_at, acknowledged_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(message_id, recipient_convo_id) DO UPDATE SET acknowledged_at = excluded.acknowledged_at
                """,
                (mid, clean_id, now, now),
            )
            count += 1
        conn.execute("COMMIT")
        return count
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def blackboard_set(
    key: str,
    value: Any,
    namespace: str = "global",
    author_convo_id: str = "",
    ttl_minutes: float | None = None,
) -> dict[str, Any]:
    """Store a JSON-serializable key/value entry on the shared blackboard."""
    clean_ns = (namespace or "global").strip() or "global"
    clean_key = (key or "").strip()
    if not clean_key:
        raise ValueError("key must be non-empty")

    now = utc_now_iso()
    expires_at = utc_offset_iso(ttl_minutes) if (ttl_minutes and ttl_minutes > 0) else None
    clean_author = author_convo_id.strip() if author_convo_id else ""
    value_json = json.dumps(value)

    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        author_alias = ""
        if clean_author:
            author_row = conn.execute(
                "SELECT alias FROM conversations WHERE conversation_id = ?",
                (clean_author,),
            ).fetchone()
            if author_row and author_row["alias"]:
                author_alias = author_row["alias"]

        conn.execute(
            """
            INSERT INTO blackboard (
                namespace, key, value_json, author_convo_id, author_alias, expires_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(namespace, key) DO UPDATE SET
                value_json = excluded.value_json,
                author_convo_id = excluded.author_convo_id,
                author_alias = excluded.author_alias,
                expires_at = excluded.expires_at,
                updated_at = excluded.updated_at
            """,
            (clean_ns, clean_key, value_json, clean_author, author_alias, expires_at, now),
        )
        row = conn.execute(
            "SELECT * FROM blackboard WHERE namespace = ? AND key = ?",
            (clean_ns, clean_key),
        ).fetchone()
        conn.execute("COMMIT")
        result = dict(row)
        result["value"] = json.loads(result.pop("value_json"))
        return result
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def blackboard_get(key: str | None = None, namespace: str = "global") -> Any:
    """Read one entry or all active entries from the shared blackboard."""
    clean_ns = (namespace or "global").strip() or "global"
    now = utc_now_iso()
    conn = connect()
    try:
        conn.execute("DELETE FROM blackboard WHERE expires_at IS NOT NULL AND expires_at <= ?", (now,))
        if key and key.strip():
            row = conn.execute(
                "SELECT * FROM blackboard WHERE namespace = ? AND key = ?",
                (clean_ns, key.strip()),
            ).fetchone()
            if row is None:
                return None
            out = dict(row)
            out["value"] = json.loads(out.pop("value_json"))
            return out

        if clean_ns == "*":
            rows = conn.execute("SELECT * FROM blackboard ORDER BY namespace ASC, key ASC").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM blackboard WHERE namespace = ? ORDER BY key ASC",
                (clean_ns,),
            ).fetchall()

        items = []
        for r in rows:
            item = dict(r)
            item["value"] = json.loads(item.pop("value_json"))
            items.append(item)
        return items
    finally:
        conn.close()


def blackboard_delete(key: str, namespace: str = "global") -> bool:
    """Delete a key from the shared blackboard."""
    clean_ns = (namespace or "global").strip() or "global"
    clean_key = (key or "").strip()
    if not clean_key:
        raise ValueError("key must be non-empty")
    conn = connect()
    try:
        cur = conn.execute(
            "DELETE FROM blackboard WHERE namespace = ? AND key = ?",
            (clean_ns, clean_key),
        )
        return cur.rowcount > 0
    finally:
        conn.close()


def record_file_touch(
    convo_id: str,
    file_path: str,
    workspace_path: str = "",
    tool_name: str = "",
) -> None:
    """Record a recent file write/edit event for cross-convo collision awareness."""
    clean_convo = (convo_id or "").strip()
    clean_file = normalize_path(file_path)
    if not clean_convo or not clean_file:
        return

    now = utc_now_iso()
    conn = connect()
    try:
        effective_ws = workspace_path.strip()
        if not effective_ws:
            convo_row = conn.execute(
                "SELECT workspace_path FROM conversations WHERE conversation_id = ?",
                (clean_convo,),
            ).fetchone()
            if convo_row:
                effective_ws = convo_row["workspace_path"] or ""

        conn.execute(
            """
            INSERT INTO file_touches (file_path, convo_id, workspace_path, tool_name, touched_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(file_path, convo_id) DO UPDATE SET
                workspace_path = excluded.workspace_path,
                tool_name = excluded.tool_name,
                touched_at = excluded.touched_at
            """,
            (clean_file, clean_convo, effective_ws, (tool_name or "").strip(), now),
        )
    finally:
        conn.close()


def check_workspace_collisions(
    convo_id: str,
    workspace_path: str,
    minutes_back: float = 15.0,
    warning_cooldown_minutes: float = 5.0,
) -> list[dict[str, Any]]:
    """Detect whether other conversations recently edited files in the same workspace."""
    ws_norm = normalize_path(workspace_path)
    if not ws_norm:
        return []

    now = utc_now_iso()
    touch_cutoff = utc_offset_iso(-abs(minutes_back))
    cooldown_cutoff = utc_offset_iso(-abs(warning_cooldown_minutes))

    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT ft.file_path, ft.convo_id, ft.workspace_path, ft.tool_name, ft.touched_at,
                   c.alias AS convo_alias
            FROM file_touches ft
            LEFT JOIN conversations c ON c.conversation_id = ft.convo_id
            WHERE ft.convo_id != ? AND ft.touched_at >= ?
            ORDER BY ft.touched_at DESC
            """,
            (convo_id, touch_cutoff),
        ).fetchall()

        by_convo: dict[str, dict[str, Any]] = {}
        for r in rows:
            ft_ws = normalize_path(r["workspace_path"])
            ft_file = r["file_path"]
            matches = (
                (ft_ws and _workspace_matches(ws_norm, ft_ws))
                or ft_file.startswith(ws_norm + "/")
                or ft_file == ws_norm
            )
            if not matches:
                continue
            other_id = r["convo_id"]
            entry = by_convo.setdefault(
                other_id,
                {
                    "convo_id": other_id,
                    "alias": r["convo_alias"] or "",
                    "files": [],
                    "last_touched_at": r["touched_at"],
                },
            )
            if ft_file not in entry["files"]:
                entry["files"].append(ft_file)

        if not by_convo:
            conn.execute("COMMIT")
            return []

        warn_row = conn.execute(
            "SELECT last_warned_at FROM collision_warnings_sent WHERE workspace_path = ? AND convo_id = ?",
            (ws_norm, convo_id),
        ).fetchone()
        if warn_row is not None and warn_row["last_warned_at"] > cooldown_cutoff:
            conn.execute("COMMIT")
            return []

        conn.execute(
            """
            INSERT INTO collision_warnings_sent (workspace_path, convo_id, last_warned_at)
            VALUES (?, ?, ?)
            ON CONFLICT(workspace_path, convo_id) DO UPDATE SET last_warned_at = excluded.last_warned_at
            """,
            (ws_norm, convo_id, now),
        )
        conn.execute("COMMIT")
        return list(by_convo.values())
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def get_workspace_activity(workspace: str | None = None, minutes_back: float = 30.0) -> list[dict[str, Any]]:
    """Return recent file edit activity across conversations."""
    cutoff = utc_offset_iso(-abs(minutes_back))
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT ft.*, c.alias AS convo_alias
            FROM file_touches ft
            LEFT JOIN conversations c ON c.conversation_id = ft.convo_id
            WHERE ft.touched_at >= ?
            ORDER BY ft.touched_at DESC
            """,
            (cutoff,),
        ).fetchall()
        results = [dict(r) for r in rows]
        if workspace and workspace.strip():
            ws_norm = normalize_path(workspace)
            results = [
                r
                for r in results
                if _workspace_matches(ws_norm, r.get("workspace_path"))
                or (r.get("file_path") or "").startswith(ws_norm + "/")
            ]
        return results
    finally:
        conn.close()


def create_handoff(
    from_convo_id: str,
    summary: str,
    next_steps: list[str] | None = None,
    key_files: list[str] | None = None,
    target_type: str = "next_any",
    target_value: str = "*",
) -> dict[str, Any]:
    """Create a structured handoff packet for another (or next new) conversation."""
    clean_from = resolve_convo_id(from_convo_id)
    clean_summary = (summary or "").strip()
    if not clean_summary:
        raise ValueError("summary must be non-empty")

    clean_type = (target_type or "next_any").strip().lower()
    if clean_type not in ("next_any", "convo", "alias", "workspace"):
        raise ValueError("target_type must be one of: next_any, convo, alias, workspace")

    clean_target = (target_value or "").strip()
    if clean_type == "next_any" and not clean_target:
        clean_target = "*"
    elif clean_type != "next_any" and not clean_target:
        raise ValueError("target_value is required when target_type is not 'next_any'")

    steps_list = [str(s).strip() for s in (next_steps or []) if str(s).strip()]
    files_list = [str(f).strip() for f in (key_files or []) if str(f).strip()]
    now = utc_now_iso()

    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        from_row = conn.execute(
            "SELECT alias, workspace_path FROM conversations WHERE conversation_id = ?",
            (clean_from,),
        ).fetchone()
        from_alias = (from_row["alias"] or "") if from_row else ""
        from_ws = (from_row["workspace_path"] or "") if from_row else ""

        cur = conn.execute(
            """
            INSERT INTO handoffs (
                from_convo_id, from_alias, from_workspace, target_type, target_value,
                summary, next_steps_json, key_files_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clean_from,
                from_alias,
                from_ws,
                clean_type,
                clean_target,
                clean_summary,
                json.dumps(steps_list),
                json.dumps(files_list),
                now,
            ),
        )
        hid = cur.lastrowid
        row = conn.execute("SELECT * FROM handoffs WHERE id = ?", (hid,)).fetchone()
        conn.execute("COMMIT")
        return _format_handoff(row)
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def claim_matching_handoffs(convo_id: str, workspace_path: str = "") -> list[dict[str, Any]]:
    """Atomically claim unclaimed handoffs matching convo_id, its alias, workspace, or next_any."""
    now = utc_now_iso()
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        convo_row = conn.execute(
            "SELECT alias, workspace_path FROM conversations WHERE conversation_id = ?",
            (convo_id,),
        ).fetchone()
        convo_alias = (convo_row["alias"] or "") if convo_row else ""
        effective_ws = workspace_path.strip() if workspace_path.strip() else (
            (convo_row["workspace_path"] or "") if convo_row else ""
        )

        rows = conn.execute(
            """
            SELECT * FROM handoffs
            WHERE claimed_by_convo_id IS NULL
              AND from_convo_id != ?
            ORDER BY created_at ASC, id ASC
            """,
            (convo_id,),
        ).fetchall()

        claimed: list[dict[str, Any]] = []
        for r in rows:
            ttype = r["target_type"]
            tval = (r["target_value"] or "").strip()
            matched = (
                ttype == "next_any"
                or (ttype == "convo" and tval == convo_id)
                or (ttype == "alias" and bool(convo_alias) and tval.lower() == convo_alias.strip().lower())
                or (ttype == "workspace" and bool(effective_ws) and _workspace_matches(tval, effective_ws))
            )
            if not matched:
                continue
            conn.execute(
                "UPDATE handoffs SET claimed_by_convo_id = ?, claimed_at = ? WHERE id = ?",
                (convo_id, now, r["id"]),
            )
            updated = conn.execute("SELECT * FROM handoffs WHERE id = ?", (r["id"],)).fetchone()
            claimed.append(_format_handoff(updated))

        conn.execute("COMMIT")
        return claimed
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def get_mesh_status() -> dict[str, Any]:
    """Return an overview snapshot of active conversations, locks, messages, and blackboard state."""
    db_path = get_db_path()
    now = utc_now_iso()
    conn = connect()
    try:
        active_convos = list_conversations(hours_back=24)
        active_locks = get_active_focus_locks(include_paused=True)
        total_msgs = conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
        unclaimed_handoffs = conn.execute(
            "SELECT COUNT(*) AS c FROM handoffs WHERE claimed_by_convo_id IS NULL"
        ).fetchone()["c"]
        bb_count = conn.execute(
            "SELECT COUNT(*) AS c FROM blackboard WHERE expires_at IS NULL OR expires_at > ?",
            (now,),
        ).fetchone()["c"]
        touch_cutoff = utc_offset_iso(-30)
        recent_touches = conn.execute(
            "SELECT COUNT(*) AS c FROM file_touches WHERE touched_at >= ?",
            (touch_cutoff,),
        ).fetchone()["c"]
        return {
            "db_path": str(db_path),
            "conversations_24h": len(active_convos),
            "conversations": active_convos,
            "active_focus_locks": active_locks,
            "total_messages": total_msgs,
            "unclaimed_handoffs": unclaimed_handoffs,
            "blackboard_entries": bb_count,
            "recent_file_touches_30m": recent_touches,
        }
    finally:
        conn.close()
