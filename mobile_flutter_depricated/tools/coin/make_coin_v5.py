"""Coin v5 — near-black minted metal, violet only on the edge.

Against the reference:
  * The body was saturated violet and the motif was LIGHT purple. Both are now
    dark brushed metal; the only violet is the barrel, which is a dark base
    lit by a strong emissive so it reads as a rim light, not as pale plastic.
  * The face was a flat disc. It now has a proper mint: bevel, a raised bezel
    ring, a step, and a recessed field carrying the motif.
  * Reeding was coarse enough to read as banding. 192 segments, finer teeth.
  * Satellites pushed back out so the cluster is not crowded.
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


from make_coin import Mesh, prism, rect, segment, pack  # noqa: E402
from make_coin_v2 import (  # noqa: E402
    quat_axis, ease_out_cubic, ease_out_back, lerp3, TILT_X, REST_Y,
)

# 192 was finer than the silhouette can show at this size; 144 keeps the
# circle smooth and the milling readable for ~25% fewer triangles.
SEG = 144
R = 1.0
T = 0.155
BARREL_Z = T - 0.052       # where the bevel meets the barrel
BEZEL_R = 0.935            # outer edge of the raised ring
FIELD_R = 0.805            # recessed field carrying the motif
FIELD_Z = T - 0.038        # how deep the field sits
EMBOSS = 0.030

SAT_L = (-1.78, 0.40, -0.45)
SAT_R = (1.66, -0.44, -0.32)
SAT_L_S, SAT_R_S = 0.36, 0.31

INTRO_SECS, INTRO_KEYS = 2.4, 96
IDLE_SECS, IDLE_KEYS = 4.4, 45
SPIN_SECS, SPIN_KEYS = 2.2, 45
SUCC_SECS, SUCC_KEYS = 1.9, 90
BOB = 0.085
# The REAL mark, extracted from the shipped app icon by extract_logo.py.
# This used to point at a scratchpad copy, which silently embedded a
# hand-drawn stand-in instead.
LOGO_PNG = _os.path.join(_MOBILE, "assets/models/logo_mark.png")


def ring(radius, z, seg=SEG):
    return [(radius * math.cos(2 * math.pi * i / seg),
             radius * math.sin(2 * math.pi * i / seg), z) for i in range(seg)]


def radial_normals(seg=SEG, zc=0.0):
    out = []
    for i in range(seg):
        a = 2 * math.pi * i / seg
        n = (math.cos(a), math.sin(a), zc)
        ln = math.sqrt(sum(k * k for k in n))
        out.append((n[0] / ln, n[1] / ln, n[2] / ln))
    return out


def band(m, lo, hi, normals=None):
    """Stitch two rings into a quad band."""
    for i in range(SEG):
        j = (i + 1) % SEG
        if normals:
            m.quad(lo[i], lo[j], hi[j], hi[i],
                   normals[i], normals[j], normals[j], normals[i])
        else:
            m.quad(lo[i], lo[j], hi[j], hi[i])


EDGE_T = 0.26   # how much of the bevel is the lit contour band


def build_body():
    """Returns (body, edge). The edge is a narrow band along the outer
    contour — the only part that glows, so the violet reads as a rim light
    rather than a violet barrel."""
    m = Mesh()
    e = Mesh()
    up, dn = (0.0, 0.0, 1.0), (0.0, 0.0, -1.0)
    bev_t = radial_normals(zc=0.85)
    bev_b = [(n[0], n[1], -n[2]) for n in bev_t]
    step_n = [(-n[0], -n[1], 0.0) for n in radial_normals()]

    mid_r = R + (BEZEL_R - R) * EDGE_T
    mid_dz = BARREL_Z + (T - BARREL_Z) * EDGE_T

    for sign, flat, bev in ((1, up, bev_t), (-1, dn, bev_b)):
        z = T * sign
        outer = ring(R, BARREL_Z * sign)
        mid = ring(mid_r, mid_dz * sign)
        bezel_o = ring(BEZEL_R, z)
        bezel_i = ring(FIELD_R, z)
        field_e = ring(FIELD_R, FIELD_Z * sign)
        if sign > 0:
            band(e, outer, mid, bev)                 # lit contour
            band(m, mid, bezel_o, bev)               # rest of the bevel
            band(m, bezel_o, bezel_i, [flat] * SEG)  # raised ring
            band(m, bezel_i, field_e, step_n)        # step down
        else:
            band(e, mid, outer, bev)
            band(m, bezel_o, mid, bev)
            band(m, bezel_i, bezel_o, [flat] * SEG)
            band(m, field_e, bezel_i, step_n)
        ctr = (0.0, 0.0, FIELD_Z * sign)
        for i in range(SEG):
            j = (i + 1) % SEG
            if sign > 0:
                m.tri(ctr, field_e[i], field_e[j], flat, flat, flat)
            else:
                m.tri(ctr, field_e[j], field_e[i], flat, flat, flat)
    return m, e


def build_rim():
    """Finely milled barrel — its own material, and the only violet on the coin."""
    m = Mesh()
    reed = [R + (0.0045 if i % 2 == 0 else -0.0045) for i in range(SEG)]
    top = [(reed[i] * math.cos(2 * math.pi * i / SEG),
            reed[i] * math.sin(2 * math.pi * i / SEG), BARREL_Z)
           for i in range(SEG)]
    bot = [(p[0], p[1], -BARREL_Z) for p in top]
    band(m, bot, top, radial_normals())
    return m


def build_emboss():
    m = Mesh()
    z0, z1 = FIELD_Z - 0.004, FIELD_Z + EMBOSS
    for cx, cy, h in [(-0.40, -0.17, 0.26), (-0.21, -0.09, 0.30),
                      (-0.02, 0.03, 0.35), (0.17, 0.16, 0.31),
                      (0.36, 0.30, 0.38)]:
        prism(m, rect(cx, cy, 0.100, h), z0, z1)
        prism(m, rect(cx, cy, 0.024, h + 0.17), z0, z1 - 0.008)
    pts = [(-0.54, -0.40), (-0.30, -0.26), (-0.09, -0.35),
           (0.14, -0.09), (0.38, 0.05)]
    for a, b in zip(pts, pts[1:]):
        prism(m, segment(a, b, 0.048), z0, z1)
    prism(m, [(0.35, 0.01), (0.54, 0.14), (0.38, 0.26)], z0, z1)
    return m


def sector(cx, cy, r, a0, a1, steps=10):
    """A convex circular sector — keep the span under 180 degrees."""
    import math as _m
    pts = [(cx, cy)]
    for i in range(steps + 1):
        a = _m.radians(a0 + (a1 - a0) * i / steps)
        pts.append((cx + r * _m.cos(a), cy + r * _m.sin(a)))
    return pts


def build_emboss_bars():
    """Satellite face: a rising bar chart."""
    m = Mesh()
    z0, z1 = FIELD_Z - 0.004, FIELD_Z + EMBOSS
    for cx, h in ((-0.30, 0.30), (0.0, 0.46), (0.30, 0.62)):
        prism(m, rect(cx, -0.34 + h / 2, 0.185, h), z0, z1)
    return m


def build_emboss_pie():
    """Satellite face: a segmented donut.

    Solid wedges turned into an unreadable blob once shrunk to satellite
    size. A ring with gaps keeps its silhouette legible.
    """
    m = Mesh()
    z0, z1 = FIELD_Z - 0.004, FIELD_Z + EMBOSS
    r_out, r_in = 0.50, 0.255
    for a0, a1 in ((8, 104), (120, 216), (232, 356)):
        steps = max(3, int((a1 - a0) / 12))
        for k in range(steps):
            b0 = math.radians(a0 + (a1 - a0) * k / steps)
            b1 = math.radians(a0 + (a1 - a0) * (k + 1) / steps)
            quad = [
                (r_in * math.cos(b0), r_in * math.sin(b0)),
                (r_out * math.cos(b0), r_out * math.sin(b0)),
                (r_out * math.cos(b1), r_out * math.sin(b1)),
                (r_in * math.cos(b1), r_in * math.sin(b1)),
            ]
            prism(m, quad, z0, z1)
    return m


def back_disc(segments=SEG):
    # 0.0025 was inside the depth buffer's noise floor against the back
    # field and the two surfaces fought. Well clear of it now.
    z = -FIELD_Z - 0.030
    pos, nrm, uv = [(0.0, 0.0, z)], [(0.0, 0.0, -1.0)], [(0.5, 0.5)]
    r = FIELD_R * 0.995
    for i in range(segments + 1):
        a = 2 * math.pi * i / segments
        x, y = r * math.cos(a), r * math.sin(a)
        pos.append((x, y, z))
        nrm.append((0.0, 0.0, -1.0))
        uv.append((0.5 - x / (2 * r), 0.5 - y / (2 * r)))  # mirrored: seen from behind
    idx = []
    for i in range(1, segments + 1):
        idx += [0, i + 1, i]
    return pos, nrm, uv, idx


def ease_in_out_cubic(t):
    return 4 * t ** 3 if t < 0.5 else 1 - ((-2 * t + 2) ** 3) / 2


def bezier(p0, c, p1, t):
    u = 1.0 - t
    return tuple(u * u * p0[i] + 2 * u * t * c[i] + t * t * p1[i] for i in range(3))


def intro_tracks():
    times = []
    m_rot, m_scale, m_pos = [], [], []
    l_pos, l_scale, l_rot, r_pos, r_scale, r_rot = [], [], [], [], [], []
    for i in range(INTRO_KEYS):
        u = i / (INTRO_KEYS - 1)
        times.append(u * INTRO_SECS)
        m_rot.append(quat_axis(0, 1, 0, (3 * 360.0 + REST_Y) * ease_out_cubic(u)))
        m_scale.append((max(ease_out_back(min(1.0, u / 0.55)), 0.001),) * 3)
        m_pos.append((0.0, 0.0, 0.0))
        v = max(0.0, (u - 0.10) / 0.90)
        vb = ease_out_back(min(1.0, v / 0.75), k=2.4)
        l_pos.append(lerp3((0.0, 0.0, 0.0), SAT_L, vb))
        r_pos.append(lerp3((0.0, 0.0, 0.0), SAT_R, vb))
        g = ease_out_back(min(1.0, v / 0.7))
        l_scale.append((max(SAT_L_S * g, 0.001),) * 3)
        r_scale.append((max(SAT_R_S * g, 0.001),) * 3)
        l_rot.append(quat_axis(0, 1, 0, (-720.0 - 35.0) * ease_out_cubic(v)))
        r_rot.append(quat_axis(0, 1, 0, (540.0 + 28.0) * ease_out_cubic(v)))
    return (times, m_rot, m_scale, m_pos, l_pos, l_scale, l_rot,
            r_pos, r_scale, r_rot)


def bob_tracks(secs, keys, with_rotation):
    times, m_pos, l_pos, r_pos = [], [], [], []
    m_rot, l_rot, r_rot = [], [], []
    for i in range(keys):
        u = i / (keys - 1)
        times.append(u * secs)
        lift = BOB * math.sin(2 * math.pi * u)
        m_pos.append((0.0, lift, 0.0))
        l_pos.append((SAT_L[0], SAT_L[1] + lift, SAT_L[2]))
        r_pos.append((SAT_R[0], SAT_R[1] + lift, SAT_R[2]))
        if with_rotation:
            w = math.sin(2 * math.pi * u)
            m_rot.append(quat_axis(0, 1, 0, REST_Y + 11.0 * w))
            l_rot.append(quat_axis(0, 1, 0, -35.0 - 26.0 * w))
            r_rot.append(quat_axis(0, 1, 0, 28.0 + 26.0 * w))
    return times, m_pos, l_pos, r_pos, m_rot, l_rot, r_rot


def success_tracks():
    times = []
    m_rot, m_scale, m_pos = [], [], []
    l_pos, l_scale, l_rot, r_pos, r_scale, r_rot = [], [], [], [], [], []
    c_l = (SAT_L[0] * 0.45 + 0.10, SAT_L[1] * 0.45 + 0.42, SAT_L[2] - 0.55)
    c_r = (SAT_R[0] * 0.45 - 0.10, SAT_R[1] * 0.45 - 0.42, SAT_R[2] - 0.55)
    origin = (0.0, 0.0, -0.70)
    for i in range(SUCC_KEYS):
        u = i / (SUCC_KEYS - 1)
        times.append(u * SUCC_SECS)
        travel = ease_in_out_cubic(min(1.0, u / 0.50))
        l_pos.append(bezier(SAT_L, c_l, origin, travel))
        r_pos.append(bezier(SAT_R, c_r, origin, travel))
        keep = max(0.0, min(1.0, 1.0 - max(0.0, (travel - 0.55) / 0.42) ** 1.6))
        l_scale.append((max(SAT_L_S * keep, 0.001),) * 3)
        r_scale.append((max(SAT_R_S * keep, 0.001),) * 3)
        l_rot.append(quat_axis(0, 1, 0, -35.0 - 400.0 * travel ** 1.7))
        r_rot.append(quat_axis(0, 1, 0, 28.0 + 340.0 * travel ** 1.7))
        w = max(0.0, (u - 0.34) / 0.66)
        m_rot.append(quat_axis(0, 1, 0, REST_Y + (180.0 - REST_Y) * ease_out_cubic(w)))
        swell = math.exp(-((u - 0.54) / 0.22) ** 2)
        m_scale.append((1.0 + 0.15 * swell,) * 3)
        m_pos.append((0.0, BOB * 0.35 * swell, 0.0))
    return (times, m_rot, m_scale, m_pos, l_pos, l_scale, l_rot,
            r_pos, r_scale, r_rot)


def s2l(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def lin(*c):
    return [round(s2l(x), 5) for x in c]


def main(out=None):
    out = out or _os.path.join(_MOBILE, "assets/models/coin.glb")
    body, edge = build_body()
    blob, specs = pack([body, edge, build_rim(), build_emboss(),
                        build_emboss_bars(), build_emboss_pie()])
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
            {"bufferView": vi, "componentType": 5125, "count": s["idx_count"], "type": "SCALAR"},
            {"bufferView": vi + 1, "componentType": 5126, "count": s["count"],
             "type": "VEC3", "min": s["min"], "max": s["max"]},
            {"bufferView": vi + 2, "componentType": 5126, "count": s["count"], "type": "VEC3"},
        ]
        prims.append({"attributes": {"POSITION": ai + 1, "NORMAL": ai + 2},
                      "indices": ai, "material": min(mat, 3)})

    def add_view(payload):
        while len(blob) % 4:
            blob.append(0)
        off = len(blob)
        blob.extend(payload)
        views.append({"buffer": 0, "byteOffset": off, "byteLength": len(payload)})
        return len(views) - 1

    def acc(fmt, values, kind, comp=5126, bounds=False):
        data = bytearray()
        for v in values:
            data.extend(struct.pack(fmt, *v) if isinstance(v, tuple) else struct.pack(fmt, v))
        a = {"bufferView": add_view(bytes(data)), "componentType": comp,
             "count": len(values), "type": kind}
        if bounds:
            a["min"], a["max"] = [min(values)], [max(values)]
        accessors.append(a)
        return len(accessors) - 1

    d_pos, d_nrm, d_uv, d_idx = back_disc()
    i_a = acc("<I", d_idx, "SCALAR", comp=5125)
    p_a = acc("<3f", d_pos, "VEC3")
    accessors[p_a]["min"] = [min(p[k] for p in d_pos) for k in range(3)]
    accessors[p_a]["max"] = [max(p[k] for p in d_pos) for k in range(3)]
    prims.append({"attributes": {"POSITION": p_a,
                                 "NORMAL": acc("<3f", d_nrm, "VEC3"),
                                 "TEXCOORD_0": acc("<2f", d_uv, "VEC2")},
                  "indices": i_a, "material": 4})
    img_view = add_view(open(LOGO_PNG, "rb").read())

    (it, m_rot, m_scale, m_pos, l_pos, l_scale, l_rot,
     r_pos, r_scale, r_rot) = intro_tracks()
    dt, d_m, d_l, d_r, _, _, _ = bob_tracks(IDLE_SECS, IDLE_KEYS, False)
    st, s_m, s_l, s_r, sm, sl, sr = bob_tracks(SPIN_SECS, SPIN_KEYS, True)
    (ct, c_rot, c_scale, c_pos, cl_pos, cl_scale, cl_rot,
     cr_pos, cr_scale, cr_rot) = success_tracks()

    it_a = acc("<f", it, "SCALAR", bounds=True)
    dt_a = acc("<f", dt, "SCALAR", bounds=True)
    st_a = acc("<f", st, "SCALAR", bounds=True)
    ct_a = acc("<f", ct, "SCALAR", bounds=True)

    def clip(name, pairs, ta):
        sm_, ch_ = [], []
        for node, path, values in pairs:
            kind = "VEC4" if path == "rotation" else "VEC3"
            fmt = "<4f" if path == "rotation" else "<3f"
            sm_.append({"input": ta, "interpolation": "LINEAR",
                        "output": acc(fmt, values, kind)})
            ch_.append({"sampler": len(sm_) - 1, "target": {"node": node, "path": path}})
        return {"name": name, "samplers": sm_, "channels": ch_}

    animations = [
        clip("Intro", [(1, "rotation", m_rot), (1, "scale", m_scale), (1, "translation", m_pos),
                       (2, "translation", l_pos), (2, "scale", l_scale), (2, "rotation", l_rot),
                       (3, "translation", r_pos), (3, "scale", r_scale), (3, "rotation", r_rot)], it_a),
        clip("Idle", [(1, "translation", d_m), (2, "translation", d_l), (3, "translation", d_r)], dt_a),
        clip("Spin", [(1, "translation", s_m), (2, "translation", s_l), (3, "translation", s_r),
                      (1, "rotation", sm), (2, "rotation", sl), (3, "rotation", sr)], st_a),
        clip("Success", [(1, "rotation", c_rot), (1, "scale", c_scale), (1, "translation", c_pos),
                         (2, "translation", cl_pos), (2, "scale", cl_scale), (2, "rotation", cl_rot),
                         (3, "translation", cr_pos), (3, "scale", cr_scale), (3, "rotation", cr_rot)], ct_a),
    ]

    gltf = {
        "asset": {"version": "2.0", "generator": "IntelliStock coin v5"},
        "scene": 0, "scenes": [{"nodes": [0]}],
        "nodes": [
            {"name": "Root", "rotation": list(quat_axis(1, 0, 0, TILT_X)), "children": [1, 2, 3]},
            {"name": "MainCoin", "mesh": 0, "rotation": list(quat_axis(0, 1, 0, REST_Y))},
            {"name": "SatL", "mesh": 1, "translation": list(SAT_L), "scale": [SAT_L_S] * 3,
             "rotation": list(quat_axis(0, 1, 0, -35.0))},
            {"name": "SatR", "mesh": 2, "translation": list(SAT_R), "scale": [SAT_R_S] * 3,
             "rotation": list(quat_axis(0, 1, 0, 28.0))},
        ],
        "meshes": [
            # Accessors are shared, so the extra faces cost only their own
            # geometry — the body and rim are not duplicated.
            {"name": "CoinMain",
             "primitives": [prims[0], prims[1], prims[2], prims[3], prims[6]]},
            {"name": "CoinBars",
             "primitives": [prims[0], prims[1], prims[2], prims[4]]},
            {"name": "CoinPie",
             "primitives": [prims[0], prims[1], prims[2], prims[5]]},
        ],
        "animations": animations,
        "images": [{"bufferView": img_view, "mimeType": "image/png"}],
        "samplers": [{"magFilter": 9729, "minFilter": 9987, "wrapS": 33071, "wrapT": 33071}],
        "textures": [{"sampler": 0, "source": 0}],
        "materials": [
            # Near-black gunmetal. No violet in the body at all.
            {"name": "CoinBody",
             "pbrMetallicRoughness": {"baseColorFactor": lin(0.105, 0.098, 0.135) + [1.0],
                                      "metallicFactor": 1.0, "roughnessFactor": 0.34},
             "emissiveFactor": lin(0.012, 0.010, 0.022)},
            # The ONLY violet: a dark base carrying a strong, saturated emissive,
            # so the edge reads as a rim light instead of pale plastic.
            # The bevel contour. Emissive draws a UNIFORM glowing ring — which
            # is what kept showing up as a hard magenta outline. A polished
            # metal with a violet cast instead: bright only where it actually
            # catches light, dark elsewhere, like the reference.
            {"name": "CoinEdge",
             "pbrMetallicRoughness": {"baseColorFactor": lin(0.560, 0.520, 0.680) + [1.0],
                                      "metallicFactor": 1.0, "roughnessFactor": 0.11},
             "emissiveFactor": lin(0.030, 0.026, 0.052)},
            # The barrel is dark METAL with a violet cast — a metal reflects
            # its own colour, so this catches violet highlights on the milling
            # without becoming a band of violet plastic.
            {"name": "CoinRim",
             "pbrMetallicRoughness": {"baseColorFactor": lin(0.135, 0.100, 0.235) + [1.0],
                                      "metallicFactor": 1.0, "roughnessFactor": 0.19},
             "emissiveFactor": lin(0.030, 0.020, 0.055)},
            # Brushed metal, not lilac — the motif is struck from the same alloy.
            {"name": "CoinEmboss",
             "pbrMetallicRoughness": {"baseColorFactor": lin(0.620, 0.590, 0.700) + [1.0],
                                      "metallicFactor": 1.0, "roughnessFactor": 0.24},
             "emissiveFactor": lin(0.075, 0.070, 0.105)},
            {"name": "CoinMark",
             "pbrMetallicRoughness": {"baseColorTexture": {"index": 0},
                                      # Full metal: a dielectric reflects the environment no
                                      # matter how dark its base, which is what
                                      # turned the plate grey. A metal reflects its
                                      # OWN colour, so near-black stays near-black.
                                      "metallicFactor": 1.0, "roughnessFactor": 0.22},
             "emissiveTexture": {"index": 0}, "emissiveFactor": [0.45, 0.45, 0.45],
             "alphaMode": "OPAQUE"},
        ],
        "buffers": [{"byteLength": len(blob)}],
        "bufferViews": views, "accessors": accessors,
    }

    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    bin_ = bytes(blob) + b"\x00" * ((4 - len(blob) % 4) % 4)
    glb = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(bin_))
    glb += struct.pack("<II", len(js), 0x4E4F534A) + js
    glb += struct.pack("<II", len(bin_), 0x004E4942) + bin_
    with open(out, "wb") as f:
        f.write(glb)
    print("wrote %s - %d bytes, %d tris, half-span %.2f"
          % (out, len(glb), sum(s["idx_count"] for s in specs) // 3,
             max(abs(SAT_L[0]), abs(SAT_R[0])) + max(SAT_L_S, SAT_R_S)))


if __name__ == "__main__":
    main()
