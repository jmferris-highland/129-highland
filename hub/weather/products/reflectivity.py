#!/opt/highland/venv/bin/python
"""
reflectivity.py — Base reflectivity radar product for the Highland weather daemon.

Fetches RainViewer radar frames, composites them over the base map, applies
overlays and interpolation, assembles an animated GIF, and delivers it to HAOS.

Publishes MQTT events on completion or failure.
Can be run standalone or invoked by the daemon scheduler.

Usage:
    python reflectivity.py [--config /path/to/radar.json]
"""

import argparse
import datetime
import logging
import os
import sys
import time

# Ensure lib is importable when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.logging_config import configure_logging
from lib.config import load_config, ProductConfig, RadarConfig
from lib.mqtt import MqttPublisher
from lib.tiles import compute_tile_grid, home_pixel
from lib.rainviewer import fetch_frame_list, tile_url, RadarFrame
from lib.cache import (
    cache_path, interp_cache_path, is_cached, are_interp_cached,
    get_interp_paths, evict_stale, evict_stale_interp, ensure_cache_dir,
)
from lib.imaging import (
    stitch_tiles, crop_and_resize, composite_radar,
    apply_overlay_and_timestamp, morph_frames, assemble_gif,
    build_static_overlay, ImagingError,
)
from lib.sftp import SftpDelivery

import requests

log = logging.getLogger(__name__)

PRODUCT_ID = "reflectivity"
LOCKS_DIR = "/var/lib/highland/weather/locks"
ASSETS_DIR = "/var/lib/highland/weather/assets"
LOOPS_DIR = f"{ASSETS_DIR}/loops"
TMP_DIR = f"{ASSETS_DIR}/tmp/{PRODUCT_ID}"           # per-product tmp workspace
BASE_MAP_PATH = f"{ASSETS_DIR}/base_map.png"         # shared — product-agnostic
STATIC_OVERLAY_PATH = f"{ASSETS_DIR}/overlays/{PRODUCT_ID}.png"  # per-product overlay
TILE_HOST = "https://tilecache.rainviewer.com"
TILE_SIZE = 512


