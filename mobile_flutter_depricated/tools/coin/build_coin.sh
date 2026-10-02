#!/usr/bin/env bash
#
# build_coin.sh — regenerate mobile/assets/models/coin.glb
#
# The coin on the login screen is a generated glTF binary, not an authored
# asset. Everything about it — geometry, materials, the four animation clips,
# and the IntelliStock mark on its reverse — comes out of the scripts here.
# Editing the .glb by hand is not the workflow; edit the scripts and re-run
# this.
#
# Requires: python3 with Pillow and numpy.
#
#   ./build_coin.sh              regenerate the model
#   ./build_coin.sh --preview    also render proof-sheet frames to /tmp
#
# NOTE: the app bundles assets at BUILD time, so a `flutter run` hot reload
# will NOT pick up a new .glb. Restart the app after regenerating.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

echo "▸ Extracting the mark from assets/app_logo.png…"
python3 extract_logo.py

echo "▸ Generating coin.glb…"
python3 make_coin_v5.py

if [ "${1:-}" = "--preview" ]; then
  echo "▸ Rendering proof frames…"
  python3 render_glb.py rest 0 /tmp/coin_rest
  python3 render_glb.py Intro 0.4,1.2,2.4 /tmp/coin_intro
  python3 render_glb.py Success 0.6,1.2,1.9 /tmp/coin_success
  echo "  frames in /tmp/coin_*.png"
fi

# The texture lives INSIDE the .glb; leaving the loose PNG in assets/ would
# ship it a second time for nothing.
rm -f ../../assets/models/logo_mark.png

echo "✓ mobile/assets/models/coin.glb rebuilt"
ls -lh ../../assets/models/coin.glb | awk '{print "  " $5}'
