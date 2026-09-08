"""Quick validation test for the mock MCP server.

Tests that the server correctly loads a schema, registers tools,
and returns appropriate responses for each strategy.

Usage:
    python tests/test_mock.py
"""

from __future__ import annotations

import json
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

SCHEMA_PATH = Path(__file__).parent / "schema.json"
FIXTURES_PATH = Path(__file__).parent / "fixtures.json"


def test_schema_loading():
    from server import load_schema
    schema = load_schema(SCHEMA_PATH)
    assert schema["name"] == "openshift-cluster-management"
    assert len(schema["tools"]) == 4
    tool_names = {t["name"] for t in schema["tools"]}
    assert tool_names == {"list_clusters", "create_cluster", "get_cluster", "install_cluster"}
    print("PASS: schema loading")


def test_static_strategy():
    from server import StaticStrategy
    strategy = StaticStrategy()

    schema = json.loads(SCHEMA_PATH.read_text())
    tool_map = {t["name"]: t for t in schema["tools"]}

    response = strategy.generate("list_clusters", tool_map["list_clusters"], {})
    parsed = json.loads(response)
    assert "clusters" in parsed
    assert parsed["total"] == 2
    print("PASS: static strategy returns outputExample")

    response = strategy.generate("create_cluster", tool_map["create_cluster"], {
        "name": "test", "version": "4.18.2", "base_domain": "test.com", "single_node": True,
    })
    parsed = json.loads(response)
    assert "cluster_id" in parsed
    assert parsed["status"] == "preparing"
    print("PASS: static strategy ignores input (returns same example)")


def test_fixtures_strategy():
    from server import FixturesStrategy
    strategy = FixturesStrategy(FIXTURES_PATH)

    schema = json.loads(SCHEMA_PATH.read_text())
    tool_map = {t["name"]: t for t in schema["tools"]}

    response = strategy.generate("create_cluster", tool_map["create_cluster"], {
        "name": "edge-sno", "version": "4.18.2", "base_domain": "lab.example.com", "single_node": True,
    })
    parsed = json.loads(response)
    assert parsed["cluster_id"] == "cc001122-dead-beef-cafe-001122334455"
    assert parsed["name"] == "edge-sno"
    assert parsed["high_availability_mode"] == "None"
    print("PASS: fixtures strategy returns correct fixture for create_cluster")

    response = strategy.generate("get_cluster", tool_map["get_cluster"], {
        "cluster_id": "cc001122-dead-beef-cafe-001122334455",
    })
    parsed = json.loads(response)
    assert parsed["cluster_id"] == "cc001122-dead-beef-cafe-001122334455"
    assert parsed["name"] == "edge-sno"
    assert parsed["host_count"] == 1
    print("PASS: fixtures strategy maintains coherence across calls")

    response = strategy.generate("install_cluster", tool_map["install_cluster"], {
        "cluster_id": "cc001122-dead-beef-cafe-001122334455",
    })
    parsed = json.loads(response)
    assert parsed["status"] == "installing"
    print("PASS: fixtures strategy returns correct fixture for install_cluster")


def test_server_build():
    from server import build_server, load_schema, StaticStrategy
    schema = load_schema(SCHEMA_PATH)
    strategy = StaticStrategy()
    mcp = build_server(schema, strategy)

    tools = asyncio.run(mcp.list_tools())
    tool_names = {t.name for t in tools}
    assert tool_names == {"list_clusters", "create_cluster", "get_cluster", "install_cluster"}
    print("PASS: server registers all 4 tools")

    for tool in tools:
        if tool.name == "create_cluster":
            schema_props = tool.input_schema.get("properties", {})
            assert "name" in schema_props
            assert "version" in schema_props
            assert "single_node" in schema_props
            print("PASS: create_cluster has correct input schema")


def test_tool_execution():
    from server import build_server, load_schema, FixturesStrategy
    schema = load_schema(SCHEMA_PATH)
    strategy = FixturesStrategy(FIXTURES_PATH)
    mcp = build_server(schema, strategy)

    async def run():
        result = await mcp.call_tool("create_cluster", {
            "name": "edge-sno",
            "version": "4.18.2",
            "base_domain": "lab.example.com",
            "single_node": True,
        })
        text = result.content[0].text
        parsed = json.loads(text)
        assert parsed["cluster_id"] == "cc001122-dead-beef-cafe-001122334455"
        print("PASS: tool execution via MCP returns fixture response")

    asyncio.run(run())


if __name__ == "__main__":
    test_schema_loading()
    test_static_strategy()
    test_fixtures_strategy()
    test_server_build()
    test_tool_execution()
    print("\nAll tests passed.")
