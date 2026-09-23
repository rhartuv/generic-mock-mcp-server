"""Generic MCP Mock Server.

Reads an OpenAPI-style tool schema (schema.json) and dynamically registers
MCP tools via FastMCP. Supports three response strategies:

  - static:   Returns the outputExample from the schema (deterministic, no coherence).
  - fixtures: Returns pre-defined responses from a fixtures.json file (deterministic, coherent).
  - llm:      Uses an LLM to generate coherent responses based on schema + call history.

Usage:
    # Static mode (default)
    python server.py --schema schema.json

    # Fixtures mode
    python server.py --schema schema.json --strategy fixtures --fixtures fixtures.json

    # LLM mode
    python server.py --schema schema.json --strategy llm --llm-model claude-haiku-4-5-20251001

    # With custom transport
    python server.py --schema schema.json --transport streamable-http --port 8080
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer as FastMCP

log_file = os.environ.get("MOCK_LOG_FILE")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    **({"filename": log_file, "filemode": "a"} if log_file else {}),
)
logger = logging.getLogger(__name__)


class ResponseStrategy(ABC):
    """Base class for response generation strategies."""

    @abstractmethod
    def generate(self, tool_name: str, tool_schema: dict, arguments: dict[str, Any]) -> str:
        """Generate a response for a tool call."""
        ...


class StaticStrategy(ResponseStrategy):
    """Returns the outputExample from the schema. No state, no coherence."""

    def generate(self, tool_name: str, tool_schema: dict, arguments: dict[str, Any]) -> str:
        example = tool_schema.get("outputExample")
        if example is None:
            return json.dumps({"message": f"{tool_name} completed successfully"})
        return json.dumps(example, indent=2)


def _values_equal(expected: Any, actual: Any) -> bool:
    if expected == actual:
        return True
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected == actual
    return str(expected) == str(actual)


def fixture_input_matches(expected: Any, arguments: dict[str, Any]) -> bool:
    """True if fixture input is empty (any args) or is a subset of the call arguments."""
    if not expected:
        return True
    if not isinstance(expected, dict):
        return False
    for key, value in expected.items():
        if key not in arguments or not _values_equal(value, arguments[key]):
            return False
    return True


def _format_fixture_output(output: Any) -> str:
    if isinstance(output, str):
        return output
    return json.dumps(output, indent=2)


class FixturesStrategy(ResponseStrategy):
    """Returns responses from a fixtures file.

    Prefers the first unused sequence entry whose tool name matches and whose
    ``input`` is a subset of the call arguments. Empty ``input`` matches any
    arguments. If nothing matches, logs a warning and falls back to outputExample.
    """

    def __init__(self, fixtures_path: Path):
        with open(fixtures_path) as f:
            data = json.load(f)
        self._sequence = data.get("sequence", [])
        self._used: set[int] = set()

    def generate(self, tool_name: str, tool_schema: dict, arguments: dict[str, Any]) -> str:
        for index, fixture in enumerate(self._sequence):
            if index in self._used or fixture.get("tool") != tool_name:
                continue
            if fixture_input_matches(fixture.get("input"), arguments):
                self._used.add(index)
                return _format_fixture_output(fixture.get("output"))

        unused_for_tool = [
            fixture.get("input")
            for index, fixture in enumerate(self._sequence)
            if index not in self._used and fixture.get("tool") == tool_name
        ]
        if unused_for_tool:
            logger.warning(
                "No fixture input matched %s(%s); unused inputs=%s; falling back to outputExample",
                tool_name,
                json.dumps(arguments, default=str),
                json.dumps(unused_for_tool, default=str),
            )
        example = tool_schema.get("outputExample")
        if example:
            return json.dumps(example, indent=2)
        return json.dumps({"message": f"{tool_name} completed (no fixture matched)"})


class LLMStrategy(ResponseStrategy):
    """Uses an LLM to generate coherent responses based on schema and call history."""

    def __init__(self, model: str, api_key: str | None = None):
        import os
        try:
            from anthropic import Anthropic
        except ImportError:
            logger.error("anthropic package required for LLM strategy: pip install anthropic")
            raise

        self._client = Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self._model = model
        self._call_history: list[dict[str, Any]] = []

    def generate(self, tool_name: str, tool_schema: dict, arguments: dict[str, Any]) -> str:
        output_schema = tool_schema.get("outputSchema", {})
        output_example = tool_schema.get("outputExample", {})

        history_text = ""
        if self._call_history:
            history_text = "Previous tool calls in this session (maintain coherence with these):\n"
            for call in self._call_history:
                history_text += f"  - {call['tool']}({json.dumps(call['input'])}) → {call['output']}\n"

        prompt = f"""You are simulating an MCP server. Generate a realistic JSON response for this tool call.

Tool: {tool_name}
Description: {tool_schema.get('description', '')}
Input arguments: {json.dumps(arguments)}

Output schema (response MUST conform to this structure):
{json.dumps(output_schema, indent=2)}

Example output (for reference):
{json.dumps(output_example, indent=2)}

{history_text}

