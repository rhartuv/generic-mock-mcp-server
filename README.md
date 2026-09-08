# Generic Mock MCP Server

A config-driven mock server that replaces real [MCP](https://modelcontextprotocol.io/) servers during AI skill evaluation. It reads a tool schema, dynamically registers MCP-compliant tools, and returns configurable responses — no credentials, no infrastructure, no side effects.

## Overview

AI skills depend on MCP servers for tool access. Evaluating skills end-to-end requires the agent to call tools, but real MCP servers have side effects, require credentials, and mix skill failures with infrastructure failures. This mock isolates the skill so evaluation measures only how well it guides the LLM.

**One image, any MCP.** The schema defines the tools; the fixtures define the responses. No per-MCP custom code needed.

## Features

- Dynamic tool registration from any `schema.json`
- Three response strategies: static, fixtures, and LLM-generated
- Streamable HTTP and stdio transports
- Session-aware fixture sequencing with automatic fallback
- Container-ready (UBI 10 minimal, non-root, ~50MB)

## Project structure

```
.
├── README.md
├── Containerfile
├── requirements.txt
├── src/
│   └── server.py              # Mock server implementation
├── tests/
│   ├── test_mock.py            # Unit tests
│   ├── schema.json             # Test fixture schema
│   └── fixtures.json           # Test fixture responses
└── configs/                    # Pre-built MCP configs
    ├── openshift-mcp-server/
    │   ├── schema.json
    │   └── fixtures.json
    └── lightspeed-mcp/
        ├── schema.json
        ├── fixtures.json
        └── USAGE.md            # Curl test guide
```

## Prerequisites

- Python 3.12+
- Podman or Docker (for container builds)

## Installation

```bash
pip install -r requirements.txt
```

## Configuration

The server requires one or two JSON files:

| File | Required | Purpose |
|---|---|---|
| `schema.json` | Yes | Tool definitions (name, description, inputSchema, outputSchema, outputExample) |
| `fixtures.json` | For `fixtures` strategy | Ordered sequence of tool responses for multi-step flows |

### Schema format

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

### Fixtures format

Skill authors provide a `fixtures.json` for multi-step flows:

```json
{
  "sequence": [
    {"tool": "tool_a", "input": {...}, "output": {...}},
    {"tool": "tool_b", "input": {...}, "output": {...}}
  ]
}
```

Responses are matched by tool name and served in sequence order. When fixtures for a tool are exhausted, the server falls back to the schema's `outputExample`.

### Response strategies

| Strategy | Behavior | Use case |
|---|---|---|
| `static` | Returns the `outputExample` from the schema verbatim | Single-tool testing, smoke tests |
| `fixtures` | Returns ordered responses from `fixtures.json`, matched by tool name | Multi-step skill evaluation, certification gates |
| `llm` | Generates coherent responses via LLM (requires `ANTHROPIC_API_KEY`) | Exploratory testing, regression sweeps |

### Settings

All settings can be passed as CLI flags (which take precedence) or environment variables.

| Variable | CLI flag | Default | Description |
|---|---|---|---|
| `MOCK_SCHEMA_PATH` | `--schema` | — | Path to tool schema (required) |
| `MOCK_FIXTURES_PATH` | `--fixtures` | — | Path to fixtures file |
| `MOCK_STRATEGY` | `--strategy` | `static` | `static`, `fixtures`, or `llm` |
| `MOCK_TRANSPORT` | `--transport` | `stdio` | `stdio` or `streamable-http` |
| `MOCK_PORT` | `--port` | `8080` | Port for HTTP transport |
| `MOCK_LLM_MODEL` | `--llm-model` | `claude-haiku-4-5-20251001` | Model for LLM strategy |

## Usage

### Run locally

```bash
# Static strategy (returns outputExample from schema)
python src/server.py --schema configs/lightspeed-mcp/schema.json --strategy static

# Fixtures strategy (returns ordered responses)
python src/server.py \
  --schema configs/lightspeed-mcp/schema.json \
  --strategy fixtures \
  --fixtures configs/lightspeed-mcp/fixtures.json

# HTTP transport (exposes JSON-RPC at POST /mcp)
python src/server.py \
  --schema configs/lightspeed-mcp/schema.json \
  --strategy fixtures \
  --fixtures configs/lightspeed-mcp/fixtures.json \
  --transport streamable-http --port 8080
```

### Run in container

Build:

```bash
podman build -t mock-mcp-server:latest -f Containerfile .
```

HTTP transport (default):

```bash
podman run --rm -d -p 8080:8080 \
  -v ./configs/<mcp-name>/schema.json:/config/schema.json:ro,Z \
  -v ./configs/<mcp-name>/fixtures.json:/config/fixtures.json:ro,Z \
  -e MOCK_STRATEGY=fixtures \
  mock-mcp-server:latest
```

stdio transport (for local MCP clients like Claude Code):

```bash
podman run --rm -i \
  -e MOCK_TRANSPORT=stdio \
  -e MOCK_STRATEGY=fixtures \
  -v ./configs/<mcp-name>/schema.json:/config/schema.json:ro,Z \
  -v ./configs/<mcp-name>/fixtures.json:/config/fixtures.json:ro,Z \
  mock-mcp-server:latest
```

## Testing with curl

The MCP protocol requires a session handshake before tool calls. The server exposes JSON-RPC at `POST /mcp`.

### 1. Initialize and capture session ID

```bash
SESSION=$(curl -s -D- -X POST http://127.0.0.1:8080/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "initialize",
    "params": {
      "protocolVersion": "2024-11-05",
      "capabilities": {},
      "clientInfo": {"name": "curl-test", "version": "1.0"}
    },
    "id": 1
  }' 2>&1 | grep -i 'mcp-session-id' | awk '{print $2}' | tr -d '\r')

echo "Session: $SESSION"
```

### 2. Send initialized notification

```bash
curl -s -X POST http://127.0.0.1:8080/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -H "Mcp-Session-Id: $SESSION" \
  -d '{"jsonrpc": "2.0", "method": "notifications/initialized"}'
```

### 3. List registered tools

```bash
curl -s -X POST http://127.0.0.1:8080/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -H "Mcp-Session-Id: $SESSION" \
  -d '{"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 2}' | python3 -m json.tool
```

### 4. Call a tool

```bash
curl -s -X POST http://127.0.0.1:8080/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -H "Mcp-Session-Id: $SESSION" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {
      "name": "<tool_name>",
      "arguments": {}
    },
    "id": 3
  }' | python3 -m json.tool
```

For MCP-specific curl test guides with complete step-by-step commands, see the `USAGE.md` inside each `configs/<mcp-name>/` directory.

## Running tests

```bash
python tests/test_mock.py
```

## Adding a new MCP config

1. Create `configs/<mcp-name>/schema.json` with all tools from the real MCP server.
2. Optionally create `configs/<mcp-name>/fixtures.json` with a coherent multi-step scenario.
3. Optionally create `configs/<mcp-name>/USAGE.md` with curl commands that exercise the fixtures.
4. Smoke test:
   ```bash
   python src/server.py --schema configs/<mcp-name>/schema.json --strategy static
   ```

## Available configs

| Config | MCP server | Fixtures | Test guide |
|---|---|---|---|
| `configs/openshift-mcp-server/` | openshift-mcp-server | OOMKilled troubleshooting | — |
| `configs/lightspeed-mcp/` | lightspeed-mcp | CVE Impact Analysis, CVE Validation | [USAGE.md](configs/lightspeed-mcp/USAGE.md) |

## License

Apache-2.0
