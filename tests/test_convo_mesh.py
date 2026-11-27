"""Cross-platform automated tests for the Convo Mesh plugin (Antigravity + Claude Code)."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from core import db
from hooks import mesh_hook

PYTHON_EXE = sys.executable


class ConvoMeshTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test_mesh.db"
        self.prev_env = os.environ.get("CONVO_MESH_DB")
        os.environ["CONVO_MESH_DB"] = str(self.db_path)

    def tearDown(self) -> None:
        if self.prev_env is None:
            os.environ.pop("CONVO_MESH_DB", None)
        else:
            os.environ["CONVO_MESH_DB"] = self.prev_env
        self.tmpdir.cleanup()

    def _run_hook_subproc(self, raw_stdin: str) -> tuple[int, str, str, float]:
        env = os.environ.copy()
        env["CONVO_MESH_DB"] = str(self.db_path)
        start = time.perf_counter()
        proc = subprocess.run(
            [PYTHON_EXE, str(PLUGIN_ROOT / "hooks" / "mesh_hook.py")],
            input=raw_stdin,
            text=True,
            capture_output=True,
            cwd=str(PLUGIN_ROOT),
            env=env,
            timeout=10,
        )
        elapsed = time.perf_counter() - start
        return proc.returncode, proc.stdout, proc.stderr, elapsed

    def test_01_focus_guard_soft_strict_allowed_snooze_cooldown(self) -> None:
        db.upsert_conversation("convo-a", workspace_path=r"d:\Study", alias="learning")
        lock = db.set_focus_lock(
            owner_convo_id="convo-a",
            goal="Master Antigravity",
            mode="soft",
            allowed_workspaces=[r"d:\AllowedDocs"],
            reason="Stay on task",
        )
        self.assertEqual(lock["mode"], "soft")

        allowed_res = mesh_hook.handle_event(
            {
                "hookEventName": "PreInvocation",
                "conversationId": "convo-allowed",
                "workspacePaths": [r"d:\AllowedDocs\sub"],
                "invocationNum": 1,
                "lastUserInput": "Check docs",
            }
        )
        self.assertEqual(allowed_res, {})

        res1 = mesh_hook.handle_event(
            {
                "hookEventName": "PreInvocation",
                "conversationId": "convo-b",
                "workspacePaths": [r"d:\Distraction"],
                "invocationNum": 1,
                "lastUserInput": "Build something else",
            }
        )
        text1 = res1["injectSteps"][0]["systemMessage"]["systemMessage"]
        self.assertIn("[CONVO-MESH FOCUS REMINDER]", text1)
        self.assertIn("Master Antigravity", text1)

        res2 = mesh_hook.handle_event(
            {
                "hookEventName": "PreInvocation",
                "conversationId": "convo-b",
                "workspacePaths": [r"d:\Distraction"],
                "invocationNum": 2,
                "lastUserInput": "Build something else",
            }
        )
        self.assertEqual(res2, {})

        res_cooldown = mesh_hook.handle_event(
            {
                "hookEventName": "PreInvocation",
                "conversationId": "convo-b",
                "workspacePaths": [r"d:\Distraction"],
                "invocationNum": 1,
                "lastUserInput": "Another prompt",
            }
        )
        self.assertEqual(res_cooldown, {})

        db.set_focus_lock(
            owner_convo_id="convo-a",
            goal="Master Antigravity",
            mode="strict",
        )
        res_strict = mesh_hook.handle_event(
            {
                "hookEventName": "PreInvocation",
                "conversationId": "convo-b",
                "workspacePaths": [r"d:\Distraction"],
                "invocationNum": 1,
                "lastUserInput": "Do unrelated work",
            }
        )
        strict_text = res_strict["injectSteps"][0]["systemMessage"]["systemMessage"]
        self.assertIn("[CONVO-MESH STRICT FOCUS LOCK]", strict_text)

        db.snooze_focus_lock(minutes=15)
        res_snoozed = mesh_hook.handle_event(
            {
                "hookEventName": "PreInvocation",
                "conversationId": "convo-b",
                "workspacePaths": [r"d:\Distraction"],
                "invocationNum": 1,
                "lastUserInput": "Work after snooze",
            }
        )
        self.assertEqual(res_snoozed, {})

    def test_02_messaging_alias_workspace_broadcast_and_dedupe(self) -> None:
        db.upsert_conversation("convo-a", workspace_path=r"d:\Study", alias="learning")
        db.upsert_conversation("convo-b", workspace_path=r"d:\Frontend", alias="frontend")

        db.send_message(
            sender_convo_id="convo-a",
            target_type="alias",
            target_value="frontend",
            body="Finish dashboard layout",
            priority="urgent",
            subject="Sprint Priority",
        )
        db.send_message(
            sender_convo_id="convo-a",
            target_type="broadcast",
            target_value="*",
            body="Standup sync at 6 PM",
            priority="info",
        )
        db.send_message(
            sender_convo_id="convo-a",
            target_type="workspace",
            target_value=r"d:\Frontend",
            body="Use Next.js App Router",
        )

        res_b1 = mesh_hook.handle_event(
            {
                "hookEventName": "PreInvocation",
                "conversationId": "convo-b",
                "workspacePaths": [r"d:\Frontend"],
                "invocationNum": 1,
                "lastUserInput": "Start coding",
            }
        )
        text_b = res_b1["injectSteps"][0]["systemMessage"]["systemMessage"]
        self.assertEqual(text_b.count("[CONVO-MESH INCOMING MESSAGE]"), 3)
        self.assertIn("Finish dashboard layout", text_b)
        self.assertIn("Standup sync at 6 PM", text_b)
        self.assertIn("Use Next.js App Router", text_b)

        res_b2 = mesh_hook.handle_event(
            {
                "hookEventName": "PreInvocation",
                "conversationId": "convo-b",
                "workspacePaths": [r"d:\Frontend"],
                "invocationNum": 1,
                "lastUserInput": "Continue coding",
            }
        )
        self.assertEqual(res_b2, {})

        res_a = mesh_hook.handle_event(
            {
                "hookEventName": "PreInvocation",
                "conversationId": "convo-a",
                "workspacePaths": [r"d:\Study"],
                "invocationNum": 1,
                "lastUserInput": "Check messages",
            }
        )
        self.assertEqual(res_a, {})

    def test_03_handoff_packet_claiming(self) -> None:
        db.upsert_conversation("convo-a", workspace_path=r"d:\Backend", alias="api-arch")
        db.create_handoff(
            from_convo_id="convo-a",
            summary="Completed Prisma schema migration",
            next_steps=["Run seed script", "Verify auth middleware"],
            key_files=[r"d:\Backend\prisma\schema.prisma"],
            target_type="next_any",
        )

        res_c = mesh_hook.handle_event(
            {
                "hookEventName": "SessionStart",
                "conversationId": "convo-c",
                "workspacePaths": [r"d:\Backend"],
            }
        )
        text_c = res_c["injectSteps"][0]["systemMessage"]["systemMessage"]
        self.assertIn("[CONVO-MESH HANDOFF]", text_c)
        self.assertIn("Completed Prisma schema migration", text_c)
        self.assertIn("Run seed script", text_c)

        res_d = mesh_hook.handle_event(
            {
                "hookEventName": "SessionStart",
                "conversationId": "convo-d",
                "workspacePaths": [r"d:\Backend"],
            }
        )
        if res_d:
            text_d = res_d["injectSteps"][0]["systemMessage"]["systemMessage"]
            self.assertNotIn("[CONVO-MESH HANDOFF]", text_d)

    def test_04_file_workspace_collision_guard(self) -> None:
        db.upsert_conversation("convo-a", workspace_path=r"d:\Project", alias="frontend-worker")
        mesh_hook.handle_event(
            {
                "hookEventName": "PostToolUse",
                "conversationId": "convo-a",
                "workspacePaths": [r"d:\Project"],
                "toolCall": {
                    "name": "edit_file",
                    "args": {"Path": r"d:\Project\app.ts"},
                },
            }
        )

        res_b = mesh_hook.handle_event(
            {
                "hookEventName": "PreInvocation",
                "conversationId": "convo-b",
                "workspacePaths": [r"d:\Project"],
                "invocationNum": 1,
                "lastUserInput": "Edit app.ts",
            }
        )
        text_b = res_b["injectSteps"][0]["systemMessage"]["systemMessage"]
        self.assertIn("[CONVO-MESH COLLISION WARNING]", text_b)
        self.assertIn("d:/project/app.ts", text_b)
        self.assertIn("@frontend-worker", text_b)

    def test_05_shared_blackboard_set_get_namespace_delete_expiry(self) -> None:
        db.blackboard_set(
            "api_port",
            {"port": 8080, "tls": False},
            namespace="backend",
            author_convo_id="convo-a",
        )
        db.blackboard_set(
            "active_theme",
            "dark-zinc",
            namespace="global",
            author_convo_id="convo-a",
        )
        conn = db.connect()
        try:
            conn.execute(
                """
                INSERT INTO blackboard (namespace, key, value_json, author_convo_id, author_alias, expires_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("global", "stale_token", '"old"', "convo-a", "", db.utc_offset_iso(-5), db.utc_now_iso()),
            )
        finally:
            conn.close()

        item = db.blackboard_get(key="api_port", namespace="backend")
        self.assertEqual(item["value"], {"port": 8080, "tls": False})

        global_items = db.blackboard_get(namespace="global")
        self.assertEqual(len(global_items), 1)
        self.assertEqual(global_items[0]["key"], "active_theme")

        all_items = db.blackboard_get(namespace="*")
        self.assertEqual(len(all_items), 2)

        self.assertTrue(db.blackboard_delete("api_port", namespace="backend"))
        self.assertIsNone(db.blackboard_get(key="api_port", namespace="backend"))

    def test_06_stdio_mcp_server_jsonrpc_e2e(self) -> None:
        env = os.environ.copy()
        env["CONVO_MESH_DB"] = str(self.db_path)

        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "register_convo",
                    "arguments": {
                        "conversation_id": "mcp-convo-1",
                        "alias": "planner",
                        "client": "claude-code",
                        "workspace_path": "/workspace/demo",
                        "current_goal": "Verify MCP server",
                    },
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "set_focus_lock",
                    "arguments": {
                        "owner_convo_id": "mcp-convo-1",
                        "goal": "Ship Convo Mesh",
                        "mode": "soft",
                    },
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "send_message",
                    "arguments": {
                        "sender_convo_id": "mcp-convo-1",
                        "target_type": "client",
                        "target_value": "antigravity",
                        "body": "Mesh online across agents",
                    },
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "blackboard_set",
                    "arguments": {"key": "status", "value": "green", "namespace": "mesh"},
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {
                    "name": "blackboard_get",
                    "arguments": {"key": "status", "namespace": "mesh"},
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 8,
                "method": "tools/call",
                "params": {"name": "list_convos", "arguments": {"hours_back": 24, "client": "claude-code"}},
            },
            {"jsonrpc": "2.0", "id": 9, "method": "resources/list", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "resources/read",
                "params": {"uri": "convo-mesh://status"},
            },
        ]

        stdin_payload = "\n".join(json.dumps(r) for r in requests) + "\n"
        proc = subprocess.run(
            [PYTHON_EXE, "-u", str(PLUGIN_ROOT / "mcp" / "server.py")],
            input=stdin_payload,
            text=True,
            capture_output=True,
            cwd=str(PLUGIN_ROOT),
            env=env,
            timeout=10,
        )
        self.assertEqual(proc.returncode, 0, msg=f"stderr: {proc.stderr}")
        self.assertEqual(proc.stderr.strip(), "")

        responses = [json.loads(line) for line in proc.stdout.strip().splitlines() if line.strip()]
        by_id = {r["id"]: r for r in responses}
        self.assertEqual(len(by_id), 10)
        self.assertEqual(by_id[1]["result"]["serverInfo"]["name"], "convo-mesh")

        tools = by_id[2]["result"]["tools"]
        self.assertEqual(len(tools), 14)

        for call_id in (3, 4, 5, 6, 7, 8):
            self.assertFalse(by_id[call_id]["result"]["isError"], msg=f"Call {call_id} failed: {by_id[call_id]}")

        bb_val = json.loads(by_id[7]["result"]["content"][0]["text"])
        self.assertEqual(bb_val["value"], "green")

        convos = json.loads(by_id[8]["result"]["content"][0]["text"])
        self.assertEqual(convos[0]["alias"], "planner")
        self.assertEqual(convos[0]["client"], "claude-code")

        uris = {r["uri"] for r in by_id[9]["result"]["resources"]}
        self.assertEqual(uris, {"convo-mesh://status", "convo-mesh://blackboard"})

        status_text = by_id[10]["result"]["contents"][0]["text"]
        status_json = json.loads(status_text)
        self.assertEqual(status_json["conversations_24h"], 1)
        self.assertEqual(status_json["total_messages"], 1)

    def test_07_hook_fail_open_on_malformed_stdin(self) -> None:
        code, stdout, _stderr, _elapsed = self._run_hook_subproc("{not valid json!!!")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.strip()), {})

    def test_08_hook_subprocess_latency(self) -> None:
        payload = json.dumps(
            {
                "hookEventName": "PreInvocation",
                "conversationId": "latency-check",
                "workspacePaths": ["/workspace/demo"],
                "invocationNum": 1,
                "lastUserInput": "Ping",
            }
        )
        code, stdout, stderr, elapsed = self._run_hook_subproc(payload)
        self.assertEqual(code, 0, msg=stderr)
        self.assertEqual(json.loads(stdout.strip()), {})
        self.assertLess(elapsed, 1.0, msg=f"Hook took too long: {elapsed:.3f}s")

    def test_09_posix_workspace_collision_detection(self) -> None:
        db.upsert_conversation("posix-a", workspace_path="/Users/dev/project", alias="backend-dev")
        mesh_hook.handle_event(
            {
                "hookEventName": "PostToolUse",
                "conversationId": "posix-a",
                "workspacePaths": ["/Users/dev/project"],
                "toolCall": {
                    "name": "write_file",
                    "args": {"path": "/Users/dev/project/src/app.py"},
                },
            }
        )

        res_b = mesh_hook.handle_event(
            {
                "hookEventName": "PreInvocation",
                "conversationId": "posix-b",
                "workspacePaths": ["/Users/dev/project/"],
                "invocationNum": 1,
                "lastUserInput": "Update app.py",
            }
        )
        text_b = res_b["injectSteps"][0]["systemMessage"]["systemMessage"]
        self.assertIn("[CONVO-MESH COLLISION WARNING]", text_b)
        self.assertIn("/users/dev/project/src/app.py", text_b)
        self.assertIn("@backend-dev", text_b)

    def test_10_claude_code_hooks_cross_agent_bridge_and_quota_continuity(self) -> None:
        # 1. Antigravity session works in /workspace/saas, edits a file, sets a prompt, and sends a message to client:claude-code
        db.upsert_conversation(
            "agy-session-1",
            workspace_path="/workspace/saas",
            alias="agy-architect",
            goal="Implement billing webhook",
            last_user_input="Finish Stripe webhook signature check",
            client="antigravity",
        )
        db.record_file_touch(
            "agy-session-1",
            "/workspace/saas/src/webhook.ts",
            workspace_path="/workspace/saas",
            tool_name="edit_file",
        )
        db.send_message(
            sender_convo_id="agy-session-1",
            target_type="client",
            target_value="claude-code",
            body="Antigravity quota reached; finish unit tests for webhook.ts",
            priority="urgent",
        )

        # 2. User opens Claude Code in /workspace/saas -> SessionStart fires
        claude_start = mesh_hook.handle_event(
            {
                "hook_event_name": "SessionStart",
                "session_id": "claude-sess-1",
                "cwd": "/workspace/saas",
            }
        )
        self.assertIn("hookSpecificOutput", claude_start)
        self.assertEqual(claude_start["hookSpecificOutput"]["hookEventName"], "SessionStart")
        ctx = claude_start["hookSpecificOutput"]["additionalContext"]
        self.assertIn("[CONVO-MESH INCOMING MESSAGE]", ctx)
        self.assertIn("Antigravity quota reached; finish unit tests for webhook.ts", ctx)
        self.assertIn("[CONVO-MESH WORKSPACE CONTINUITY]", ctx)
        self.assertIn("Finish Stripe webhook signature check", ctx)
        self.assertIn("/workspace/saas/src/webhook.ts", ctx)

        # 3. Claude Code edits a file via PostToolUse (Write/Edit) and sends message back to client:antigravity
        mesh_hook.handle_event(
            {
                "hook_event_name": "PostToolUse",
                "session_id": "claude-sess-1",
                "cwd": "/workspace/saas",
                "tool_name": "Edit",
                "tool_input": {"file_path": "/workspace/saas/tests/webhook.test.ts"},
            }
        )
        db.send_message(
            sender_convo_id="claude-sess-1",
            target_type="client",
            target_value="antigravity",
            body="Webhook tests passing in Claude Code",
        )

        # 4. Antigravity session resumes -> receives Claude's message AND collision warning on /workspace/saas
        agy_resume = mesh_hook.handle_event(
            {
                "hookEventName": "PreInvocation",
                "conversationId": "agy-session-1",
                "workspacePaths": ["/workspace/saas"],
                "invocationNum": 1,
                "lastUserInput": "Check status",
            }
        )
        agy_text = agy_resume["injectSteps"][0]["systemMessage"]["systemMessage"]
        self.assertIn("[CONVO-MESH INCOMING MESSAGE]", agy_text)
        self.assertIn("Webhook tests passing in Claude Code", agy_text)
        self.assertIn("[CONVO-MESH COLLISION WARNING]", agy_text)
        self.assertIn("[claude-code]", agy_text)
        self.assertIn("/workspace/saas/tests/webhook.test.ts", agy_text)

    def test_11_legacy_v1_schema_migration(self) -> None:
        # Simulate a v1.0 database without `client` column or `client` in CHECK constraints
        raw_conn = sqlite3.connect(str(self.db_path))
        raw_conn.executescript(
            """
            CREATE TABLE conversations (
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
            INSERT INTO conversations VALUES (
                'legacy-1', 'old-chat', 'd:/Legacy', 'Test migration', 'active', 'hi',
                '2026-11-23T00:00:00.000000Z', '2026-11-25T00:00:00.000000Z', '2026-11-25T00:00:00.000000Z'
            );
            CREATE TABLE messages (
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
            """
        )
        raw_conn.close()

        # Connecting via db.connect() must auto-migrate v1.0 -> v1.1 without losing legacy-1
        msg = db.send_message(
            sender_convo_id="legacy-1",
            target_type="client",
            target_value="claude-code",
            body="Migrated message to Claude",
        )
        self.assertEqual(msg["target_type"], "client")
        convos = db.list_conversations(hours_back=9999)
        self.assertEqual(len(convos), 1)
        self.assertEqual(convos[0]["conversation_id"], "legacy-1")
        self.assertEqual(convos[0]["client"], "antigravity")

    def test_12_utf8_arabic_emoji_and_lone_surrogate(self) -> None:
        text = "العربية 🚀"
        dirty = text + " \udc9d"
        clean = text + " ?"

        # db.py: every string parameter (and JSON column) tolerates a lone surrogate
        row = db.upsert_conversation("utf8-db", workspace_path="/ws/utf8", last_user_input=dirty)
        self.assertEqual(row["last_user_input"], clean)
        msg = db.send_message("utf8-db", "broadcast", "*", body=dirty, subject=dirty)
        self.assertEqual((msg["body"], msg["subject"]), (clean, clean))
        self.assertEqual(db.blackboard_set("k", {"v": [dirty]})["value"], {"v": [clean]})
        handoff = db.create_handoff("utf8-db", summary=dirty, next_steps=[dirty], key_files=[dirty])
        self.assertEqual((handoff["summary"], handoff["next_steps"], handoff["key_files"]), (clean, [clean], [clean]))
        lock = db.set_focus_lock("utf8-db", goal=dirty, allowed_workspaces=[dirty])
        self.assertEqual((lock["goal"], lock["allowed_workspaces"]), (clean, [clean]))

        # mesh_hook.py and mcp/server.py: raw UTF-8 bytes over pipes that default to cp1252
        env = os.environ.copy()
        env.pop("PYTHONUTF8", None)
        env["PYTHONIOENCODING"] = "cp1252"

        def run(script: str, payload: str) -> str:
            proc = subprocess.run(
                [PYTHON_EXE, "-X", "utf8=0", str(PLUGIN_ROOT / script)],
                input=payload.encode("utf-8"),
                capture_output=True,
                cwd=str(PLUGIN_ROOT),
                env=env,
                timeout=10,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            return proc.stdout.decode("utf-8")

        # "\udc9d" travels as a JSON escape, exactly how a host would serialize it
        prompt = json.dumps(text, ensure_ascii=False)[:-1] + ' \\udc9d"'
        out = run(
            "hooks/mesh_hook.py",
            '{"hook_event_name": "UserPromptSubmit", "session_id": "utf8-hook", '
            f'"cwd": "/ws/utf8", "prompt": {prompt}}}',
        )
        context = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn(clean, context)
        stored = {c["conversation_id"]: c for c in db.list_conversations()}
        self.assertEqual(stored["utf8-hook"]["last_user_input"], clean)

        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "blackboard_set", "arguments": {"key": "mcp", "value": text}},
        }
        resp = json.loads(run("mcp/server.py", json.dumps(request, ensure_ascii=False) + "\n"))
        self.assertFalse(resp["result"]["isError"], msg=resp)
        self.assertEqual(db.blackboard_get("mcp")["value"], text)


if __name__ == "__main__":
    unittest.main()
