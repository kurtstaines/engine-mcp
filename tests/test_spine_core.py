"""Tests for the pure-Python Spine lanes and the CLI argument builders (no Spine editor needed)."""

import inspect
import json
from pathlib import Path

import pytest
from PIL import Image

from engine_mcp import spine_core as core

RIG = {
    "skeleton": {"spine": "4.2.00"},
    "bones": [{"name": "root"}, {"name": "arm", "parent": "root", "x": 10, "rotation": 30}],
    "slots": [{"name": "body_slot", "bone": "root", "attachment": "body"}],
    "skins": [
        {
            "name": "default",
            "attachments": {
                "body_slot": {
                    "body": {
                        "type": "mesh",
                        "uvs": [0, 0, 1, 0, 1, 1],
                        "triangles": [0, 1, 2],
                        # weighted vertices: count, then (bone, x, y, weight) per influence
                        "vertices": [1, 0, 0, 0, 1, 1, 1, 5, 0, 1, 2, 0, 5, 5, 0.5, 1, 1, 1, 0.5],
                        "hull": 3,
                    }
                }
            },
        }
    ],
    "animations": {
        "wave": {
            "bones": {
                "arm": [
                    {"time": 0, "rotation": 0},
                    {"time": 0.5, "rotation": 45},
                    {"time": 1, "rotation": 0},
                ]
            }
        }
    },
}


@pytest.fixture
def rig(tmp_path: Path) -> Path:
    path = tmp_path / "hero.json"
    path.write_text(json.dumps(RIG), encoding="utf-8")
    return path


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# --- prep lane ---------------------------------------------------------------


def test_rig_editor_prep_unweights_meshes_and_normalizes_timelines(rig: Path):
    prep = core.rig_editor_prep(str(rig))
    assert prep["ok"], prep
    assert prep["meshes_unweighted"] == 1 and prep["animations_normalized"] == 1
    fixed = _load(prep["editor_json"])
    verts = fixed["skins"][0]["attachments"]["body_slot"]["body"]["vertices"]
    assert len(verts) == 6  # 3 vertices, x/y each
    rotate = fixed["animations"]["wave"]["bones"]["arm"]["rotate"]
    assert [k["value"] for k in rotate] == [0, 45, 0]


def test_rig_editor_prep_leaves_source_untouched_and_is_idempotent(rig: Path):
    before = rig.read_text(encoding="utf-8")
    first = core.rig_editor_prep(str(rig))
    assert rig.read_text(encoding="utf-8") == before
    again = core.rig_editor_prep(first["editor_json"])
    assert again["meshes_unweighted"] == 0  # already unweighted
    assert _load(again["editor_json"]) == _load(first["editor_json"])


def test_rig_editor_prep_missing_file_returns_not_ok(tmp_path: Path):
    assert core.rig_editor_prep(str(tmp_path / "nope.json"))["ok"] is False


def test_fix_curves_converts_normalized_bezier_to_absolute():
    frames = [
        {"time": 0, "value": 0, "curve": [0.25, 0, 0.75, 1]},
        {"time": 2, "value": 10, "curve": [0.25, 0, 0.75, 1]},
    ]
    core._fix_curves(frames, ("value",))
    assert frames[0]["curve"] == [0.5, 0, 1.5, 10]
    assert "curve" not in frames[1]  # last key has no segment to curve


# --- scene lane --------------------------------------------------------------


def test_scene_compose_prefixes_names_and_places_roots(rig: Path, tmp_path: Path):
    scene = tmp_path / "out" / "duo.json"
    res = core.scene_compose(
        [
            {"json": str(rig), "prefix": "a", "x": -300},
            {"json": str(rig), "prefix": "b", "x": 300, "scale": 2},
        ],
        str(scene),
        "duo",
    )
    assert res["ok"], res
    assert res["total_bones"] == 5 and res["total_slots"] == 2 and res["total_animations"] == 2
    merged = _load(str(scene))
    bones = {b["name"]: b for b in merged["bones"]}
    assert len(bones) == len(merged["bones"]), "bone names must stay unique after merge"
    assert bones["a_root"]["parent"] == "root" and bones["a_root"]["x"] == -300
    assert bones["b_root"]["scaleX"] == 2 and bones["b_arm"]["parent"] == "b_root"
    assert set(merged["animations"]) == {"a_wave", "b_wave"}
    assert "b_arm" in merged["animations"]["b_wave"]["bones"]


