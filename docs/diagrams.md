# Diagrams for "Why I Built consult-cli"

## Diagram 1: The Problem — Human as Message Router

```mermaid
graph LR
    subgraph "Without consult-cli"
        C[Claude Code] -->|"copy output"| H((Human))
        H -->|"paste context"| X[Codex]
        X -->|"copy findings"| H
        H -->|"paste back"| C
    end

    style H fill:#e8927c,stroke:#c4694f,color:#fff
    style C fill:#f5ebe0,stroke:#c4a882
    style X fill:#f5ebe0,stroke:#c4a882
```

## Diagram 2: The Solution — Agents Consult Directly

```mermaid
graph LR
    subgraph "With consult-cli"
        C2[Claude Code] -->|"create --to codex"| L[(events.jsonl)]
        L -->|"dispatch"| X2[Codex]
        X2 -->|"note + handoff"| L
        L -->|"--wait returns"| C2
    end

    H2((Human)) -.->|"reviews result"| C2

    style H2 fill:#7eb8a0,stroke:#5a9478,color:#fff
    style C2 fill:#f5ebe0,stroke:#c4a882
    style X2 fill:#f5ebe0,stroke:#c4a882
    style L fill:#d4c5a9,stroke:#b8a88c
```

## Diagram 3: Item Lifecycle

```mermaid
sequenceDiagram
    participant Dev as Developer
    participant CLI as consult-cli
    participant C as Claude Code
    participant X as Codex

    Dev->>CLI: create --wait --to codex
    CLI->>CLI: append item_created event
    CLI->>CLI: append handoff event
    CLI->>X: dispatch (codex exec)

    X->>CLI: claim
    X->>CLI: note "Found issue in auth..."
    X->>CLI: note "Edge case in retry..."
    X->>CLI: handoff --to claude --no-dispatch

    CLI-->>Dev: --wait returns final state
    Dev->>CLI: show (full timeline)
```

## Diagram 4: Protocol Layer Comparison

```mermaid
graph TB
    subgraph "Cloud Agent Services"
        A2A[A2A Protocol<br/><i>Always-on agents with endpoints</i>]
    end

    subgraph "Agent Capabilities"
        MCP[MCP<br/><i>Connect agents to tools & data</i>]
    end

    subgraph "Agent Hierarchy"
        TU[Tool-Use / Agent SDK<br/><i>Orchestrator calls subordinates</i>]
    end

    subgraph "Local Agent Coordination"
        CC[consult-cli<br/><i>Peer handoffs between CLI sessions</i>]
    end

    style A2A fill:#e8e8e8,stroke:#999
    style MCP fill:#e8e8e8,stroke:#999
    style TU fill:#e8e8e8,stroke:#999
    style CC fill:#e8927c,stroke:#c4694f,color:#fff
```

## Diagram 5: Agent Registry & Dispatch

```mermaid
flowchart LR
    CREATE["./consult create<br/>--to codex"]
    REG{"Agent Registry"}
    BI["Built-in<br/>claude → claude -p<br/>codex → codex exec"]
    CF["agents.json<br/>gemini → gemini --prompt<br/>aider → aider --message"]
    DISPATCH["Spawn process<br/>with prompt"]

    CREATE --> REG
    REG --> BI
    REG --> CF
    BI --> DISPATCH
    CF --> DISPATCH
```

## Usage Notes

- Diagrams 1 & 2 work as a side-by-side pair (before/after)
- Diagram 3 is the most important — shows the full lifecycle
- Diagram 4 should be simple, not a full comparison matrix
- Diagram 5 is optional — only if the README needs a dispatch explainer
