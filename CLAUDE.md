# CLAUDE.md

## What This Is

A generic MCP mock server that replaces real MCP servers during skill evaluation. It reads a tool schema and dynamically registers MCP tools, returning configurable responses without requiring the real server, credentials, or infrastructure.

## Why It Exists

Skills (AI agent task executors) depend on MCP servers for tool access. Evaluating skills end-to-end requires the agent to actually call tools — but real MCP servers have side effects (creating clusters, modifying infrastructure), require credentials and network access, and mix skill failures with MCP failures. This mock isolates the skill so evaluation measures only how well the skill guides the LLM.

## Architecture

```
schema.json ──→ Server ──→ MCP tools registered dynamically
                  │
                  ├── Static strategy:   returns outputExample from schema
                  ├── Fixtures strategy: returns ordered responses from fixtures.json
                  └── LLM strategy:      generates coherent responses via LLM
```

The server is a single `server.py` that uses the MCP SDK (v2) to register tools at startup from any `schema.json`. No per-MCP custom code needed.

### Response Strategies

- **Static**: Returns the `outputExample` from the schema verbatim. Deterministic, no coherence between calls. Good for single-tool skills.
- **Fixtures**: Returns pre-defined responses from `fixtures.json`, matched by tool name in sequence order. Deterministic and coherent across multi-step flows. Good for certification/gate evaluations.
- **LLM**: Uses an LLM to generate responses based on schema + call history. Zero maintenance, automatic coherence. Good for exploratory/regression evaluations. Reuses the evaluation pipeline's LLM endpoint.

### Configuration

Two JSON files, bind-mounted into the container at `/config/`:

- **`schema.json`** — tool definitions with `inputSchema`, `outputSchema`, and `outputExample` per tool.
- **`fixtures.json`** — ordered sequence of tool responses for multi-step flows.

The image has no schemas baked in — you choose which files to mount. Pre-built configs for specific MCP servers live in `configs/`, one subdirectory per server.

### Transport

- **HTTP** (default): Streamable HTTP on port 8080, exposes JSON-RPC at `POST /mcp`. Designed for pipeline sidecar deployments.
- **stdio**: For local MCP clients (Claude Code, IDEs). Set `MOCK_TRANSPORT=stdio`.

## Repository Structure

```
generic-mock-mcp-server/
├── README.md              # Project documentation
├── Containerfile          # UBI 10 minimal + Python 3.12, non-root
├── requirements.txt       # mcp SDK, anthropic (for LLM strategy)
├── src/
│   └── server.py          # Mock server (strategies, dynamic tool registration)
├── tests/
│   ├── test_mock.py       # Unit tests (schema loading, strategies, MCP execution)
│   ├── schema.json        # Test fixture schema (4 tools)
│   └── fixtures.json      # Test fixture responses
└── configs/               # Pre-built configs per MCP server
    ├── openshift-mcp-server/
    │   ├── schema.json    # 20 tools (pods, nodes, resources, config)
    │   └── fixtures.json  # OOMKilled troubleshooting scenario
    └── lightspeed-mcp/
        ├── schema.json    # 13 tools (vulnerability, inventory, remediations)
        ├── fixtures.json  # CVE Impact Analysis scenario (7 steps)
        └── USAGE.md       # Curl test guide
```

## Schema Format

MCP server developers publish a `schema.json` alongside their server:

```json
{
  "name": "my-mcp-server",
  "version": "1.0.0",
  "tools": [
    {
      "name": "tool_name",
      "description": "What the tool does",
      "inputSchema": {
        "type": "object",
        "properties": { ... },
        "required": [...]
      },
      "outputSchema": { ... },
      "outputExample": { ... }
    }
  ]
}
```

## Fixtures Format

Skill authors provide a `fixtures.json` for multi-step flows:

```json
{
  "sequence": [
    {"tool": "tool_a", "input": {...}, "output": {...}},
    {"tool": "tool_b", "input": {...}, "output": {...}}
  ]
}
```

Responses are matched by tool name and served in order. If a tool is called more times than it has fixtures, the server falls back to the schema's `outputExample`.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MOCK_SCHEMA_PATH` | `/config/schema.json` | Path to tool schema |
| `MOCK_FIXTURES_PATH` | `/config/fixtures.json` | Path to fixtures |
| `MOCK_STRATEGY` | `fixtures` | `static`, `fixtures`, or `llm` |
| `MOCK_TRANSPORT` | `streamable-http` | `stdio` or `streamable-http` |
| `MOCK_PORT` | `8080` | Port for HTTP transport |
| `MOCK_LLM_MODEL` | `claude-haiku-4-5-20251001` | Model for LLM strategy |

CLI flags override env vars.

## Development

```bash
# Run tests (no container needed)
python tests/test_mock.py

# Build image
podman build -t mock-mcp-server:latest -f Containerfile .

# Run with a config
podman run --rm -d -p 8080:8080 \
  -v ./configs/openshift-mcp-server/schema.json:/config/schema.json:ro,Z \
  -v ./configs/openshift-mcp-server/fixtures.json:/config/fixtures.json:ro,Z \
  -e MOCK_STRATEGY=fixtures \
  mock-mcp-server:latest
```

See `README.md` for full usage and curl test commands.

## Adding a New MCP Config

1. Create `configs/<mcp-name>/schema.json` with all tools from the MCP server.
2. Optionally create `configs/<mcp-name>/fixtures.json` with a coherent multi-step scenario.
3. Test: `python src/server.py --schema configs/<mcp-name>/schema.json --strategy static`

## Key Design Decisions

- **Schema ownership**: The MCP server developer publishes and maintains `schema.json`. It is the contract between the MCP, the skills that use it, and the mock.
- **Fixtures for certification, LLM for exploration**: Fixtures give deterministic pass/fail; LLM gives zero-maintenance coherence. Choose based on evaluation goal.
- **LLM strategy reuses the pipeline's LLM**: No extra infrastructure — the mock calls the same LLM endpoint the evaluation pipeline already provisions.
- **Generic server, config-driven**: One image, any MCP. The schema defines the tools; the fixtures define the responses.
