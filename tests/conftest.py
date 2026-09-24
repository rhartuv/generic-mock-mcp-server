"""Shared paths and fixtures for the mock MCP tests."""

from pathlib import Path

import pytest

from server import load_schema

TESTS = Path(__file__).parent


@pytest.fixture
def fixtures_path() -> Path:
    return TESTS / "fixtures.json"


@pytest.fixture
def create_sno_args() -> dict:
    return {
        "name": "edge-sno",
        "version": "4.18.2",
        "base_domain": "lab.example.com",
        "single_node": True,
    }


@pytest.fixture
def example_schema():
    return load_schema(TESTS / "schema.json")


@pytest.fixture
def example_tools(example_schema):
    return {tool["name"]: tool for tool in example_schema["tools"]}
