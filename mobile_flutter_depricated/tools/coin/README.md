# The login coin

`mobile/assets/models/coin.glb` is **generated**, not authored. If you open it
in a 3D editor and save it back, the next run of these scripts overwrites your
work. Change the scripts instead.

```sh
./build_coin.sh              # regenerate the model
./build_coin.sh --preview    # regenerate + render proof frames to /tmp
```

Needs `python3` with **Pillow** and **numpy**.

## What is in the model

Three coins — a main one plus two satellites — sharing body and rim geometry
and differing only in the motif struck on the face (candlesticks, bars, donut).
Materials are near-black gunmetal with the violet confined to a narrow band on
the outer bevel.

Four animation clips, all baked as keyframes:

| Clip | Length | Used for |
|---|---|---|
| `Intro` | 2.4s | The coins burst out of the centre and settle |
| `Idle` | 4.4s | A slow bob, all three in phase, loops |
| `Spin` | 2.2s | Held while a sign-in request is in flight |
| `Success` | 1.9s | Satellites collapse inward; the coin turns to show its reverse |

The motion is baked into the model rather than driven from Dart on purpose: the
viewer is a WebView, so animating from Flutter would mean a JavaScript bridge
call every frame.

## Files

| File | Role |
|---|---|
| `extract_logo.py` | Lifts the mark out of `assets/app_logo.png` into a texture |
| `make_coin.py` | Mesh primitives — prisms, rings, buffer packing |
| `make_coin_v2.py` | Shared geometry and easing helpers |
| `make_coin_v5.py` | The generator: geometry, materials, clips, GLB assembly |
| `render_glb.py` | Offline software renderer, for looking before deploying |
| `build_coin.sh` | Runs the pipeline |

## Things that cost time to learn

**glTF colours are LINEAR, not sRGB.** A `baseColorFactor` of 0.15 displays as
roughly sRGB 0.42 — a mid-tone, not the dark you picked. `make_coin_v5.py`
takes sRGB values and converts them; keep it that way.

**A full metal has no diffuse term.** `metallicFactor: 1.0` means the surface is
pure reflection, so a dark base under dim lighting renders black. Conversely a
*dielectric* reflects the environment regardless of how dark its base is, which
is what turns near-black plates grey. Both mistakes were made here.

**Emissive is uniform.** An emissive surface is equally bright at every angle,
so putting emissive on a rim draws a hard glowing outline rather than a glint.
The bevel highlight is a polished metal, deliberately.

**model-viewer auto-frames.** It fits the whole bounding box to the viewport,
so the coins only look bigger if the bounding box gets *tighter* — moving the
satellites in or out changes the apparent size of everything.

**Interrupting a looping clip leaves its target where it stood.** `Spin` only
oscillates a few degrees for this reason: a full rotation snapped back to the
rest angle whenever a sign-in completed mid-loop.

## Rendering previews

`render_glb.py` parses the GLB, evaluates a clip at a given time, and
rasterises it. It approximates model-viewer's environment — good enough to
catch framing, crowding, occlusion and gross colour errors, not a match for
exact brightness.

```sh
python3 render_glb.py rest 0 /tmp/proof
python3 render_glb.py Success 0.6,1.2,1.9 /tmp/success
```

Its ambient constants are calibrated against a real device screenshot. If
previews stop matching the phone, that calibration is the thing to revisit.
