#!/usr/bin/env python3
"""Stdlib tests for consult-cli."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import consult_cli


class ConsultCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        full_argv = ["--root", str(self.root), *argv]
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = consult_cli.main(full_argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def create_item(self, **kwargs: str) -> str:
        args = [
            "--json",
            "create",
            "--no-dispatch",
            "--kind",
            kwargs.get("kind", "review"),
            "--from",
            kwargs.get("from_agent", "codex"),
            "--to",
            kwargs.get("to_agent", "claude"),
            "--title",
            kwargs.get("title", "Test item"),
        ]
        body = kwargs.get("body")
        if body is not None:
            args.extend(["--body", body])
        code, stdout, stderr = self.run_cli(*args)
        self.assertEqual(code, 0, stderr)
        return json.loads(stdout)["item_id"]

    def test_full_baton_flow(self) -> None:
        item_id = self.create_item(body="Initial handoff")

        code, stdout, stderr = self.run_cli("inbox", "--agent", "claude")
        self.assertEqual(code, 0, stderr)
        self.assertIn(item_id, stdout)
        self.assertIn("owner: claude", stdout)

        self.assertEqual(self.run_cli("claim", item_id, "--agent", "claude")[0], 0)
        self.assertEqual(
            self.run_cli(
                "note",
                item_id,
                "--agent",
                "claude",
                "--body",
                "Reviewed the diff.",
            )[0],
            0,
        )
        self.assertEqual(
            self.run_cli(
                "handoff",
                item_id,
                "--no-dispatch",
                "--from",
                "claude",
                "--to",
                "codex",
                "--summary",
                "Back to you.",
            )[0],
            0,
        )

        code, stdout, stderr = self.run_cli("inbox", "--agent", "codex")
        self.assertEqual(code, 0, stderr)
        self.assertIn(item_id, stdout)
        self.assertIn("owner: codex", stdout)

        self.assertEqual(
            self.run_cli("close", item_id, "--agent", "codex", "--summary", "Done.")[0],
            0,
        )

        code, stdout, stderr = self.run_cli("show", item_id)
        self.assertEqual(code, 0, stderr)
        self.assertIn("Status: closed", stdout)
        self.assertIn("Latest summary: Done.", stdout)
        self.assertIn("claude noted", stdout)
        self.assertIn("codex closed", stdout)

    def test_inbox_filters_by_latest_owner_not_requester(self) -> None:
        item_a = self.create_item(title="For Claude")
        item_b = self.create_item(from_agent="claude", to_agent="codex", title="For Codex")

        code, stdout, stderr = self.run_cli("inbox", "--agent", "claude")
        self.assertEqual(code, 0, stderr)
        self.assertIn(item_a, stdout)
        self.assertNotIn(item_b, stdout)

        code, stdout, stderr = self.run_cli("inbox", "--agent", "codex")
        self.assertEqual(code, 0, stderr)
        self.assertIn(item_b, stdout)
        self.assertNotIn(item_a, stdout)

    def test_wrong_owner_cannot_handoff_or_close(self) -> None:
        item_id = self.create_item()

        code, _stdout, stderr = self.run_cli(
            "handoff",
            item_id,
            "--from",
            "codex",
            "--to",
            "mistral",
            "--summary",
            "Nope",
        )
        self.assertEqual(code, 1)
        self.assertIn("currently owned by claude", stderr)

        code, _stdout, stderr = self.run_cli(
            "close", item_id, "--agent", "codex", "--summary", "Nope"
        )
        self.assertEqual(code, 1)
        self.assertIn("currently owned by claude", stderr)

    def test_json_output_is_machine_readable(self) -> None:
        item_id = self.create_item(title="JSON mode")

        code, stdout, stderr = self.run_cli("--json", "show", item_id)
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["state"]["item_id"], item_id)
        self.assertEqual(payload["state"]["current_owner"], "claude")
        self.assertEqual(len(payload["events"]), 2)

        code, stdout, stderr = self.run_cli("--json", "inbox", "--agent", "claude")
        self.assertEqual(code, 0, stderr)
        inbox = json.loads(stdout)
        self.assertEqual(inbox["agent"], "claude")
        self.assertEqual(inbox["items"][0]["item_id"], item_id)

    def test_blank_fields_fail_fast(self) -> None:
        code, _stdout, stderr = self.run_cli(
            "create",
            "--kind",
            "review",
            "--from",
            "codex",
            "--to",
            "claude",
            "--title",
            "   ",
        )
        self.assertEqual(code, 1)
        self.assertIn("title must not be blank", stderr)

        item_id = self.create_item()
        code, _stdout, stderr = self.run_cli(
            "handoff",
            item_id,
            "--from",
            "claude",
            "--to",
            "codex",
            "--summary",
            "   ",
        )
        self.assertEqual(code, 1)
        self.assertIn("summary must not be blank", stderr)

    def test_corrupt_log_fails_clearly(self) -> None:
        item_id = self.create_item()
        path = consult_cli.events_path(self.root, item_id)
        with path.open("a", encoding="utf-8") as handle:
            handle.write("{bad json\n")

        code, _stdout, stderr = self.run_cli("show", item_id)
        self.assertEqual(code, 1)
        self.assertIn("Corrupt event log", stderr)
        self.assertIn("line 3", stderr)


    def test_no_dispatch_flag_prevents_dispatch(self) -> None:
        code, stdout, stderr = self.run_cli(
            "--json",
            "create",
            "--no-dispatch",
            "--kind", "consult",
            "--from", "claude",
            "--to", "codex",
            "--title", "No dispatch test",
        )
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertFalse(payload["dispatched"])

    def test_dispatch_with_agents_config(self) -> None:
        # Write an agents.json that points to a harmless command
        config_path = self.root / "agents.json"
        config_path.write_text(json.dumps({
            "agents": {
                "echo-agent": {"command": ["echo", "dispatched"]},
            }
        }))
        code, stdout, stderr = self.run_cli(
            "--json",
            "create",
            "--kind", "consult",
            "--from", "claude",
            "--to", "echo-agent",
            "--title", "Config dispatch test",
        )
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertTrue(payload["dispatched"])

    def test_dispatch_prompt_contains_item_context(self) -> None:
        prompt = consult_cli.build_dispatch_prompt(
            self.root, "test-id-123", "claude", "codex", "review",
            "Review the auth module", "Check for race conditions",
        )
        self.assertIn("test-id-123", prompt)
        self.assertIn("codex", prompt)
        self.assertIn("claude", prompt)
        self.assertIn("Review the auth module", prompt)
        self.assertIn("Check for race conditions", prompt)
        self.assertIn("--no-dispatch", prompt)
        self.assertIn("close test-id-123 --agent codex --summary \"done\"", prompt)

    def test_wait_flag_blocks_until_dispatch_exits(self) -> None:
        # Use echo as a fast "agent" — it exits immediately
        config_path = self.root / "agents.json"
        config_path.write_text(json.dumps({
            "agents": {
                "echo-agent": {"command": ["echo", "dispatched"]},
            }
        }))
        code, stdout, stderr = self.run_cli(
            "--json",
            "create",
            "--wait",
            "--kind", "consult",
            "--from", "claude",
            "--to", "echo-agent",
            "--title", "Wait test",
        )
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertTrue(payload["dispatched"])
        self.assertEqual(payload["dispatch_exit_code"], 0)
        # final_state should be present since --wait was used
        self.assertIn("final_state", payload)
        self.assertEqual(payload["final_state"]["item_id"], payload["item_id"])

    def test_resolve_agent_command_from_config(self) -> None:
        config_path = self.root / "agents.json"
        config_path.write_text(json.dumps({
            "agents": {
                "custom": {"command": ["/usr/bin/custom-agent", "--flag"]},
            }
        }))
        cmd = consult_cli.resolve_agent_command(self.root, "custom")
        self.assertEqual(cmd, ["/usr/bin/custom-agent", "--flag"])

    def test_resolve_agent_command_builtin_agents(self) -> None:
        claude_cmd = consult_cli.resolve_agent_command(self.root, "claude")
        codex_cmd = consult_cli.resolve_agent_command(self.root, "codex")

        if shutil.which("claude"):
            self.assertEqual(claude_cmd, ["claude", "-p"])
        else:
            self.assertIsNone(claude_cmd)

        if shutil.which("codex"):
            self.assertEqual(codex_cmd, [shutil.which("codex"), "exec"])
        elif consult_cli.DEFAULT_CODEX_PATH.exists():
            self.assertEqual(codex_cmd, [str(consult_cli.DEFAULT_CODEX_PATH), "exec"])
        else:
            self.assertIsNone(codex_cmd)

    def test_resolve_agent_command_unknown_agent_returns_none(self) -> None:
        cmd = consult_cli.resolve_agent_command(self.root, "unknown")
        self.assertIsNone(cmd)

    def test_build_dispatch_command_for_builtin_codex_grants_consult_access(self) -> None:
        cmd = consult_cli.build_dispatch_command(self.root, "codex", Path("/tmp"))
        if cmd is None:
            self.skipTest("codex binary is not available")
        self.assertIn("--sandbox", cmd)
        self.assertIn("workspace-write", cmd)
        self.assertIn("--skip-git-repo-check", cmd)
        self.assertIn("--add-dir", cmd)
        self.assertIn(str(self.root), cmd)
        self.assertIn(str(consult_cli.CONSULT_CLI_PATH.parent), cmd)

    def test_build_dispatch_command_for_builtin_claude_grants_consult_access(self) -> None:
        cmd = consult_cli.build_dispatch_command(self.root, "claude", Path("/tmp"))
        if cmd is None:
            self.skipTest("claude binary is not available")
        self.assertIn("--add-dir", cmd)
        self.assertIn(str(self.root), cmd)
        self.assertIn(str(consult_cli.CONSULT_CLI_PATH.parent), cmd)


if __name__ == "__main__":
    unittest.main()
