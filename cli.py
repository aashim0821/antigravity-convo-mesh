"""Command-line interface for managing Convo Mesh state locally."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from core import db


def _print_json(data: object) -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(data, indent=2, ensure_ascii=False))


def cmd_status(_args: argparse.Namespace) -> int:
    status = db.get_mesh_status()
    _print_json(status)
    return 0


def cmd_focus(args: argparse.Namespace) -> int:
    owner = db.resolve_convo_id(args.convo_id)
    allowed = [a.strip() for a in args.allowed.split(",") if a.strip()] if args.allowed else None
    lock = db.set_focus_lock(
        owner_convo_id=owner,
        goal=args.goal,
        mode=args.mode,
        duration_minutes=args.minutes,
        allowed_workspaces=allowed,
        reason=args.reason or "",
    )
    _print_json(lock)
    return 0


def cmd_clear_focus(args: argparse.Namespace) -> int:
    count = db.clear_focus_lock(lock_id=args.lock_id, owner_convo_id=args.convo_id)
    _print_json({"cleared_count": count})
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    sender = db.resolve_convo_id(args.from_convo)
    msg = db.send_message(
        sender_convo_id=sender,
        target_type=args.type,
        target_value=args.to,
        body=args.body,
        priority=args.priority,
        subject=args.subject or "",
    )
    _print_json(msg)
    return 0


def cmd_blackboard(args: argparse.Namespace) -> int:
    if args.delete:
        if not args.key:
            raise SystemExit("--key is required with --delete")
        deleted = db.blackboard_delete(key=args.key, namespace=args.ns)
        _print_json({"deleted": deleted})
        return 0

    if args.set_json is not None:
        if not args.key:
            raise SystemExit("--key is required with --set-json")
        value = json.loads(args.set_json)
        entry = db.blackboard_set(key=args.key, value=value, namespace=args.ns, author_convo_id="cli")
        _print_json(entry)
        return 0

    data = db.blackboard_get(key=args.key, namespace=args.ns)
    _print_json(data)
    return 0


def cmd_reset_db(args: argparse.Namespace) -> int:
    if not args.yes:
        print("Refusing to reset mesh.db without --yes flag.", file=sys.stderr)
        return 1
    db_path = db.get_db_path()
    removed = []
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()
            removed.append(str(p))
    conn = db.connect()
    conn.close()
    _print_json({"reset": True, "removed": removed, "db_path": str(db_path)})
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    res = db.watch_for_wakeup(
        client=args.client,
        convo_id=args.convo_id or "",
        task_id=args.task_id,
        timeout_seconds=args.timeout,
        poll_interval=args.interval,
    )
    _print_json(res)
    return 0


def cmd_relay_start(args: argparse.Namespace) -> int:
    task = db.start_relay_task(
        goal=args.goal,
        slug=args.slug,
        repo_path=args.repo or "",
        initiator_convo_id="cli",
        assign_to_client=args.to,
        max_hops=args.max_hops,
        initial_brief=args.brief or "",
    )
    _print_json(task)
    return 0


def cmd_relay_turn(args: argparse.Namespace) -> int:
    task = db.relay_submit_turn(
        task_id=args.task_id,
        summary=args.summary,
        next_status=args.status,
        actor_convo_id="cli",
        actor_client=args.actor_client or "",
    )
    _print_json(task)
    return 0


def cmd_relay_list(args: argparse.Namespace) -> int:
    tasks = db.list_relay_tasks(task_id=args.task_id, status=args.status)
    _print_json(tasks)
    return 0


def cmd_pause(args: argparse.Namespace) -> int:
    paused = db.pause_relay_tasks(task_id=args.task_id)
    _print_json({"paused_count": len(paused), "tasks": paused})
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    task = db.resume_relay_task(
        task_id=args.task_id,
        assign_to_client=args.to,
        add_hops=args.add_hops,
        note=args.note,
    )
    _print_json(task)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="convo-mesh", description="Convo Mesh CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", help="Show active conversations, focus locks, and mesh counts")
    p_status.set_defaults(func=cmd_status)

    p_focus = sub.add_parser("focus", help="Set an active focus lock")
    p_focus.add_argument("--goal", required=True, help="Focus goal description")
    p_focus.add_argument("--mode", choices=["soft", "strict"], default="soft")
    p_focus.add_argument("--convo-id", dest="convo_id", default=None, help="Owner conversation ID")
    p_focus.add_argument("--minutes", type=float, default=None, help="Optional expiry in minutes")
    p_focus.add_argument("--allowed", default="", help="Comma-separated allowed workspace paths")
    p_focus.add_argument("--reason", default="", help="Optional reason")
    p_focus.set_defaults(func=cmd_focus)

    p_clear = sub.add_parser("clear-focus", help="Clear active focus locks")
    p_clear.add_argument("--lock-id", type=int, default=None)
    p_clear.add_argument("--convo-id", dest="convo_id", default=None)
    p_clear.set_defaults(func=cmd_clear_focus)

    p_send = sub.add_parser("send", help="Send a cross-conversation or cross-agent message")
    p_send.add_argument("--to", required=True, help="Target convo ID, alias, workspace, client ('claude-code'/'antigravity'), or '*'")
    p_send.add_argument("--body", required=True, help="Message body")
    p_send.add_argument("--type", choices=["convo", "alias", "workspace", "client", "broadcast"], default="alias")
    p_send.add_argument("--from", dest="from_convo", default="cli")
    p_send.add_argument("--priority", choices=["info", "normal", "reminder", "urgent"], default="normal")
    p_send.add_argument("--subject", default="")
    p_send.set_defaults(func=cmd_send)

    p_bb = sub.add_parser("blackboard", help="Read, write, or delete blackboard entries")
    p_bb.add_argument("--ns", default="global", help="Namespace (use '*' to list all)")
    p_bb.add_argument("--key", default=None)
    p_bb.add_argument("--set-json", dest="set_json", default=None, help="JSON value to store")
    p_bb.add_argument("--delete", action="store_true")
    p_bb.set_defaults(func=cmd_blackboard)

    p_reset = sub.add_parser("reset-db", help="Reset the Convo Mesh SQLite database")
    p_reset.add_argument("--yes", action="store_true", help="Confirm destructive reset")
    p_reset.set_defaults(func=cmd_reset_db)

    p_watch = sub.add_parser("watch", help="Block at 0 LLM tokens until a relay turn or unread message arrives")
    p_watch.add_argument("--client", required=True, choices=["antigravity", "claude-code"], help="Waiting agent client")
    p_watch.add_argument("--convo-id", dest="convo_id", default="", help="Optional conversation/session ID for inbox messages")
    p_watch.add_argument("--task-id", dest="task_id", default=None, help="Optional specific relay task_id to watch")
    p_watch.add_argument("--timeout", type=float, default=1800.0, help="Timeout in seconds (default 1800)")
    p_watch.add_argument("--interval", type=float, default=0.5, help="SQLite poll interval in seconds (default 0.5)")
    p_watch.set_defaults(func=cmd_watch)

    p_rstart = sub.add_parser("relay-start", help="Start a bounded autonomous relay task")
    p_rstart.add_argument("--goal", required=True)
    p_rstart.add_argument("--slug", default=None)
    p_rstart.add_argument("--repo", default="")
    p_rstart.add_argument("--to", default="claude-code", choices=["claude-code", "antigravity"])
    p_rstart.add_argument("--max-hops", dest="max_hops", type=int, default=4)
    p_rstart.add_argument("--brief", default="")
    p_rstart.set_defaults(func=cmd_relay_start)

    p_rturn = sub.add_parser("relay-turn", help="Submit a turn on a relay task")
    p_rturn.add_argument("--task-id", dest="task_id", required=True)
    p_rturn.add_argument("--summary", required=True)
    p_rturn.add_argument("--status", default="review", choices=["in_progress", "review", "changes_requested", "done", "paused"])
    p_rturn.add_argument("--actor-client", dest="actor_client", default="")
    p_rturn.set_defaults(func=cmd_relay_turn)

    p_rlist = sub.add_parser("relay-list", help="List relay tasks")
    p_rlist.add_argument("--task-id", dest="task_id", default=None)
    p_rlist.add_argument("--status", default=None)
    p_rlist.set_defaults(func=cmd_relay_list)

    p_pause = sub.add_parser("pause", help="Pause one or all active relay tasks immediately")
    p_pause.add_argument("--task-id", dest="task_id", default=None)
    p_pause.set_defaults(func=cmd_pause)

    p_resume = sub.add_parser("resume", help="Resume a paused or hop-halted relay task")
    p_resume.add_argument("--task-id", dest="task_id", required=True)
    p_resume.add_argument("--to", required=True, choices=["claude-code", "antigravity"])
    p_resume.add_argument("--add-hops", dest="add_hops", type=int, default=2)
    p_resume.add_argument("--note", default="Resumed via CLI")
    p_resume.set_defaults(func=cmd_resume)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
