"""Build the sample rig: one flat character image in, a Spine skeleton with animations out.

Keys the white background out of ``source/mecha_girl.png``, cuts it into body / head /
upper arms / forearms, and writes ``mecha.json`` plus ``images/`` next to this file. The idle,
wave and cheer animations are plain functions of time, sampled into keyframes. A Pillow
forward-kinematics preview of them is written to ``out/preview.gif``.

    python examples/build_rig.py

The joint positions and cut polygons below were picked by eye for this one image.
"""

import json
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

OUT = Path(__file__).parent
SRC = OUT / "source" / "mecha_girl.png"
IMG = OUT / "images"
PREVIEW = OUT / "out"
IMG.mkdir(exist_ok=True)
PREVIEW.mkdir(exist_ok=True)

src = Image.open(SRC).convert("RGB")
W, H = src.size

# 1. background key: flood from the border, then pull the edge in a pixel to drop the white fringe
key = src.copy()
MARK = (255, 0, 255)
border = [(x, y) for x in range(0, W, 8) for y in (0, H - 1)]
border += [(x, y) for y in range(0, H, 8) for x in (0, W - 1)]
for xy in border:
    if key.getpixel(xy) != MARK:
        ImageDraw.floodfill(key, xy, MARK, thresh=42)
r, g, b = ImageChops.difference(key, Image.new("RGB", (W, H), MARK)).split()
alpha = ImageChops.lighter(ImageChops.lighter(r, g), b).point(lambda v: 255 if v else 0)
alpha = alpha.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.7))
full = src.convert("RGBA")
full.putalpha(alpha)

# 2. joints and part masks, in image pixels. Left = screen-left; the right side mirrors about CX.
CX, FEET_Y = 416, 1195


def mirror(pt):
    return (2 * CX - pt[0], pt[1])


BODY = (CX, FEET_Y)
NECK = (CX, 204)
SHOULDER_L, ELBOW_L, WRIST_L = (338, 258), (226, 360), (110, 455)
arm_l_poly = [
    (352, 212),
    (352, 306),
    (292, 400),
    (200, 505),
    (0, 580),
    (0, 380),
    (240, 228),
    (300, 200),
]
head_poly = [(250, 0), (582, 0), (582, 150), (470, 212), (362, 212), (250, 150)]


def poly_mask(poly):
    m = Image.new("L", (W, H), 0)
    ImageDraw.Draw(m).polygon(poly, fill=255)
    return m


def half_plane(origin, direction, offset):
    """Mask of pixels whose projection on `direction` from `origin` is greater than `offset`."""
    length = math.hypot(*direction)
    dx, dy = direction[0] / length, direction[1] / length
    far = 3000
    a = (origin[0] + dx * offset, origin[1] + dy * offset)
    quad = [
        (a[0] - dy * far, a[1] + dx * far),
        (a[0] + dy * far, a[1] - dx * far),
        (a[0] + dy * far + dx * far, a[1] - dx * far + dy * far),
        (a[0] - dy * far + dx * far, a[1] + dx * far + dy * far),
    ]
    return poly_mask(quad)


def arm_masks(poly, shoulder, elbow, wrist):
    whole = poly_mask(poly)
    along = (wrist[0] - shoulder[0], wrist[1] - shoulder[1])
    lower = ImageChops.multiply(whole, half_plane(elbow, along, -7))
    upper = ImageChops.multiply(whole, ImageChops.invert(half_plane(elbow, along, 7)))
    return whole, upper, lower


whole_l, m_arm_l, m_fore_l = arm_masks(arm_l_poly, SHOULDER_L, ELBOW_L, WRIST_L)
whole_r, m_arm_r, m_fore_r = arm_masks(
    [mirror(p) for p in arm_l_poly], mirror(SHOULDER_L), mirror(ELBOW_L), mirror(WRIST_L)
)
m_head = poly_mask(head_poly)
# the body keeps a strip under each joint so small rotations do not open a gap
overlap = Image.new("L", (W, H), 0)
d = ImageDraw.Draw(overlap)
d.rectangle((330, 212, 352, 306), fill=255)
d.rectangle((2 * CX - 352, 212, 2 * CX - 330, 306), fill=255)
d.rectangle((362, 190, 470, 212), fill=255)
taken = ImageChops.lighter(ImageChops.lighter(m_head, whole_l), whole_r)
m_body = ImageChops.lighter(ImageChops.invert(taken), overlap)

# name -> (mask, pivot, parent)
PARTS = {
    "body": (m_body, BODY, None),
    "arm_l": (m_arm_l, SHOULDER_L, "body"),
    "forearm_l": (m_fore_l, ELBOW_L, "arm_l"),
    "arm_r": (m_arm_r, mirror(SHOULDER_L), "body"),
    "forearm_r": (m_fore_r, mirror(ELBOW_L), "arm_r"),
    "head": (m_head, NECK, "body"),
}
layers, atts = {}, {}
for name, (m, pivot, _) in PARTS.items():
    layer = full.copy()
    layer.putalpha(ImageChops.multiply(alpha, m))
    layers[name] = layer
    box = layer.getchannel("A").getbbox()
    layer.crop(box).save(IMG / f"{name}.png")
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    atts[name] = {
        "x": round(cx - pivot[0], 1),
        "y": round(pivot[1] - cy, 1),
        "width": box[2] - box[0],
        "height": box[3] - box[1],
    }

