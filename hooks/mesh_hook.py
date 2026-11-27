"""Fail-open dual-host (Antigravity + Claude Code) lifecycle hook handler for Convo Mesh."""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from core import db


def _detect_host_and_normalize(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize either an Antigravity or Claude Code hook stdin payload."""
    is_claude = ("hook_event_name" in payload or "session_id" in payload) and ("hookEventName" not in payload)
    if is_claude:
        raw_event = str(payload.get("hook_event_name") or "").strip()
        convo_id = str(payload.get("session_id") or "").strip()
        workspace = str(payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or "").strip()

        if raw_event == "SessionStart":
            event = "SessionStart"
        elif raw_event in ("UserPromptSubmit", "PreInvocation"):
            event = "PreInvocation"
        elif raw_event == "PostToolUse":
            event = "PostToolUse"
        elif raw_event == "Stop":
            event = "Stop"
        else:
            event = raw_event

        last_input = payload.get("prompt") if "prompt" in payload else payload.get("lastUserInput")
        tool_name = str(payload.get("tool_name") or "").strip()
        tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
        file_path = str(
            tool_input.get("file_path") or tool_input.get("path") or tool_input.get("Path") or ""
        ).strip()

        return {
            "host": "claude-code",
            "raw_event": raw_event or event,
            "event": event,
            "convo_id": convo_id,
            "workspace": workspace,
            "invocation_num": 1,
            "last_input": last_input,
            "tool_name": tool_name,
            "file_path": file_path,
        }

    # Antigravity payload
    raw_event = str(payload.get("hookEventName") or "").strip()
    convo_id = str(payload.get("conversationId") or "").strip()
    ws_paths = payload.get("workspacePaths")
    if isinstance(ws_paths, list) and ws_paths and str(ws_paths[0] or "").strip():
        workspace = str(ws_paths[0]).strip()
    else:
        workspace = str(payload.get("cwd") or "").strip()

    raw_inv = payload.get("invocationNum")
    try:
        invocation_num = int(raw_inv) if raw_inv is not None else 1
    except (TypeError, ValueError):
        invocation_num = 1

    tool_call = payload.get("toolCall") if isinstance(payload.get("toolCall"), dict) else {}
    tool_name = str(tool_call.get("name") or "").strip()
    args = tool_call.get("args") if isinstance(tool_call.get("args"), dict) else {}
    file_path = str(args.get("Path") or args.get("path") or args.get("file_path") or "").strip()

    return {
        "host": "antigravity",
        "raw_event": raw_event,
        "event": raw_event,
        "convo_id": convo_id,
        "workspace": workspace,
        "invocation_num": invocation_num,
        "last_input": payload.get("lastUserInput"),
        "tool_name": tool_name,
        "file_path": file_path,
    }


def _label_sender(alias: str, convo_id: str, workspace: str, client: str = "") -> str:
    parts = []
    if client:
        parts.append(f"[{client}]")
    if alias:
        parts.append(f"@{alias}")
    if convo_id:
        parts.append(f"id={convo_id}")
    if workspace:
        parts.append(f"ws={workspace}")
    return " | ".join(parts) if parts else "unknown"


def _format_focus_blocks(conflicts: list[dict[str, Any]]) -> list[str]:
    blocks: list[str] = []
    for lock in conflicts:
        owner = _label_sender(
            lock.get("owner_alias", ""),
            lock.get("owner_convo_id", ""),
            lock.get("owner_workspace", ""),
        )
        reason_line = f"\nReason: {lock['reason']}" if lock.get("reason") else ""
        expires_line = f"\nExpires: {lock['expires_at']}" if lock.get("expires_at") else ""
        if lock.get("mode") == "strict":
            blocks.append(
                "[CONVO-MESH STRICT FOCUS LOCK]\n"
                f"Lock ID: {lock['id']} | Owner: {owner}\n"
                f"Locked Goal: {lock['goal']}{reason_line}{expires_line}\n"
                "INSTRUCTION: DO NOT execute off-topic work right now. Remind the user of their locked goal "
                "and ask whether they want to return to the owner conversation or explicitly call "
                "`snooze_focus_lock` / `clear_focus_lock` (you may only proceed after they confirm)."
            )
        else:
            blocks.append(
                "[CONVO-MESH FOCUS REMINDER]\n"
                f"Lock ID: {lock['id']} | Owner: {owner}\n"
                f"Active Focus Goal: {lock['goal']}{reason_line}{expires_line}\n"
                "INSTRUCTION: Briefly remind the user at the start of your reply that they have an active focus "
                "session on that goal, and offer to keep helping here, pause with `snooze_focus_lock`, "
                "or end it with `clear_focus_lock`."
            )
    return blocks


def _format_message_blocks(messages: list[dict[str, Any]]) -> list[str]:
    blocks: list[str] = []
    for msg in messages:
        sender = _label_sender(
            msg.get("sender_alias", ""),
            msg.get("sender_convo_id", ""),
            msg.get("sender_workspace", ""),
        )
        subj_line = f"\nSubject: {msg['subject']}" if msg.get("subject") else ""
        reply_line = f" | Reply-To: #{msg['reply_to']}" if msg.get("reply_to") else ""
        blocks.append(
            "[CONVO-MESH INCOMING MESSAGE]\n"
            f"Message ID: {msg['id']} | Priority: {str(msg.get('priority', 'normal')).upper()}{reply_line}\n"
            f"From: {sender} -> Target: {msg.get('target_type')}:{msg.get('target_value')}{subj_line}\n"
            f"Body: {msg['body']}\n"
            "INSTRUCTION: Surface this message to the user/act on it as appropriate, and use `ack_messages` "
            "or `send_message` (with `reply_to`) if a response is needed."
        )
    return blocks


def _format_handoff_blocks(handoffs: list[dict[str, Any]]) -> list[str]:
    blocks: list[str] = []
    for h in handoffs:
        sender = _label_sender(
            h.get("from_alias", ""),
            h.get("from_convo_id", ""),
            h.get("from_workspace", ""),
        )
        steps = h.get("next_steps") or []
        files = h.get("key_files") or []
        steps_line = f"\nNext Steps: {'; '.join(steps)}" if steps else ""
        files_line = f"\nKey Files: {', '.join(files)}" if files else ""
        blocks.append(
            "[CONVO-MESH HANDOFF]\n"
            f"Handoff ID: {h['id']} | From: {sender}\n"
            f"Summary: {h['summary']}{steps_line}{files_line}"
        )
    return blocks


def _format_continuity_block(continuity: dict[str, Any] | None) -> list[str]:
    if not continuity:
        return []
    prev = continuity["previous_convo"]
    recent_files = continuity.get("recent_files") or []
    who = _label_sender(
        prev.get("alias") or "",
        prev.get("conversation_id") or "",
        prev.get("workspace_path") or "",
        prev.get("client") or "antigravity",
    )
    goal_line = f"\nGoal: {prev['current_goal']}" if prev.get("current_goal") else ""
    prompt_line = f"\nLast User Prompt: {prev['last_user_input']}" if prev.get("last_user_input") else ""
    files_line = f"\nRecently Modified Files: {', '.join(recent_files)}" if recent_files else ""
    return [
        "[CONVO-MESH WORKSPACE CONTINUITY]\n"
        f"Previous In-Flight Session in This Workspace: {who} (last active: {prev.get('last_seen_at')}){goal_line}{prompt_line}{files_line}\n"
        "INSTRUCTION: If the user is continuing work or switched agents/models after a quota limit, use this context seamlessly."
    ]


def _format_collision_blocks(collisions: list[dict[str, Any]]) -> list[str]:
    if not collisions:
        return []
    lines = ["[CONVO-MESH COLLISION WARNING]"]
    for item in collisions:
        client_tag = f"[{item.get('client', 'antigravity')}] " if item.get("client") else ""
        who = f"@{item['alias']} ({item['convo_id']})" if item.get("alias") else item["convo_id"]
        files = ", ".join(item.get("files", [])[:5])
        if len(item.get("files", [])) > 5:
            files += f" (+{len(item['files']) - 5} more)"
        lines.append(
            f"- {client_tag}Convo {who} recently modified files in this workspace (last: {item['last_touched_at']}): {files}"
        )
    lines.append("INSTRUCTION: Warn the user before overwriting overlapping files or coordinate via `send_message`.")
    return ["\n".join(lines)]


def _format_active_convos_summary(other_convos: list[dict[str, Any]]) -> list[str]:
    if not other_convos:
        return []
    lines = ["[CONVO-MESH ACTIVE CONVERSATIONS]"]
    for c in other_convos:
        client_tag = f"[{c.get('client', 'antigravity')}] "
        label = f"@{c['alias']}" if c.get("alias") else c["conversation_id"]
        goal = f" | goal: {c['current_goal']}" if c.get("current_goal") else ""
        ws = c.get("workspace_path") or "no-workspace"
        lines.append(f"- {client_tag}{label} ({c['conversation_id']}) [{ws}]{goal}")
    return ["\n".join(lines)]


def _build_response(
    blocks: list[str],
    host: str,
    raw_event: str,
    include_termination: bool = False,
) -> dict[str, Any]:
    if not blocks:
        return {}
    text = "\n\n".join(blocks)
    if host == "claude-code":
        return {
            "hookSpecificOutput": {
                "hookEventName": raw_event,
                "additionalContext": text,
            }
        }
    out: dict[str, Any] = {
        "injectSteps": [
            {
                "systemMessage": {
                    "systemMessage": text,
                }
            }
        ]
    }
    if include_termination:
        out["terminationBehavior"] = ""
    return out


def handle_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Handle one Antigravity or Claude Code hook lifecycle event."""
    norm = _detect_host_and_normalize(payload)
    host = norm["host"]
    raw_event = norm["raw_event"]
    event = norm["event"]
    convo_id = norm["convo_id"]
    workspace = norm["workspace"]

    if event == "SessionStart":
        if convo_id:
            db.upsert_conversation(convo_id, workspace_path=workspace, status="active", client=host)
        handoffs = db.claim_matching_handoffs(convo_id, workspace, client=host) if convo_id else []
        conflicts = db.check_focus_conflict(convo_id, workspace, invocation_num=1) if convo_id else []
        messages = db.pop_undelivered_messages(convo_id, workspace, client=host) if convo_id else []
        continuity = db.get_workspace_continuity(convo_id, workspace) if (convo_id and workspace) else None
        other_convos = [
            c for c in db.list_conversations(hours_back=4) if c["conversation_id"] != convo_id
        ][:5]

        blocks: list[str] = []
        blocks.extend(_format_focus_blocks(conflicts))
        blocks.extend(_format_handoff_blocks(handoffs))
        blocks.extend(_format_message_blocks(messages))
        blocks.extend(_format_continuity_block(continuity))
        blocks.extend(_format_active_convos_summary(other_convos))
        return _build_response(blocks, host=host, raw_event=raw_event, include_termination=False)

    if event == "PreInvocation":
        last_input = norm["last_input"]
        invocation_num = norm["invocation_num"]

        if convo_id:
            db.upsert_conversation(
                convo_id,
                workspace_path=workspace,
                last_user_input=str(last_input) if last_input is not None else None,
                status="active",
                mark_turn=(invocation_num == 1),
                client=host,
            )

        blocks = []
        if convo_id:
            messages = db.pop_undelivered_messages(convo_id, workspace, client=host)
            if invocation_num == 1:
                conflicts = db.check_focus_conflict(convo_id, workspace, invocation_num=1)
                handoffs = db.claim_matching_handoffs(convo_id, workspace, client=host)
                collisions = db.check_workspace_collisions(convo_id, workspace)
                blocks.extend(_format_focus_blocks(conflicts))
                blocks.extend(_format_handoff_blocks(handoffs))
                blocks.extend(_format_collision_blocks(collisions))
            blocks.extend(_format_message_blocks(messages))
        return _build_response(blocks, host=host, raw_event=raw_event, include_termination=True)

    if event == "PostToolUse":
        tool_name = norm["tool_name"]
        file_path = norm["file_path"]
        if convo_id and tool_name in ("write_file", "edit_file", "Write", "Edit", "MultiEdit") and file_path:
            db.record_file_touch(
                convo_id,
                file_path,
                workspace_path=workspace,
                tool_name=tool_name,
            )
        return {}

    if event == "Stop":
        if convo_id:
            db.upsert_conversation(convo_id, workspace_path=workspace, status="active", client=host)
        return {}

    return {}


def main() -> int:
    try:
        # Hosts send UTF-8; Windows pipes would otherwise decode with the ANSI code page (cp1252).
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw and raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
        result = handle_event(payload)
        sys.stdout.write(json.dumps(result) + "\n")
        sys.stdout.flush()
    except Exception:
        try:
            log_path = db.get_error_log_path()
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(f"[{db.utc_now_iso()}] Hook error:\n{traceback.format_exc()}\n")
        except Exception:
            pass
        sys.stdout.write("{}\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