Rules:
1. Return ONLY valid JSON matching the output schema. No markdown, no explanation.
2. Use the input arguments to generate contextually appropriate values (e.g., if name="prod-cluster", reflect that in the response).
3. Maintain coherence with previous calls — if a cluster was created with a specific ID, use that same ID when referenced.
4. Generate plausible values (realistic UUIDs, valid IP addresses, proper timestamps).
"""

        response = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )

        response_text = response.content[0].text.strip()

        self._call_history.append({
            "tool": tool_name,
            "input": arguments,
            "output": response_text,
        })

        return response_text


def load_schema(schema_path: Path) -> dict:
    with open(schema_path) as f:
        return json.load(f)


def create_tool_handler(tool_def: dict, strategy: ResponseStrategy):
    """Create an async handler function for a tool definition."""
    import inspect

    tool_name = tool_def["name"]
    input_schema = tool_def.get("inputSchema", {})
    properties = input_schema.get("properties", {})
    required_params = set(input_schema.get("required", []))

    type_map = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "object": dict,
        "array": list,
    }

    params = []
    for param_name in sorted(properties, key=lambda p: p not in required_params):
        param_def = properties[param_name]
        param_type = param_def.get("type", "string")
        python_type = type_map.get(param_type, str)

        if param_name in required_params:
            params.append(inspect.Parameter(
                param_name,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=python_type,
            ))
        else:
            params.append(inspect.Parameter(
                param_name,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=None,
                annotation=python_type | None,
            ))

    async def handler(**kwargs: Any) -> str:
        logger.info("Tool called: %s(%s)", tool_name, json.dumps(kwargs, default=str))
        response = strategy.generate(tool_name, tool_def, kwargs)
        logger.info("Tool response: %s → %s", tool_name, response[:200])
        return response

    handler.__name__ = tool_name
    handler.__qualname__ = tool_name
    handler.__doc__ = tool_def.get("description", f"Mock implementation of {tool_name}")
    handler.__signature__ = inspect.Signature(params, return_annotation=str)
    handler.__annotations__ = {p.name: p.annotation for p in params}
    handler.__annotations__["return"] = str

    return handler


def build_server(schema: dict, strategy: ResponseStrategy, transport: str = "stdio") -> FastMCP:
    server_name = schema.get("name", "mock-mcp-server")

    mcp = FastMCP(server_name)

    for tool_def in schema.get("tools", []):
        handler = create_tool_handler(tool_def, strategy)
        mcp.tool()(handler)
        logger.info("Registered tool: %s", tool_def["name"])

    logger.info(
        "Mock MCP server '%s' ready with %d tools (strategy: %s)",
        server_name,
        len(schema.get("tools", [])),
        type(strategy).__name__,
    )

    return mcp


def main():
    parser = argparse.ArgumentParser(description="Generic MCP Mock Server")
    parser.add_argument(
        "--schema",
        default=os.environ.get("MOCK_SCHEMA_PATH"),
        help="Path to schema.json (env: MOCK_SCHEMA_PATH)",
    )
    parser.add_argument(
        "--strategy",
        choices=["static", "fixtures", "llm"],
        default=os.environ.get("MOCK_STRATEGY", "static"),
        help="Response generation strategy (env: MOCK_STRATEGY, default: static)",
    )
    parser.add_argument(
        "--fixtures",
        default=os.environ.get("MOCK_FIXTURES_PATH"),
        help="Path to fixtures.json (env: MOCK_FIXTURES_PATH)",
    )
    parser.add_argument(
        "--llm-model",
        default=os.environ.get("MOCK_LLM_MODEL", "claude-haiku-4-5-20251001"),
        help="Model for LLM strategy (env: MOCK_LLM_MODEL)",
    )
    parser.add_argument("--llm-api-key", help="API key for LLM strategy (or set ANTHROPIC_API_KEY)")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default=os.environ.get("MOCK_TRANSPORT", "stdio"),
        help="Transport protocol (env: MOCK_TRANSPORT, default: stdio)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("MOCK_PORT", "8080")),
        help="Port for HTTP transport (env: MOCK_PORT, default: 8080)",
    )

    args = parser.parse_args()

    if not args.schema:
        print("ERROR: --schema or MOCK_SCHEMA_PATH is required", file=sys.stderr)
        sys.exit(1)

    schema = load_schema(Path(args.schema))

    if args.strategy == "static":
        strategy = StaticStrategy()
    elif args.strategy == "fixtures":
        if not args.fixtures:
            print("ERROR: --fixtures required for fixtures strategy", file=sys.stderr)
            sys.exit(1)
        strategy = FixturesStrategy(Path(args.fixtures))
    elif args.strategy == "llm":
        strategy = LLMStrategy(model=args.llm_model, api_key=args.llm_api_key)
    else:
        strategy = StaticStrategy()

    mcp = build_server(schema, strategy, transport=args.transport)

    kwargs = {}
    if args.transport == "streamable-http":
        kwargs["host"] = "0.0.0.0"
        kwargs["port"] = args.port

    mcp.run(transport=args.transport, **kwargs)


if __name__ == "__main__":
    main()
