#!/bin/sh
# Builds ios/IntelliStock/Resources/Coin/coin_<clip>.usdz from the Flutter
# app's mobile/assets/models/coin.glb, one USDZ per animation clip.
#
# The system usdcat converts only a file's FIRST glTF animation, so each clip
# gets its own GLB (split_glb.py, always named coin.glb so every layer's root
# prim is /coin and the clips bind to the same paths), then:
#   usdextract  → the embedded CoinMark texture as a loose PNG
#   usdcat      → USDA, whose texture path is rewritten to that PNG
#   retime_usda → 60 whole time codes per second (RealityKit samples whole
#                 codes; the converter's 1 code/s kept 2 samples of a 1.9 s clip)
#   usdcat      → USDC
#   usdzip      → USDZ (layer + texture)
#
# Usage: ios/tools/coin/build_coin.sh [path/to/coin.glb]
set -eu

here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)
glb=${1:-"$repo/mobile/assets/models/coin.glb"}
out="$repo/ios/IntelliStock/Resources/Coin"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

python3 "$here/split_glb.py" "$glb" "$work"
mkdir -p "$out"

for dir in "$work"/*/; do
    dir=${dir%/}
    clip=$(basename "$dir")
    (
        cd "$dir"
        usdextract coin.glb -o . >/dev/null
        usdcat coin.glb -o coin.usda 2>/dev/null
        # @/abs/path/coin.glb[CoinMark_diffuse.png]@ → @CoinMark_diffuse.png@
        sed -E 's#@[^@]*\.glb\[([^]@]+)\]@#@\1@#g' coin.usda > fixed.usda
        python3 "$here/retime_usda.py" fixed.usda coin.usda 60
        usdcat coin.usda -o coin.usdc
        textures=$(find . -maxdepth 1 -name '*.png' -exec basename {} \;)
        rm -f "$out/coin_$clip.usdz"
        # shellcheck disable=SC2086
        usdzip "$out/coin_$clip.usdz" coin.usdc $textures >/dev/null
    )
    echo "$out/coin_$clip.usdz"
done
