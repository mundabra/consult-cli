# Why I Built consult-cli

I use Claude Code and Codex on the same projects. Both are good. They're good at different things. Claude reasons carefully about architecture and edge cases. Codex is fast and confident at execution. When I ask Claude to review code that Codex wrote, it finds real issues that Codex missed — not because Codex is worse, but because they see differently.

The problem is getting them to actually talk to each other.

Claude finishes a review and has findings that Codex needs to act on. There's no way to hand that over — not the findings, not the context, not the ownership. I copy Claude's output, open a Codex session, paste it in, wait, copy the result back. I am the coordination layer. A human message router between two systems that can't talk to each other.

consult-cli fixes that.

## The handoff problem

Each coding assistant runs in its own session with its own context. Claude Code doesn't know what Codex just did. Codex doesn't know what Claude is working on. When one needs a second opinion — a review, a consult, a question — there's no way to hand work across vendors with context and get a structured response back.

Within a single vendor's ecosystem, this is partially solved. Claude Code has Agent Teams for intra-Claude coordination. Cursor has background agents. But none of these work *across* tools. Claude can't dispatch to Codex. Codex can't consult Claude. The cross-vendor handoff is the gap.

[Oren Katz described this well](https://www.linkedin.com/pulse/my-agent-found-vulnerability-2-am-9-another-had-fixed-oren-katz-gqk6f/) when building an autonomous security pipeline: "The hardest part isn't any individual agent. It's the handoff." His three-agent system could find and fix vulnerabilities in 7 hours with 15 minutes of human review. The breakthrough wasn't any individual agent — it was structuring each agent's output to be the next agent's input.

[Addy Osmani's research](https://addyosmani.com/blog/code-agent-orchestra/) supports the same pattern: three focused agents consistently outperform one generalist working three times as long. But only if they can coordinate.

## Why not one agent?

There's a common assumption that one sufficiently powerful model should handle everything. Just give it a bigger context window, better tools, more compute. This doesn't hold, for the same reason a company doesn't hire one person to do engineering, security, and devops.

Different models have different strengths. Different training data catches different bugs. When Claude reviews code that Codex wrote, it finds things Codex didn't flag. When Codex reviews Claude's output, it catches practical issues Claude overthought. The output of cross-model review is better than either produces alone.

This mirrors how human teams work. You don't pit your senior engineer against your security specialist to see who's "better." You have them consult each other. The specialist reviews the engineer's auth code. The engineer reviews the specialist's performance recommendations.

That's the mental model: agents consulting each other, not competing.

Today's multi-agent tools are single-vendor. Claude Code Agent Teams coordinates Claude instances. Cursor subagents stay within Cursor. Kiro agents stay within Kiro. The cross-vendor, peer-to-peer layer — where any assistant can consult any other — doesn't exist yet at the developer tooling level. That's the gap.

## Why not A2A, MCP, or tool-use?

Before explaining what I built, I should address the obvious question: aren't there already protocols for this?

**Google's A2A protocol** enables collaboration between agents that are remotely addressable — discoverable via Agent Cards, communicating over HTTP/JSON-RPC. It's the right model when agents are long-lived services with endpoints. But coding assistants aren't services. Claude Code is a CLI session that starts, does work, and exits. There's no endpoint to discover, no server to register with. A2A assumes heavier service-style integration than makes sense for ephemeral local CLI sessions.

**MCP** standardizes how models connect to tools, resources, and data sources. It's broader than just tool access — it covers prompts, lifecycle negotiation, and resource discovery. But MCP doesn't define ownership transfer, dispatch, or cross-agent work routing. It answers "what capabilities does this agent have?" not "who should do this work next and how do I get it to them?" You could build a coordination MCP server that wraps consult-cli — and that might be a good idea — but MCP itself doesn't provide the handoff semantics.

**Tool-use (agent-as-tool)** is the pattern where Agent A calls Agent B and gets a response in the same context. Anthropic's Agent SDK does this well. It works when one agent is the orchestrator and others are subordinate. But it requires both agents to share a runtime and orchestration context. consult-cli is for handoffs across separate processes, separate vendors, and separate context windows — with persistent ownership and an audit trail.

These protocols solve real problems at different layers. consult-cli operates at a specific layer none of them cover: local, cross-vendor, file-based coordination between CLI coding assistants on a developer's machine.

## What I built

consult-cli is a local, append-only baton board. One owner at a time. When you create an item or hand it off, the CLI dispatches a new agent session — a real `claude -p` or `codex exec` process — with a prompt that tells the agent to claim the item, do the work, and hand back.

The design is deliberately simple:

**Event-sourced state.** Each item is a folder with an `events.jsonl` file. State is derived by replaying the log. No database, no server, no account. Just files on disk.

**Single ownership.** An item has one owner at a time. Only the current owner can add notes, hand off, or close. This eliminates "who's working on this?" confusion.

**Auto-dispatch.** `create` and `handoff` spawn a new agent session for the target. The dispatched agent claims the item, does its work, and writes findings back to the log. The `--wait` flag blocks until the agent finishes — one call, one result, no polling.

**Agent-agnostic.** Built-in support for Claude Code, Codex, and Kiro. An `agents.json` config file adds any other assistant that accepts a prompt via CLI.

The entire thing is 800 lines of Python with zero dependencies. Clone the repo and run `./consult`.

## How it actually works

Here's a real interaction from the session where I built this tool. I asked Claude to consult Codex about a PR:

```
$ ./consult create --wait --kind review --from claude --to codex \
    --title "Review PR #36: proactive visibility" \
    --body "10 files, +165/-4. Check origin filtering and empty states."
```

The CLI created an item, dispatched a `codex exec` session, and blocked. Codex claimed the item, read the PR diff, added review notes, and handed back. The full timeline — who did what, when, with what findings — was in the event log.

No copy-pasting. No context switching. No manual routing.

Later, I used the same tool to have Codex review this very blog post. It came back with 11 findings — factual corrections on my timeline, suggestions to tighten the A2A/MCP section, notes on unsupported claims. Cross-model review on prose, not just code.

## What I learned building it

The first version didn't have dispatch. It was just a log — you'd write an item, manually open another agent session, tell it to check its inbox. I tested it by "consulting Codex" for a code review and felt good about the clean timeline it produced.

Then I realized I'd been playing both roles myself. Claude created the item, Claude played Codex's response, Claude closed the loop. It was journaling with extra steps.

Adding dispatch — actually spawning a `codex exec` process that picks up the item and writes back — changed everything. The first real dispatch took 20 seconds. Codex claimed the item, ran `date`, added a note with the time, and closed it. No human routing. That was the moment it stopped being a log and became a coordination layer.

The second thing I learned: polling is expensive. When Claude dispatched a PR review to Codex, it burned tokens checking the item every few seconds for two minutes. The fix was `--wait` — the CLI blocks on the dispatched process instead of polling the filesystem. One call, one notification when it's done. Process exit is the signal.

## What this enables

**Cross-model review.** Claude writes code, Codex reviews it. Or vice versa. Different training data catches different bugs. In the session where I built consult-cli, cross-model review of this blog post caught factual errors in my timeline and overclaims in my protocol comparisons — things I wouldn't have caught reviewing my own output.

**Specialist agents.** Configure a "security-reviewer" in `agents.json` — Claude with a security-focused system prompt. Any agent can consult it when touching auth or crypto code.

**Audit trails.** Every interaction is an append-only event log. Who asked what, who found what, when. The log is the proof that review happened.

## The pattern, not the tool

consult-cli is 800 lines of Python. It's not a framework. It's a pattern: append-only event log, single ownership, structured handoff, auto-dispatch. The pattern matters more than the implementation.

If you're using multiple AI coding assistants and want them to consult each other, you need something like this. Maybe not this exact tool — maybe you need HTTP instead of files, maybe SQLite instead of JSONL. But you need structured handoffs with clear ownership across vendor boundaries.

The alternative is being the human message router. That works for two agents. It doesn't scale to three.

The code is [MIT licensed](https://github.com/mundabra/consult-cli). Clone it, try the example, send a ping to Codex.

The hardest part isn't any individual agent. It's the handoff.
