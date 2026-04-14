#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# Dispatch & Wait — consult-cli example
#
# Dispatches a simple task to another agent and blocks until
# the agent finishes. Demonstrates the --wait flag.
#
# Requires: claude or codex installed and on PATH.
#
# Run:  chmod +x examples/dispatch-and-wait.sh && ./examples/dispatch-and-wait.sh
# ─────────────────────────────────────────────────────────────

set -euo pipefail

CLI="$(cd "$(dirname "$0")/.." && pwd)/consult"

# Detect which agent is available
if command -v codex &>/dev/null; then
  TARGET="codex"
elif command -v claude &>/dev/null; then
  TARGET="claude"
else
  echo "error: No supported agent found. Install claude or codex first."
  exit 1
fi

echo "consult-cli — Dispatch & Wait"
echo "=============================="
echo "Target agent: $TARGET"
echo ""

echo "Creating item and dispatching (this blocks until $TARGET finishes)..."
echo ""

OUTPUT=$("$CLI" --json create --wait \
  --kind consult \
  --from user \
  --to "$TARGET" \
  --title "What time is it?" \
  --body "Run the 'date' command and reply with the current time in a note, then close the item.")

ITEM_ID=$(echo "$OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['item_id'])")

echo "Item: $ITEM_ID"
echo ""
echo "Final state:"
echo "$OUTPUT" | python3 -c "
import sys, json
data = json.load(sys.stdin)
state = data.get('final_state', {})
print(f\"  Status:  {state.get('status', 'unknown')}\")
print(f\"  Owner:   {state.get('current_owner', 'unknown')}\")
print(f\"  Notes:   {state.get('note_count', 0)}\")
print(f\"  Summary: {state.get('latest_summary', '(none)')}\")
"

echo ""
echo "Full timeline:"
"$CLI" show "$ITEM_ID"
