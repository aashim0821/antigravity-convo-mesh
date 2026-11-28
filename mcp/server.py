"""Zero-dependency Stdio JSON-RPC 2.0 MCP server for Convo Mesh (Antigravity + Claude Code)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from core import db


TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "name": "register_convo",
        "description": "Register or update this conversation's alias, client ('antigravity' or 'claude-code'), workspace, current goal, and status in the global Convo Mesh registry.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string", "description": "Your Conversation ID (or Claude Code session_id)."},
                "alias": {"type": "string", "description": "Short memorable name (e.g. 'learning', 'backend-api', 'frontend')."},
                "client": {"type": "string", "description": "Agent client host (e.g. 'antigravity', 'claude-code')."},
                "workspace_path": {"type": "string", "description": "Primary workspace folder path."},
                "current_goal": {"type": "string", "description": "What this conversation is currently working on."},
                "status": {"type": "string", "enum": ["active", "paused", "completed"], "description": "Conversation status."},
            },
            "required": ["conversation_id"],
        },
    },
    {
        "name": "list_convos",
        "description": "List active conversations across all Antigravity and Claude Code workspaces, including their aliases, client hosts, goals, and last turn timestamps.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hours_back": {"type": "number", "description": "Lookback window in hours (default 24)."},
                "status": {"type": "string", "description": "Optional status filter (e.g. 'active')."},
                "workspace": {"type": "string", "description": "Optional workspace path substring filter."},
                "client": {"type": "string", "description": "Optional client filter (e.g. 'antigravity' or 'claude-code')."},
            },
        },
    },
    {
        "name": "set_focus_lock",
        "description": "Activate a cross-conversation & cross-agent focus lock. Other Antigravity and Claude Code sessions will remind the user (soft) or refuse off-topic derailments (strict).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "owner_convo_id": {"type": "string", "description": "Conversation ID owning this focus session."},
                "goal": {"type": "string", "description": "The focus goal to protect (e.g. 'Finish CSIT114 UML report')."},
                "mode": {"type": "string", "enum": ["soft", "strict"], "description": "'soft' gently nudges; 'strict' instructs other convos not to run off-topic work."},
                "duration_minutes": {"type": "number", "description": "Optional auto-expiry duration in minutes."},
                "allowed_workspaces": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional workspace paths exempt from focus reminders.",
                },
                "reason": {"type": "string", "description": "Optional context or reason for the focus lock."},
            },
            "required": ["goal"],
        },
    },
    {
        "name": "get_focus_status",
        "description": "Return all currently active focus locks (and optionally snoozed/paused ones).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_paused": {"type": "boolean", "description": "Include temporarily snoozed focus locks (default true)."},
            },
        },
    },
    {
        "name": "snooze_focus_lock",
        "description": "Temporarily pause active focus lock enforcement for N minutes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "minutes": {"type": "number", "description": "Snooze duration in minutes (default 15)."},
                "lock_id": {"type": "integer", "description": "Specific lock ID to snooze (omit to snooze all active locks)."},
            },
        },
    },
    {
        "name": "clear_focus_lock",
        "description": "Deactivate active focus lock(s) by lock_id, owner_convo_id, or all active locks.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lock_id": {"type": "integer", "description": "Specific lock ID to clear."},
                "owner_convo_id": {"type": "string", "description": "Clear active locks owned by this conversation ID."},
            },
        },
    },
    {
        "name": "send_message",
        "description": "Send a message from this conversation to a specific convo ID, alias, workspace, agent client ('claude-code' or 'antigravity'), or broadcast to all conversations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sender_convo_id": {"type": "string", "description": "Sending Conversation ID."},
                "target_type": {
                    "type": "string",
                    "enum": ["convo", "alias", "workspace", "client", "broadcast"],
                    "description": "How to address recipients ('client' targets 'claude-code' or 'antigravity').",
                },
                "target_value": {
                    "type": "string",
                    "description": "Target convo ID, alias name, workspace path, client name ('claude-code'/'antigravity'), or '*' for broadcast.",
                },
                "body": {"type": "string", "description": "Message content."},
                "priority": {"type": "string", "enum": ["info", "normal", "reminder", "urgent"], "description": "Message priority (default 'normal')."},
                "subject": {"type": "string", "description": "Optional short subject line."},
                "reply_to": {"type": "integer", "description": "Optional message ID being replied to."},
                "ttl_minutes": {"type": "number", "description": "Optional message expiry in minutes."},
            },
            "required": ["target_type", "body"],
        },
    },
    {
        "name": "read_inbox",
        "description": "Read messages addressed to this conversation (direct, by alias, by workspace, by client, or broadcast).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "convo_id": {"type": "string", "description": "Recipient Conversation ID."},
                "include_acknowledged": {"type": "boolean", "description": "Include already-acknowledged messages (default false)."},
                "limit": {"type": "integer", "description": "Maximum messages to return (default 25)."},
            },
        },
    },
    {
        "name": "ack_messages",
        "description": "Acknowledge/mark read incoming messages for this conversation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "convo_id": {"type": "string", "description": "Recipient Conversation ID."},
                "message_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Specific message IDs to acknowledge (omit to acknowledge all unread messages).",
                },
            },
        },
    },
    {
        "name": "blackboard_set",
        "description": "Store a shared JSON key-value fact or decision accessible across every Antigravity and Claude Code conversation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Blackboard key name."},
                "value": {"description": "Any JSON-serializable value (string, number, boolean, array, object)."},
                "namespace": {"type": "string", "description": "Namespace such as 'global', 'api', or 'study' (default 'global')."},
                "author_convo_id": {"type": "string", "description": "Author Conversation ID."},
                "ttl_minutes": {"type": "number", "description": "Optional expiry in minutes."},
            },
            "required": ["key", "value"],
        },
    },
    {
        "name": "blackboard_get",
        "description": "Read one key or list all keys in a namespace (use namespace='*' to list across all namespaces).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Specific key to retrieve (omit to list all keys in namespace)."},
                "namespace": {"type": "string", "description": "Namespace (default 'global'; '*' reads all namespaces)."},
            },
        },
    },
    {
        "name": "blackboard_delete",
        "description": "Delete a key from the shared blackboard.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Blackboard key name to delete."},
                "namespace": {"type": "string", "description": "Namespace (default 'global')."},
            },
            "required": ["key"],
        },
    },
    {
        "name": "create_handoff",
        "description": "Create a structured handoff packet so the next conversation (or a specific alias, workspace, or client like 'claude-code') automatically picks up your summary, next steps, and key files.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_convo_id": {"type": "string", "description": "Source Conversation ID."},
                "summary": {"type": "string", "description": "Concise summary of current state and decisions made."},
                "next_steps": {"type": "array", "items": {"type": "string"}, "description": "Ordered next actions."},
                "key_files": {"type": "array", "items": {"type": "string"}, "description": "Relevant file paths."},
                "target_type": {
                    "type": "string",
                    "enum": ["next_any", "convo", "alias", "workspace", "client"],
                    "description": "Who should claim this handoff (default 'next_any'; use 'client' to target 'claude-code' or 'antigravity').",
                },
                "target_value": {"type": "string", "description": "Target convo ID, alias, workspace path, client name, or '*' for next_any."},
            },
            "required": ["summary"],
        },
    },
    {
        "name": "get_workspace_activity",
        "description": "Inspect recent file writes/edits across Antigravity and Claude Code conversations to check for parallel edits or collisions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace": {"type": "string", "description": "Optional workspace path filter."},
                "minutes_back": {"type": "number", "description": "Lookback window in minutes (default 30)."},
            },
        },
    },
    {
        "name": "start_relay_task",
        "description": "Start a bounded autonomous peer-relay task between Antigravity and Claude Code with SQLite hop-cap protection (default max_hops=4) and an optional isolated Git worktree (<repo>/.worktrees/<slug> on branch relay/<slug>).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "Clear task objective and acceptance criteria."},
                "slug": {"type": "string", "description": "Optional short kebab-case slug for the worktree/branch."},
                "repo_path": {"type": "string", "description": "Optional git repository path to create an isolated worktree in."},
                "initiator_convo_id": {"type": "string", "description": "Initiating Conversation ID."},
                "assign_to_client": {
                    "type": "string",
                    "enum": ["claude-code", "antigravity"],
                    "description": "Which agent takes the first turn (default 'claude-code').",
                },
                "max_hops": {"type": "integer", "description": "Hard autonomous turn limit before auto-halting for human review (default 4, max 12)."},
                "initial_brief": {"type": "string", "description": "Optional detailed handoff brief for hop 0."},
            },
            "required": ["goal"],
        },
    },
    {
        "name": "relay_submit_turn",
        "description": "Submit your completed turn on a relay task and pass the baton to the peer agent (or mark 'done'). SQLite automatically halts at max_hops to prevent infinite loops.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Relay task ID (e.g. 'relay-...')."},
                "summary": {"type": "string", "description": "Concise summary of changes made, test/QA results, and what the peer should do next."},
                "next_status": {
                    "type": "string",
                    "enum": ["in_progress", "review", "changes_requested", "done", "paused"],
                    "description": "Next task state ('review' after coding, 'changes_requested' if QA fails, 'done' when verified complete).",
                },
                "next_client": {
                    "type": "string",
                    "enum": ["claude-code", "antigravity"],
                    "description": "Optional explicit next client (defaults to the opposite peer).",
                },
                "actor_convo_id": {"type": "string", "description": "Your Conversation/Session ID."},
                "actor_client": {
                    "type": "string",
                    "enum": ["claude-code", "antigravity"],
                    "description": "Your client identity ('claude-code' or 'antigravity').",
                },
            },
            "required": ["task_id", "summary"],
        },
    },
    {
        "name": "list_relay_tasks",
        "description": "Inspect one or more autonomous peer-relay tasks, including worktree path, hop_count/max_hops, active_turn_client, and turn history.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Optional exact relay task_id."},
                "status": {"type": "string", "description": "Optional status filter."},
                "active_turn_client": {"type": "string", "description": "Optional filter ('claude-code', 'antigravity', 'human')."},
                "limit": {"type": "integer", "description": "Max tasks to return (default 20)."},
            },
        },
    },
    {
        "name": "control_relay",
        "description": "Pause active relay tasks (kill-switch), resume a paused/halted task with extra hops, or clean up a clean relay Git worktree.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["pause", "resume", "cleanup_worktree"],
                    "description": "Control operation to perform.",
                },
                "task_id": {"type": "string", "description": "Target relay task_id (optional for 'pause' to pause all active tasks)."},
                "assign_to_client": {
                    "type": "string",
                    "enum": ["claude-code", "antigravity"],
                    "description": "Target agent when action='resume'.",
                },
                "add_hops": {"type": "integer", "description": "Additional hops to grant when action='resume' (default 2)."},
                "note": {"type": "string", "description": "Optional note recorded on resume."},
                "delete_branch": {"type": "boolean", "description": "Also delete merged branch when action='cleanup_worktree' (default false)."},
            },
            "required": ["action"],
        },
    },
]


def _call_tool(name: str, args: dict[str, Any]) -> Any:
    if name == "register_convo":
        return db.upsert_conversation(
            convo_id=args["conversation_id"],
            workspace_path=args.get("workspace_path") or "",
            alias=args.get("alias"),
            goal=args.get("current_goal"),
            status=args.get("status"),
            client=args.get("client"),
        )
    if name == "list_convos":
        return db.list_conversations(
            hours_back=float(args.get("hours_back", 24.0)),
            status=args.get("status"),
            workspace=args.get("workspace"),
            client=args.get("client"),
        )
    if name == "set_focus_lock":
        owner = db.resolve_convo_id(args.get("owner_convo_id"))
        return db.set_focus_lock(
            owner_convo_id=owner,
            goal=args["goal"],
            mode=args.get("mode", "soft"),
            duration_minutes=args.get("duration_minutes"),
            allowed_workspaces=args.get("allowed_workspaces"),
            reason=args.get("reason", ""),
        )
    if name == "get_focus_status":
        include_paused = bool(args.get("include_paused", True))
        return db.get_active_focus_locks(include_paused=include_paused)
    if name == "snooze_focus_lock":
        return db.snooze_focus_lock(
            minutes=float(args.get("minutes", 15.0)),
            lock_id=args.get("lock_id"),
        )
    if name == "clear_focus_lock":
        cleared = db.clear_focus_lock(
            lock_id=args.get("lock_id"),
            owner_convo_id=args.get("owner_convo_id"),
        )
        return {"cleared_count": cleared}
    if name == "send_message":
        sender = db.resolve_convo_id(args.get("sender_convo_id"))
        return db.send_message(
            sender_convo_id=sender,
            target_type=args["target_type"],
            target_value=args.get("target_value", ""),
            body=args["body"],
            priority=args.get("priority", "normal"),
            subject=args.get("subject", ""),
            reply_to=args.get("reply_to"),
            ttl_minutes=args.get("ttl_minutes"),
        )
    if name == "read_inbox":
        cid = db.resolve_convo_id(args.get("convo_id"))
        return db.read_inbox(
            convo_id=cid,
            include_acknowledged=bool(args.get("include_acknowledged", False)),
            limit=int(args.get("limit", 25)),
        )
    if name == "ack_messages":
        cid = db.resolve_convo_id(args.get("convo_id"))
        count = db.ack_messages(
            convo_id=cid,
            message_ids=args.get("message_ids"),
        )
        return {"acknowledged_count": count}
    if name == "blackboard_set":
        if "value" not in args:
            raise ValueError("value is required")
        return db.blackboard_set(
            key=args["key"],
            value=args["value"],
            namespace=args.get("namespace", "global"),
            author_convo_id=args.get("author_convo_id", ""),
            ttl_minutes=args.get("ttl_minutes"),
        )
    if name == "blackboard_get":
        return db.blackboard_get(
            key=args.get("key"),
            namespace=args.get("namespace", "global"),
        )
    if name == "blackboard_delete":
        deleted = db.blackboard_delete(
            key=args["key"],
            namespace=args.get("namespace", "global"),
        )
        return {"deleted": deleted}
    if name == "create_handoff":
        from_id = db.resolve_convo_id(args.get("from_convo_id"))
        return db.create_handoff(
            from_convo_id=from_id,
            summary=args["summary"],
            next_steps=args.get("next_steps"),
            key_files=args.get("key_files"),
            target_type=args.get("target_type", "next_any"),
            target_value=args.get("target_value", "*"),
        )
    if name == "get_workspace_activity":
        return db.get_workspace_activity(
            workspace=args.get("workspace"),
            minutes_back=float(args.get("minutes_back", 30.0)),
        )
    if name == "start_relay_task":
        return db.start_relay_task(
            goal=args["goal"],
            slug=args.get("slug"),
            repo_path=args.get("repo_path") or "",
            initiator_convo_id=args.get("initiator_convo_id") or "",
            assign_to_client=args.get("assign_to_client", "claude-code"),
            max_hops=int(args.get("max_hops", 4)),
            initial_brief=args.get("initial_brief") or "",
        )
    if name == "relay_submit_turn":
        return db.relay_submit_turn(
            task_id=args["task_id"],
            summary=args["summary"],
            next_status=args.get("next_status", "review"),
            next_client=args.get("next_client"),
            actor_convo_id=args.get("actor_convo_id") or "",
            actor_client=args.get("actor_client") or "",
        )
    if name == "list_relay_tasks":
        return db.list_relay_tasks(
            task_id=args.get("task_id"),
            status=args.get("status"),
            active_turn_client=args.get("active_turn_client"),
            limit=int(args.get("limit", 20)),
        )
    if name == "control_relay":
        act = args["action"]
        if act == "pause":
            return {"paused_tasks": db.pause_relay_tasks(task_id=args.get("task_id"))}
        if act == "resume":
            if not args.get("task_id") or not args.get("assign_to_client"):
                raise ValueError("task_id and assign_to_client are required for action='resume'")
            return db.resume_relay_task(
                task_id=args["task_id"],
                assign_to_client=args["assign_to_client"],
                add_hops=int(args.get("add_hops", 2)),
                note=args.get("note") or "Resumed by human",
            )
        if act == "cleanup_worktree":
            if not args.get("task_id"):
                raise ValueError("task_id is required for action='cleanup_worktree'")
            return db.cleanup_relay_worktree(
                task_id=args["task_id"],
                delete_branch=bool(args.get("delete_branch", False)),
            )
        raise ValueError(f"Unsupported control_relay action: {act}")
    raise ValueError(f"Unknown tool: {name}")


def _make_response(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _make_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def handle_request(req: dict[str, Any]) -> dict[str, Any] | None:
    method = req.get("method")
    req_id = req.get("id")
    params = req.get("params") or {}

    if method == "notifications/initialized" or (isinstance(method, str) and method.startswith("notifications/")):
        return None

    if req_id is None:
        return None

    if method == "initialize":
        proto = params.get("protocolVersion") or "2024-11-05"
        return _make_response(
            req_id,
            {
                "protocolVersion": proto,
                "capabilities": {
                    "tools": {},
                    "resources": {},
                },
                "serverInfo": {
                    "name": "convo-mesh",
                    "version": "1.2.0",
                },
            },
        )

    if method == "ping":
        return _make_response(req_id, {})

    if method == "tools/list":
        return _make_response(req_id, {"tools": TOOLS_SCHEMA})

    if method == "tools/call":
        tool_name = str(params.get("name") or "")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return _make_response(
                req_id,
                {"content": [{"type": "text", "text": "Error: arguments must be a JSON object"}], "isError": True},
            )
        try:
            res = _call_tool(tool_name, args)
            return _make_response(
                req_id,
                {"content": [{"type": "text", "text": json.dumps(res, indent=2)}], "isError": False},
            )
        except Exception as exc:
            return _make_response(
                req_id,
                {"content": [{"type": "text", "text": f"Error: {exc}"}], "isError": True},
            )

    if method == "resources/list":
        return _make_response(
            req_id,
            {
                "resources": [
                    {
                        "uri": "convo-mesh://status",
                        "name": "Convo Mesh Status",
                        "description": "Live overview of active conversations, focus locks, messages, handoffs, and blackboard entries.",
                        "mimeType": "application/json",
                    },
                    {
                        "uri": "convo-mesh://blackboard",
                        "name": "Convo Mesh Blackboard",
                        "description": "All active shared blackboard key-value entries across all namespaces.",
                        "mimeType": "application/json",
                    },
                ]
            },
        )

    if method == "resources/read":
        uri = str(params.get("uri") or "")
        if uri == "convo-mesh://status":
            data = db.get_mesh_status()
        elif uri == "convo-mesh://blackboard":
            data = db.blackboard_get(namespace="*")
        else:
            return _make_error(req_id, -32602, f"Unknown resource URI: {uri}")
        return _make_response(
            req_id,
            {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps(data, indent=2),
                    }
                ]
            },
        )

    return _make_error(req_id, -32601, f"Method not found: {method}")


def main() -> int:
    # MCP stdio is UTF-8; Windows pipes would otherwise use the ANSI code page (cp1252).
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for line in sys.stdin:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            req = json.loads(stripped)
        except json.JSONDecodeError as exc:
            sys.stderr.write(f"convo-mesh mcp invalid JSON: {exc}\n")
            sys.stderr.flush()
            continue

        if not isinstance(req, dict):
            continue

        try:
            resp = handle_request(req)
        except Exception as exc:
            sys.stderr.write(f"convo-mesh mcp unexpected error: {exc}\n")
            sys.stderr.flush()
            req_id = req.get("id")
            resp = _make_error(req_id, -32603, str(exc)) if req_id is not None else None

        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
