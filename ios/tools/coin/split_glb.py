#!/usr/bin/env python3
"""Write one GLB per animation clip of the login coin.

The system `usdcat` converts only the FIRST glTF animation of a file (the
coin's Intro), so Idle, Spin and Success would be lost in a single USDZ. This
rewrites the GLB once per clip with `animations` holding only that clip.
Nodes, meshes, materials, accessors and the binary chunk are untouched, so
every output shares the same entity hierarchy (Root / MainCoin / SatL / SatR)
and RealityKit can play any clip on the Intro file's entities.

Usage: split_glb.py <coin.glb> <out_dir>
Writes <out_dir>/<clip>/coin.glb for every clip (lower-cased name). Every
file is named coin.glb so usdcat gives every layer the same root prim
(/coin), keeping animation binding paths identical across the files.
"""

import json
import struct
import sys
from pathlib import Path

GLB_MAGIC = 0x46546C67  # "glTF"
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942


def read_glb(path: Path):
    data = path.read_bytes()
    magic, version, length = struct.unpack_from("<III", data, 0)
    if magic != GLB_MAGIC or version != 2:
        raise SystemExit(f"{path}: not a glTF 2.0 binary")
    offset = 12
    doc = None
    binary = b""
    while offset < length:
        chunk_len, chunk_type = struct.unpack_from("<II", data, offset)
        body = data[offset + 8: offset + 8 + chunk_len]
        if chunk_type == CHUNK_JSON:
            doc = json.loads(body)
        elif chunk_type == CHUNK_BIN:
            binary = body
        offset += 8 + chunk_len
    if doc is None:
        raise SystemExit(f"{path}: no JSON chunk")
    return doc, binary


def pad(chunk: bytes, filler: bytes) -> bytes:
    return chunk + filler * ((4 - len(chunk) % 4) % 4)


def write_glb(path: Path, doc, binary: bytes) -> None:
    json_chunk = pad(json.dumps(doc, separators=(",", ":")).encode("utf-8"), b" ")
    bin_chunk = pad(binary, b"\x00")
    total = 12 + 8 + len(json_chunk) + (8 + len(bin_chunk) if bin_chunk else 0)
    out = bytearray(struct.pack("<III", GLB_MAGIC, 2, total))
    out += struct.pack("<II", len(json_chunk), CHUNK_JSON) + json_chunk
    if bin_chunk:
        out += struct.pack("<II", len(bin_chunk), CHUNK_BIN) + bin_chunk
    path.write_bytes(bytes(out))


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    source, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    doc, binary = read_glb(source)
    clips = doc.get("animations", [])
    if not clips:
        raise SystemExit(f"{source}: no animations")
    for clip in clips:
        single = dict(doc)
        single["animations"] = [clip]
        name = clip.get("name", "clip").lower()
        (out_dir / name).mkdir(parents=True, exist_ok=True)
        target = out_dir / name / "coin.glb"
        write_glb(target, single, binary)
        print(f"{target}  ({clip.get('name')}: {len(clip['channels'])} channels)")


if __name__ == "__main__":
    main()
