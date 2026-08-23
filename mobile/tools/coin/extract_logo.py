"""Build the coin's reverse texture from the REAL app icon.

Every hand-drawn attempt at the mark was an approximation and every one was
wrong. The actual artwork ships in the repo at mobile/assets/app_logo.png, so
lift the glyph straight out of it: threshold the bright strokes off the dark
icon background, keep their own colour, and composite onto the coin's plate.
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

import numpy as np
from PIL import Image

SRC = _os.path.join(_MOBILE, "assets/app_logo.png")
OUT = _os.path.join(_MOBILE, "assets/models/logo_mark.png")
# 512 is right-sized: the reverse never renders above ~500px, and the source
# icon is now 512 too, so this is close to 1:1 rather than an upscale.
SIZE = 512
PLATE = (16, 15, 21)      # the coin's field — near-black, so only the mark lights up
FILL = 0.72               # how much of the coin face the mark spans


def main():
    src = Image.open(SRC).convert("RGBA")
    a = np.asarray(src).astype(np.float64)
    rgb, alpha = a[..., :3], a[..., 3]

    # The glyph is the bright part of the icon; the rounded-square backdrop and
    # its faint grid are dark. Luminance separates them cleanly.
    lum = rgb @ np.array([0.2126, 0.7152, 0.0722])
    # 90 also caught the icon's rounded-square border glow, which threw the
    # bounding box off and dragged an arc into the crop. The strokes are far
    # brighter than that halo.
    # Ignore the outer frame entirely — that is where the rounded-square
    # border halo lives — then a LOW threshold can be used, which is what
    # the bounding box needs. A high threshold found only the bright half
    # of the mark and cropped the darker right-hand end off.
    inset = int(min(a.shape[:2]) * 0.11)
    region = np.zeros(lum.shape, bool)
    region[inset:-inset, inset:-inset] = True
    mask = (lum > 55) & (alpha > 8) & region
    if not mask.any():
        raise SystemExit("no glyph found — thresholds need adjusting")

    ys, xs = np.nonzero(mask)
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    side = max(x1 - x0, y1 - y0) + 1
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    half = side // 2 + int(side * 0.05)   # breathing room for the gradient tails
    box = (max(cx - half, 0), max(cy - half, 0),
           min(cx + half, a.shape[1]), min(cy + half, a.shape[0]))
    print("glyph bbox %dx%d at (%d,%d), cropped to %s"
          % (x1 - x0 + 1, y1 - y0 + 1, x0, y0, box))

    # Soft alpha from luminance keeps the stroke's anti-aliased edges.
    # The bbox threshold above only has to FIND the glyph. The alpha ramp
    # must be far lower, because the mark is a gradient — its right-hand
    # end is deep violet and a high floor sheared it off. The halo cannot
    # contaminate this: it lives outside the crop.
    soft = np.clip((lum - 36) / 58.0, 0, 1) * (alpha / 255.0)
    glyph = np.dstack([rgb, soft * 255.0]).astype(np.uint8)
    g = Image.fromarray(glyph, "RGBA").crop(box)

    span = int(SIZE * FILL)
    g = g.resize((span, span), Image.LANCZOS)

    out = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    # The plate fills the inscribed circle — the UVs map the coin's field to it.
    from PIL import ImageDraw
    ImageDraw.Draw(out).ellipse([0, 0, SIZE, SIZE], fill=PLATE + (255,))
    off = (SIZE - span) // 2
    out.alpha_composite(g, (off, off))
    out.save(OUT)

    px = np.asarray(out)
    lit = (px[..., :3] @ np.array([0.2126, 0.7152, 0.0722]) > 90).mean()
    print("wrote %s - %dx%d, mark covers %.1f%%" % (OUT, SIZE, SIZE, lit * 100))


if __name__ == "__main__":
    main()
