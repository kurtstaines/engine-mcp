"""Minimal example: merge two copies of a rig into one scene, then make it editor-importable.

Pure Python, no Spine install needed:

    python examples/compose_scene.py
"""

import shutil
from pathlib import Path

from engine_mcp import spine_core

HERE = Path(__file__).parent
OUT = HERE / "out"
rig = str(HERE / "mecha.json")

scene = spine_core.scene_compose(
    [
        {"json": rig, "prefix": "left", "x": -330},
        {"json": rig, "prefix": "right", "x": 330, "scale": 0.8},
    ],
    str(OUT / "duo.json"),
    scene_name="duo",
)
print("scene_compose:", scene)

prep = spine_core.rig_editor_prep(scene["scene_json"])
print("rig_editor_prep:", prep)

# The scene looks for its art in ./images/ next to the project file.
shutil.copytree(HERE / "images", OUT / "images", dirs_exist_ok=True)
# Next step, with Spine installed:
#   spine_core.spine_import(prep["editor_json"], str(OUT / "duo.spine"))