def run(config: RadarConfig) -> None:
    """
    Main entry point for the reflectivity product.
    Raises on unrecoverable error.
    """
    product = config.get_product(PRODUCT_ID)
    if product is None:
        raise ValueError(f"Product '{PRODUCT_ID}' not found in config")
    if not product.enabled:
        log.info(f"Product '{PRODUCT_ID}' is disabled — skipping")
        return

    mqtt = MqttPublisher(
        host=config.mqtt.host,
        port=config.mqtt.port,
        username=config.mqtt.username,
        password=config.mqtt.password,
    )

    mqtt.publish_status(PRODUCT_ID, "running")
    log.info(f"Starting reflectivity product run")

    try:
        # Ensure lockfile exists (daemon writes it, we just confirm and clean up on exit)
        lock_file = os.path.join(LOCKS_DIR, f"{PRODUCT_ID}.lock")

        # Ensure directories exist
        for d in [LOOPS_DIR, TMP_DIR,
                  os.path.join(ASSETS_DIR, "overlays")]:
            os.makedirs(d, exist_ok=True)
        ensure_cache_dir(PRODUCT_ID)

        # Compute tile grid
        grid = compute_tile_grid(
            lat=config.location.latitude,
            lon=config.location.longitude,
        )
        home_x, home_y = home_pixel(grid.crop_params)

        # Rebuild base map if missing or stale
        _ensure_base_map(config, grid)

        # Rebuild static overlay if missing
        _ensure_static_overlay(home_x, home_y)

        # Fetch frame list from RainViewer
        log.info("Fetching RainViewer frame list...")
        frames = fetch_frame_list(frame_count=product.frame_count)
        log.info(f"Fetched {len(frames)} frames")

        # Short-circuit if nothing has changed since last run
        last_hashes_path = os.path.join(ASSETS_DIR, f"{PRODUCT_ID}_last_hashes.txt")
        current_hashes = " ".join(f.hash for f in frames)
        if os.path.exists(last_hashes_path):
            with open(last_hashes_path) as fh:
                if fh.read().strip() == current_hashes:
                    log.info("No new frames since last run — skipping render")
                    mqtt.publish_status(PRODUCT_ID, "idle")
                    return
        with open(last_hashes_path, "w") as fh:
            fh.write(current_hashes)

        # Evict stale cache entries
        active_hashes = {f.hash for f in frames}
        evicted = evict_stale(active_hashes, PRODUCT_ID)
        if evicted:
            log.info(f"Evicted {evicted} stale real frames from cache")

        # Fetch and composite any uncached frames
        for frame in frames:
            if not is_cached(frame.hash, PRODUCT_ID):
                _fetch_and_composite_frame(frame, config, product, grid)
            else:
                log.debug(f"Frame {frame.hash}: cache hit")

        # Apply overlays and timestamps to real frames
        timezone = config.location.timezone if hasattr(config.location, 'timezone') else "America/New_York"

        log.info("Applying overlays...")
        stamped_paths = {}
        for frame in frames:
            stamped_path = os.path.join(TMP_DIR, f"stamped_{frame.hash}.png")
            ts = _format_timestamp(frame.time, timezone)
            apply_overlay_and_timestamp(
                frame_path=cache_path(frame.hash, PRODUCT_ID),
                overlay_path=STATIC_OVERLAY_PATH,
                timestamp_str=ts,
                output_path=stamped_path,
            )
            stamped_paths[frame.hash] = stamped_path

        # Interpolation
        if product.interpolated_frames > 0:
            log.info("Generating interpolated frames...")
            evict_stale_interp(active_hashes, PRODUCT_ID)
            _ensure_interpolated_frames(frames, product, stamped_paths)

        # Assemble GIF
        output_filename = product.output_filename
        output_path = os.path.join(LOOPS_DIR, output_filename)
        log.info("Assembling GIF...")
        _assemble_gif(frames, product, stamped_paths, output_path)

        log.info(f"GIF written to {output_path}")

        # Deliver to HAOS
        _deliver_to_haos(config, output_path, output_filename)

        # Publish success
        mqtt.publish_rendered(PRODUCT_ID, output_path)
        mqtt.publish_status(PRODUCT_ID, "idle")
        mqtt.publish_log("info", f"Reflectivity loop rendered: {output_filename}", product=PRODUCT_ID)
        log.info(f"Reflectivity product run complete")

    except Exception as e:
        log.error(f"Reflectivity product failed: {e}", exc_info=True)
        mqtt.publish_error(PRODUCT_ID, str(e))
        mqtt.publish_status(PRODUCT_ID, "error")
        mqtt.publish_log("error", f"Reflectivity product failed: {e}", product=PRODUCT_ID)
        raise

    finally:
        # Always remove lockfile on exit — whether success, failure, or exception
        try:
            os.remove(lock_file)
            log.debug(f"Lockfile removed: {lock_file}")
        except OSError:
            pass  # Already removed or never created — not a problem


# --- Internal helpers ---

def _ensure_base_map(config: RadarConfig, grid) -> None:
    """Rebuild the base map if it doesn't exist or is older than the configured refresh period."""
    refresh_seconds = config.base_map_refresh_days * 86400

    if os.path.exists(BASE_MAP_PATH):
        age = time.time() - os.path.getmtime(BASE_MAP_PATH)
        if age < refresh_seconds:
            log.debug("Base map is current — skipping rebuild")
            return
        log.info(f"Base map is {age / 3600:.1f}h old — rebuilding")
    else:
        log.info("Base map not found — building")

    _build_base_map(config, grid)


