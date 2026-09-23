# Lightspeed MCP — Deploy & Test Guide

How to deploy the mock server for the Red Hat Lightspeed MCP and test it with Claude Code skills and with curl.

## Available fixture sets

| File | Skill | Steps | Scenario |
|---|---|---|---|
| `fixtures-cve-validation.json` | `/cve-validation` | 3 | Validate CVE-2026-31337 (remediable) and CVE-2026-99999 (not remediable) |
| `fixtures-cve-impact.json` | `/cve-impact` | 7 | Full CVE Impact Analysis: discover, drill-down, classify hosts, dashboard |

## 1. Build the container image

From the repo root:

```bash
podman build -t mock-mcp-server:latest -f Containerfile .
```

## 2. Run the mock server

Choose the fixture set that matches the skill you want to test.

**CVE Validation** (3-step flow):

```bash
podman run --name mock-lightspeed -d -p 8080:8080 \
  -v ./configs/lightspeed-mcp/schema.json:/config/schema.json:ro,Z \
  -v ./configs/lightspeed-mcp/fixtures-cve-validation.json:/config/fixtures.json:ro,Z \
  -e MOCK_STRATEGY=fixtures \
  mock-mcp-server:latest
```

**CVE Impact Analysis** (7-step flow):

```bash
podman run --name mock-lightspeed -d -p 8080:8080 \
  -v ./configs/lightspeed-mcp/schema.json:/config/schema.json:ro,Z \
  -v ./configs/lightspeed-mcp/fixtures-cve-impact.json:/config/fixtures.json:ro,Z \
  -e MOCK_STRATEGY=fixtures \
  mock-mcp-server:latest
```

Monitor logs in a separate terminal:

```bash
podman logs -f mock-lightspeed
```

## 3. Verify the server is running

```bash
curl -s -X POST http://127.0.0.1:8080/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{
    "jsonrpc": "2.0",
    "method": "initialize",
    "params": {
      "protocolVersion": "2024-11-05",
      "capabilities": {},
      "clientInfo": {"name": "curl-test", "version": "1.0"}
    },
    "id": 1
  }'
```

You should see an SSE response with `event: message` and the server capabilities.

> **Important**: Use `127.0.0.1`, not `localhost`. Podman only maps the port on IPv4 — `localhost` may resolve to IPv6 (`::1`) and fail silently.

## 4. Configure Claude Code

Create a `.mcp.json` in the directory where you will run Claude Code:

```json
{
  "mcpServers": {
    "lightspeed-mcp": {
      "type": "http",
      "url": "http://127.0.0.1:8080/mcp"
    }
  }
}
```

Key settings:
- **`type`** must be `"http"` (not `"sse"`). The server uses the Streamable HTTP transport.
- **`url`** must use `127.0.0.1` (not `localhost`) to avoid IPv6 resolution.

### Load skills

The mock server provides tools — the skills that use those tools are defined separately. To test a skill against this mock, place or symlink the skill's directory under `.claude/skills/` in the working directory where you run Claude Code:

```
.claude/skills/
├── cve-validation/
│   └── SKILL.md
└── mcp-lightspeed-validator/
    └── SKILL.md
```

Each skill's `SKILL.md` declares which MCP tools it uses via the `allowed-tools` frontmatter field. The mock must have those tools registered in its `schema.json` and, if using the fixtures strategy, matching entries in the fixtures file.

## 5. Test with Claude Code

Start Claude Code from the directory that contains the `.mcp.json`:

```bash
claude
```

Check the MCP connection status with `/mcp`. The lightspeed-mcp server should show status **connected**.

### Run the CVE Validation skill

```
/cve-validation "Validate CVE-2026-31337"
```

Expected behavior:
1. Calls `get_mcp_version` — returns version 1.4.2 (fixture 1/3)
2. Calls `vulnerability__get_cve` with `CVE-2026-31337` — returns CVSS 9.8, advisory available, RHSA-2026:4501 (fixture 2/3)
3. Reports CVE is valid and remediable

### Run the MCP Validator skill

```
/mcp-lightspeed-validator
```

Expected behavior: Calls `get_mcp_version` and confirms connectivity.

## 6. Teardown

