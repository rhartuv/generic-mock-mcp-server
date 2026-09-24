# schema.json specification

This is the contract MCP developers publish alongside their server so the generic mock can register the same tools and return useful responses. The format is independent of how you author the schema; generating it from OpenAPI or Compass is out of scope.

## Ownership

| File | Owner | Role |
|---|---|---|
| `schema.json` | **MCP developers** | Publish and maintain the tool catalog. This file is the contract between the real MCP, the skills that call it, and the mock. |
| `fixtures-<scenario>.json` | **Skill authors** | Publish one fixtures file per evaluation scenario. Fixtures are not part of the MCP server; they describe a coherent multi-step flow for skill evaluation. |

Copies under `configs/<mcp-name>/` in this repository are evaluation snapshots. The MCP developer’s published `schema.json` is the source of truth.

## `schema.json`

A JSON object. The mock fails to start if the file is missing, is not valid JSON, is not an object, or has no `tools` array. Every `tools[]` entry must be an object with a non-empty `name`.

### Top-level fields

| Field | Spec | Runtime | Description |
|---|---|---|---|
| `tools` | **Required** | Required | Array of tool definitions. Empty is valid JSON but registers nothing. |
| `name` | Recommended | Optional | MCP server name. Used as the FastMCP server name; defaults to `mock-mcp-server`. |
| `version` | Recommended | Ignored | Schema or server version (for humans and for matching fixtures `recorded_from`). |
| `description` | Optional | Ignored | Human-readable description of the MCP. |

### Tool fields (`tools[]`)