def _build_base_map(config: RadarConfig, grid) -> None:
    """Fetch Stadia Maps tiles and build the base map PNG."""
    stitch_dir = TMP_DIR
    ct = grid.center_tile
    cp = grid.crop_params

    x_min = ct.x - 2
    y_min = ct.y - 2

    log.info(f"Fetching 5x5 Stadia Maps tile grid (zoom {ct.z}, center {ct.x},{ct.y})")

    tile_paths = []
    for y in range(y_min, y_min + 5):
        for x in range(x_min, x_min + 5):
            url = (
                f"https://tiles.stadiamaps.com/tiles/{config.stadia.style}"
                f"/{ct.z}/{x}/{y}@2x.png?api_key={config.stadia.api_key}"
            )
            tile_path = os.path.join(stitch_dir, f"stile_{x}_{y}.png")
            _download_tile(url, tile_path)
            tile_paths.append(tile_path)

    # Stitch into canvas
    stitched_path = os.path.join(stitch_dir, "base_stitched.png")
    stitch_tiles(tile_paths, grid_w=5, output_path=stitched_path)

    # Crop to output size
    crop_and_resize(
        input_path=stitched_path,
        crop_x=cp.crop_x,
        crop_y=cp.crop_y,
        crop_w=cp.crop_size,
        crop_h=cp.crop_size,
        output_size=cp.crop_size,
        output_path=BASE_MAP_PATH,
    )

    # Clean up tile files
    for p in tile_paths:
        try:
            os.remove(p)
        except OSError:
            pass
    try:
        os.remove(stitched_path)
    except OSError:
        pass

    log.info("Base map built successfully")


def _ensure_static_overlay(home_x: int, home_y: int) -> None:
    """Build the static overlay if it doesn't exist."""
    if os.path.exists(STATIC_OVERLAY_PATH):
        log.debug("Static overlay exists — skipping rebuild")
        return
    log.info("Building static overlay...")
    build_static_overlay(
        home_x=home_x,
        home_y=home_y,
        output_path=STATIC_OVERLAY_PATH,
    )
    log.info("Static overlay built")


def _fetch_and_composite_frame(
    frame: RadarFrame,
    config: RadarConfig,
    product: ProductConfig,
    grid,
) -> None:
    """Fetch radar tiles for a frame and composite over base map into cache."""
    log.info(f"Fetching frame {frame.hash} (t={frame.time})")
    rc = grid.radar
    tile_paths = []

    for ry in range(rc.origin_y, rc.origin_y + rc.grid_h):
        for rx in range(rc.origin_x, rc.origin_x + rc.grid_w):
            url = (
                f"{TILE_HOST}{frame.path}/{TILE_SIZE}/7"
                f"/{rx}/{ry}/{product.color_scheme}/1_1.png"
            )
            tile_path = os.path.join(TMP_DIR, f"rtile_{rx}_{ry}.png")
            _download_tile(url, tile_path)
            tile_paths.append(tile_path)

    # Stitch radar tiles
    stitched_path = os.path.join(TMP_DIR, "radar_stitched.png")
    stitch_tiles(tile_paths, grid_w=rc.grid_w, output_path=stitched_path)

    # Crop to base map bounds
    cropped_path = os.path.join(TMP_DIR, "radar_cropped.png")
    crop_and_resize(
        input_path=stitched_path,
        crop_x=rc.crop_x,
        crop_y=rc.crop_y,
        crop_w=rc.crop_w,
        crop_h=rc.crop_h,
        output_size=1280,
        output_path=cropped_path,
    )

    # Composite over base map
    composite_radar(
        base_map_path=BASE_MAP_PATH,
        radar_path=cropped_path,
        opacity=product.radar_opacity,
        output_path=cache_path(frame.hash, PRODUCT_ID),
    )

    # Clean up
    for p in tile_paths:
        try:
            os.remove(p)
        except OSError:
            pass
    for p in [stitched_path, cropped_path]:
        try:
            os.remove(p)
        except OSError:
            pass

    log.debug(f"Frame {frame.hash} cached")


