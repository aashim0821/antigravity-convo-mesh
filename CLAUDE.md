# Convo Mesh — Cross-Conversation & Cross-Agent Coordination Protocol

You are connected to **Convo Mesh**, a machine-wide coordination bus linking every **Claude Code** and **Google Antigravity** session across all workspaces via a shared local SQLite WAL store.

## Handling `[CONVO-MESH ...]` System Notices
When lifecycle hooks inject `[CONVO-MESH ...]` notices into your context, follow them strictly:
1. **`[CONVO-MESH STRICT FOCUS LOCK]`**: **Do NOT execute off-topic work.** Remind the user of their locked goal and the owner conversation/workspace, and ask if they want to return to that conversation or explicitly confirm calling `snooze_focus_lock` / `clear_focus_lock`.
2. **`[CONVO-MESH FOCUS REMINDER]`**: At the top of your reply, briefly remind the user that they have an active focus session (naming the goal and owner), and offer to keep helping here, pause it via `snooze_focus_lock`, or end it via `clear_focus_lock`.
3. **`[CONVO-MESH INCOMING MESSAGE]`**: Surface the message clearly to the user, act on it if relevant, and call `ack_messages` (or `send_message` with `reply_to`) when handled.
4. **`[CONVO-MESH HANDOFF]` & `[CONVO-MESH WORKSPACE CONTINUITY]`**: Use the handoff summary, previous session's last prompt/goal, next steps, and recently modified files as starting context (especially when the user switches between Antigravity and Claude Code after hitting a quota limit) without asking the user to re-explain.
5. **`[CONVO-MESH COLLISION WARNING]`**: Warn the user before modifying overlapping files recently touched by another active Claude Code or Antigravity conversation, and coordinate via `send_message` if needed.

## Using `convo-mesh` MCP Tools
- **Identity & Focus:** Call `register_convo` when the user names a session (passing `client: "claude-code"`). Call `set_focus_lock` when they ask to stay focused (`soft` for gentle reminders, `strict` to block off-topic derailments).
- **Cross-Agent Messaging (`Claude Code <-> Antigravity`):** Use `send_message` with `target_type: "client"` and `target_value: "antigravity"` (or `"claude-code"`, `"alias"`, `"workspace"`, `"broadcast"`) to send instructions directly between Claude Code and Antigravity. Use `blackboard_set` / `blackboard_get` for shared facts, `list_convos` to inspect active sessions across both agents, and `create_handoff` before ending a session.
