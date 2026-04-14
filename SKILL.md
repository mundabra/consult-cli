---
name: consult-cli
description: Use this skill when work needs explicit local baton-passing, consultation, or review handoff between agents or humans through an append-only filesystem log.
---

# consult-cli

<!-- Replace the path below with the absolute path to your consult-cli clone -->
Use the local CLI at `/path/to/consult-cli/consult`.

This tool is for `single-owner, append-only` coordination. It is not a generic chat channel.

## When to use it

- A task has one current owner and needs a durable handoff.
- One agent wants a focused review or consultation from another agent.
- Work should leave an inspectable local trail instead of disappearing into chat history.
- You need machine-readable state for wrappers or local automation.

## Operating rules

- Keep one owner at a time.
- Use `note` for findings, context, or intermediate reasoning.
- Use `handoff` when ownership changes.
- Use `close` only when the current owner is done.
- Do not edit `events.jsonl` manually. The log is append-only.
- Prefer `--json` when another tool or wrapper will parse the result.
- **Dispatch is automatic.** `create` and `handoff` spawn a new agent session for the target. Use `--no-dispatch` only when handing back (to avoid loops) or when the target agent is already running.
- When handing off back to the requester, always use `--no-dispatch`.

## Waiting for dispatch to complete

Use `--wait` on `create` or `handoff` to block until the dispatched agent finishes. **This is the preferred approach** — no polling, no wasted tokens. One call, one result.

```bash
./consult --json create --wait --kind review --from claude --to codex --title "Review X" --body "..."
```

The JSON output includes a `final_state` object with the item's state after the agent finished.

**If the dispatched agent seems stuck** (no completion after several minutes), check the dispatch log:

```bash
tail -30 ~/.consult-cli/items/<item-id>/dispatch-*.log
```

**Fallback polling** — only if `--wait` can't be used (e.g., item dispatched in a previous session):

```bash
./consult --json show <item-id>
```

## Storage

- Default root: `~/.consult-cli`
- Override root with `CONSULT_ROOT` or `--root /path`
- Each item is stored at `items/<item-id>/events.jsonl`
- Dispatch logs are stored at `items/<item-id>/dispatch-<uuid>.log`

## Core commands

```bash
./consult create --kind review --from claude --to codex --title "Review task lifecycle" --body "Focus on idempotency and failure handling."
./consult claim <item-id> --agent claude
./consult note <item-id> --agent claude --body "One blocking issue in workflow state."
./consult handoff <item-id> --from claude --to codex --summary "Review complete. Needs one fix." --no-dispatch
./consult inbox --agent codex
./consult show <item-id>
./consult close <item-id> --agent codex --summary "Integrated and verified."
```

## JSON mode

```bash
./consult --json create --kind consult --from claude --to codex --title "Architecture question"
./consult --json inbox --agent claude
./consult --json show <item-id>
```

## Good usage pattern

1. Create an item with a narrow ask — a new agent session is auto-dispatched.
2. Use `--wait` to block until the target agent responds.
3. Report the findings to the user.
4. If handing back, always use `--no-dispatch` to avoid infinite loops.
5. Close only after the current owner finishes the loop.

## Testing dispatch

Start with a simple ping before sending complex work:

```bash
./consult create --wait --kind consult --from claude --to codex --title "Ping" --body "Reply with a note saying hello, then close."
```

## Avoid

- Using this as a freeform conversation thread.
- Handing the same item to multiple owners at once.
- Mixing unrelated asks into one item.
- Relying on manual log edits or hidden side channels.
- Role-playing the target agent yourself — dispatch does this for real.
