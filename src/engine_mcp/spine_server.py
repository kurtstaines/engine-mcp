#!/usr/bin/env python3
"""Spine MCP server -- drive the Spine editor CLI and a JSON scene compositor
from any MCP client (Claude Code, Codex CLI, Cursor, ...).

Run (stdio):   engine-mcp-spine            (or: python -m engine_mcp.spine_server)
Self-test:     engine-mcp-spine --selftest  (lists tools, starts no server)

Env: SPINE_EXE (default C:\\Program Files\\Spine\\Spine.com), SPINE_UPDATE (pin editor version).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from mcp.server.fastmcp import FastMCP  # noqa: E402

from engine_mcp import spine_core as core  # noqa: E402

mcp = FastMCP("spine")


@mcp.tool()
def spine_version() -> dict[str, Any]:
    """Report Spine editor/launcher version, license state, and the CLI path in use."""
    return core.spine_version()


@mcp.tool()
def spine_export(
    input_path: str, output_path: str, export: str = "json", clean: bool = False, version: str = ""
) -> dict[str, Any]:
    """Export a .spine project or skeleton data via the official Spine CLI.
    `export`: shorthand 'json', 'binary', 'json+pack', 'binary+pack', OR a path to an
    export-settings JSON saved from the editor's Export dialog (required for
    PNG/GIF/APNG/video exports). `version` optionally pins the editor version."""
    return core.spine_export(input_path, output_path, export, clean, version)


@mcp.tool()
def spine_import(
    data_path: str, project_path: str, name: str = "", scale: float = 0.0, prep: bool = True
) -> dict[str, Any]:
    """Import skeleton JSON or .skel data into a .spine project file (creates it).
    `prep` (default true) first repairs generated JSON into editor-valid form
    (rig_editor_prep); `scale` scales on import; `name` picks one skeleton."""
    return core.spine_import(data_path, project_path, name, scale, prep)


@mcp.tool()
def rig_editor_prep(rig_json: str, output: str = "") -> dict[str, Any]:
    """Repair generated rig JSON so the Spine editor imports it: weighted
    meshes -> unweighted (editor re-binds natively), loose keyframe lists ->
    valid Spine 4.x timelines. Writes <stem>_editor.json; non-destructive."""
    return core.rig_editor_prep(rig_json, output)


@mcp.tool()
def spine_pack(
    images_dir: str,
    output_dir: str,
    name: str = "atlas",
    pack_settings: str = "",
    project: str = "",
) -> dict[str, Any]:
    """Pack a folder of images into a Spine texture atlas (.atlas + PNG pages)."""
    return core.spine_pack(images_dir, output_dir, name, pack_settings, project)


@mcp.tool()
def spine_unpack(atlas_path: str, images_dir: str, output_dir: str) -> dict[str, Any]:
    """Unpack a texture atlas back into individual images."""
    return core.spine_unpack(atlas_path, images_dir, output_dir)


@mcp.tool()
def spine_clean(project_path: str, output_path: str = "") -> dict[str, Any]:
    """Run Spine's animation cleanup on a project (removes redundant keys)."""
    return core.spine_clean(project_path, output_path)


@mcp.tool()
def spine_open_editor(project_path: str = "") -> dict[str, Any]:
    """Open the Spine editor GUI, optionally loading a project (non-blocking)."""
    return core.spine_open_editor(project_path)


@mcp.tool()
def analyze_alpha(image: str) -> dict[str, Any]:
    """Inspect an image's alpha channel (transparent/opaque/partial
    percentages) and rule whether it's already keyed or needs a cutout pass."""
    return core.analyze_alpha(image)


@mcp.tool()
def scene_compose(
    characters: list[dict[str, Any]], output_json: str, scene_name: str = "scene"
) -> dict[str, Any]:
    """Merge N rigged characters into ONE Spine skeleton JSON (a scene).
    characters: [{"json": rigPath, "prefix": name, "x": 0, "y": 0, "scale": 1.0,
    "rotation": 0}, ...]. Bones/slots/skins/constraints/animations are prefixed
    and grafted under per-character roots; weighted-mesh bone indices are re-based."""
    return core.scene_compose(characters, output_json, scene_name)


@mcp.tool()
def scene_preview(
    characters: list[dict[str, Any]], output_png: str, width: int = 1920, height: int = 1080
) -> dict[str, Any]:
    """Composite character textures at their scene placements -> layout preview PNG.
    characters: [{"json" or "texture": path, "x": 0, "y": 0, "scale": 1.0}, ...]."""
    return core.scene_preview(characters, output_png, width, height)


@mcp.tool()
def scene_to_project(scene_json: str, project_path: str, scale: float = 0.0) -> dict[str, Any]:
    """Import a composed scene JSON into a .spine project for polish in the editor."""
    return core.scene_to_project(scene_json, project_path, scale)


def _selftest() -> int:
    import asyncio

    tools = asyncio.run(mcp.list_tools())
    print(f"[spine-mcp] {len(tools)} tools registered:")
    for t in tools:
        print(f"  - {t.name}")
    print(
        f"[spine-mcp] SPINE_EXE = {core.SPINE_EXE} "
        f"({'found' if Path(core.SPINE_EXE).exists() else 'MISSING'})"
    )
    return 0


def main() -> int:
    """Console entry point: run the stdio server, or --selftest to list tools."""
    if "--selftest" in sys.argv:
        return _selftest()
    mcp.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
