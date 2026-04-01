"""
imaging.py — ImageMagick subprocess wrappers for the Highland weather daemon.

All ImageMagick operations go through this module. Each function constructs
the appropriate magick command, executes it as a subprocess, and raises
ImagingError on failure with the stderr output included.

The output path convention throughout is write-to-tmp then atomic rename,
ensuring consumers never see a partial file.

Note: Uses ImageMagick 6 (convert) as IMv7 is not available via apt on Ubuntu 24.
"""

import logging
import os
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

# Use full path to avoid PATH issues when running as hub-daemon service account
# Ubuntu 24 ships ImageMagick 6 via apt — uses 'convert' not 'magick'
MAGICK = "/usr/bin/convert"
FONT = "DejaVu-Sans"
OUTPUT_SIZE = 1280
BAR_HEIGHT = 72  # increased to accommodate larger legend text with balanced spacing
STRIPS_PER_SEGMENT = 12
STRIP_W = 10  # pixels per strip (12 * 10 = 120px per segment)
SEG_W = STRIPS_PER_SEGMENT * STRIP_W  # 120px per 10 dBZ segment
ATTRIBUTION = "© Stadia Maps © OpenMapTiles © OpenStreetMap contributors"

# Universal Blue (color scheme 2) gradient endpoints per 10 dBZ segment
# Format: (r1, g1, b1, r2, g2, b2) for each segment
LEGEND_SEGMENTS = [
    (136, 221, 238,   0, 119, 170),  # 15-25: light blue → medium blue
    (  0, 119, 170,   0,  71, 104),  # 25-35: medium blue → deep blue
    (255, 238,   0, 255, 129,   0),  # 35-45: yellow → orange
    (255, 129,   0, 193,   0,   0),  # 45-55: orange → dark red
    (193,   0,   0, 255, 170, 255),  # 55-65: dark red → pink/magenta
]

# Environment for all subprocess calls — fontconfig needs a writable home
_SUBPROCESS_ENV = {**os.environ, "HOME": "/home/hub-daemon"}


class ImagingError(Exception):
    pass


def _run(args: List[str], description: str = "") -> None:
    """Run a convert command. Raises ImagingError on non-zero exit."""
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
            env=_SUBPROCESS_ENV,
        )
        if result.returncode != 0:
            raise ImagingError(
                f"convert failed{' (' + description + ')' if description else ''}: "
                f"{result.stderr.strip()}"
            )
    except FileNotFoundError:
        raise ImagingError(f"convert not found — is ImageMagick installed?")


def _atomic_write(tmp_path: str, final_path: str) -> None:
    """Rename tmp to final atomically."""
    os.replace(tmp_path, final_path)


