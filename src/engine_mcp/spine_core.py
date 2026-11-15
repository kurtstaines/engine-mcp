#!/usr/bin/env python3
"""spine_core -- operations behind the Spine MCP server.

Three lanes:
  EDITOR lane -> official Spine CLI: version / export / import / pack / unpack / clean / open
  PREP lane   -> repair skeleton JSON so the Spine 4.x editor importer accepts it
  SCENE lane  -> pure-JSON multi-skeleton compositor and layout preview

Every function returns a dict with an "ok" bool so MCP tools and scripts share one contract.
"""

from __future__ import annotations

import copy
import json
import math
import os
import subprocess
import time
from pathlib import Path
from typing import Any

SPINE_EXE = os.environ.get("SPINE_EXE", r"C:\Program Files\Spine\Spine.com")
SPINE_UPDATE = os.environ.get("SPINE_UPDATE", "")  # pin editor version, e.g. "4.3.23"


def _run(cmd: list[str], timeout: int = 600) -> dict[str, Any]:
    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "command": subprocess.list2cmdline(cmd),
            "stdout": (proc.stdout or "")[-8000:],
            "stderr": (proc.stderr or "")[-4000:],
            "seconds": round(time.time() - start, 1),
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "exit_code": -1,
            "command": subprocess.list2cmdline(cmd),
            "stdout": "",
            "stderr": f"timeout after {timeout}s",
            "seconds": timeout,
        }
    except OSError as exc:
        return {
            "ok": False,
            "exit_code": -1,
            "command": subprocess.list2cmdline(cmd),
            "stdout": "",
            "stderr": str(exc),
            "seconds": 0,
        }


def _spine(args: list[str], timeout: int = 600) -> dict[str, Any]:
    if not Path(SPINE_EXE).exists():
        return {
            "ok": False,
            "stderr": f"Spine CLI not found at {SPINE_EXE}. "
            "Set SPINE_EXE env var to Spine.com path.",
        }
    cmd = [SPINE_EXE]
    if SPINE_UPDATE:
        cmd += ["--update", SPINE_UPDATE]
    return _run(cmd + args, timeout=timeout)


def spine_version() -> dict[str, Any]:
    """Report the Spine editor/launcher version and the CLI path in use."""
    r = _spine(["-v"], timeout=180)
    r["spine_exe"] = SPINE_EXE
    return r


def spine_export(
    input_path: str, output_path: str, export: str = "json", clean: bool = False, version: str = ""
) -> dict[str, Any]:
    """Export a .spine project or data file. `export` is a shorthand
    (json, binary, json+pack, binary+pack) or a path to an export settings JSON
    saved from the editor's Export dialog (required for PNG/GIF/video)."""
    args = []
    if version:
        args += ["--update", version]
    args += ["-i", input_path, "-o", output_path]
    if clean:
        args += ["-m"]
    args += ["-e", export]
    r = _spine(args)
    r["output"] = output_path
    return r


def spine_import(
    data_path: str, project_path: str, name: str = "", scale: float = 0.0, prep: bool = True
) -> dict[str, Any]:
    """Import skeleton JSON / .skel data into a .spine project file.
    prep=True (default) first runs rig_editor_prep (generated JSON -> editor-valid JSON).
    Pass prep=False to import the file as-is."""
    if prep and data_path.endswith(".json"):
        prepped = rig_editor_prep(data_path)
        if not prepped.get("ok"):
            return prepped
        data_path = prepped["editor_json"]
    args = ["-i", data_path, "-o", project_path]
    if scale:
        args += ["-s", str(scale)]
    args += ["-r"]
    if name:
        args += [name]
    r = _spine(args)
    r["project"] = project_path
    return r


def spine_pack(
    images_dir: str,
    output_dir: str,
    name: str = "atlas",
    pack_settings: str = "",
    project: str = "",
) -> dict[str, Any]:
    """Pack a folder of images into a texture atlas."""
    args = ["-i", images_dir]
    if project:
        args += ["-j", project]
    args += ["-o", output_dir]
    if pack_settings:
        args += ["-n", name, "-p", pack_settings]
    else:
        args += ["-p", name]
    return _spine(args)


def spine_unpack(atlas_path: str, images_dir: str, output_dir: str) -> dict[str, Any]:
    """Unpack a texture atlas back into individual images."""
    return _spine(["-i", images_dir, "-o", output_dir, "-c", atlas_path])


def spine_clean(project_path: str, output_path: str = "") -> dict[str, Any]:
    """Run Spine animation cleanup on a project."""
    out = output_path or project_path
    return _spine(["-i", project_path, "-o", out, "-m", "-e", "json"])


