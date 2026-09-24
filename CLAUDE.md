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
- **Fixtures**: Returns pre-defined responses from `fixtures.json`, matched by tool name and `input` (empty `input` matches any args). Deterministic and coherent across multi-step flows. Good for certification/gate evaluations.
- **LLM**: Uses an LLM to generate responses based on schema + call history. Zero maintenance, automatic coherence. Good for exploratory/regression evaluations. Reuses the evaluation pipeline's LLM endpoint.

### Configuration

Two JSON files, bind-mounted into the container at `/config/`. Field-level contract: **[SCHEMA.md](SCHEMA.md)**.

- **`schema.json`** — tool definitions with `inputSchema`, `outputSchema`, and `outputExample` per tool (MCP developers own this file).
- **`fixtures-<scenario>.json`** — ordered sequence of tool responses for one multi-step flow (skill authors own this file; one file per scenario).

The image has no schemas baked in — you choose which files to mount. Pre-built configs for specific MCP servers live in `configs/`, one subdirectory per server.

### Transport

- **CLI default is `stdio`**: for local MCP clients (Claude Code, IDEs).
- **Container default is `streamable-http`**: Streamable HTTP on port 8080, JSON-RPC at `POST /mcp`, for pipeline sidecar deployments.

## Repository Structure

```
generic-mock-mcp-server/
├── README.md              # Project documentation
├── SCHEMA.md              # schema.json / fixtures contract for MCP developers
├── Containerfile          # UBI 10 minimal + Python 3.12, non-root
├── requirements.txt       # mcp SDK, anthropic (for LLM strategy)
├── requirements-dev.txt   # pytest
├── src/
│   └── server.py          # Mock server (strategies, dynamic tool registration)
├── tests/                 # pytest: engine, configs, LLM fake client
│   ├── schema.json        # Test fixture schema (4 tools)
│   └── fixtures.json      # Test fixture responses
└── configs/               # Pre-built configs per MCP server
    ├── openshift-mcp-server/
    │   ├── schema.json              # 20 tools (pods, nodes, resources, config)
    │   ├── fixtures-oomkilled.json  # OOMKilled troubleshooting scenario
    │   └── USAGE.md                 # Deploy & curl test guide
    └── lightspeed-mcp/
        ├── schema.json                    # Lightspeed MCP tool catalog
        ├── fixtures-cve-impact.json       # CVE Impact Analysis scenario (7 steps)
        ├── fixtures-cve-validation.json   # CVE Validation scenario (3 steps)
        └── USAGE.md                       # Deploy & curl test guide
```

## Schema and fixtures

The standalone contract is **[SCHEMA.md](SCHEMA.md)**. MCP developers publish and maintain `schema.json` (`name`, `description`, `inputSchema`, `outputSchema`, `outputExample` per tool). Skill authors publish `fixtures-<scenario>.json` (one file per evaluation flow). Do not invent extra schema fields or auto-generate from OpenAPI/Compass unless a later ticket asks for it.

When `input` is present, the mock matches tool name plus those arguments (extra call args are ignored). Empty `input` matches any arguments. If nothing matches, a warning is logged and the schema `outputExample` is returned.

## Environment Variables

CLI flags override env vars. Values below are **container image** defaults (`Containerfile` `ENV`). Running `python src/server.py` with no env uses different CLI defaults: `--strategy static`, `--transport stdio`, and `--schema` / `--fixtures` are required.

| Variable | CLI default | Container default | Description |
|---|---|---|---|
| `MOCK_SCHEMA_PATH` | required | `/config/schema.json` | Path to tool schema |
| `MOCK_FIXTURES_PATH` | required for `fixtures` | `/config/fixtures.json` | Path to fixtures |
| `MOCK_STRATEGY` | `static` | `fixtures` | `static`, `fixtures`, or `llm` |
| `MOCK_TRANSPORT` | `stdio` | `streamable-http` | `stdio` or `streamable-http` |
| `MOCK_PORT` | `8080` | `8080` | Port for HTTP transport |
| `MOCK_LLM_MODEL` | `claude-haiku-4-5-20251001` | `claude-haiku-4-5-20251001` | Model for LLM strategy |

## Development

```bash
# Run tests (no container needed)
pip install -r requirements-dev.txt
pytest

# Build image
podman build -t mock-mcp-server:latest -f Containerfile .

# Run with a config
podman run --rm -d -p 8080:8080 \
  -v ./configs/openshift-mcp-server/schema.json:/config/schema.json:ro,Z \
  -v ./configs/openshift-mcp-server/fixtures-oomkilled.json:/config/fixtures.json:ro,Z \
  -e MOCK_STRATEGY=fixtures \
  mock-mcp-server:latest
```

See `README.md` for full usage and curl test commands.

## Adding a New MCP Config

Follow **[SCHEMA.md](SCHEMA.md)** (required fields, `outputExample` rules, checklists).

1. Create `configs/<mcp-name>/schema.json` with all tools from the MCP server.
2. Optionally create `configs/<mcp-name>/fixtures-<scenario>.json` with a coherent multi-step scenario (one file per scenario).
3. Optionally create `configs/<mcp-name>/USAGE.md` with curl commands that exercise the fixtures.
4. Test: `python src/server.py --schema configs/<mcp-name>/schema.json --strategy static`

## Key Design Decisions

- **Schema ownership**: The MCP server developer publishes and maintains `schema.json` (see [SCHEMA.md](SCHEMA.md)). It is the contract between the MCP, the skills that use it, and the mock. Skill authors publish scenario fixtures.
- **Fixtures for certification, LLM for exploration**: Fixtures give deterministic pass/fail; LLM gives zero-maintenance coherence. Choose based on evaluation goal.
- **LLM strategy reuses the pipeline's LLM**: No extra infrastructure — the mock calls the same LLM endpoint the evaluation pipeline already provisions.
- **Generic server, config-driven**: One image, any MCP. The schema defines the tools; the fixtures define the responses.