```bash
podman stop mock-lightspeed && podman rm mock-lightspeed
```

## Curl test reference

For detailed curl commands that exercise every step of the CVE Impact Analysis fixture sequence (7 steps), see the sections below.

### Session setup

Every MCP session starts with an initialize + notification handshake.

```bash
# Initialize and capture session ID
SESSION=$(curl -s -D- -X POST http://127.0.0.1:8080/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
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

# Send initialized notification
curl -s -X POST http://127.0.0.1:8080/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: $SESSION" \
  -d '{"jsonrpc": "2.0", "method": "notifications/initialized"}'
```

### Call a tool

```bash
curl -s -X POST http://127.0.0.1:8080/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: $SESSION" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {
      "name": "get_mcp_version",
      "arguments": {}
    },
    "id": 10
  }'
```

### CVE Impact Analysis — full fixture sequence

Run in order against `fixtures-cve-impact.json` (not `fixtures-cve-validation.json`). Each step is also matched by `input`, so a call with a different `cve_id` / `host_id` will not receive another tool's payload.

| Step | Tool | Arguments | Expected result |
|---|---|---|---|
| 1 | `get_mcp_version` | `{}` | `{"version": "1.4.2", ...}` |
| 2 | `vulnerability__get_cves` | `{"impact":"7,6","sort":"-cvss_score","advisory_available":"true"}` | 3 CVEs, highest CVSS 9.8 |
| 3 | `vulnerability__get_cve` | `{"cve_id":"CVE-2026-31337"}` | OpenSSL RCE, CVSS 9.8, errata RHSA-2026:4501 |
| 4 | `vulnerability__get_cve_systems` | `{"cve":"CVE-2026-31337"}` | 3 systems (2 prod, 1 staging) |
| 5 | `inventory__get_host_details` | `{"host_id":"68ce32aa-..."}` | prod-webserver-01, RHEL 9.4 |
| 6 | `inventory__get_host_details` | `{"host_id":"f7e8d9c0-..."}` | prod-api-03, RHEL 9.4 |
| 7 | `vulnerability__load_cve_dashboard` | `{"query":"CVE-2026-31337"}` | Dashboard summary: 3 critical, 12 high |

### Fixture exhaustion

After all fixtures for a tool are consumed, subsequent calls fall back to the `outputExample` from the schema:

```bash
# Third call to inventory__get_host_details — falls back to schema default
curl -s -X POST http://127.0.0.1:8080/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: $SESSION" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {
      "name": "inventory__get_host_details",
      "arguments": {"host_id": "any-uuid"}
    },
    "id": 20
  }'
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Claude Code shows "connecting..." forever | URL uses `localhost` (resolves to IPv6) | Change URL to `127.0.0.1` in `.mcp.json` |
| Claude Code shows "socket closed unexpectedly" | Server built with `json_response=True` | Rebuild image — the server must use SSE (default) |
| Claude Code shows `type` field warning | Missing `"type": "http"` in `.mcp.json` | Add `"type": "http"` to the server config |
| curl returns "Connection reset by peer" | curl tried IPv6 | Use `curl -4` or `127.0.0.1` |
| Fixture returns wrong data | Call arguments did not match fixture `input`, or fixtures for that tool are used up | Check `input` in the fixtures file (e.g. `cve_id`); restart the container to reuse fixtures |
| No logs in `podman logs` | Server not started or crashed | Check `podman ps`; rebuild if code changed |

## Schema tools (46)

The schema contains 46 tools reflecting the tools exposed by the Red Hat Lightspeed MCP server. 13 have full inputSchema, outputSchema, and outputExample definitions. The remaining 33 are stubs with minimal schemas and a `{"status": "ok"}` default response.

Detailed tools: `get_mcp_version`, `vulnerability__get_cves`, `vulnerability__get_cve`, `vulnerability__get_cve_systems`, `vulnerability__get_system_cves`, `vulnerability__load_cve_dashboard`, `vulnerability__explain_cves`, `inventory__list_hosts`, `inventory__find_host_by_name`, `inventory__get_host_details`, `inventory__get_host_system_profile`, `inventory__load_inventory_dashboard`, `remediations__create_vuln_playbook`.