# 3. animations as functions of time -> {bone: {"rot": deg, "y": px}}; sampled into keyframes
TAU = 2 * math.pi


def idle(t, T=3.0):
    """Breathing loop: every part follows the body a beat late (overlapping action)."""
    p = TAU * t / T
    return {
        "body": {"y": 5 * math.sin(p)},
        "head": {"rot": 2.2 * math.sin(p - 0.7)},
        "arm_l": {"rot": -3.0 * math.sin(p - 0.9)},
        "forearm_l": {"rot": -4.5 * math.sin(p - 1.7)},
        "arm_r": {"rot": 3.0 * math.sin(p - 0.9)},
        "forearm_r": {"rot": 4.5 * math.sin(p - 1.7)},
    }


def ease(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def wave(t, T=2.4):
    """Raise the right arm, wave from the elbow three times, lower it."""
    up = ease(t / 0.45) * ease((T - t) / 0.45)
    swing = math.sin(TAU * (t - 0.45) / 0.5) if 0.45 < t < T - 0.45 else 0.0
    return {
        "body": {"y": 4 * up},
        "head": {"rot": -5 * up + 1.5 * swing * up},
        "arm_r": {"rot": 44 * up},
        "forearm_r": {"rot": (14 + 16 * swing) * up},
        "arm_l": {"rot": 4 * up},
        "forearm_l": {"rot": 6 * up},
    }


def cheer(t, T=1.8):
    """Crouch, then throw both arms up with a small hop, arms settling after the body."""
    crouch = ease(t / 0.3) * ease((0.6 - t) / 0.3)
    hop = math.sin(math.pi * min(1.0, max(0.0, (t - 0.5) / 0.7)))
    arms = ease((t - 0.4) / 0.35) * ease((T - t) / 0.4)
    lag = ease((t - 0.55) / 0.35) * ease((T - t) / 0.3)
    return {
        "body": {"y": -14 * crouch + 26 * hop},
        "head": {"rot": 3 * crouch - 4 * hop},
        "arm_r": {"rot": 40 * arms},
        "forearm_r": {"rot": 20 * lag},
        "arm_l": {"rot": -40 * arms},
        "forearm_l": {"rot": -20 * lag},
    }


ANIMS = {"idle": (idle, 3.0), "wave": (wave, 2.4), "cheer": (cheer, 1.8)}


def timelines(fn, T, step=1 / 12):
    n = round(T / step)
    out = {}
    for i in range(n + 1):
        t = min(T, i * step)
        for bone, ch in fn(t).items():
            tl = out.setdefault(bone, {})
            if "rot" in ch:
                tl.setdefault("rotate", []).append(
                    {"time": round(t, 4), "value": round(ch["rot"], 2)}
                )
            if "y" in ch:
                tl.setdefault("translate", []).append({"time": round(t, 4), "y": round(ch["y"], 2)})
    return {"bones": out}


def bone(name, pivot, parent):
    b = {"name": name, "parent": parent or "root"}
    if parent:
        pp = PARTS[parent][1]
        b["x"], b["y"] = pivot[0] - pp[0], pp[1] - pivot[1]
    return b


rig = {
    "skeleton": {"spine": "4.2.00", "images": "./images/"},
    "bones": [{"name": "root"}] + [bone(n, p[1], p[2]) for n, p in PARTS.items()],
    "slots": [{"name": n, "bone": n, "attachment": n} for n in PARTS],
    "skins": [{"name": "default", "attachments": {n: {n: a} for n, a in atts.items()}}],
    "animations": {name: timelines(fn, T) for name, (fn, T) in ANIMS.items()},
}
(OUT / "mecha.json").write_text(json.dumps(rig) + "\n", encoding="utf-8", newline="\n")


# 4. preview: forward kinematics with Pillow (child rotates about its joint, then follows parents)
PADX, PADY = 150, 70  # room for raised arms and the hop
PW, PH = W + 2 * PADX, H + PADY
padded = {}
for _name, _layer in layers.items():
    canvas = Image.new("RGBA", (PW, PH), (0, 0, 0, 0))
    canvas.paste(_layer, (PADX, PADY))
    padded[_name] = canvas


def render(pose, scale=0.4):
    frame = Image.new("RGBA", (PW, PH), (36, 38, 44, 255))
    for name in PARTS:
        layer, chain = padded[name], name
        while chain:
            ch = pose.get(chain, {})
            px, py = PARTS[chain][1]
            layer = layer.rotate(
                ch.get("rot", 0),
                center=(px + PADX, py + PADY),
                resample=Image.Resampling.BICUBIC,
                translate=(0, -ch.get("y", 0)),
            )
            chain = PARTS[chain][2]
        frame.alpha_composite(layer)
    return frame.convert("RGB").resize(
        (round(PW * scale), round(PH * scale)), Image.Resampling.LANCZOS
    )


FPS = 15
frames = []
for name in ("idle", "wave", "idle", "cheer"):
    fn, T = ANIMS[name]
    frames += [render(fn(i / FPS)) for i in range(round(T * FPS))]
frames[0].save(
    PREVIEW / "preview.gif",
    save_all=True,
    append_images=frames[1:],
    duration=round(1000 / FPS),
    loop=0,
    optimize=True,
)
print(f"wrote {OUT / 'mecha.json'}: {len(PARTS)} parts, {len(ANIMS)} animations")
