"""Coin v2 — a three-coin cluster that bursts outward, then settles at an angle.

What the first pass got wrong, and what changed:

  * It settled FACE-ON, so the bevel and reeded edge were edge-invisible and the
    coin read as a flat disc. It now rests at a 3/4 angle, showing thickness.
  * Emissive was cranked to fight a black render and blew out to near-white.
    Emissive is now low on the body; the light comes from a dedicated RIM
    material instead — which is what actually reads as "lit" in the reference.
  * There was one lonely coin. There are now two satellites that explode out of
    the centre with the main coin and settle into orbit.

Clips:
  Intro - 2.4s. All three burst from the centre with an overshoot, the main
          coin spinning three turns and easing to its 3/4 rest pose.
  Spin  - 1.4s linear loop for the signing-in state; satellites counter-rotate.
"""

import os as _os
import sys as _sys

# Resolve everything from this file's location so the pipeline runs from a
# clean checkout, from any working directory.
_HERE = _os.path.dirname(_os.path.abspath(__file__))
_REPO = _os.path.abspath(_os.path.join(_HERE, "..", "..", ".."))
_MOBILE = _os.path.join(_REPO, "mobile")
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)

import json
import math
import struct
import sys


from make_coin import Mesh, ring, radial_normals, prism, rect, segment, pack  # noqa: E402
from make_coin import R, T, BEV_Z, FACE_R, EMBOSS, SEG  # noqa: E402

TILT_X = -13.0        # slight top-down, as in the reference
REST_Y = -30.0        # rest pose: turned enough to show the rim
INTRO_SECS = 2.4
INTRO_TURNS = 3.0
KEYS = 96
SPIN_SECS = 1.4
SPIN_KEYS = 25

# Where the satellites land when the burst settles.
SAT_L = (-1.95, 0.42, -0.55)
SAT_R = (1.80, -0.50, -0.40)
SAT_L_SCALE = 0.40
SAT_R_SCALE = 0.34


def quat_axis(ax, ay, az, deg):
    a = math.radians(deg) / 2.0
    s = math.sin(a)
    return (ax * s, ay * s, az * s, math.cos(a))


def ease_out_cubic(t):
    return 1.0 - (1.0 - t) ** 3


def ease_out_back(t, k=1.9):
    """Overshoots past 1 then settles — gives the burst its snap."""
    return 1.0 + (k + 1.0) * (t - 1.0) ** 3 + k * (t - 1.0) ** 2


