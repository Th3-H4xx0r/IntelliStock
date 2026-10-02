"""Generate a violet metallic trading coin as a glTF 2.0 binary (.glb).

Built rather than downloaded: free coin models are gold and cartoon-styled, and
would not sit in the app's violet palette. This one is authored to the theme —
a bevelled, reeded disc with a rising-candlestick motif embossed on its face.

Geometry is flat-shaded (per-face normals) except the barrel, which is smoothed
around its circumference so the rim catches a moving highlight instead of
faceting. Output is a single GLB with two primitives (body, emboss).
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

SEG = 128          # segments around the coin — silhouette must read as round
R = 1.0            # outer radius
T = 0.15           # half-thickness (total 0.30)
BEV = 0.10         # bevel width, taken off the radius
BEV_Z = 0.055      # bevel depth in z
EMBOSS = 0.030     # how far the motif stands proud of the face
FACE_R = R - BEV   # flat face radius


class Mesh:
    """Accumulates triangles as (position, normal) vertex pairs."""

    def __init__(self):
        self.pos = []
        self.nrm = []
        self.idx = []

    def tri(self, a, b, c, na=None, nb=None, nc=None):
        if na is None:
            na = nb = nc = _face_normal(a, b, c)
        base = len(self.pos)
        for p, n in ((a, na), (b, nb), (c, nc)):
            self.pos.append(p)
            self.nrm.append(n)
        self.idx += [base, base + 1, base + 2]

    def quad(self, a, b, c, d, na=None, nb=None, nc=None, nd=None):
        if na is None:
            n = _face_normal(a, b, c)
            na = nb = nc = nd = n
        self.tri(a, b, c, na, nb, nc)
        self.tri(a, c, d, na, nc, nd)

    def fan(self, poly, z, nz):
        """Triangulate a convex 2D polygon at height z, facing +nz."""
        n = (0.0, 0.0, nz)
        for i in range(1, len(poly) - 1):
            a = (poly[0][0], poly[0][1], z)
            b = (poly[i][0], poly[i][1], z)
            c = (poly[i + 1][0], poly[i + 1][1], z)
            if nz < 0:
                b, c = c, b
            self.tri(a, b, c, n, n, n)


def _sub(u, v):
    return (u[0] - v[0], u[1] - v[1], u[2] - v[2])


def _face_normal(a, b, c):
    u, v = _sub(b, a), _sub(c, a)
    n = (
        u[1] * v[2] - u[2] * v[1],
        u[2] * v[0] - u[0] * v[2],
        u[0] * v[1] - u[1] * v[0],
    )
    ln = math.sqrt(sum(k * k for k in n)) or 1.0
    return (n[0] / ln, n[1] / ln, n[2] / ln)


def ring(radius, z, seg=SEG):
    return [
        (radius * math.cos(2 * math.pi * i / seg),
         radius * math.sin(2 * math.pi * i / seg),
         z)
        for i in range(seg)
    ]


def radial_normals(seg=SEG, zc=0.0):
    out = []
    for i in range(seg):
        a = 2 * math.pi * i / seg
        n = (math.cos(a), math.sin(a), zc)
        ln = math.sqrt(sum(k * k for k in n))
        out.append((n[0] / ln, n[1] / ln, n[2] / ln))
    return out


def build_body():
    m = Mesh()

    # Reeded barrel: alternate the radius slightly so the edge has milling.
    # Smoothed normals around the circumference keep the highlight travelling.
    reed = [R + (0.012 if i % 2 == 0 else -0.012) for i in range(SEG)]
    outer_top = [
        (reed[i] * math.cos(2 * math.pi * i / SEG),
         reed[i] * math.sin(2 * math.pi * i / SEG),
         T - BEV_Z)
        for i in range(SEG)
    ]
    outer_bot = [(p[0], p[1], -(T - BEV_Z)) for p in outer_top]
    rn = radial_normals()

    face_top = ring(FACE_R, T)
    face_bot = ring(FACE_R, -T)

    # Bevel normals point out and up (or down).
    bev_n_top = radial_normals(zc=0.9)
    bev_n_bot = [(n[0], n[1], -n[2]) for n in bev_n_top]

    for i in range(SEG):
        j = (i + 1) % SEG
        # Barrel.
        m.quad(outer_bot[i], outer_bot[j], outer_top[j], outer_top[i],
               rn[i], rn[j], rn[j], rn[i])
        # Top bevel and bottom bevel.
        m.quad(outer_top[i], outer_top[j], face_top[j], face_top[i],
               bev_n_top[i], bev_n_top[j], bev_n_top[j], bev_n_top[i])
        m.quad(face_bot[i], face_bot[j], outer_bot[j], outer_bot[i],
               bev_n_bot[i], bev_n_bot[j], bev_n_bot[j], bev_n_bot[i])

    # Faces, as fans from the centre.
    up, dn = (0.0, 0.0, 1.0), (0.0, 0.0, -1.0)
    ctr_t, ctr_b = (0.0, 0.0, T), (0.0, 0.0, -T)
    for i in range(SEG):
        j = (i + 1) % SEG
        m.tri(ctr_t, face_top[i], face_top[j], up, up, up)
        m.tri(ctr_b, face_bot[j], face_bot[i], dn, dn, dn)
    return m


def prism(m, poly, z0, z1):
    """Extrude a convex 2D polygon between two z planes, capped both ends."""
    m.fan(poly, z1, 1.0)
    m.fan(poly, z0, -1.0)
    n = len(poly)
    for i in range(n):
        a2, b2 = poly[i], poly[(i + 1) % n]
        m.quad((a2[0], a2[1], z0), (b2[0], b2[1], z0),
               (b2[0], b2[1], z1), (a2[0], a2[1], z1))


def rect(cx, cy, w, h):
    hw, hh = w / 2, h / 2
    return [(cx - hw, cy - hh), (cx + hw, cy - hh),
            (cx + hw, cy + hh), (cx - hw, cy + hh)]


def segment(p, q, w):
    """A thick line segment between two 2D points, as a convex quad."""
    dx, dy = q[0] - p[0], q[1] - p[1]
    ln = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / ln * w / 2, dx / ln * w / 2
    return [(p[0] + nx, p[1] + ny), (q[0] + nx, q[1] + ny),
            (q[0] - nx, q[1] - ny), (p[0] - nx, p[1] - ny)]


def build_emboss():
    """Rising candlesticks with a trend line breaking upward through them."""
    m = Mesh()
    # Start just BELOW the face so the prisms' bottom caps are buried
    # inside the coin rather than coplanar with it (which would z-fight).
    z0, z1 = T - 0.004, T + EMBOSS

    # Five candles, climbing left to right.
    candles = [
        (-0.46, -0.20, 0.30),
        (-0.24, -0.10, 0.34),
        (-0.02, 0.04, 0.40),
        (0.20, 0.18, 0.36),
        (0.42, 0.34, 0.44),
    ]
    for cx, cy, h in candles:
        prism(m, rect(cx, cy, 0.115, h), z0, z1)          # body
        prism(m, rect(cx, cy, 0.028, h + 0.20), z0, z1 - 0.008)  # wick

    # Trend line rising through them.
    pts = [(-0.62, -0.46), (-0.34, -0.30), (-0.10, -0.40),
           (0.16, -0.10), (0.44, 0.06)]
    for a, b in zip(pts, pts[1:]):
        prism(m, segment(a, b, 0.055), z0, z1)

    # Arrowhead at the end of the trend line.
    prism(m, [(0.40, 0.02), (0.62, 0.16), (0.44, 0.30)], z0, z1)
    return m


def pack(meshes):
    """Lay the meshes into one binary buffer; return (blob, accessor specs)."""
    blob = bytearray()
    specs = []
    for m in meshes:
        # Indices (uint32), then positions, then normals — each 4-byte aligned.
        while len(blob) % 4:
            blob.append(0)
        idx_off = len(blob)
        for i in m.idx:
            blob += struct.pack("<I", i)

        while len(blob) % 4:
            blob.append(0)
        pos_off = len(blob)
        for p in m.pos:
            blob += struct.pack("<3f", *p)

        while len(blob) % 4:
            blob.append(0)
        nrm_off = len(blob)
        for n in m.nrm:
            blob += struct.pack("<3f", *n)

        xs = [p[0] for p in m.pos]
        ys = [p[1] for p in m.pos]
        zs = [p[2] for p in m.pos]
        specs.append({
            "idx_off": idx_off, "idx_count": len(m.idx),
            "pos_off": pos_off, "nrm_off": nrm_off, "count": len(m.pos),
            "min": [min(xs), min(ys), min(zs)],
            "max": [max(xs), max(ys), max(zs)],
        })
    return bytes(blob), specs


def main(out=None):
    out = out or _os.path.join(_MOBILE, "assets/models/coin.glb")
    body, emboss = build_body(), build_emboss()
    blob, specs = pack([body, emboss])

    views, accessors, prims = [], [], []
    for s in specs:
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
        prims.append({
            "attributes": {"POSITION": ai + 1, "NORMAL": ai + 2},
            "indices": ai,
            "material": len(prims),
        })

    gltf = {
        "asset": {"version": "2.0", "generator": "IntelliStock coin generator"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": "Coin"}],
        "meshes": [{"name": "Coin", "primitives": prims}],
        "materials": [
            {
                "name": "CoinBody",
                "pbrMetallicRoughness": {
                    # Deep violet-black metal — the body reads dark, the light
                    # does the work.
                    "baseColorFactor": [0.145, 0.098, 0.255, 1.0],
                    "metallicFactor": 1.0,
                    "roughnessFactor": 0.28,
                },
                "emissiveFactor": [0.055, 0.030, 0.115],
            },
            {
                "name": "CoinEmboss",
                "pbrMetallicRoughness": {
                    # Bright lavender, polished — this is what catches the eye.
                    "baseColorFactor": [0.725, 0.647, 0.961, 1.0],
                    "metallicFactor": 1.0,
                    "roughnessFactor": 0.16,
                },
                "emissiveFactor": [0.240, 0.190, 0.420],
            },
        ],
        "buffers": [{"byteLength": len(blob)}],
        "bufferViews": views,
        "accessors": accessors,
    }

    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    bin_ = blob + b"\x00" * ((4 - len(blob) % 4) % 4)

    glb = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(bin_))
    glb += struct.pack("<II", len(js), 0x4E4F534A) + js
    glb += struct.pack("<II", len(bin_), 0x004E4942) + bin_

    with open(out, "wb") as f:
        f.write(glb)
    tris = sum(s["idx_count"] for s in specs) // 3
    print("wrote %s — %d bytes, %d triangles" % (out, len(glb), tris))


if __name__ == "__main__":
    main()
