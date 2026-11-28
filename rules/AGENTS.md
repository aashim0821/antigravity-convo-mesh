# Convo Mesh — Cross-Conversation & Cross-Agent Coordination Protocol

You are connected to **Convo Mesh**, a machine-wide coordination bus linking every **Google Antigravity** and **Claude Code** session across all workspaces via a shared local SQLite WAL store.

## Handling `[CONVO-MESH ...]` System Notices
When lifecycle hooks inject `[CONVO-MESH ...]` notices into your context, follow them strictly:
1. **`[CONVO-MESH STRICT FOCUS LOCK]`**: **Do NOT execute off-topic work.** Remind the user of their locked goal and the owner conversation/workspace, and ask if they want to return to that conversation or explicitly confirm calling `snooze_focus_lock` / `clear_focus_lock`.
2. **`[CONVO-MESH FOCUS REMINDER]`**: At the top of your reply, briefly remind the user that they have an active focus session (naming the goal and owner), and offer to keep helping here, pause it via `snooze_focus_lock`, or end it via `clear_focus_lock`.
3. **`[CONVO-MESH INCOMING MESSAGE]`**: Surface the message clearly to the user, act on it if relevant, and call `ack_messages` (or `send_message` with `reply_to`) when handled.
4. **`[CONVO-MESH HANDOFF]` & `[CONVO-MESH WORKSPACE CONTINUITY]`**: Use the handoff summary, previous session's last prompt/goal, next steps, and recently modified files as starting context (especially when the user switches between Antigravity and Claude Code after hitting a quota limit) without asking the user to re-explain.
5. **`[CONVO-MESH COLLISION WARNING]`**: Warn the user before modifying overlapping files recently touched by another active Antigravity or Claude Code conversation, and coordinate via `send_message` if needed.

## Using `convo-mesh` MCP Tools
- **Always pass your `Conversation ID` (or Claude Code `session_id`)** as `conversation_id` / `owner_convo_id` / `sender_convo_id` / `convo_id` / `from_convo_id`.
- **Identity & Focus:** Call `register_convo` when the user names a session (e.g. `"learning"`, `"backend"`, `"docs"`). Call `set_focus_lock` when they ask to stay focused (`soft` for gentle reminders, `strict` to block off-topic derailments).
- **Cross-Agent Messaging & State:** Use `send_message` to leave instructions/reminders for another conversation (`convo`, `alias`, `workspace`, `client` such as `"claude-code"` or `"antigravity"`, or `broadcast`). Use `blackboard_set` / `blackboard_get` for cross-workspace facts and decisions, `list_convos` to inspect active sessions across both agents, and `create_handoff` before ending a complex session.
- **Autonomous Peer Relay (`Mode B`):** Use `start_relay_task`, `relay_submit_turn`, `list_relay_tasks`, and `control_relay` for bounded (`max_hops=4`) autonomous back-and-forth sprints with isolated `<repo>/.worktrees/<slug>` Git worktrees, sleeping between turns at zero tokens via background `cli.py watch --client <client> --task-id <task_id>`.