| Field | Spec | Runtime | Description |
|---|---|---|---|
| `name` | **Required** | Required | Tool name registered with MCP. Must match the real server. Unique within the file. |
| `description` | **Required** | Optional | What the tool does. Exposed as the MCP tool docstring; the evaluating LLM sees this. Defaults to `Mock implementation of <name>` if omitted. |
| `inputSchema` | **Required** | Optional | JSON Schema for arguments (`type`, `properties`, `required`). Drives handler signatures. Defaults to `{}` (no parameters). |
| `outputSchema` | **Required** | Optional | JSON Schema for the response body. Used by the **LLM** strategy as the structure the model must follow. Static and fixtures strategies do not validate against it. Defaults to `{}`. |
| `outputExample` | **Required** | Optional | Concrete example payload. See [outputExample rules](#outputexample-rules). |

“Spec” is what MCP developers must publish. “Runtime” is what `server.py` currently requires to start. A schema that only satisfies runtime will register tools but is not a complete contract.

### `inputSchema` rules

JSON Schema object, typically:

```json
{
  "type": "object",
  "properties": {
    "cluster_id": { "type": "string", "description": "Cluster UUID" }
  },
  "required": ["cluster_id"]
}
```

- `properties` keys become handler parameters. Names must match the real MCP.
- `required` lists parameters with no default. Other properties are optional (`None` if omitted).
- Supported `type` values: `string`, `integer`, `number`, `boolean`, `object`, `array`. Anything else is treated as `string`.
- Extra JSON Schema keywords (`enum`, `description`, `format`, `items`, …) are allowed and useful for documentation and the LLM strategy; the mock does not enforce them at call time.

A tool with no arguments still needs an `inputSchema`:

```json
{ "type": "object", "properties": {}, "required": [] }
```

### `outputSchema` rules

JSON Schema for the JSON object (or other JSON value) the tool returns. Keep it aligned with the real MCP response.

The LLM strategy injects this schema into the prompt and asks the model to conform. Static and fixtures strategies ignore it except as documentation.

### `outputExample` rules

`outputExample` is the default mock payload for that tool.

| Strategy | How `outputExample` is used |
|---|---|
| `static` | Returned verbatim (`json.dumps`, indented) on every call. If omitted, `{"message": "<tool> completed successfully"}`. |
| `fixtures` | Returned when no unused fixture matches the call (name + `input`). If omitted or empty, `{"message": "<tool> completed (no fixture matched)"}`. |
| `llm` | Shown to the model as a reference example. If omitted, `{}`. |

Rules for authors:

1. Must be JSON-serializable. Prefer a JSON object that **conforms to `outputSchema`**.
2. Keep it **scenario-neutral** (generic happy path). Story-specific data (OOMKilled pods, a particular CVE) belongs in `fixtures-<scenario>.json`, not here.
3. Field names and types in the example should match `outputSchema` (and the real MCP). Skills that inspect the payload depend on this.
4. Do not put secrets, credentials, or real customer data in examples.

## `fixtures-<scenario>.json`

Owned by skill authors. One file per multi-step flow. The mock does not care about the filename; the container always reads `MOCK_FIXTURES_PATH` (default `/config/fixtures.json`). Use this naming in source control and mount the chosen file onto that path.

The mock fails to start if the file is missing (fixtures strategy), is not valid JSON, is not an object, or has no `sequence` array.

### Top-level fields

| Field | Spec | Runtime | Description |
|---|---|---|---|
| `sequence` | **Required** | Required | Ordered list of fixture steps. |
| `description` | Recommended | Ignored | What scenario this file covers. |
| `recorded_from` | Optional | Ignored | Schema name/version the fixtures were written against. |

### Sequence entry fields

| Field | Spec | Runtime | Description |
|---|---|---|---|
| `tool` | **Required** | Required to match | Must be a `name` from `schema.json`. Unknown names log a warning at startup. |
| `input` | Recommended | Optional | Argument subset used for matching. Missing or `{}` matches any arguments for that tool. |
| `output` | **Required** | Optional | Response body. Objects are JSON-encoded; strings are returned as-is. |

Matching:

- First unused sequence entry whose `tool` equals the call and whose `input` is a **subset** of the call arguments wins (extra call arguments are ignored). Values compare equal or as strings (`"1"` matches `1`; booleans are not coerced).
- After a match, that entry is consumed and will not match again.
- If nothing matches, the mock logs a warning and returns `outputExample`.

## Minimal valid example

```json
{
  "name": "example-mcp-server",
  "version": "1.0.0",
  "description": "Minimal schema a mock can register.",
  "tools": [
    {
      "name": "get_status",
      "description": "Return service health.",
      "inputSchema": {
        "type": "object",
        "properties": {
          "verbose": {
            "type": "boolean",
            "description": "Include extra diagnostic fields."
          }
        },
        "required": []
      },
      "outputSchema": {
        "type": "object",
        "properties": {
          "status": { "type": "string", "enum": ["ok", "degraded", "error"] },
          "version": { "type": "string" }
        },
        "required": ["status"]
      },
      "outputExample": {
        "status": "ok",
        "version": "1.0.0"
      }
    }
  ]
}
```

Matching fixtures file (`fixtures-health-check.json`):

```json
{
  "description": "Health check returns degraded once, then ok.",
  "recorded_from": "example-mcp-server schema v1.0.0",
  "sequence": [
    {
      "tool": "get_status",
      "input": { "verbose": true },
      "output": { "status": "degraded", "version": "1.0.0" }
    },
    {
      "tool": "get_status",
      "input": {},
      "output": { "status": "ok", "version": "1.0.0" }
    }
  ]
}
```

Smoke-test the schema with the static strategy:

```bash
python src/server.py --schema path/to/schema.json --strategy static
```

## Checklist (MCP developers)

- [ ] File is named `schema.json` and lives with the MCP server (repo, image, or published artifact).
- [ ] Top-level `name` and `version` identify this MCP.
- [ ] `tools` lists **every** tool the real server exposes, with the same `name` strings.
- [ ] Each tool has `description`, `inputSchema`, `outputSchema`, and `outputExample`.
- [ ] `inputSchema.properties` / `required` match the real tool arguments.
- [ ] `outputExample` conforms to `outputSchema` and is scenario-neutral.
- [ ] `python src/server.py --schema schema.json --strategy static` starts and `tools/list` shows the catalog.

## Checklist (skill authors)

- [ ] One `fixtures-<scenario>.json` per evaluation flow (for example `fixtures-oomkilled.json`).
- [ ] Every `sequence[].tool` exists in the MCP’s `schema.json`.
- [ ] `input` keys are properties from that tool’s `inputSchema` (empty `input` only when any arguments should match).
- [ ] `output` is coherent across steps (same IDs, names, and state).
- [ ] Scenario-specific data lives in fixtures, not in `schema.json` `outputExample`.
- [ ] Mount the file as `/config/fixtures.json` (or pass `--fixtures`) when running the mock.