def test_scene_compose_rebases_weighted_mesh_bone_indices(rig: Path, tmp_path: Path):
    scene = tmp_path / "duo.json"
    core.scene_compose(
        [{"json": str(rig), "prefix": "a"}, {"json": str(rig), "prefix": "b"}], scene
    )
    atts = _load(str(scene))["skins"][0]["attachments"]
    # scene root is bone 0, so character a starts at 1 and character b at 3
    assert atts["a_body_slot"]["body"]["vertices"][1] == 1
    assert atts["b_body_slot"]["body"]["vertices"][1] == 3


def test_scene_compose_rejects_empty_and_missing(tmp_path: Path):
    assert core.scene_compose([], str(tmp_path / "s.json"))["ok"] is False
    res = core.scene_compose([{"json": str(tmp_path / "nope.json")}], str(tmp_path / "s.json"))
    assert res["ok"] is False


def test_scene_preview_composites_textures(rig: Path, tmp_path: Path):
    Image.new("RGBA", (10, 20), "red").save(rig.with_suffix(".png"))  # found next to the rig
    out = tmp_path / "preview.png"
    res = core.scene_preview([{"json": str(rig), "x": 0, "y": 0}], str(out), width=100, height=50)
    assert res["ok"], res
    assert res["placed"][0]["at"] == [45, 30]  # centred on x, standing on the bottom edge
    img = Image.open(out)
    assert img.size == (100, 50) and img.getpixel((50, 40))[:3] == (255, 0, 0)


def test_scene_preview_missing_texture_returns_not_ok(tmp_path: Path):
    res = core.scene_preview([{"json": str(tmp_path / "x.json")}], str(tmp_path / "p.png"))
    assert res["ok"] is False


# --- image lane --------------------------------------------------------------


def test_analyze_alpha(tmp_path: Path):
    keyed, opaque, rgb = tmp_path / "keyed.png", tmp_path / "opaque.png", tmp_path / "rgb.png"
    Image.new("RGBA", (4, 4), (0, 0, 0, 0)).save(keyed)
    Image.new("RGBA", (4, 4), (0, 0, 0, 255)).save(opaque)
    Image.new("RGB", (4, 4)).save(rgb)
    assert core.analyze_alpha(str(keyed))["keyed"] is True
    assert core.analyze_alpha(str(keyed))["transparent_pct"] == 100.0
    assert core.analyze_alpha(str(opaque))["keyed"] is False
    assert core.analyze_alpha(str(rgb))["keyed"] is False
    assert core.analyze_alpha(str(tmp_path / "nope.png"))["ok"] is False


# --- editor lane (Spine CLI stubbed) -----------------------------------------


@pytest.fixture
def spine_args(monkeypatch: pytest.MonkeyPatch) -> list:
    """Capture the arguments each editor tool would pass to the Spine CLI."""
    calls: list = []

    def fake(args: list, timeout: int = 600) -> dict:
        calls.append(args)
        return {"ok": True}

    monkeypatch.setattr(core, "_spine", fake)
    return calls


def test_editor_tools_build_expected_cli_args(spine_args: list):
    core.spine_export("in.spine", "out", "binary", clean=True, version="4.2.00")
    core.spine_import("rig.skel", "p.spine", name="hero", scale=0.5)
    core.spine_pack("imgs", "out", name="hero")
    core.spine_unpack("a.atlas", "imgs", "out")
    core.spine_clean("p.spine")
    assert spine_args == [
        ["--update", "4.2.00", "-i", "in.spine", "-o", "out", "-m", "-e", "binary"],
        ["-i", "rig.skel", "-o", "p.spine", "-s", "0.5", "-r", "hero"],
        ["-i", "imgs", "-o", "out", "-p", "hero"],
        ["-i", "imgs", "-o", "out", "-c", "a.atlas"],
        ["-i", "p.spine", "-o", "p.spine", "-m", "-e", "json"],
    ]


def test_spine_import_preps_json_by_default(rig: Path, spine_args: list):
    core.spine_import(str(rig), "p.spine")
    assert spine_args[0][1].endswith("hero_editor.json")
    core.spine_import(str(rig), "p.spine", prep=False)
    assert spine_args[1][1] == str(rig)
    assert inspect.signature(core.scene_to_project).parameters["prep"].default is True


def test_missing_spine_cli_returns_not_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(core, "SPINE_EXE", str(tmp_path / "no-spine"))
    res = core.spine_version()
    assert res["ok"] is False and "SPINE_EXE" in res["stderr"]