def spine_open_editor(project_path: str = "") -> dict[str, Any]:
    """Open the Spine editor GUI (non-blocking), optionally with a project."""
    exe = SPINE_EXE.replace("Spine.com", "Spine.exe")
    if not Path(exe).exists():
        exe = SPINE_EXE
    try:
        cmd = [exe] + ([project_path] if project_path else [])
        subprocess.Popen(cmd, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        return {"ok": True, "launched": exe, "project": project_path or "(none)"}
    except OSError as exc:
        return {"ok": False, "stderr": str(exc)}


def analyze_alpha(image: str) -> dict[str, Any]:
    """Inspect an image's alpha channel to decide whether it needs a cutout pass.
    keyed=True (already transparent) -> skip cutout; otherwise cutout needed."""
    try:
        from PIL import Image
    except ImportError:
        return {"ok": False, "stderr": "Pillow not installed (pip install pillow)"}
    p = Path(image)
    if not p.exists():
        return {"ok": False, "stderr": f"image not found: {p}"}
    img = Image.open(p)
    if img.mode != "RGBA":
        return {
            "ok": True,
            "mode": img.mode,
            "size": list(img.size),
            "keyed": False,
            "transparent_pct": 0.0,
            "opaque_pct": 100.0,
            "partial_pct": 0.0,
            "verdict": "no alpha channel -> cutout needed",
        }
    hist = img.getchannel("A").histogram()
    total = img.width * img.height
    transparent = sum(hist[:16]) / total * 100
    opaque = sum(hist[240:]) / total * 100
    partial = 100 - transparent - opaque
    keyed = transparent >= 3.0
    return {
        "ok": True,
        "mode": "RGBA",
        "size": list(img.size),
        "keyed": keyed,
        "transparent_pct": round(transparent, 1),
        "opaque_pct": round(opaque, 1),
        "partial_pct": round(partial, 1),
        "verdict": "already keyed -> use alpha directly"
        if keyed
        else "opaque alpha -> cutout needed",
    }


def _world_transforms(bones: list[dict[str, Any]]) -> dict[str, Any]:
    world = {}
    for b in bones:  # assumes parents precede children, as in Spine JSON
        rot = math.radians(b.get("rotation", 0))
        sx, sy = b.get("scaleX", 1), b.get("scaleY", 1)
        la, lb = math.cos(rot) * sx, -math.sin(rot) * sy
        lc, ld = math.sin(rot) * sx, math.cos(rot) * sy
        x, y = b.get("x", 0), b.get("y", 0)
        p = world.get(b.get("parent"))
        if p is None:
            world[b["name"]] = (la, lb, lc, ld, x, y)
        else:
            pa, pb, pc, pd, px, py = p
            world[b["name"]] = (
                pa * la + pb * lc,
                pa * lb + pb * ld,
                pc * la + pd * lc,
                pc * lb + pd * ld,
                pa * x + pb * y + px,
                pc * x + pd * y + py,
            )
    return world


def _unweight_mesh(
    att: dict[str, Any], world: dict[str, Any], bones: list[dict[str, Any]], slot_bone: str
) -> bool:
    verts = att.get("vertices")
    uvs = att.get("uvs")
    if not isinstance(verts, list) or not isinstance(uvs, list):
        return False
    n = len(uvs) // 2
    if len(verts) == n * 2:
        return False  # already unweighted
    sa, sb, sc, sd, sx, sy = world[slot_bone]
    det = sa * sd - sb * sc or 1e-9
    ia, ib, ic, id_ = sd / det, -sb / det, -sc / det, sa / det
    out = []
    i = 0
    for _ in range(n):
        cnt = int(verts[i])
        i += 1
        wx = wy = 0.0
        for _ in range(cnt):
            bi, lx, ly, w = int(verts[i]), verts[i + 1], verts[i + 2], verts[i + 3]
            i += 4
            ba, bb, bc, bd, bx, by = world[bones[bi]["name"]]
            wx += (ba * lx + bb * ly + bx) * w
            wy += (bc * lx + bd * ly + by) * w
        dx, dy = wx - sx, wy - sy
        out += [ia * dx + ib * dy, ic * dx + id_ * dy]
    att["vertices"] = out
    return True


def _clean_keys(frames: list[dict[str, Any]]) -> list:
    out = []
    for f in frames:
        f = dict(f)
        if f.get("curve") == "linear":
            del f["curve"]
        out.append(f)
    return out


_TIMELINE_VALUE_KEYS = {
    "rotate": ("value",),
    "translate": ("x", "y"),
    "scale": ("x", "y"),
    "shear": ("x", "y"),
}


def _fix_curves(frames: list[dict[str, Any]], val_keys: tuple) -> None:
    """Convert 3.x normalized Bezier curves [a,b,c,d] to Spine 4.x absolute
    control points (4 values per value component), in place."""
    for i, f in enumerate(frames):
        c = f.get("curve")
        if not (isinstance(c, list) and len(c) == 4):
            continue
        if i + 1 >= len(frames):
            f.pop("curve", None)
            continue
        nf = frames[i + 1]
        t0, t1 = f.get("time", 0), nf.get("time", 0)
        a, b, cx, d = c
        new = []
        for k in val_keys:
            v0, v1 = f.get(k, 0), nf.get(k, 0)
            new += [t0 + a * (t1 - t0), v0 + b * (v1 - v0), t0 + cx * (t1 - t0), v0 + d * (v1 - v0)]
        f["curve"] = new


def _normalize_animation(anim: dict[str, Any]) -> dict[str, Any]:
    fixed = {}
    for section, body in anim.items():
        if section == "name":
            continue  # stray key some generators write; not Spine JSON
        if section == "bones":
            new_bones = {}
            for bone, tl in body.items():
                if isinstance(tl, dict):
                    new_tl = {}
                    for kind, frames in tl.items():
                        frames = _clean_keys(frames)
                        if kind == "rotate":
                            for f in frames:
                                if "value" not in f:
                                    f["value"] = f.pop("rotation", f.pop("angle", 0))
                        if kind in _TIMELINE_VALUE_KEYS:
                            _fix_curves(frames, _TIMELINE_VALUE_KEYS[kind])
                        new_tl[kind] = frames
                    new_bones[bone] = new_tl
                elif isinstance(tl, list):  # raw per-bone keyframe list
                    rot, trans, scale = [], [], []
                    for f in _clean_keys(tl):
                        base = {"time": f.get("time", 0)}
                        if "curve" in f:
                            base["curve"] = f["curve"]
                        if "rotation" in f or "angle" in f:
                            rot.append({**base, "value": f.get("rotation", f.get("angle", 0))})
                        if "x" in f or "y" in f:
                            trans.append({**base, "x": f.get("x", 0), "y": f.get("y", 0)})
                        if "scaleX" in f or "scaleY" in f:
                            scale.append({**base, "x": f.get("scaleX", 1), "y": f.get("scaleY", 1)})
                    new_tl = {}
                    if rot:
                        _fix_curves(rot, _TIMELINE_VALUE_KEYS["rotate"])
                        new_tl["rotate"] = rot
                    if trans:
                        _fix_curves(trans, _TIMELINE_VALUE_KEYS["translate"])
                        new_tl["translate"] = trans
                    if scale:
                        _fix_curves(scale, _TIMELINE_VALUE_KEYS["scale"])
                        new_tl["scale"] = scale
                    if new_tl:
                        new_bones[bone] = new_tl
                else:
                    new_bones[bone] = tl
            fixed["bones"] = new_bones
        elif section == "slots" and isinstance(body, dict):
            new_slots = {}
            for slot, tl in body.items():
                if isinstance(tl, list):  # raw list -> attachment timeline
                    new_slots[slot] = {
                        "attachment": [
                            {"time": f.get("time", 0), "name": f.get("name", f.get("attachment"))}
                            for f in tl
                        ]
                    }
                else:
                    new_slots[slot] = tl
            if new_slots:
                fixed["slots"] = new_slots
        else:
            fixed[section] = body
    return fixed


def rig_editor_prep(rig_json: str, output: str = "") -> dict[str, Any]:
    """Repair generated rig JSON into Spine-editor-importable form:
    weighted meshes -> unweighted (editor re-binds weights natively),
    loose keyframe lists -> valid Spine 4.x timelines. Writes a new file
    (<stem>_editor.json by default); the source is left untouched."""
    src = Path(rig_json)
    if not src.exists():
        return {"ok": False, "stderr": f"rig not found: {src}"}
    data = json.loads(src.read_text(encoding="utf-8-sig"))
    bones = data.get("bones", [])
    world = _world_transforms(bones)
    slot_bone = {s["name"]: s["bone"] for s in data.get("slots", [])}

    unweighted = 0
    skins = data.get("skins", [])
    if isinstance(skins, dict):
        skins = [{"name": k, "attachments": v} for k, v in skins.items()]
        data["skins"] = skins
    for skin in skins:
        for slot, atts in skin.get("attachments", {}).items():
            for att in atts.values():
                if att.get("type") == "mesh" and slot in slot_bone:
                    if _unweight_mesh(att, world, bones, slot_bone[slot]):
                        unweighted += 1

    anims = data.get("animations", {})
    data["animations"] = {k: _normalize_animation(v) for k, v in anims.items()}

    out = Path(output) if output else src.with_name(src.stem + "_editor.json")
    out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return {
        "ok": True,
        "editor_json": str(out),
        "meshes_unweighted": unweighted,
        "animations_normalized": len(anims),
    }


def _offset_attachment_bone_indices(att: dict[str, Any], offset: int) -> None:
    verts = att.get("vertices")
    if not isinstance(verts, list):
        return
    if "uvs" in att:
        weighted = len(verts) != len(att["uvs"])
    elif "vertexCount" in att:
        weighted = len(verts) != att["vertexCount"] * 2
    else:
        return
    if not weighted:
        return
    i = 0
    while i < len(verts):
        n = int(verts[i])
        i += 1
        for _ in range(n):
            verts[i] = int(verts[i]) + offset
            i += 4


def _rename_anim_block(
    anim: dict[str, Any],
    bone_map: dict[str, Any],
    slot_map: dict[str, Any],
    con_map: dict[str, Any],
    skin_map: dict[str, Any],
) -> dict[str, Any]:
    out = {}
    for section, body in anim.items():
        if section == "bones":
            out[section] = {bone_map.get(k, k): v for k, v in body.items()}
        elif section == "slots":
            out[section] = {slot_map.get(k, k): v for k, v in body.items()}
        elif section in ("ik", "transform", "path", "physics"):
            out[section] = {con_map.get(k, k): v for k, v in body.items()}
        elif section == "deform":
            out[section] = {
                skin_map.get(sk, sk): {slot_map.get(sl, sl): atts for sl, atts in slots.items()}
                for sk, slots in body.items()
            }
        elif section == "drawOrder" or section == "draworder":
            new = copy.deepcopy(body)
            for frame in new:
                for off in frame.get("offsets", []):
                    off["slot"] = slot_map.get(off["slot"], off["slot"])
            out["drawOrder"] = new
        else:
            out[section] = body
    return out


def scene_compose(
    characters: list[dict[str, Any]], output_json: str, scene_name: str = "scene"
) -> dict[str, Any]:
    """Merge N rigged characters into ONE Spine skeleton JSON (a scene).

    characters: list[dict[str, Any]] of dicts:
      {"json": path, "prefix": name, "x": 0, "y": 0, "scale": 1.0, "rotation": 0}
    Each character's bones/slots/skins/constraints/animations are prefixed and
    grafted under a per-character root placed at (x, y) * scale. Weighted-mesh
    bone indices are re-based so deformation survives the merge.
    """
    if not characters:
        return {"ok": False, "stderr": "no characters given"}
    merged = {
        "skeleton": {"spine": "4.0", "hash": scene_name, "images": "./images/"},
        "bones": [{"name": "root"}],
        "slots": [],
        "ik": [],
        "transform": [],
        "skins": [],
        "animations": {},
    }
    default_skin = {"name": "default", "attachments": {}}
    merged["skins"].append(default_skin)
    report = []

    for idx, spec in enumerate(characters):
        path = Path(spec["json"])
        if not path.exists():
            return {"ok": False, "stderr": f"rig not found: {path}"}
        data = json.loads(path.read_text(encoding="utf-8"))
        prefix = (spec.get("prefix") or path.stem.split("_")[0] or f"char{idx}") + "_"

        bones = copy.deepcopy(data.get("bones", []))
        slots = copy.deepcopy(data.get("slots", []))
        skins = copy.deepcopy(data.get("skins", []))
        if isinstance(skins, dict):  # legacy 3.x dict form
            skins = [{"name": k, "attachments": v} for k, v in skins.items()]
        iks = copy.deepcopy(data.get("ik", []))
        transforms = copy.deepcopy(data.get("transform", []))
        anims = copy.deepcopy(data.get("animations", {}))

        bone_map = {b["name"]: prefix + b["name"] for b in bones}
        slot_map = {s["name"]: prefix + s["name"] for s in slots}
        con_map = {c["name"]: prefix + c["name"] for c in iks + transforms}
        skin_map = {s["name"]: (prefix + s["name"]) for s in skins if s["name"] != "default"}

        bone_offset = len(merged["bones"])
        for b in bones:
            b["name"] = bone_map[b["name"]]
            if "parent" in b:
                b["parent"] = bone_map.get(b["parent"], b["parent"])
            else:  # this character's root: place it in the scene
                b["parent"] = "root"
                b["x"] = float(b.get("x", 0)) + float(spec.get("x", 0))
                b["y"] = float(b.get("y", 0)) + float(spec.get("y", 0))
                if spec.get("scale"):
                    b["scaleX"] = float(b.get("scaleX", 1)) * float(spec["scale"])
                    b["scaleY"] = float(b.get("scaleY", 1)) * float(spec["scale"])
                if spec.get("rotation"):
                    b["rotation"] = float(b.get("rotation", 0)) + float(spec["rotation"])
        merged["bones"].extend(bones)

        for s in slots:
            s["name"] = slot_map[s["name"]]
            s["bone"] = bone_map.get(s["bone"], s["bone"])
        merged["slots"].extend(slots)

        for group, target in ((iks, "ik"), (transforms, "transform")):
            for c in group:
                c["name"] = con_map[c["name"]]
                c["order"] = int(c.get("order", 0)) + idx * 100
                c["bones"] = [bone_map.get(x, x) for x in c.get("bones", [])]
                if "target" in c:
                    c["target"] = bone_map.get(c["target"], c["target"])
                merged[target].append(c)

        for skin in skins:
            new_atts = {}
            for slot_name, atts in skin.get("attachments", {}).items():
                for att in atts.values():
                    # weighted-mesh bone indices point into the skeleton bones
                    # array; this character's bone 0 now sits at bone_offset
                    _offset_attachment_bone_indices(att, bone_offset)
                new_atts[slot_map.get(slot_name, slot_name)] = atts
            if skin["name"] == "default":
                default_skin["attachments"].update(new_atts)
            else:
                merged["skins"].append({"name": skin_map[skin["name"]], "attachments": new_atts})

        for anim_name, anim in anims.items():
            merged["animations"][prefix + anim_name] = _rename_anim_block(
                anim, bone_map, slot_map, con_map, skin_map
            )

        report.append(
            {
                "character": prefix.rstrip("_"),
                "bones": len(bones),
                "slots": len(slots),
                "animations": len(anims),
            }
        )

    out = Path(output_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
    return {
        "ok": True,
        "scene_json": str(out),
        "characters": report,
        "total_bones": len(merged["bones"]),
        "total_slots": len(merged["slots"]),
        "total_animations": len(merged["animations"]),
    }


def scene_preview(
    characters: list[dict[str, Any]],
    output_png: str,
    width: int = 1920,
    height: int = 1080,
    background: str = "#202028",
) -> dict[str, Any]:
    """Composite each character's texture at its scene placement -> layout PNG.
    Fast visual proof of a scene; full skeletal renders go through spine_export
    with a saved PNG/GIF export-settings file."""
    try:
        from PIL import Image
    except ImportError:
        return {"ok": False, "stderr": "Pillow not installed (pip install pillow)"}
    canvas = Image.new("RGBA", (width, height), background)
    placed = []
    for spec in characters:
        tex_path = spec.get("texture", "")
        if not tex_path:
            rig = Path(spec.get("json", ""))
            guess = rig.with_suffix(".png")
            if not guess.exists():
                stem = rig.stem.split("_")[0]
                guess = rig.with_name(stem + ".png")
            tex_path = str(guess)
        if not Path(tex_path).exists():
            return {"ok": False, "stderr": f"texture not found for scene preview: {tex_path}"}
        img = Image.open(tex_path).convert("RGBA")
        scale = float(spec.get("scale", 1.0))
        if scale != 1.0:
            img = img.resize(
                (max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS
            )
        x = int(width / 2 + float(spec.get("x", 0)) - img.width / 2)
        y = int(height - float(spec.get("y", 0)) - img.height)
        canvas.alpha_composite(img, (x, y))
        placed.append({"texture": tex_path, "at": [x, y], "scale": scale})
    out = Path(output_png)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    return {"ok": True, "preview": str(out), "placed": placed}


def scene_to_project(
    scene_json: str, project_path: str, scale: float = 0.0, prep: bool = True
) -> dict[str, Any]:
    """Import a composed scene JSON into a .spine project for editor polish.
    prep=True (default) runs rig_editor_prep first so the editor importer
    accepts generated or composed JSON, matching spine_import."""
    return spine_import(scene_json, project_path, name="", scale=scale, prep=prep)