def stitch_tiles(
    tile_paths: List[str],
    grid_w: int,
    output_path: str,
) -> None:
    """
    Stitch a grid of tile images into a single canvas.
    Tiles must be in row-major order (left to right, top to bottom).
    """
    tmp = output_path + ".tmp"
    rows = []
    n = len(tile_paths)

    tmp_dir = str(Path(output_path).parent)
    for row_idx in range(n // grid_w):
        row_tiles = tile_paths[row_idx * grid_w : (row_idx + 1) * grid_w]
        row_path = os.path.join(tmp_dir, f"_row_{row_idx}.png")
        _run([MAGICK] + row_tiles + ["+append", f"PNG:{row_path}"], f"stitch row {row_idx}")
        rows.append(row_path)

    _run([MAGICK] + rows + ["-append", f"PNG:{tmp}"], "stitch canvas")
    _atomic_write(tmp, output_path)

    for row_path in rows:
        try:
            os.remove(row_path)
        except OSError:
            pass


def crop_and_resize(
    input_path: str,
    crop_x: int,
    crop_y: int,
    crop_w: int,
    crop_h: int,
    output_size: int,
    output_path: str,
) -> None:
    """Crop a region from an image and resize to output_size x output_size."""
    tmp = output_path + ".tmp"
    _run([
        MAGICK, input_path,
        "-crop", f"{crop_w}x{crop_h}+{crop_x}+{crop_y}",
        "+repage",
        "-resize", f"{output_size}x{output_size}!",
        f"PNG:{tmp}",
    ], "crop and resize")
    _atomic_write(tmp, output_path)


def composite_radar(
    base_map_path: str,
    radar_path: str,
    opacity: float,
    output_path: str,
) -> None:
    """Composite a radar layer over the base map with the given opacity."""
    tmp = output_path + ".tmp"
    # IMv6: use -dissolve for opacity-based compositing
    opacity_pct = int(round(opacity * 100))
    _run([
        MAGICK, base_map_path,
        radar_path,
        "-compose", "Over",
        "-define", f"compose:args={opacity_pct}",
        "-composite",
        f"PNG:{tmp}",
    ], "composite radar")
    _atomic_write(tmp, output_path)


def apply_overlay_and_timestamp(
    frame_path: str,
    overlay_path: str,
    timestamp_str: str,
    output_path: str,
) -> None:
    """
    Composite the static overlay over a frame and stamp the timestamp.
    Timestamp is right-anchored using SouthEast gravity.
    """
    tmp = output_path + ".tmp"
    args = [MAGICK, frame_path]

    if os.path.exists(overlay_path):
        args += [overlay_path, "-compose", "Over", "-composite"]

    args += [
        "-font", FONT,
        "-pointsize", "46",
        "-fill", "white",
        "-gravity", "SouthEast",
        "-annotate", "+10+17", timestamp_str,
        tmp,
    ]
    args[-1] = f"PNG:{tmp}"  # force PNG output format regardless of .tmp extension
    _run(args, "apply overlay and timestamp")
    _atomic_write(tmp, output_path)


def morph_frames(
    frame_a_path: str,
    frame_b_path: str,
    n_frames: int,
    output_paths: List[str],
) -> None:
    """
    Generate n_frames interpolated frames between frame_a and frame_b.
    -morph produces n+2 files: 000=copy_A, 001..N=interp, last=copy_B.
    Only the middle n frames are kept.
    """
    if not output_paths or len(output_paths) != n_frames:
        raise ValueError(f"output_paths must have exactly {n_frames} entries")

    tmp_dir = str(Path(output_paths[0]).parent)
    morph_pattern = os.path.join(tmp_dir, "_morph_%03d.png")

    _run([
        MAGICK, frame_a_path, frame_b_path,
        "-morph", str(n_frames),
        morph_pattern,
    ], "morph frames")

    for i, out_path in enumerate(output_paths):
        src = morph_pattern % (i + 1)
        os.replace(src, out_path)

    try:
        os.remove(morph_pattern % 0)
        os.remove(morph_pattern % (n_frames + 1))
    except OSError:
        pass


def assemble_gif(
    frame_paths: List[str],
    frame_delays_cs: List[int],
    output_path: str,
) -> None:
    """
    Assemble an animated GIF from a list of frames with per-frame delays.
    Writes command to a temp script to avoid argument expansion issues.
    Delays are in centiseconds.
    """
    if len(frame_paths) != len(frame_delays_cs):
        raise ValueError("frame_paths and frame_delays_cs must be the same length")

    tmp_output = f"GIF:{output_path}.tmp"
    tmp_dir = str(Path(output_path).parent)
    script_path = os.path.join(tmp_dir, "_assemble_gif.sh")

    parts = ["/usr/bin/convert"]
    for path, delay in zip(frame_paths, frame_delays_cs):
        parts.append(f"-delay {delay}")
        parts.append(path)
    parts.append("-loop 0")
    parts.append(f'"{tmp_output}"')

    with open(script_path, "w") as f:
        f.write("#!/bin/sh\n")
        f.write(" ".join(parts) + "\n")
    os.chmod(script_path, 0o755)

    try:
        result = subprocess.run(
            ["/bin/sh", script_path],
            capture_output=True,
            text=True,
            check=False,
            env=_SUBPROCESS_ENV,
        )
        if result.returncode != 0:
            raise ImagingError(f"GIF assembly failed: {result.stderr.strip()}")
    finally:
        try:
            os.remove(script_path)
        except OSError:
            pass

    _atomic_write(f"{output_path}.tmp", output_path)


def build_static_overlay(
    home_x: int,
    home_y: int,
    output_path: str,
    output_size: int = OUTPUT_SIZE,
    bar_height: int = BAR_HEIGHT,
) -> None:
    """
    Generate the static overlay PNG: bottom bar, legend gradient, crosshair.
    Uses a temp draw script to avoid shell expansion with hex color strings.
    No -colorspace flag — IMv6 handles explicit hex colors correctly without it.
    """
    tmp_output = output_path + ".tmp"
    tmp_dir = str(Path(output_path).parent)
    script_path = os.path.join(tmp_dir, "_draw_overlay.sh")
    bar_y = output_size - bar_height

    # Layout: 10px top gap, 20px gradient, 10px gap, ~18px text, ~14px bottom gap
    # dBZ label left of gradient with room to breathe — grad_x shifted right
    grad_x = 65           # start of gradient (leaves room for dBZ label)
    grad_y = bar_y + 10   # 10px from top of bar
    seg_h = 20            # gradient strip height
    label_y = grad_y + seg_h + 10 + 16  # 10px gap + ~16px ascent = text baseline
    separator_x = grad_x + SEG_W * 2

    # Attribution: right-aligned, clear of bar top
    # NorthEast gravity: y is from image top, so attrib_y = bar_y - text_height - gap
    attrib_y = bar_y - 20  # positions text cleanly above bar with breathing room
    attrib_x = output_size - 10  # right-aligned with 10px margin

    # Build gradient strip draw commands
    draw_cmds = []
    for seg_idx, (r1, g1, b1, r2, g2, b2) in enumerate(LEGEND_SEGMENTS):
        for strip in range(STRIPS_PER_SEGMENT):
            r = r1 + (r2 - r1) * strip // STRIPS_PER_SEGMENT
            g = g1 + (g2 - g1) * strip // STRIPS_PER_SEGMENT
            b = b1 + (b2 - b1) * strip // STRIPS_PER_SEGMENT
            hex_color = f"#{r:02x}{g:02x}{b:02x}"
            px = grad_x + seg_idx * SEG_W + strip * STRIP_W
            px2 = px + STRIP_W - 1
            py2 = grad_y + seg_h - 1
            draw_cmds.append(f'-fill "{hex_color}" -draw "rectangle {px},{grad_y} {px2},{py2}"')

    # Separator at 35 dBZ threshold
    draw_cmds.append(
        f'-fill "#ffffff" -draw "rectangle {separator_x},{grad_y - 1} {separator_x + 1},{grad_y + seg_h}"'
    )

    # Crosshair commands
    cx, cy = home_x, home_y
    half, gap = 14, 4
    crosshair_cmds = [
        f'-stroke "rgba(255,255,255,0.80)" -strokewidth 1.5 -fill none',
        f'-draw "line {cx - half},{cy} {cx - gap},{cy}"',
        f'-draw "line {cx + gap},{cy} {cx + half},{cy}"',
        f'-draw "line {cx},{cy - half} {cx},{cy - gap}"',
        f'-draw "line {cx},{cy + gap} {cx},{cy + half}"',
    ]

    with open(script_path, "w") as f:
        f.write("#!/bin/sh\n")
        # No -colorspace flag — IMv6 doesn't support it before xc:none
        f.write(f'/usr/bin/convert -size {output_size}x{output_size} xc:none \\\n')
        f.write(f'  -fill "rgba(0,0,0,0.75)" -draw "rectangle 0,{bar_y} {output_size},{output_size}" \\\n')
        for cmd in draw_cmds:
            f.write(f'  {cmd} \\\n')
        # dBZ label — vertically centered on gradient strip
        dbz_y = grad_y + seg_h // 2 + 9  # baseline centered on gradient
        f.write(f'  -font "{FONT}" -pointsize 18 \\\n')
        f.write(f'  -fill "rgba(255,255,255,0.80)" -annotate "+8+{dbz_y}" "dBZ" \\\n')
        # Numeric legend labels below gradient
        f.write(f'  -fill "white" \\\n')
        for i, label in enumerate(["15", "25", "35", "45", "55", "65+"]):
            x = grad_x + i * SEG_W - (14 if label == "65+" else 2)
            f.write(f'  -annotate "+{x}+{label_y}" "{label}" \\\n')
        # Attribution — right-aligned, subtle gray, clear above bar
        f.write(f'  -font "{FONT}" -pointsize 15 \\\n')
        f.write(f'  -fill "rgba(170,170,170,0.75)" -gravity NorthEast \\\n')
        f.write(f'  -annotate "+10+{attrib_y}" "{ATTRIBUTION}" \\\n')
        f.write(f'  -gravity None \\\n')
        for cmd in crosshair_cmds:
            f.write(f'  {cmd} \\\n')
        f.write(f'  "PNG:{tmp_output}"\n')

    os.chmod(script_path, 0o755)

    try:
        result = subprocess.run(
            ["/bin/sh", script_path],
            capture_output=True,
            text=True,
            check=False,
            env=_SUBPROCESS_ENV,
        )
        if result.returncode != 0:
            raise ImagingError(f"Static overlay build failed: {result.stderr.strip()}")
    finally:
        try:
            os.remove(script_path)
        except OSError:
            pass

    _atomic_write(tmp_output, output_path)