def _ensure_interpolated_frames(
    frames: list,
    product: ProductConfig,
    stamped_paths: dict,
) -> None:
    """Generate interpolated frames between consecutive stamped real frames."""
    for i in range(len(frames) - 1):
        a = frames[i]
        b = frames[i + 1]
        if are_interp_cached(a.hash, b.hash, product.interpolated_frames, PRODUCT_ID):
            log.debug(f"Interp {a.hash}>{b.hash}: cache hit")
            continue
        log.debug(f"Interp {a.hash}>{b.hash}: generating")
        output_paths = get_interp_paths(a.hash, b.hash, product.interpolated_frames, PRODUCT_ID)
        morph_frames(
            frame_a_path=stamped_paths[a.hash],
            frame_b_path=stamped_paths[b.hash],
            n_frames=product.interpolated_frames,
            output_paths=output_paths,
        )


def _assemble_gif(
    frames: list,
    product: ProductConfig,
    stamped_paths: dict,
    output_path: str,
) -> None:
    """Build the ordered frame/delay lists and assemble the animated GIF."""
    frame_delay_cs = product.frame_delay_ms // 10
    loop_delay_cs = product.loop_delay_ms // 10
    interp_delay_cs = 3  # fixed short dissolve transition

    gif_frames = []
    gif_delays = []

    for i, frame in enumerate(frames):
        is_last = i == len(frames) - 1

        # Real frame
        real_path = stamped_paths.get(frame.hash, cache_path(frame.hash, PRODUCT_ID))
        gif_frames.append(real_path)
        gif_delays.append(loop_delay_cs if is_last else frame_delay_cs)

        # Interpolated frames after this real frame (not after last)
        if not is_last and product.interpolated_frames > 0:
            next_frame = frames[i + 1]
            interp_paths = get_interp_paths(frame.hash, next_frame.hash, product.interpolated_frames, PRODUCT_ID)
            for ipath in interp_paths:
                if os.path.exists(ipath):
                    gif_frames.append(ipath)
                    gif_delays.append(interp_delay_cs)

    assemble_gif(
        frame_paths=gif_frames,
        frame_delays_cs=gif_delays,
        output_path=output_path,
    )


def _deliver_to_haos(config: RadarConfig, local_path: str, filename: str) -> None:
    """SFTP the GIF to HAOS /config/www/hub.local/weather/radar/."""
    try:
        sftp = SftpDelivery(
            host=config.haos.host,
            port=config.haos.port,
            username=config.haos.username,
            ssh_key_path=config.haos.ssh_key_path,
            www_path=config.haos.www_path,
        )
        remote_subpath = f"weather/radar/{filename}"
        sftp.deliver(local_path, remote_subpath)
        log.info(f"Delivered to HAOS: /local/hub.local/weather/radar/{filename}")
    except Exception as e:
        log.warning(f"SFTP delivery failed (non-fatal): {e}")


# Re-use the same User-Agent as rainviewer.py for all outbound HTTP requests
from lib.rainviewer import USER_AGENT
_HTTP_HEADERS = {"User-Agent": USER_AGENT}


def _download_tile(url: str, output_path: str) -> None:
    """Download a single tile via HTTP. Raises requests.RequestException on failure."""
    response = requests.get(url, timeout=15, stream=True, headers=_HTTP_HEADERS)
    response.raise_for_status()
    with open(output_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)


def _format_timestamp(unix_time: int, timezone: str = "America/New_York") -> str:
    """Format a Unix timestamp in local time with timezone abbreviation (EDT/EST)."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(timezone)
    dt = datetime.datetime.fromtimestamp(unix_time, tz=tz)
    return dt.strftime("%Y-%m-%d %H:%M %Z")


# --- CLI entry point ---

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Highland weather — reflectivity product")
    parser.add_argument(
        "--config",
        default="/var/lib/highland/weather/config/weather.json",
        help="Path to weather config JSON",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    configure_logging(level=logging.DEBUG if args.debug else logging.INFO)

    try:
        config = load_config(args.config)
        run(config)
        sys.exit(0)
    except Exception as e:
        log.error(f"Fatal error: {e}")
        sys.exit(1)
