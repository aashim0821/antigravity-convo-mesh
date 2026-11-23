---
name: convo-mesh
description: >-
  Coordinate across separate top-level Antigravity conversations and workspaces
  using Convo Mesh: set soft or strict focus locks, send direct/alias/workspace/broadcast
  messages, share persistent facts on the blackboard, detect parallel file edit collisions,
  and create structured session handoffs. Use when the user asks to link conversations,
  stay focused across chats, leave a reminder for another session, share cross-workspace
  context, or hand off work to a new chat.
---

# Convo Mesh Skill

Convo Mesh connects isolated top-level conversations through a local SQLite WAL coordination bus (`~/.gemini/antigravity/convo-mesh/mesh.db`), exposed via the `convo-mesh` MCP server and automated lifecycle hooks.

## Core Identity Rule
Always pass your current **Conversation ID** (found in `<user_information>`) as `conversation_id`, `owner_convo_id`, `sender_convo_id`, `convo_id`, or `from_convo_id` when calling `convo-mesh` MCP tools.

## Workflows

### 1. Registering a Session & Setting Focus Locks
When the user names a session or asks to protect focus across chats:
1. Call `register_convo` with `conversation_id`, a short `alias` (e.g., `"learning"`, `"api-refactor"`), `workspace_path`, and `current_goal`.
2. Call `set_focus_lock`:
   - `mode: "soft"` — Gently reminds the user at the top of replies in other conversations (respects cooldown).
   - `mode: "strict"` — Instructs other conversations not to execute off-topic work until the user returns or explicitly confirms `snooze_focus_lock` / `clear_focus_lock`.
   - Use `allowed_workspaces` for exempt directories (e.g., docs or reference folders) and `duration_minutes` for time-boxed sprints.

### 2. Cross-Conversation Messaging
To send instructions, updates, or reminders to another conversation:
1. Call `list_convos` if you need to discover active conversation IDs, aliases, or workspaces.
2. Call `send_message` with `target_type` set to:
   - `"alias"` (e.g., `target_value: "frontend"`)
   - `"convo"` (exact `conversation_id`)
   - `"workspace"` (path match)
   - `"broadcast"` (`target_value: "*"`)
3. Incoming messages arrive automatically on the recipient's next prompt via `PreInvocation` hook (`[CONVO-MESH INCOMING MESSAGE]`), or manually via `read_inbox`. Call `ack_messages` once handled.

### 3. Shared Blackboard
Use `blackboard_set`, `blackboard_get`, and `blackboard_delete` to store structured JSON facts, architecture decisions, or environment notes across workspaces (e.g., `namespace: "project-x"`, `key: "api_port"`, optional `ttl_minutes`).

### 4. Structured Session Handoffs
Before ending a complex session so the next conversation picks up cleanly:
1. Call `create_handoff` with `summary`, `next_steps`, `key_files`, and `target_type` (`"next_any"`, `"workspace"`, `"alias"`, or `"convo"`).
2. The target conversation automatically claims the handoff once via `SessionStart` / `PreInvocation`.

### 5. Handling `[CONVO-MESH ...]` Hook Injections
- **`[CONVO-MESH STRICT FOCUS LOCK]`**: Stop before doing off-topic work; remind the user of their locked goal and ask whether to return or explicitly run `snooze_focus_lock` / `clear_focus_lock`.
- **`[CONVO-MESH FOCUS REMINDER]`**: Briefly remind the user at the top of your reply and offer to keep helping, snooze, or clear the lock.
- **`[CONVO-MESH COLLISION WARNING]`**: Warn the user before editing files recently modified by another conversation (`get_workspace_activity` shows recent file touches).

## Fallback Without Full Plugin Installation
If the `convo-mesh` MCP server is not enabled in the current environment, use `cli.py` (requires Python 3.10+):
- `python cli.py status`
- `python cli.py focus --goal "Finish module" --mode soft`
- `python cli.py send --type alias --to frontend --body "Schema updated"`
- `python cli.py blackboard --ns global`
