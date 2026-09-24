"""Smoke tests for shipped configs and the Lightspeed CVE skill contract."""

from pathlib import Path

import pytest

from server import load_fixtures, load_schema

CONFIGS = Path(__file__).resolve().parent.parent / "configs"

SHIPPED = [
    pytest.param(
        CONFIGS / "lightspeed-mcp",
        ("fixtures-cve-validation.json", "fixtures-cve-impact.json"),
        id="lightspeed-mcp",
    ),
    pytest.param(
        CONFIGS / "openshift-mcp-server",
        ("fixtures-oomkilled.json",),
        id="openshift-mcp-server",
    ),
]


@pytest.mark.parametrize("config_dir, fixture_files", SHIPPED)
def test_shipped_fixtures_match_schema(config_dir: Path, fixture_files: tuple[str, ...]):
    schema = load_schema(config_dir / "schema.json")
    tools = {tool["name"]: tool for tool in schema["tools"]}

    for fixtures_name in fixture_files:
        fixtures = load_fixtures(config_dir / fixtures_name)
        for step in fixtures["sequence"]:
            tool_name = step["tool"]
            assert tool_name in tools, f"{fixtures_name}: unknown tool {tool_name}"
            input_props = tools[tool_name].get("inputSchema", {}).get("properties") or {}
            for key in step.get("input") or {}:
                assert key in input_props, (
                    f"{fixtures_name}: {tool_name} input {key!r} not in schema"
                )


def test_get_cve_skill_fields_are_in_schema_and_example():
    schema = load_schema(CONFIGS / "lightspeed-mcp" / "schema.json")
    tools = {tool["name"]: tool for tool in schema["tools"]}

    get_cve = tools["vulnerability__get_cve"]
    assert "cve_id" in get_cve["inputSchema"]["properties"]
    assert "cve" not in get_cve["inputSchema"]["properties"]

    attrs = get_cve["outputSchema"]["properties"]["attributes"]["properties"]
    example = get_cve["outputExample"]["attributes"]
    for key in ("advisory_available", "remediation", "advisories_list", "rules"):
        assert key in attrs
        assert key in example
    assert attrs["remediation"]["type"] == "integer"
    assert example["remediation"] == 2

    get_systems = tools["vulnerability__get_cve_systems"]
    assert "cve" in get_systems["inputSchema"]["properties"]
    assert "cve_id" not in get_systems["inputSchema"]["properties"]
