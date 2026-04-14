#!/usr/bin/env python3
"""Append-only baton board for local agent consultation."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


VALID_KINDS = ("task", "consult", "review")


class ConsultError(Exception):
    """Base exception for user-facing CLI failures."""


class ConsultDataError(ConsultError):
    """Raised when the append-only log cannot be replayed safely."""


@dataclass(slots=True)
class ConsultState:
    item_id: str
    kind: str | None = None
    title: str | None = None
    body: str | None = None
    requester: str | None = None
    current_owner: str | None = None
    status: str = "open"
    note_count: int = 0
    latest_summary: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    claim_count: int = 0


@dataclass(slots=True)
class DispatchResult:
    dispatched: bool
    pid: int | None = None
    log_path: str | None = None
    exit_code: int | None = None


def default_root() -> Path:
    raw = os.environ.get("CONSULT_ROOT")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".consult-cli"


def root_path(args: argparse.Namespace) -> Path:
    if getattr(args, "root", None):
        return Path(args.root).expanduser()
    return default_root()


def items_root(root: Path) -> Path:
    return root / "items"


def item_dir(root: Path, item_id: str) -> Path:
    return items_root(root) / item_id


def events_path(root: Path, item_id: str) -> Path:
    return item_dir(root, item_id) / "events.jsonl"


def ensure_item_storage(root: Path, item_id: str) -> Path:
    directory = item_dir(root, item_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def iso_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def require_nonblank(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ConsultError(f"{field_name} must not be blank.")
    return normalized


def append_event(
    root: Path,
    item_id: str,
    event_type: str,
    actor: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    ensure_item_storage(root, item_id)
    event = {
        "event_id": str(uuid4()),
        "item_id": item_id,
        "event_type": event_type,
        "timestamp": iso_now(),
        "actor": actor,
        "payload": payload,
    }
    path = events_path(root, item_id)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, separators=(",", ":")))
        handle.write("\n")
    return event


def load_events(root: Path, item_id: str) -> list[dict[str, Any]]:
    path = events_path(root, item_id)
    if not path.exists():
        raise ConsultError(f'Item "{item_id}" does not exist.')

    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.rstrip("\n")
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ConsultDataError(
                    f"Corrupt event log at {path} line {line_number}: {exc.msg}"
                ) from exc
            if not isinstance(event, dict):
                raise ConsultDataError(
                    f"Corrupt event log at {path} line {line_number}: event must be a JSON object"
                )
            events.append(event)

    if not events:
        raise ConsultDataError(f'Item "{item_id}" has an empty event log.')
    return events


def derive_state(item_id: str, events: list[dict[str, Any]]) -> ConsultState:
    state = ConsultState(item_id=item_id)

    for index, event in enumerate(events, start=1):
        event_type = event.get("event_type")
        actor = event.get("actor")
        timestamp = event.get("timestamp")
        payload = event.get("payload")

        if not isinstance(event_type, str) or not isinstance(actor, str):
            raise ConsultDataError(
                f'Item "{item_id}" has an invalid event at position {index}: missing event_type or actor'
            )
        if not isinstance(payload, dict):
            raise ConsultDataError(
                f'Item "{item_id}" has an invalid event at position {index}: payload must be an object'
            )

        state.updated_at = timestamp if isinstance(timestamp, str) else state.updated_at

        if event_type == "item_created":
            kind = payload.get("kind")
            title = payload.get("title")
            body = payload.get("body")
            if not isinstance(kind, str) or kind not in VALID_KINDS:
                raise ConsultDataError(
                    f'Item "{item_id}" has an invalid item_created event at position {index}: bad kind'
                )
            if not isinstance(title, str) or not title.strip():
                raise ConsultDataError(
                    f'Item "{item_id}" has an invalid item_created event at position {index}: missing title'
                )
            if body is not None and not isinstance(body, str):
                raise ConsultDataError(
                    f'Item "{item_id}" has an invalid item_created event at position {index}: body must be a string'
                )
            state.kind = kind
            state.title = title
            state.body = body
            state.requester = actor
            state.status = "open"
            state.created_at = timestamp if isinstance(timestamp, str) else state.created_at
        elif event_type == "handoff":
            to_agent = payload.get("to")
            summary = payload.get("summary")
            if not isinstance(to_agent, str) or not to_agent.strip():
                raise ConsultDataError(
                    f'Item "{item_id}" has an invalid handoff event at position {index}: missing "to"'
                )
            if summary is not None and not isinstance(summary, str):
                raise ConsultDataError(
                    f'Item "{item_id}" has an invalid handoff event at position {index}: summary must be a string'
                )
            state.current_owner = to_agent
            state.latest_summary = summary or state.latest_summary
        elif event_type == "claimed":
            state.current_owner = actor
            state.claim_count += 1
        elif event_type == "note_added":
            body = payload.get("body")
            if not isinstance(body, str) or not body.strip():
                raise ConsultDataError(
                    f'Item "{item_id}" has an invalid note_added event at position {index}: missing body'
                )
            state.note_count += 1
        elif event_type == "closed":
            summary = payload.get("summary")
            if summary is not None and not isinstance(summary, str):
                raise ConsultDataError(
                    f'Item "{item_id}" has an invalid closed event at position {index}: summary must be a string'
                )
            state.status = "closed"
            state.latest_summary = summary or state.latest_summary
        else:
            raise ConsultDataError(
                f'Item "{item_id}" has an unknown event type at position {index}: {event_type}'
            )

    if state.title is None or state.kind is None or state.requester is None:
        raise ConsultDataError(f'Item "{item_id}" is missing an item_created event.')

    return state


def load_state(root: Path, item_id: str) -> tuple[ConsultState, list[dict[str, Any]]]:
    events = load_events(root, item_id)
    return derive_state(item_id, events), events


def iter_item_ids(root: Path) -> list[str]:
    directory = items_root(root)
    if not directory.exists():
        return []
    return sorted(
        child.name for child in directory.iterdir() if child.is_dir()
    )


def require_open_item(root: Path, item_id: str) -> tuple[ConsultState, list[dict[str, Any]]]:
    state, events = load_state(root, item_id)
    if state.status == "closed":
        raise ConsultError(f'Item "{item_id}" is already closed.')
    return state, events


def render_event(event: dict[str, Any]) -> str:
    timestamp = event.get("timestamp", "?")
    actor = event.get("actor", "?")
    event_type = event.get("event_type", "?")
    payload = event.get("payload", {})

    if event_type == "item_created":
        return f"{timestamp}  {actor} created {payload.get('kind')} — {payload.get('title')}"
    if event_type == "handoff":
        return (
            f"{timestamp}  {actor} handed off to {payload.get('to')} — "
            f"{payload.get('summary', '')}"
        )
    if event_type == "claimed":
        return f"{timestamp}  {actor} claimed the item"
    if event_type == "note_added":
        return f"{timestamp}  {actor} noted — {payload.get('body')}"
    if event_type == "closed":
        return f"{timestamp}  {actor} closed — {payload.get('summary', '')}"
    return f"{timestamp}  {actor} {event_type}"


def state_to_dict(state: ConsultState) -> dict[str, Any]:
    return {
        "item_id": state.item_id,
        "kind": state.kind,
        "title": state.title,
        "body": state.body,
        "requester": state.requester,
        "current_owner": state.current_owner,
        "status": state.status,
        "note_count": state.note_count,
        "latest_summary": state.latest_summary,
        "created_at": state.created_at,
        "updated_at": state.updated_at,
        "claim_count": state.claim_count,
    }


def emit_output(args: argparse.Namespace, payload: dict[str, Any], text: str) -> None:
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print(text)


# ── Dispatch ─────────────────────────────────────────────────

CONSULT_CLI_PATH = Path(__file__).resolve().parent / "consult"
DEFAULT_CODEX_PATH = Path("/Applications/Codex.app/Contents/Resources/codex")


def built_in_agent_command(agent_name: str) -> list[str] | None:
    """Resolve a known local agent binary without requiring agents.json."""
    normalized = agent_name.strip().lower()

    if normalized == "claude" and shutil.which("claude"):
        return ["claude", "-p"]
    if normalized == "codex":
        codex_binary = shutil.which("codex")
        if codex_binary:
            return [codex_binary, "exec"]
        if DEFAULT_CODEX_PATH.exists():
            return [str(DEFAULT_CODEX_PATH), "exec"]
    if normalized == "kiro":
        kiro_binary = shutil.which("kiro-cli")
        if kiro_binary:
            return [kiro_binary, "chat", "--no-interactive", "--trust-all-tools"]
    return None


def load_agents_config(root: Path) -> dict[str, Any]:
    """Load agents.json from the consult root. Returns {} if absent."""
    config_path = root / "agents.json"
    if not config_path.exists():
        return {}
    try:
        with config_path.open("r", encoding="utf-8") as f:
            config = json.load(f)
    except json.JSONDecodeError as exc:
        raise ConsultError(
            f"Invalid agents.json at {config_path}: {exc.msg}"
        ) from exc

    if not isinstance(config, dict):
        raise ConsultError(f"Invalid agents.json at {config_path}: top-level JSON must be an object.")
    agents = config.get("agents", {})
    if agents is not None and not isinstance(agents, dict):
        raise ConsultError(f'Invalid agents.json at {config_path}: "agents" must be an object.')
    return config


def resolve_agent_command(root: Path, agent_name: str) -> list[str] | None:
    """Resolve the shell command for an agent. Returns None if dispatch is impossible."""
    config = load_agents_config(root)
    agents = config.get("agents", {})

    if agent_name in agents:
        cmd = agents[agent_name].get("command")
        if cmd:
            return cmd if isinstance(cmd, list) else [cmd]

    return built_in_agent_command(agent_name)


def build_dispatch_command(root: Path, agent_name: str, working_dir: Path) -> list[str] | None:
    """Build the full subprocess command used to dispatch a built-in or configured agent."""
    config = load_agents_config(root)
    agents = config.get("agents", {})
    if agent_name in agents:
        return resolve_agent_command(root, agent_name)

    built_in = built_in_agent_command(agent_name)
    if built_in is None:
        return None

    normalized = agent_name.strip().lower()
    consult_dir = str(CONSULT_CLI_PATH.parent)
    consult_root = str(root)

    if normalized == "claude":
        return [
            *built_in,
            "--add-dir",
            consult_dir,
            "--add-dir",
            consult_root,
        ]

    if normalized == "codex":
        return [
            *built_in,
            "--sandbox",
            "workspace-write",
            "--skip-git-repo-check",
            "-C",
            str(working_dir),
            "--add-dir",
            consult_dir,
            "--add-dir",
            consult_root,
        ]

    if normalized == "kiro":
        return built_in

    return built_in


def build_dispatch_prompt(
    root: Path,
    item_id: str,
    requester: str,
    agent_name: str,
    kind: str,
    title: str,
    body: str | None,
) -> str:
    """Build the prompt sent to the dispatched agent session."""
    cli = str(CONSULT_CLI_PATH)
    root_flag = f" --root {root}" if root != default_root() else ""

    return f"""You have a consult-cli item assigned to you.

