#!/usr/bin/env python3
"""Re-time a USDA layer from 1 time code per second to FPS time codes per second.

The glTF converter writes `timeCodesPerSecond = 1` with fractional time codes
(0.025, 0.05, … seconds). RealityKit samples USD animation at whole time codes,
so a 1.9 s clip at 1 tcps keeps only the samples at 0 and 1 — the coin's
Success turn stopped at about 103° instead of 180°. Scaling every time-sample
key (and the stage's start/end) by FPS keeps the same timing in seconds with
FPS whole codes per second.

Usage: retime_usda.py <in.usda> <out.usda> [fps=60]
"""

import re
import sys

KEY = re.compile(r"^(\s*)(-?[0-9][0-9.eE+-]*)(:\s)")


def retime(text: str, fps: float) -> str:
    out = []
    in_samples = False
    depth = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if not in_samples and stripped.endswith(".timeSamples = {"):
            in_samples = True
            depth = 1
            out.append(line)
            continue
        if in_samples:
            # Values never contain braces, so the block ends at its closing one.
            depth += line.count("{") - line.count("}")
            if depth <= 0:
                in_samples = False
                out.append(line)
                continue
            m = KEY.match(line)
            if m:
                scaled = float(m.group(2)) * fps
                line = f"{m.group(1)}{scaled:.6g}{m.group(3)}{line[m.end():]}"
            out.append(line)
            continue
        out.append(line)
    result = "".join(out)

    def scale_meta(match: re.Match) -> str:
        return f"{match.group(1)}{float(match.group(2)) * fps:.6g}"

    result = re.sub(r"(endTimeCode = )([0-9.eE+-]+)", scale_meta, result, count=1)
    result = re.sub(r"(startTimeCode = )([0-9.eE+-]+)", scale_meta, result, count=1)
    result = re.sub(r"timeCodesPerSecond = [0-9.]+", f"timeCodesPerSecond = {fps:g}", result, count=1)
    if "framesPerSecond" not in result:
        result = result.replace(f"timeCodesPerSecond = {fps:g}", f"framesPerSecond = {fps:g}\n    timeCodesPerSecond = {fps:g}", 1)
    return result


def main() -> None:
    if len(sys.argv) not in (3, 4):
        raise SystemExit(__doc__)
    fps = float(sys.argv[3]) if len(sys.argv) == 4 else 60.0
    with open(sys.argv[1], encoding="utf-8") as f:
        text = f.read()
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        f.write(retime(text, fps))


if __name__ == "__main__":
    main()
