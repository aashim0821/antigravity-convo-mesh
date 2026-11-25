---
name: convo-mesh
description: >-
  Coordinate across separate top-level Google Antigravity and Claude Code conversations
  and workspaces using Convo Mesh: set soft or strict focus locks, send direct/alias/workspace/client/broadcast
  messages between Antigravity and Claude Code, share persistent facts on the blackboard,
  detect parallel file edit collisions, and automatically recover workspace context on quota
  failover. Use when the user asks to link conversations, message Claude from Antigravity
  (or vice versa), stay focused across chats, share cross-workspace context, or hand off work.
---

# Convo Mesh Skill (Antigravity + Claude Code)

Convo Mesh connects isolated top-level **Antigravity** and **Claude Code** sessions through a shared local SQLite WAL coordination bus (`~/.gemini/antigravity/convo-mesh/mesh.db`), exposed via the `convo-mesh` MCP server and dual-protocol lifecycle hooks.

## Core Identity Rule
Always pass your current **Conversation ID** (from `<user_information>` in Antigravity, or `session_id` in Claude Code) as `conversation_id`, `owner_convo_id`, `sender_convo_id`, `convo_id`, or `from_convo_id` when calling `convo-mesh` MCP tools.

## Workflows

### 1. Registering a Session & Setting Focus Locks
When the user names a session or asks to protect focus across chats:
1. Call `register_convo` with `conversation_id`, a short `alias` (e.g., `"learning"`, `"api-refactor"`), `client` (`"antigravity"` or `"claude-code"`), `workspace_path`, and `current_goal`.
2. Call `set_focus_lock`:
   - `mode: "soft"` — Gently reminds the user at the top of replies in other Antigravity or Claude Code conversations (respects cooldown).
   - `mode: "strict"` — Instructs other conversations not to execute off-topic work until the user returns or explicitly confirms `snooze_focus_lock` / `clear_focus_lock`.
   - Use `allowed_workspaces` for exempt directories and `duration_minutes` for time-boxed sprints.

### 2. Cross-Conversation & Cross-Agent Messaging (`Antigravity <-> Claude Code`)
To send instructions, updates, or reminders to another conversation or agent:
1. Call `list_convos` if you need to discover active conversation IDs, aliases, client hosts, or workspaces.
2. Call `send_message` with `target_type` set to:
   - `"client"` (`target_value: "claude-code"` or `"antigravity"`) — delivers directly to the next active session in that agent!
   - `"alias"` (e.g., `target_value: "frontend"`)
   - `"convo"` (exact `conversation_id`)
   - `"workspace"` (path match)
   - `"broadcast"` (`target_value: "*"`)
3. Incoming messages arrive automatically on the recipient's next prompt via lifecycle hook (`PreInvocation` in Antigravity, `UserPromptSubmit` in Claude Code), or manually via `read_inbox`. Call `ack_messages` once handled.

### 3. Automatic Quota Failover & Session Handoffs
- **Automatic In-Flight Workspace Continuity:** Every turn checkpoints the active session's `client`, `workspace_path`, `current_goal`, `last_user_input`, and edited files (`file_touches`). If one agent hits a quota limit mid-task and the user opens the other agent in the same workspace, `SessionStart` automatically injects `[CONVO-MESH WORKSPACE CONTINUITY]` with the last prompt and touched files.
- **Explicit Handoffs:** Call `create_handoff` with `summary`, `next_steps`, `key_files`, and `target_type` (`"next_any"`, `"client"`, `"workspace"`, `"alias"`, or `"convo"`).

### 4. Shared Blackboard
Use `blackboard_set`, `blackboard_get`, and `blackboard_delete` to store structured JSON facts, architecture decisions, or environment notes across workspaces and across both Antigravity and Claude Code.

### 5. Handling `[CONVO-MESH ...]` Hook Injections
- **`[CONVO-MESH STRICT FOCUS LOCK]`**: Stop before doing off-topic work; remind the user of their locked goal and ask whether to return or explicitly run `snooze_focus_lock` / `clear_focus_lock`.
- **`[CONVO-MESH FOCUS REMINDER]`**: Briefly remind the user at the top of your reply and offer to keep helping, snooze, or clear the lock.
- **`[CONVO-MESH WORKSPACE CONTINUITY]` / `[CONVO-MESH HANDOFF]`**: Pick up seamlessly from the previous session's state without asking the user to repeat context.
- **`[CONVO-MESH COLLISION WARNING]`**: Warn the user before editing files recently modified by another conversation (`get_workspace_activity` shows recent file touches).

## Fallback Without Full Plugin Installation
If the `convo-mesh` MCP server is not enabled in the current environment, use `cli.py` (requires Python 3.10+):
- `python cli.py status`
- `python cli.py focus --goal "Finish module" --mode soft`
- `python cli.py send --type client --to claude-code --body "Continue tests from Antigravity"`
- `python cli.py blackboard --ns global`
