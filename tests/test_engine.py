"""Engine tests: response strategies, MCP registration, and fail-fast config loading."""

import asyncio
import json
import logging

import pytest

from server import (
    ConfigError,
    FixturesStrategy,
    StaticStrategy,
    build_server,
    load_fixtures,
    load_schema,
    main,
    warn_unknown_fixture_tools,
)


def test_static_strategy_returns_output_example(example_tools):
    strategy = StaticStrategy()
    listed = json.loads(strategy.generate("list_clusters", example_tools["list_clusters"], {}))
    assert listed["total"] == 2
    assert "clusters" in listed

    created = json.loads(
        strategy.generate(
            "create_cluster",
            example_tools["create_cluster"],
            {"name": "test", "version": "4.18.2", "base_domain": "test.com", "single_node": True},
        )
    )
    again = json.loads(
        strategy.generate("create_cluster", example_tools["create_cluster"], {"name": "other"})
    )
    assert created["cluster_id"] == again["cluster_id"]


def test_fixtures_follow_sequence(example_tools, fixtures_path, create_sno_args):
    strategy = FixturesStrategy(fixtures_path)

    created = json.loads(
        strategy.generate("create_cluster", example_tools["create_cluster"], create_sno_args)
    )
    assert created["cluster_id"] == "cc001122-dead-beef-cafe-001122334455"
    assert created["name"] == "edge-sno"

    cluster = json.loads(
        strategy.generate(
            "get_cluster",
            example_tools["get_cluster"],
            {"cluster_id": "cc001122-dead-beef-cafe-001122334455"},
        )
    )
    assert cluster["host_count"] == 1

    installed = json.loads(
        strategy.generate(
            "install_cluster",
            example_tools["install_cluster"],
            {"cluster_id": "cc001122-dead-beef-cafe-001122334455"},
        )
    )
    assert installed["status"] == "installing"


def test_fixtures_match_by_input_not_call_order(example_tools, tmp_path):
    path = tmp_path / "fixtures.json"
    path.write_text(
        json.dumps(
            {
                "sequence": [
                    {
                        "tool": "create_cluster",
                        "input": {"name": "alpha"},
                        "output": {"cluster_id": "cluster-alpha"},
                    },
                    {
                        "tool": "create_cluster",
                        "input": {"name": "beta"},
                        "output": {"cluster_id": "cluster-beta"},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    strategy = FixturesStrategy(path)
    tool = example_tools["create_cluster"]

    first = json.loads(strategy.generate("create_cluster", tool, {"name": "beta"}))
    second = json.loads(strategy.generate("create_cluster", tool, {"name": "alpha"}))
    assert first["cluster_id"] == "cluster-beta"
    assert second["cluster_id"] == "cluster-alpha"


def test_fixtures_mismatch_warns_and_falls_back(example_tools, fixtures_path, create_sno_args, caplog):
    strategy = FixturesStrategy(fixtures_path)
    with caplog.at_level(logging.WARNING, logger="server"):
        parsed = json.loads(
            strategy.generate(
                "create_cluster",
                example_tools["create_cluster"],
                {
                    "name": "wrong-name",
                    "version": "4.18.2",
                    "base_domain": "lab.example.com",
                    "single_node": True,
                },
            )
        )
    assert parsed["cluster_id"] == "ff001122-aabb-ccdd-eeff-001122334455"
    assert any("No fixture input matched create_cluster" in record.message for record in caplog.records)

    matched = json.loads(
        strategy.generate("create_cluster", example_tools["create_cluster"], create_sno_args)
    )
    assert matched["cluster_id"] == "cc001122-dead-beef-cafe-001122334455"


def test_fixtures_exhaustion_falls_back_to_output_example(example_tools, fixtures_path, create_sno_args):
    strategy = FixturesStrategy(fixtures_path)
    first = json.loads(
        strategy.generate("create_cluster", example_tools["create_cluster"], create_sno_args)
    )
    second = json.loads(
        strategy.generate("create_cluster", example_tools["create_cluster"], create_sno_args)
    )
    assert first["cluster_id"] == "cc001122-dead-beef-cafe-001122334455"
    assert second["cluster_id"] == "ff001122-aabb-ccdd-eeff-001122334455"


def test_server_registers_example_tools(example_schema):
    mcp = build_server(example_schema, StaticStrategy())
    tools = asyncio.run(mcp.list_tools())
    names = {tool.name for tool in tools}
    assert names == {"list_clusters", "create_cluster", "get_cluster", "install_cluster"}

    create = next(tool for tool in tools if tool.name == "create_cluster")
    props = create.input_schema.get("properties", {})
    assert {"name", "version", "single_node"} <= set(props)


def test_tool_execution_returns_fixture_response(example_schema, fixtures_path, create_sno_args):
    mcp = build_server(example_schema, FixturesStrategy(fixtures_path))

    async def run():
        result = await mcp.call_tool("create_cluster", create_sno_args)
        return json.loads(result.content[0].text)

    parsed = asyncio.run(run())
    assert parsed["cluster_id"] == "cc001122-dead-beef-cafe-001122334455"


def test_missing_schema_file(tmp_path):
    with pytest.raises(ConfigError, match="schema not found"):
        load_schema(tmp_path / "missing.json")


def test_missing_fixtures_file(tmp_path):
    with pytest.raises(ConfigError, match="fixtures not found"):
        load_fixtures(tmp_path / "missing.json")


def test_invalid_schema_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid JSON"):
        load_schema(path)


def test_invalid_fixtures_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid JSON"):
        load_fixtures(path)


def test_schema_requires_tools_list(tmp_path):
    path = tmp_path / "schema.json"
    path.write_text('{"name": "x"}', encoding="utf-8")
    with pytest.raises(ConfigError, match="tools"):
        load_schema(path)


def test_fixtures_require_sequence_list(tmp_path):
    path = tmp_path / "fixtures.json"
    path.write_text('{"description": "x"}', encoding="utf-8")
    with pytest.raises(ConfigError, match="sequence"):
        load_fixtures(path)


def test_main_exits_on_missing_schema(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["server.py", "--schema", str(tmp_path / "missing.json")])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    assert capsys.readouterr().err.startswith("ERROR:")


def test_unknown_fixture_tool_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="server"):
        warn_unknown_fixture_tools(
            {"tools": [{"name": "known"}]},
            [{"tool": "unknown_tool", "input": {}, "output": {}}],
        )
    assert any("unknown_tool" in record.message for record in caplog.records)
