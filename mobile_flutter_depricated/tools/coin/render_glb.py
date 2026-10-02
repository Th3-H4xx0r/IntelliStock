"""A small software renderer for the coin, so it can be inspected offline.

Deploying to the phone to find out what a change looks like costs a two-minute
rebuild per guess. This parses the GLB we generate, evaluates a clip at a given
time, and rasterises it with a PBR approximation close enough to model-viewer's
neutral environment to catch the things that actually go wrong: washed-out
plates, bad framing, crowding, wrong occlusion.

It is NOT a match for the real renderer's IBL — treat it as a proof sheet.

  python3 render_glb.py <clip> <t0,t1,...> out_prefix
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
import numpy as np
from PIL import Image

W = H = 460
# Stand-ins for model-viewer's neutral environment.
ENV_DIFF = np.array([1.75, 1.75, 1.85])
ENV_SPEC = np.array([2.40, 2.40, 2.60])
KEY_DIR = np.array([-0.35, 0.55, 0.75])
KEY_DIR = KEY_DIR / np.linalg.norm(KEY_DIR)
KEY_COL = np.array([1.25, 1.20, 1.35])


def load(path):
    b = open(path, "rb").read()
    off, chunks = 12, {}
    while off < len(b):
        ln, ty = struct.unpack("<II", b[off:off + 8])
        chunks[ty] = b[off + 8:off + 8 + ln]
        off += 8 + ln
    return json.loads(chunks[0x4E4F534A]), chunks[0x004E4942]


def accessor(g, bin_, i):
    a = g["accessors"][i]
    v = g["bufferViews"][a["bufferView"]]
    n = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[a["type"]]
    dt = np.uint32 if a["componentType"] == 5125 else np.float32
    arr = np.frombuffer(bin_, dtype=dt, count=a["count"] * n,
                        offset=v["byteOffset"])
    return arr.reshape(-1, n) if n > 1 else arr


def quat_to_mat(q):
    x, y, z, w = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def slerp(a, b, t):
    d = float(np.dot(a, b))
    if d < 0:
        b, d = -b, -d
    if d > 0.9995:
        return (a + t * (b - a)) / np.linalg.norm(a + t * (b - a))
    th = math.acos(d)
    return (math.sin((1 - t) * th) * a + math.sin(t * th) * b) / math.sin(th)


def sample(g, bin_, clip, node, path, t):
    for ch in clip["channels"]:
        if ch["target"]["node"] != node or ch["target"]["path"] != path:
            continue
        s = clip["samplers"][ch["sampler"]]
        times = accessor(g, bin_, s["input"])
        vals = accessor(g, bin_, s["output"])
        t = min(max(t, float(times[0])), float(times[-1]))
        i = int(np.searchsorted(times, t) )
        i = max(1, min(i, len(times) - 1))
        t0, t1 = float(times[i - 1]), float(times[i])
        f = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
        if path == "rotation":
            return slerp(vals[i - 1].astype(np.float64),
                         vals[i].astype(np.float64), f)
        return vals[i - 1] * (1 - f) + vals[i] * f
    return None


def node_matrix(g, bin_, clip, i, t):
    n = g["nodes"][i]
    tr = np.array(n.get("translation", [0, 0, 0]), dtype=np.float64)
    rot = np.array(n.get("rotation", [0, 0, 0, 1]), dtype=np.float64)
    sc = np.array(n.get("scale", [1, 1, 1]), dtype=np.float64)
    if clip is not None:
        v = sample(g, bin_, clip, i, "translation", t)
        if v is not None:
            tr = np.asarray(v, dtype=np.float64)
        v = sample(g, bin_, clip, i, "rotation", t)
        if v is not None:
            rot = np.asarray(v, dtype=np.float64)
        v = sample(g, bin_, clip, i, "scale", t)
        if v is not None:
            sc = np.asarray(v, dtype=np.float64)
    m = np.eye(4)
    m[:3, :3] = quat_to_mat(rot) @ np.diag(sc)
    m[:3, 3] = tr
    return m


def gather(g, bin_, clip, t):
    """Return (verts, normals, uvs, tris, material_index_per_tri) in world space."""
    out_v, out_n, out_uv, out_t, out_m = [], [], [], [], []
    root = g["scenes"][0]["nodes"][0]

    def walk(i, parent):
        m = parent @ node_matrix(g, bin_, clip, i, t)
        n = g["nodes"][i]
        if "mesh" in n:
            nm = np.linalg.inv(m[:3, :3]).T
            for p in g["meshes"][n["mesh"]]["primitives"]:
                pos = accessor(g, bin_, p["attributes"]["POSITION"]).astype(np.float64)
                nor = accessor(g, bin_, p["attributes"]["NORMAL"]).astype(np.float64)
                idx = accessor(g, bin_, p["indices"]).astype(np.int64)
                uv = (accessor(g, bin_, p["attributes"]["TEXCOORD_0"]).astype(np.float64)
                      if "TEXCOORD_0" in p["attributes"]
                      else np.zeros((len(pos), 2)))
                base = sum(len(v) for v in out_v)
                out_v.append(pos @ m[:3, :3].T + m[:3, 3])
                out_n.append(nor @ nm.T)
                out_uv.append(uv)
                tris = idx.reshape(-1, 3) + base
                out_t.append(tris)
                out_m.append(np.full(len(tris), p["material"]))
        for c in n.get("children", []):
            walk(c, m)

    walk(root, np.eye(4))
    return (np.vstack(out_v), np.vstack(out_n), np.vstack(out_uv),
            np.vstack(out_t), np.concatenate(out_m))


def srgb(x):
    x = np.clip(x, 0, 1)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * x ** (1 / 2.4) - 0.055)


def render(path, clip_name, t, out):
    g, bin_ = load(path)
    clip = None
    if clip_name != "rest":
        clip = next(a for a in g["animations"] if a["name"] == clip_name)

    # Frame on the REST pose, as model-viewer does — otherwise every frame
    # would re-zoom and the preview would lie about how big things look.
    rv, _, _, _, _ = gather(g, bin_, None, 0.0)
    centre = (rv.min(0) + rv.max(0)) / 2
    radius = float(np.linalg.norm(rv - centre, axis=1).max())

    verts, norms, uvs, tris, mats = gather(g, bin_, clip, t)

    tex = None
    if "images" in g:
        iv = g["bufferViews"][g["images"][0]["bufferView"]]
        import io as _io
        tex = np.asarray(Image.open(_io.BytesIO(
            bin_[iv["byteOffset"]:iv["byteOffset"] + iv["byteLength"]]
        )).convert("RGB"), dtype=np.float64) / 255.0

    fov = math.radians(38)
    dist = radius / math.tan(fov / 2) * 1.06
    eye = centre + np.array([0, 0, dist])

    vv = verts - eye
    f = (H / 2) / math.tan(fov / 2)
    z = -vv[:, 2]
    px = W / 2 + vv[:, 0] * f / np.maximum(z, 1e-6)
    py = H / 2 - vv[:, 1] * f / np.maximum(z, 1e-6)

    img = np.zeros((H, W, 3))
    zbuf = np.full((H, W), np.inf)
    V = np.array([0, 0, 1.0])

    def mat_of(i):
        m = g["materials"][i]
        pbr = m["pbrMetallicRoughness"]
        base = np.array(pbr.get("baseColorFactor", [1, 1, 1, 1])[:3])
        return (base, pbr.get("metallicFactor", 1.0),
                pbr.get("roughnessFactor", 0.5),
                np.array(m.get("emissiveFactor", [0, 0, 0])),
                "baseColorTexture" in pbr)

    drawn = {}
    culled = {}
    order = np.argsort(-z[tris].mean(1))
    for ti in order:
        a, b, c = tris[ti]
        x0, y0 = px[a], py[a]
        x1, y1 = px[b], py[b]
        x2, y2 = px[c], py[c]
        area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
        if area >= 0:
            culled[int(mats[ti])] = culled.get(int(mats[ti]), 0) + 1
            continue  # back-facing
        lo_x = max(int(min(x0, x1, x2)), 0)
        hi_x = min(int(max(x0, x1, x2)) + 1, W - 1)
        lo_y = max(int(min(y0, y1, y2)), 0)
        hi_y = min(int(max(y0, y1, y2)) + 1, H - 1)
        if lo_x > hi_x or lo_y > hi_y:
            continue
        ys, xs = np.mgrid[lo_y:hi_y + 1, lo_x:hi_x + 1]
        w0 = ((x1 - x0) * (ys - y0) - (y1 - y0) * (xs - x0)) / area
        w1 = ((x2 - x1) * (ys - y1) - (y2 - y1) * (xs - x1)) / area
        w2 = 1 - w0 - w1
        inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
        if not inside.any():
            continue
        zz = w1 * z[a] + w2 * z[b] + w0 * z[c]
        sel = inside & (zz < zbuf[lo_y:hi_y + 1, lo_x:hi_x + 1])
        if not sel.any():
            continue

        base, metal, rough, emis, has_tex = mat_of(mats[ti])
        n = norms[a] + norms[b] + norms[c]
        n = n / (np.linalg.norm(n) + 1e-9)

        if has_tex and tex is not None:
            # Weights are (rows, cols); UVs are 2-vectors — add an axis
            # or numpy tries to broadcast them against each other.
            uv = (w1[..., None] * uvs[a] + w2[..., None] * uvs[b]
                  + w0[..., None] * uvs[c])
            tx = np.clip((uv[..., 0] * (tex.shape[1] - 1)).astype(int),
                         0, tex.shape[1] - 1)
            ty = np.clip(((uv[..., 1]) * (tex.shape[0] - 1)).astype(int),
                         0, tex.shape[0] - 1)
            texel = tex[ty, tx]
            alb = np.where(texel <= 0.04045, texel / 12.92,
                           ((texel + 0.055) / 1.055) ** 2.4)
            emis_px = alb * emis
        else:
            alb = base
            emis_px = emis

        f0 = 0.04 * (1 - metal) + alb * metal
        ndv = max(float(np.dot(n, V)), 0.0)
        hv = (KEY_DIR + V) / np.linalg.norm(KEY_DIR + V)
        ndh = max(float(np.dot(n, hv)), 0.0)
        ndl = max(float(np.dot(n, KEY_DIR)), 0.0)
        shin = 2.0 / (rough ** 4 + 1e-4) - 2.0
        spec = f0 * (ENV_SPEC * (1 - rough * 0.65) * (0.25 + 0.75 * (1 - ndv) ** 3)
                     + KEY_COL * ndh ** min(shin, 400) * 0.6)
        diff = alb * (1 - metal) * ENV_DIFF * (0.45 + 0.55 * ndl)
        col = diff + spec + emis_px

        col = col / (1.0 + col)  # tone map
        patch = img[lo_y:hi_y + 1, lo_x:hi_x + 1]
        if col.ndim == 1:
            col = np.broadcast_to(col, patch.shape)
        patch[sel] = col[sel] if col.ndim == 3 else col
        img[lo_y:hi_y + 1, lo_x:hi_x + 1] = patch
        zb = zbuf[lo_y:hi_y + 1, lo_x:hi_x + 1]
        zb[sel] = zz[sel]
        zbuf[lo_y:hi_y + 1, lo_x:hi_x + 1] = zb
        drawn[int(mats[ti])] = drawn.get(int(mats[ti]), 0) + 1

    print('    drawn by material:', dict(sorted(drawn.items())),
          '| culled:', dict(sorted(culled.items())))
    Image.fromarray((srgb(img) * 255).astype(np.uint8)).save(out)
    print("  %s  t=%.2f -> %s" % (clip_name, t, out))


if __name__ == "__main__":
    clip = sys.argv[1]
    ts = [float(x) for x in sys.argv[2].split(",")]
    prefix = sys.argv[3]
    for k, t in enumerate(ts):
        render(_os.path.join(_MOBILE, "assets/models/coin.glb"), clip, t, "%s_%d.png" % (prefix, k))
