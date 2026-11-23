"""Cross-platform automated tests for the Convo Mesh plugin."""

from __future__ import annotations

import json
import os
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

        # Allowed workspace should NOT trigger reminder
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

        # Non-allowed workspace on invocation 1 triggers soft reminder
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

        # Invocation 2 in same turn must NOT spam
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

        # Immediate next turn within soft cooldown also avoids duplicate soft spam
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

        # Upgrade to strict -> invocation 1 MUST enforce strict lock
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

        # Snooze lock -> no longer blocks Convo B
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

        # Convo B receives all three once
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

        # Convo B receives {} on next turn (zero duplicates)
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

        # Convo A must NOT receive its own broadcast
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

        # Brand-new Convo C claims it on SessionStart
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

        # Convo D on SessionStart does NOT get the already-claimed handoff
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
        # Insert an already-expired key directly to verify expiry pruning
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
                        "target_type": "broadcast",
                        "target_value": "*",
                        "body": "Mesh online",
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
                "params": {"name": "list_convos", "arguments": {"hours_back": 24}},
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


if __name__ == "__main__":
    unittest.main()