def lerp3(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


# ── Geometry: body, rim and emboss as three separate materials ───────────────

def build_body_faces():
    """Faces plus bevels — the dark part of the coin."""
    m = Mesh()
    face_top, face_bot = ring(FACE_R, T), ring(FACE_R, -T)
    outer_top = ring(R, T - BEV_Z)
    outer_bot = ring(R, -(T - BEV_Z))
    bev_t = radial_normals(zc=0.9)
    bev_b = [(n[0], n[1], -n[2]) for n in bev_t]

    for i in range(SEG):
        j = (i + 1) % SEG
        m.quad(outer_top[i], outer_top[j], face_top[j], face_top[i],
               bev_t[i], bev_t[j], bev_t[j], bev_t[i])
        m.quad(face_bot[i], face_bot[j], outer_bot[j], outer_bot[i],
               bev_b[i], bev_b[j], bev_b[j], bev_b[i])

    up, dn = (0.0, 0.0, 1.0), (0.0, 0.0, -1.0)
    ct, cb = (0.0, 0.0, T), (0.0, 0.0, -T)
    for i in range(SEG):
        j = (i + 1) % SEG
        m.tri(ct, face_top[i], face_top[j], up, up, up)
        m.tri(cb, face_bot[j], face_bot[i], dn, dn, dn)
    return m


def build_rim():
    """The milled barrel. Its own material, lit — this is the glowing edge."""
    m = Mesh()
    reed = [R + (0.007 if i % 2 == 0 else -0.007) for i in range(SEG)]
    top = [(reed[i] * math.cos(2 * math.pi * i / SEG),
            reed[i] * math.sin(2 * math.pi * i / SEG),
            T - BEV_Z) for i in range(SEG)]
    bot = [(p[0], p[1], -(T - BEV_Z)) for p in top]
    rn = radial_normals()
    for i in range(SEG):
        j = (i + 1) % SEG
        m.quad(bot[i], bot[j], top[j], top[i], rn[i], rn[j], rn[j], rn[i])
    return m


def build_emboss():
    m = Mesh()
    z0, z1 = T - 0.004, T + EMBOSS
    for cx, cy, h in [(-0.46, -0.20, 0.30), (-0.24, -0.10, 0.34),
                      (-0.02, 0.04, 0.40), (0.20, 0.18, 0.36),
                      (0.42, 0.34, 0.44)]:
        prism(m, rect(cx, cy, 0.115, h), z0, z1)
        prism(m, rect(cx, cy, 0.028, h + 0.20), z0, z1 - 0.008)
    pts = [(-0.62, -0.46), (-0.34, -0.30), (-0.10, -0.40),
           (0.16, -0.10), (0.44, 0.06)]
    for a, b in zip(pts, pts[1:]):
        prism(m, segment(a, b, 0.055), z0, z1)
    prism(m, [(0.40, 0.02), (0.62, 0.16), (0.44, 0.30)], z0, z1)
    return m


def build_back_emboss():
    """The IntelliStock mark on the reverse: a smooth curve between two rings.

    The first attempt was a thin zigzag between filled dots, which read as two
    unrelated specks — the connecting line was too fine to survive at this
    size. This samples a real curve into a thick continuous stroke, and the end
    nodes are rings (as in the mark) rather than blobs.

    Everything sits on the -Z face, so it is hidden until the success flip.
    """
    m = Mesh()
    z1, z0 = -T + 0.004, -(T + EMBOSS * 1.7)
    STROKE = 0.105

    def disc(cx, cy, r, n=24):
        return [(cx + r * math.cos(2 * math.pi * i / n),
                 cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]

    def ring_prism(cx, cy, r_out, r_in, n=28):
        """An annulus — a filled disc would lose the mark's open nodes."""
        back = (0.0, 0.0, -1.0)
        for i in range(n):
            a0 = 2 * math.pi * i / n
            a1 = 2 * math.pi * (i + 1) / n
            o0 = (cx + r_out * math.cos(a0), cy + r_out * math.sin(a0))
            o1 = (cx + r_out * math.cos(a1), cy + r_out * math.sin(a1))
            i0 = (cx + r_in * math.cos(a0), cy + r_in * math.sin(a0))
            i1 = (cx + r_in * math.cos(a1), cy + r_in * math.sin(a1))
            # Visible face, wound so its normal points away from the coin.
            m.quad((o0[0], o0[1], z0), (i0[0], i0[1], z0),
                   (i1[0], i1[1], z0), (o1[0], o1[1], z0),
                   back, back, back, back)
            # Outer and inner walls give the ring its relief.
            m.quad((o0[0], o0[1], z0), (o1[0], o1[1], z0),
                   (o1[0], o1[1], z1), (o0[0], o0[1], z1))
            m.quad((i1[0], i1[1], z0), (i0[0], i0[1], z0),
                   (i0[0], i0[1], z1), (i1[0], i1[1], z1))

    # The mark's curve: a rise with one wave through it. Mirrored in x, because
    # the reverse face is seen from behind — otherwise it reads backwards once
    # the coin has turned over.
    N = 34
    pts = []
    for i in range(N + 1):
        t = i / N
        x = -0.46 + 0.92 * t
        y = -0.26 + 0.52 * t + 0.155 * math.sin(2 * math.pi * t)
        pts.append((-x, y))

    for a, b in zip(pts, pts[1:]):
        prism(m, segment(a, b, STROKE), z0, z1)
    # Round the joints so the polyline reads as one drawn stroke.
    for p in pts[1:-1:2]:
        prism(m, disc(p[0], p[1], STROKE / 2, n=8), z0, z1)

    for c in (pts[0], pts[-1]):
        ring_prism(c[0], c[1], 0.185, 0.095)
    return m


# ── Animation tracks ─────────────────────────────────────────────────────────

def intro_tracks():
    """(times, main_rot, main_scale, satL_pos, satL_scale, satR_pos, satR_scale)"""
    times, m_rot, m_scale = [], [], []
    l_pos, l_scale, l_rot = [], [], []
    r_pos, r_scale, r_rot = [], [], []

    for i in range(KEYS):
        u = i / (KEYS - 1)
        times.append(u * INTRO_SECS)

        # Main coin: three eased turns landing on the rest pose, and a burst
        # of scale that overshoots before settling.
        ang = (INTRO_TURNS * 360.0 + REST_Y) * ease_out_cubic(u)
        m_rot.append(quat_axis(0, 1, 0, ang))
        s = ease_out_back(min(1.0, u / 0.55))
        m_scale.append((max(s, 0.001),) * 3)

        # Satellites lag slightly, then fly out past their mark and back.
        v = max(0.0, (u - 0.10) / 0.90)
        vb = ease_out_back(min(1.0, v / 0.75), k=2.4)
        l_pos.append(lerp3((0.0, 0.0, 0.0), SAT_L, vb))
        r_pos.append(lerp3((0.0, 0.0, 0.0), SAT_R, vb))
        ls = max(SAT_L_SCALE * ease_out_back(min(1.0, v / 0.7)), 0.001)
        rs = max(SAT_R_SCALE * ease_out_back(min(1.0, v / 0.7)), 0.001)
        l_scale.append((ls,) * 3)
        r_scale.append((rs,) * 3)
        # They tumble as they fly, settling at their own angles.
        l_rot.append(quat_axis(0, 1, 0, (-720.0 - 35.0) * ease_out_cubic(v)))
        r_rot.append(quat_axis(0, 1, 0, (540.0 + 28.0) * ease_out_cubic(v)))

    return times, m_rot, m_scale, l_pos, l_scale, l_rot, r_pos, r_scale, r_rot


SUCCESS_SECS = 1.7
SUCCESS_KEYS = 72


def success_tracks():
    """Satellites collapse into the centre; the main coin flips to its back."""
    times = []
    m_rot, m_scale = [], []
    l_pos, l_scale, r_pos, r_scale = [], [], [], []

    for i in range(SUCCESS_KEYS):
        u = i / (SUCCESS_KEYS - 1)
        times.append(u * SUCCESS_SECS)

        # Satellites rush in first and vanish into the middle.
        v = min(1.0, u / 0.55)
        e = ease_out_cubic(v)
        l_pos.append(lerp3(SAT_L, (0.0, 0.0, 0.0), e))
        r_pos.append(lerp3(SAT_R, (0.0, 0.0, 0.0), e))
        l_scale.append((max(SAT_L_SCALE * (1.0 - e), 0.001),) * 3)
        r_scale.append((max(SAT_R_SCALE * (1.0 - e), 0.001),) * 3)

        # The main coin waits a beat, then turns over to show the mark and
        # settles face-on so the logo reads square to the viewer.
        w = max(0.0, (u - 0.22) / 0.78)
        m_rot.append(quat_axis(0, 1, 0, REST_Y + (180.0 - REST_Y) * ease_out_cubic(w)))
        m_scale.append((1.0 + 0.16 * math.sin(math.pi * min(1.0, u)),) * 3)

    return times, m_rot, m_scale, l_pos, l_scale, r_pos, r_scale


def spin_tracks():
    times, m_rot, l_rot, r_rot = [], [], [], []
    for i in range(SPIN_KEYS):
        u = i / (SPIN_KEYS - 1)
        times.append(u * SPIN_SECS)
        m_rot.append(quat_axis(0, 1, 0, REST_Y + 360.0 * u))
        # Counter-rotating satellites read as a system, not three loose discs.
        l_rot.append(quat_axis(0, 1, 0, -35.0 - 360.0 * u))
        r_rot.append(quat_axis(0, 1, 0, 28.0 + 360.0 * u))
    return times, m_rot, l_rot, r_rot


def main(out=None):
    out = out or _os.path.join(_MOBILE, "assets/models/coin.glb")
    faces, rim, emboss = build_body_faces(), build_rim(), build_emboss()
    back = build_back_emboss()
    blob, specs = pack([faces, rim, emboss, back])
    blob = bytearray(blob)

    views, accessors, prims = [], [], []
    for mat, s in enumerate(specs):
        vi = len(views)
        views += [
            {"buffer": 0, "byteOffset": s["idx_off"], "byteLength": s["idx_count"] * 4},
            {"buffer": 0, "byteOffset": s["pos_off"], "byteLength": s["count"] * 12},
            {"buffer": 0, "byteOffset": s["nrm_off"], "byteLength": s["count"] * 12},
        ]
        ai = len(accessors)
        accessors += [
            {"bufferView": vi, "componentType": 5125, "count": s["idx_count"],
             "type": "SCALAR"},
            {"bufferView": vi + 1, "componentType": 5126, "count": s["count"],
             "type": "VEC3", "min": s["min"], "max": s["max"]},
            {"bufferView": vi + 2, "componentType": 5126, "count": s["count"],
             "type": "VEC3"},
        ]
        prims.append({"attributes": {"POSITION": ai + 1, "NORMAL": ai + 2},
                      "indices": ai, "material": min(mat, 2)})

    def acc(fmt, values, kind, bounds=False):
        while len(blob) % 4:
            blob.append(0)
        off = len(blob)
        for v in values:
            blob.extend(struct.pack(fmt, *v) if isinstance(v, tuple)
                        else struct.pack(fmt, v))
        views.append({"buffer": 0, "byteOffset": off,
                      "byteLength": len(blob) - off})
        a = {"bufferView": len(views) - 1, "componentType": 5126,
             "count": len(values), "type": kind}
        if bounds:
            a["min"], a["max"] = [min(values)], [max(values)]
        accessors.append(a)
        return len(accessors) - 1

    (it, m_rot, m_scale, l_pos, l_scale, l_rot,
     r_pos, r_scale, r_rot) = intro_tracks()
    st, s_m_rot, s_l_rot, s_r_rot = spin_tracks()
    (ct, c_m_rot, c_m_scale, c_l_pos, c_l_scale,
     c_r_pos, c_r_scale) = success_tracks()

    it_a = acc("<f", it, "SCALAR", bounds=True)
    st_a = acc("<f", st, "SCALAR", bounds=True)
    ct_a = acc("<f", ct, "SCALAR", bounds=True)

    def channels(pairs, time_acc):
        samplers, chans = [], []
        for node, path, values in pairs:
            kind = "VEC4" if path == "rotation" else "VEC3"
            fmt = "<4f" if path == "rotation" else "<3f"
            samplers.append({"input": time_acc, "interpolation": "LINEAR",
                             "output": acc(fmt, values, kind)})
            chans.append({"sampler": len(samplers) - 1,
                          "target": {"node": node, "path": path}})
        return samplers, chans

    intro_s, intro_c = channels([
        (1, "rotation", m_rot), (1, "scale", m_scale),
        (2, "translation", l_pos), (2, "scale", l_scale), (2, "rotation", l_rot),
        (3, "translation", r_pos), (3, "scale", r_scale), (3, "rotation", r_rot),
    ], it_a)
    spin_s, spin_c = channels([
        (1, "rotation", s_m_rot), (2, "rotation", s_l_rot),
        (3, "rotation", s_r_rot),
    ], st_a)
    succ_s, succ_c = channels([
        (1, "rotation", c_m_rot), (1, "scale", c_m_scale),
        (2, "translation", c_l_pos), (2, "scale", c_l_scale),
        (3, "translation", c_r_pos), (3, "scale", c_r_scale),
    ], ct_a)

    gltf = {
        "asset": {"version": "2.0", "generator": "IntelliStock coin v2"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [
            {"name": "Root", "rotation": list(quat_axis(1, 0, 0, TILT_X)),
             "children": [1, 2, 3]},
            {"name": "MainCoin", "mesh": 0,
             "rotation": list(quat_axis(0, 1, 0, REST_Y))},
            {"name": "SatL", "mesh": 0, "translation": list(SAT_L),
             "scale": [SAT_L_SCALE] * 3,
             "rotation": list(quat_axis(0, 1, 0, -35.0))},
            {"name": "SatR", "mesh": 0, "translation": list(SAT_R),
             "scale": [SAT_R_SCALE] * 3,
             "rotation": list(quat_axis(0, 1, 0, 28.0))},
        ],
        "meshes": [{"name": "Coin", "primitives": prims}],
        "animations": [
            {"name": "Intro", "samplers": intro_s, "channels": intro_c},
            {"name": "Spin", "samplers": spin_s, "channels": spin_c},
            {"name": "Success", "samplers": succ_s, "channels": succ_c},
        ],
        "materials": [
            {
                "name": "CoinBody",
                # Dark and mostly matte. The previous pass pushed emissive up to
                # fight a black render and blew the whole coin out to white; the
                # light belongs on the rim, not the face.
                "pbrMetallicRoughness": {
                    "baseColorFactor": [0.07324, 0.02862, 0.31855, 1.0],
                    "metallicFactor": 0.98,
                    "roughnessFactor": 0.13,
                },
                "emissiveFactor": [0.00598, 0.00350, 0.01961],
            },
            {
                "name": "CoinRim",
                # The lit edge — this is what makes it read as 3D at a glance.
                "pbrMetallicRoughness": {
                    "baseColorFactor": [0.53824, 0.36725, 1.00000, 1.0],
                    "metallicFactor": 1.00,
                    "roughnessFactor": 0.06,
                },
                "emissiveFactor": [0.19599, 0.10654, 0.71057],
            },
            {
                "name": "CoinEmboss",
                "pbrMetallicRoughness": {
                    "baseColorFactor": [0.31855, 0.21404, 0.86890, 1.0],
                    "metallicFactor": 0.95,
                    "roughnessFactor": 0.10,
                },
                "emissiveFactor": [0.03310, 0.02077, 0.12597],
            },
        ],
        "buffers": [{"byteLength": len(blob)}],
        "bufferViews": views,
        "accessors": accessors,
    }

    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    bin_ = bytes(blob) + b"\x00" * ((4 - len(blob) % 4) % 4)
    glb = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(bin_))
    glb += struct.pack("<II", len(js), 0x4E4F534A) + js
    glb += struct.pack("<II", len(bin_), 0x004E4942) + bin_
    with open(out, "wb") as f:
        f.write(glb)
    print("wrote %s - %d bytes, %d tris, 3 coins, clips: Intro (%d), Spin (%d), Success (%d)"
          % (out, len(glb), sum(s["idx_count"] for s in specs) // 3,
             len(intro_c), len(spin_c), len(succ_c)))


if __name__ == "__main__":
    main()
