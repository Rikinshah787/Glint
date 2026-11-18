#!/usr/bin/env python3
"""Assemble the walkthrough frames written by screenshots.mjs into docs/images/demo.gif (+ demo.webp).

    .venv/bin/python scripts/make_gif.py FRAMES_DIR docs/images [--width 960]

Needs Pillow (scripts/screenshots.sh installs it into .venv if missing).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    frames_dir, out_dir = Path(argv[0]), Path(argv[1])
    width = int(argv[argv.index("--width") + 1]) if "--width" in argv else 960
    spec = json.loads((frames_dir / "frames.json").read_text())
    frames, durations = [], []
    for f in spec:
        im = Image.open(f["file"]).convert("RGB")
        im = im.resize((width, round(im.height * width / im.width)), Image.Resampling.LANCZOS)
        if frames and im.tobytes() == frames[-1].tobytes():
            durations[-1] += f["ms"]  # merge identical consecutive frames
            continue
        frames.append(im)
        durations.append(int(f["ms"]))
    if not frames:
        print("no frames")
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)

    # A palette per frame keeps small accents (traffic lights, chart colours) accurate.
    gif_frames = [f.quantize(colors=255, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE) for f in frames]
    gif = out_dir / "demo.gif"
    gif_frames[0].save(gif, save_all=True, append_images=gif_frames[1:], duration=durations, loop=0,
                       optimize=True, disposal=1)
    webp = out_dir / "demo.webp"
    frames[0].save(webp, save_all=True, append_images=frames[1:], duration=durations, loop=0, quality=82, method=5)
    for p in (gif, webp):
        print(f"  {p} ({p.stat().st_size / 1e6:.1f} MB, {len(frames)} frames, {sum(durations) / 1000:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
