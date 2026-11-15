"""The MCP server registers every tool and a tool call round-trips through FastMCP."""

import asyncio
from pathlib import Path

from engine_mcp import spine_server

EXPECTED = {
    "spine_version", "spine_export", "spine_import", "spine_pack", "spine_unpack",
    "spine_clean", "spine_open_editor", "rig_editor_prep", "analyze_alpha",
    "scene_compose", "scene_preview", "scene_to_project",
}  # fmt: skip


def test_server_registers_all_tools_with_descriptions():
    tools = asyncio.run(spine_server.mcp.list_tools())
    assert {t.name for t in tools} == EXPECTED
    assert all(t.description for t in tools)


def test_tool_call_round_trips(tmp_path: Path):
    args = {"rig_json": str(tmp_path / "nope.json")}
    result = asyncio.run(spine_server.mcp.call_tool("rig_editor_prep", args))
    assert "rig not found" in str(result)


def test_selftest_exits_zero(capsys):
    assert spine_server._selftest() == 0
    assert "12 tools registered" in capsys.readouterr().out