Item: {item_id}
Kind: {kind}
Title: {title}
{f"Body: {body}" if body else ""}

You are acting as agent "{agent_name}". Use this CLI to interact:

  {cli}{root_flag} claim {item_id} --agent {agent_name}
  {cli}{root_flag} note {item_id} --agent {agent_name} --body "your findings"
  {cli}{root_flag} handoff {item_id} --from {agent_name} --to {requester} --summary "what you found" --no-dispatch
  {cli}{root_flag} close {item_id} --agent {agent_name} --summary "done"

Claim the item first. Then do the work. If the task is complete after your note, close it.
If the requester needs the result back for follow-up, hand it off to "{requester}" with --no-dispatch.
Do not ask for confirmation — just do the work."""


def dispatch_agent(
    root: Path,
    requester: str,
    agent_name: str,
    item_id: str,
    kind: str,
    title: str,
    body: str | None,
    wait: bool = False,
) -> DispatchResult:
    """Spawn a new agent session for the target agent.

    When *wait* is True the call blocks until the dispatched process exits
    and returns the process exit code.  The item's final state can then be
    read with ``load_state``.
    """
    working_dir = Path.cwd()
    cmd = build_dispatch_command(root, agent_name, working_dir)
    if cmd is None:
        return DispatchResult(dispatched=False)

    prompt = build_dispatch_prompt(root, item_id, requester, agent_name, kind, title, body)
    log_path = item_dir(root, item_id) / f"dispatch-{uuid4()}.log"

    try:
        with log_path.open("a", encoding="utf-8") as handle:
            process = subprocess.Popen(
                [*cmd, prompt],
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
                start_new_session=not wait,
                cwd=working_dir,
            )
        if wait:
            exit_code = process.wait()
            return DispatchResult(
                dispatched=True,
                pid=process.pid,
                log_path=str(log_path),
                exit_code=exit_code,
            )
        return DispatchResult(
            dispatched=True,
            pid=process.pid,
            log_path=str(log_path),
        )
    except OSError as exc:
        print(f"warning: dispatch to {agent_name} failed: {exc}", file=sys.stderr)
        return DispatchResult(dispatched=False, log_path=str(log_path))


def cmd_create(args: argparse.Namespace) -> int:
    root = root_path(args)
    item_id = str(uuid4())
    requester = require_nonblank(args.from_agent, "from agent")
    target = require_nonblank(args.to_agent, "to agent")
    title = require_nonblank(args.title, "title")
    created_event = append_event(
        root,
        item_id,
        "item_created",
        requester,
        {
            "kind": args.kind,
            "title": title,
            "body": args.body,
        },
    )
    handoff_event = append_event(
        root,
        item_id,
        "handoff",
        requester,
        {
            "from": requester,
            "to": target,
            "summary": args.body or f"Initial handoff: {title}",
        },
    )
    dispatch_result = DispatchResult(dispatched=False)
    should_dispatch = not getattr(args, "no_dispatch", False)
    should_wait = getattr(args, "wait", False)
    if should_dispatch:
        dispatch_result = dispatch_agent(
            root, requester, target, item_id, args.kind, title, args.body,
            wait=should_wait,
        )

    output: dict[str, Any] = {
        "ok": True,
        "item_id": item_id,
        "kind": args.kind,
        "requester": requester,
        "current_owner": target,
        "dispatched": dispatch_result.dispatched,
        "dispatch_pid": dispatch_result.pid,
        "dispatch_log_path": dispatch_result.log_path,
        "root": str(root),
        "events_path": str(events_path(root, item_id)),
        "created_event_id": created_event["event_id"],
        "handoff_event_id": handoff_event["event_id"],
    }

    text = f"Created {args.kind} item {item_id} for {target}."
    if dispatch_result.dispatched:
        text += f" Dispatched session to {target}."

    if should_wait and dispatch_result.dispatched:
        final_state, final_events = load_state(root, item_id)
        output["final_state"] = state_to_dict(final_state)
        output["dispatch_exit_code"] = dispatch_result.exit_code
        if dispatch_result.exit_code not in (None, 0):
            output["ok"] = False
            output["error"] = (
                f"Dispatched agent {target} exited with code {dispatch_result.exit_code}."
            )
            text = (
                f"Dispatch to {target} failed with exit code {dispatch_result.exit_code}."
                + (f" See {dispatch_result.log_path}." if dispatch_result.log_path else "")
            )

    emit_output(
        args,
        output,
        text,
    )
    return 0 if output["ok"] else 1


def cmd_claim(args: argparse.Namespace) -> int:
    root = root_path(args)
    agent = require_nonblank(args.agent, "agent")
    state, _events = require_open_item(root, args.item_id)
    if state.current_owner and state.current_owner != agent:
        raise ConsultError(
            f'Item "{args.item_id}" is currently owned by {state.current_owner}; only the current owner may claim it.'
        )
    event = append_event(root, args.item_id, "claimed", agent, {})
    emit_output(
        args,
        {
            "ok": True,
            "item_id": args.item_id,
            "event_id": event["event_id"],
            "agent": agent,
            "action": "claimed",
        },
        f"{agent} claimed item {args.item_id}.",
    )
    return 0


def cmd_note(args: argparse.Namespace) -> int:
    root = root_path(args)
    agent = require_nonblank(args.agent, "agent")
    body = require_nonblank(args.body, "body")
    state, _events = require_open_item(root, args.item_id)
    if state.current_owner and state.current_owner != agent:
        raise ConsultError(
            f'Item "{args.item_id}" is currently owned by {state.current_owner}; only the current owner may add notes.'
        )
    event = append_event(root, args.item_id, "note_added", agent, {"body": body})
    emit_output(
        args,
        {
            "ok": True,
            "item_id": args.item_id,
            "event_id": event["event_id"],
            "agent": agent,
            "action": "note_added",
        },
        f'Added note to {args.item_id}.',
    )
    return 0


def cmd_handoff(args: argparse.Namespace) -> int:
    root = root_path(args)
    from_agent = require_nonblank(args.from_agent, "from agent")
    to_agent = require_nonblank(args.to_agent, "to agent")
    summary = require_nonblank(args.summary, "summary")
    state, _events = require_open_item(root, args.item_id)
    if state.current_owner and state.current_owner != from_agent:
        raise ConsultError(
            f'Item "{args.item_id}" is currently owned by {state.current_owner}; only the current owner may hand it off.'
        )
    event = append_event(
        root,
        args.item_id,
        "handoff",
        from_agent,
        {
            "from": from_agent,
            "to": to_agent,
            "summary": summary,
        },
    )

    dispatch_result = DispatchResult(dispatched=False)
    should_dispatch = not getattr(args, "no_dispatch", False)
    should_wait = getattr(args, "wait", False)
    if should_dispatch:
        state, _events = load_state(root, args.item_id)
        dispatch_result = dispatch_agent(
            root, from_agent, to_agent, args.item_id,
            state.kind or "consult", state.title or "", state.body,
            wait=should_wait,
        )

    output: dict[str, Any] = {
        "ok": True,
        "item_id": args.item_id,
        "event_id": event["event_id"],
        "from": from_agent,
        "to": to_agent,
        "dispatched": dispatch_result.dispatched,
        "dispatch_pid": dispatch_result.pid,
        "dispatch_log_path": dispatch_result.log_path,
        "action": "handoff",
    }

    text = f"Handed off {args.item_id} from {from_agent} to {to_agent}."
    if dispatch_result.dispatched:
        text += f" Dispatched session to {to_agent}."

    if should_wait and dispatch_result.dispatched:
        final_state, _final_events = load_state(root, args.item_id)
        output["final_state"] = state_to_dict(final_state)
        output["dispatch_exit_code"] = dispatch_result.exit_code
        if dispatch_result.exit_code not in (None, 0):
            output["ok"] = False
            output["error"] = (
                f"Dispatched agent {to_agent} exited with code {dispatch_result.exit_code}."
            )
            text = (
                f"Dispatch to {to_agent} failed with exit code {dispatch_result.exit_code}."
                + (f" See {dispatch_result.log_path}." if dispatch_result.log_path else "")
            )

    emit_output(
        args,
        output,
        text,
    )
    return 0 if output["ok"] else 1


def cmd_inbox(args: argparse.Namespace) -> int:
    root = root_path(args)
    agent = require_nonblank(args.agent, "agent")
    rows: list[tuple[ConsultState, str]] = []

    for item_id in iter_item_ids(root):
        state, _events = load_state(root, item_id)
        if state.status == "open" and state.current_owner == agent:
            updated = state.updated_at or ""
            rows.append((state, updated))

    rows.sort(key=lambda pair: pair[1], reverse=True)

    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "agent": agent,
                    "items": [state_to_dict(state) for state, _updated in rows],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if not rows:
        print(f"No open items for {agent}.")
        return 0

    print(f"Inbox for {agent}")
    print("=" * (10 + len(agent)))
    for state, _updated in rows:
        print(f"{state.item_id}  [{state.kind}] {state.title}")
        print(
            f"  requester: {state.requester}  owner: {state.current_owner}  "
            f"updated: {state.updated_at or 'unknown'}"
        )
        print(f"  latest: {state.latest_summary or '(no summary)'}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    root = root_path(args)
    state, events = load_state(root, args.item_id)

    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "state": state_to_dict(state),
                    "events": events,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    print(f"Item: {state.item_id}")
    print(f"Title: {state.title}")
    print(f"Kind: {state.kind}")
    print(f"Requester: {state.requester}")
    print(f"Current owner: {state.current_owner or '(unassigned)'}")
    print(f"Status: {state.status}")
    print(f"Notes: {state.note_count}")
    print(f"Latest summary: {state.latest_summary or '(none)'}")
    print(f"Created: {state.created_at or 'unknown'}")
    print(f"Updated: {state.updated_at or 'unknown'}")
    if state.body:
        print(f"Body: {state.body}")
    print("\nTimeline")
    print("--------")
    for event in events:
        print(render_event(event))
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    root = root_path(args)
    agent = require_nonblank(args.agent, "agent")
    summary = require_nonblank(args.summary, "summary")
    state, _events = require_open_item(root, args.item_id)
    if state.current_owner and state.current_owner != agent:
        raise ConsultError(
            f'Item "{args.item_id}" is currently owned by {state.current_owner}; only the current owner may close it.'
        )
    event = append_event(
        root,
        args.item_id,
        "closed",
        agent,
        {"summary": summary},
    )
    emit_output(
        args,
        {
            "ok": True,
            "item_id": args.item_id,
            "event_id": event["event_id"],
            "agent": agent,
            "action": "closed",
        },
        f'Closed item {args.item_id}.',
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="consult",
        description="Append-only local baton board for agent consultation.",
    )
    parser.add_argument(
        "--root",
        help="Override the event-log root. Defaults to CONSULT_ROOT or ~/.consult-cli.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of human text.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create", help="Create an item and hand it off.")
    create_parser.add_argument("--kind", required=True, choices=VALID_KINDS)
    create_parser.add_argument("--from", dest="from_agent", required=True)
    create_parser.add_argument("--to", dest="to_agent", required=True)
    create_parser.add_argument("--title", required=True)
    create_parser.add_argument("--body")
    create_parser.add_argument("--no-dispatch", action="store_true", help="Skip auto-dispatching a session to the target agent.")
    create_parser.add_argument("--wait", action="store_true", help="Block until the dispatched agent finishes and return the final item state.")
    create_parser.set_defaults(func=cmd_create)

    claim_parser = subparsers.add_parser("claim", help="Claim an item assigned to you.")
    claim_parser.add_argument("item_id")
    claim_parser.add_argument("--agent", required=True)
    claim_parser.set_defaults(func=cmd_claim)

    note_parser = subparsers.add_parser("note", help="Append a note to an item.")
    note_parser.add_argument("item_id")
    note_parser.add_argument("--agent", required=True)
    note_parser.add_argument("--body", required=True)
    note_parser.set_defaults(func=cmd_note)

    handoff_parser = subparsers.add_parser("handoff", help="Pass an item to another agent.")
    handoff_parser.add_argument("item_id")
    handoff_parser.add_argument("--from", dest="from_agent", required=True)
    handoff_parser.add_argument("--to", dest="to_agent", required=True)
    handoff_parser.add_argument("--summary", required=True)
    handoff_parser.add_argument("--no-dispatch", action="store_true", help="Skip auto-dispatching a session to the target agent.")
    handoff_parser.add_argument("--wait", action="store_true", help="Block until the dispatched agent finishes and return the final item state.")
    handoff_parser.set_defaults(func=cmd_handoff)

    inbox_parser = subparsers.add_parser("inbox", help="List open items owned by an agent.")
    inbox_parser.add_argument("--agent", required=True)
    inbox_parser.set_defaults(func=cmd_inbox)

    show_parser = subparsers.add_parser("show", help="Show derived state and full event history.")
    show_parser.add_argument("item_id")
    show_parser.set_defaults(func=cmd_show)

    close_parser = subparsers.add_parser("close", help="Close an item.")
    close_parser.add_argument("item_id")
    close_parser.add_argument("--agent", required=True)
    close_parser.add_argument("--summary", required=True)
    close_parser.set_defaults(func=cmd_close)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConsultError as exc:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True), file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
