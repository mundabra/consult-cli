#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# Two-Agent Code Review — consult-cli example
#
# Demonstrates the full baton-passing lifecycle between two
# AI agents (or humans) coordinating a code review through
# an append-only event log.
#
# Run:  chmod +x examples/two-agent-review.sh && ./examples/two-agent-review.sh
# ─────────────────────────────────────────────────────────────

set -euo pipefail

CLI="$(cd "$(dirname "$0")/.." && pwd)/consult"
export CONSULT_ROOT=$(mktemp -d)

echo "consult-cli — Two-Agent Code Review"
echo "===================================="
echo "Root: $CONSULT_ROOT"
echo ""

# ── 1. Developer creates a review request ────────────────────

echo "Step 1: Developer requests a code review"
echo "-----------------------------------------"
CREATE_OUTPUT=$("$CLI" --json create \
  --no-dispatch \
  --kind review \
  --from developer \
  --to reviewer \
  --title "Add retry logic to payment processor" \
  --body "Added exponential backoff to the Stripe webhook handler. Key changes in src/payments/retry.ts and src/payments/webhook.ts. Please check: (1) max retry cap, (2) idempotency key handling, (3) error classification (transient vs permanent).")

ITEM_ID=$(echo "$CREATE_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['item_id'])")
echo "Created item: $ITEM_ID"
echo ""

# ── 2. Reviewer checks inbox and claims the item ─────────────

echo "Step 2: Reviewer checks inbox"
echo "------------------------------"
"$CLI" inbox --agent reviewer
echo ""

echo "Step 3: Reviewer claims the item"
echo "---------------------------------"
"$CLI" claim "$ITEM_ID" --agent reviewer
echo ""

# ── 3. Reviewer adds findings ────────────────────────────────

echo "Step 4: Reviewer adds findings"
echo "-------------------------------"

"$CLI" note "$ITEM_ID" --agent reviewer \
  --body "retry.ts: Max retry cap is 5 — good. But the backoff multiplier (2^attempt * 100ms) means the 5th retry waits 3.2s. For payment webhooks this is fine, but document the ceiling so future callers know the worst-case latency."

"$CLI" note "$ITEM_ID" --agent reviewer \
  --body "webhook.ts: Idempotency key is derived from event ID + attempt number. This means each retry gets a different key — that defeats the purpose. The key should be event ID only, so Stripe deduplicates across retries."

"$CLI" note "$ITEM_ID" --agent reviewer \
  --body "Error classification looks correct: network errors and 5xx are transient, 4xx are permanent. One edge case: Stripe returns 429 (rate limit) which is technically 4xx but should be treated as transient."

echo ""

# ── 4. Reviewer hands back with summary ──────────────────────

echo "Step 5: Reviewer hands off back to developer"
echo "----------------------------------------------"
"$CLI" handoff "$ITEM_ID" \
  --no-dispatch \
  --from reviewer \
  --to developer \
  --summary "Two issues: (1) idempotency key includes attempt number — should be event ID only. (2) 429 responses classified as permanent — should be transient. Backoff logic is sound."
echo ""

# ── 5. Developer checks inbox ────────────────────────────────

echo "Step 6: Developer receives the review"
echo "--------------------------------------"
"$CLI" inbox --agent developer
echo ""

# ── 6. Developer acknowledges and closes ─────────────────────

echo "Step 7: Developer fixes and closes the loop"
echo "---------------------------------------------"
"$CLI" note "$ITEM_ID" --agent developer \
  --body "Fixed both issues. Idempotency key now uses event ID only. Added 429 to transient error set. Tests updated."

"$CLI" close "$ITEM_ID" --agent developer \
  --summary "Both findings addressed. Pushed fix commit a]1b2c3d."
echo ""

# ── 7. Show the full audit trail ─────────────────────────────

echo "Step 8: Full audit trail"
echo "========================"
"$CLI" show "$ITEM_ID"
echo ""

# ── 8. Show JSON output (for integrations) ───────────────────

echo "Step 9: Machine-readable state (for wrappers/automation)"
echo "========================================================="
"$CLI" --json show "$ITEM_ID" | python3 -m json.tool
echo ""

# Cleanup
echo "---"
echo "Done. Temp root at: $CONSULT_ROOT"
echo "Inspect the raw log: cat $CONSULT_ROOT/items/$ITEM_ID/events.jsonl"
