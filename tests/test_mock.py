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


def test_fixtures_match_by_input_not_call_order():
    from server import FixturesStrategy
    strategy = FixturesStrategy(
        Path(__file__).resolve().parent.parent / "configs/lightspeed-mcp/fixtures-cve-validation.json"
    )
    tool_schema = {"outputExample": {"id": "fallback"}}

    first = json.loads(strategy.generate(
        "vulnerability__get_cve", tool_schema, {"cve_id": "CVE-2026-99999"},
    ))
    second = json.loads(strategy.generate(
        "vulnerability__get_cve", tool_schema, {"cve_id": "CVE-2026-31337"},
    ))
    assert first["id"] == "CVE-2026-99999"
    assert second["id"] == "CVE-2026-31337"
    print("PASS: fixtures match by input even when call order differs from the file")


def test_fixtures_mismatch_warns_and_falls_back():
    import logging
    from server import FixturesStrategy

    strategy = FixturesStrategy(FIXTURES_PATH)
    schema = json.loads(SCHEMA_PATH.read_text())
    tool_map = {t["name"]: t for t in schema["tools"]}
    warnings: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if record.levelno >= logging.WARNING:
                warnings.append(record.getMessage())

    log = logging.getLogger("server")
    handler = Capture()
    log.addHandler(handler)
    try:
        parsed = json.loads(strategy.generate(
            "create_cluster",
            tool_map["create_cluster"],
            {"name": "wrong-name", "version": "4.18.2", "base_domain": "lab.example.com", "single_node": True},
        ))
    finally:
        log.removeHandler(handler)

    assert parsed["cluster_id"] == "ff001122-aabb-ccdd-eeff-001122334455"
    assert any("No fixture input matched create_cluster" in w for w in warnings)
    print("PASS: input mismatch logs a warning and falls back to outputExample")

    matched = json.loads(strategy.generate("create_cluster", tool_map["create_cluster"], {
        "name": "edge-sno", "version": "4.18.2", "base_domain": "lab.example.com", "single_node": True,
    }))
    assert matched["cluster_id"] == "cc001122-dead-beef-cafe-001122334455"
    print("PASS: matching input still returns the fixture after a mismatch")


def test_fixtures_exhaustion_falls_back():
    from server import FixturesStrategy
    strategy = FixturesStrategy(FIXTURES_PATH)
    schema = json.loads(SCHEMA_PATH.read_text())
    tool_map = {t["name"]: t for t in schema["tools"]}
    args = {
        "name": "edge-sno", "version": "4.18.2", "base_domain": "lab.example.com", "single_node": True,
    }
    first = json.loads(strategy.generate("create_cluster", tool_map["create_cluster"], args))
    second = json.loads(strategy.generate("create_cluster", tool_map["create_cluster"], args))
    assert first["cluster_id"] == "cc001122-dead-beef-cafe-001122334455"
    assert second["cluster_id"] == "ff001122-aabb-ccdd-eeff-001122334455"
    print("PASS: exhausted matching fixtures fall back to outputExample")


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


def test_lightspeed_cve_schema_covers_fixtures():
    """Smoke-check Lightspeed schema vs CVE skill contract and fixture files."""
    config_dir = Path(__file__).resolve().parent.parent / "configs" / "lightspeed-mcp"
    schema = json.loads((config_dir / "schema.json").read_text())
    tools = {t["name"]: t for t in schema["tools"]}

    get_cve = tools["vulnerability__get_cve"]
    get_cve_props = get_cve["inputSchema"]["properties"]
    assert "cve_id" in get_cve_props
    assert "cve" not in get_cve_props

    attrs = get_cve["outputSchema"]["properties"]["attributes"]["properties"]
    for key in ("advisory_available", "remediation", "advisories_list", "rules"):
        assert key in attrs, f"vulnerability__get_cve outputSchema missing {key}"
        assert key in get_cve["outputExample"]["attributes"], (
            f"vulnerability__get_cve outputExample missing {key}"
        )
    assert attrs["remediation"]["type"] == "integer"
    assert get_cve["outputExample"]["attributes"]["remediation"] == 2

    get_systems = tools["vulnerability__get_cve_systems"]
    systems_props = get_systems["inputSchema"]["properties"]
    assert "cve" in systems_props
    assert "cve_id" not in systems_props

    for fixtures_name in ("fixtures-cve-validation.json", "fixtures-cve-impact.json"):
        fixtures = json.loads((config_dir / fixtures_name).read_text())
        for step in fixtures["sequence"]:
            tool_name = step["tool"]
            assert tool_name in tools, f"{fixtures_name}: unknown tool {tool_name}"
            input_props = tools[tool_name]["inputSchema"].get("properties") or {}
            for key in step.get("input") or {}:
                assert key in input_props, (
                    f"{fixtures_name}: {tool_name} input {key!r} not in schema"
                )
            if tool_name == "vulnerability__get_cve":
                output_attrs = (step.get("output") or {}).get("attributes") or {}
                for key in output_attrs:
                    assert key in attrs, (
                        f"{fixtures_name}: get_cve output field {key!r} not in schema"
                    )
                if "remediation" in output_attrs:
                    assert isinstance(output_attrs["remediation"], int), (
                        f"{fixtures_name}: remediation must be an integer "
                        "(2 = automated, 0 = not available)"
                    )
    print("PASS: lightspeed CVE schema covers fixtures and skill contract")


if __name__ == "__main__":
    test_schema_loading()
    test_static_strategy()
    test_fixtures_strategy()
    test_fixtures_match_by_input_not_call_order()
    test_fixtures_mismatch_warns_and_falls_back()
    test_fixtures_exhaustion_falls_back()
    test_server_build()
    test_tool_execution()
    test_lightspeed_cve_schema_covers_fixtures()
    print("\nAll tests passed.")
