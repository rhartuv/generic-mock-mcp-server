# OpenShift MCP — Deploy & Test Guide

How to deploy the mock server for the OpenShift MCP Server and test it with curl (and local MCP clients).

## Available fixture sets

| File | Steps | Scenario |
|---|---|---|
| `fixtures-oomkilled.json` | 12 | Pod in CrashLoopBackOff due to OOMKilled in namespace `myapp`: diagnose, fix memory limits, verify |

## 1. Build the container image

From the repo root:

```bash
podman build -t mock-mcp-server:latest -f Containerfile .
```

## 2. Run the mock server

**OOMKilled troubleshooting** (12-step flow):

```bash
podman run --name mock-openshift -d -p 8080:8080 \
  -v ./configs/openshift-mcp-server/schema.json:/config/schema.json:ro,Z \
  -v ./configs/openshift-mcp-server/fixtures-oomkilled.json:/config/fixtures.json:ro,Z \
  -e MOCK_STRATEGY=fixtures \
  mock-mcp-server:latest
```

Or locally without a container:

```bash
python src/server.py \
  --schema configs/openshift-mcp-server/schema.json \
  --strategy fixtures \
  --fixtures configs/openshift-mcp-server/fixtures-oomkilled.json \
  --transport streamable-http \
  --port 8080
```

Monitor logs in a separate terminal:

```bash
podman logs -f mock-openshift
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

You should see an SSE response with `event: message` and the server capabilities (`serverInfo.name`: `openshift-mcp-server`).

> **Important**: Use `127.0.0.1`, not `localhost`. Podman only maps the port on IPv4 — `localhost` may resolve to IPv6 (`::1`) and fail silently.

## 4. Configure a local MCP client (optional)

Create a `.mcp.json` in the directory where you will run the client:

```json
{
  "mcpServers": {
    "openshift-mcp-server": {
      "type": "http",
      "url": "http://127.0.0.1:8080/mcp"
    }
  }
}
```

Key settings:
- **`type`** must be `"http"` (not `"sse"`). The server uses the Streamable HTTP transport.
- **`url`** must use `127.0.0.1` (not `localhost`) to avoid IPv6 resolution.

## 5. Teardown

```bash
podman stop mock-openshift && podman rm mock-openshift
```

## Curl test reference

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

### List tools

```bash
curl -s -X POST http://127.0.0.1:8080/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: $SESSION" \
  -d '{"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 2}'
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
      "name": "namespaces_list",
      "arguments": {}
    },
    "id": 10
  }'
```

### OOMKilled troubleshooting — full fixture sequence

Run in order against `fixtures-oomkilled.json`:

| Step | Tool | Arguments (summary) | Expected result |
|---|---|---|---|
| 1 | `namespaces_list` | `{}` | Includes namespace `myapp` |
| 2 | `pods_list_in_namespace` | `namespace=myapp` | Pod `api-server-...-x2k1p` in CrashLoopBackOff |
| 3 | `pods_get` | pod + namespace | `last_termination_reason: OOMKilled` |
| 4 | `pods_log` | `previous: true` | Previous container logs |
| 5 | `pods_log` | current logs | Current crash logs |
| 6 | `events_list` | `namespace=myapp` | OOM / BackOff events |
| 7 | `nodes_top` | `{}` | Node resource usage |
| 8 | `pods_top` | `namespace=myapp` | Pod memory pressure |
| 9 | `resources_get` | Deployment `api-server` | Current memory limits too low |
| 10 | `resources_create_or_update` | patched Deployment | Memory limits increased |
| 11 | `pods_list_in_namespace` | `namespace=myapp` | Pod Running after fix |
| 12 | `resources_get` | Deployment `api-server` | Updated limits confirmed |

Fixtures are matched by **tool name in sequence order**, not by argument values. Restart the server (or container) to reset fixture cursors.

### Fixture exhaustion

After all fixtures for a tool are consumed, subsequent calls fall back to the `outputExample` from the schema.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Client shows "connecting..." forever | URL uses `localhost` (resolves to IPv6) | Change URL to `127.0.0.1` |
| curl returns "Connection reset by peer" | curl tried IPv6 | Use `curl -4` or `127.0.0.1` |
| Fixture returns unexpected data | Fixtures are sequential per tool name | Restart the server to reset fixture cursors |
| Wrong scenario responses | Mounted the wrong fixtures file | Confirm `fixtures-oomkilled.json` is mounted to `/config/fixtures.json` |

## Schema tools (20)

The schema covers OpenShift MCP core + config toolsets: contexts/kubeconfig, namespaces, pods, events, nodes, and generic resources.
