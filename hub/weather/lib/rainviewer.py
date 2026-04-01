"""
rainviewer.py — RainViewer API client for the Highland weather daemon.

Fetches the current frame list from the RainViewer public API.
All paths in the response are hashes (not timestamps) as of the March 2026 API change.
"""

import logging
from dataclasses import dataclass
from typing import List, Optional

import requests

log = logging.getLogger(__name__)

API_URL = "https://api.rainviewer.com/public/weather-maps.json"
TILE_HOST = "https://tilecache.rainviewer.com"
TILE_SIZE = 512
MAX_ZOOM = 7
REQUEST_TIMEOUT = 15

# Identify ourselves to upstream APIs
USER_AGENT = "(Highland-SmartHome, home@ferris.network)"


@dataclass
class RadarFrame:
    path: str        # /v2/radar/{hash}
    time: int        # Unix timestamp
    hash: str        # extracted hash — used as cache key

    @property
    def tile_url(self) -> str:
        return f"{TILE_HOST}{self.path}"


def fetch_frame_list(frame_count: int = 10) -> List[RadarFrame]:
    """
    Fetch the current RainViewer frame list and return the last frame_count frames.
    Frames are returned oldest-first.

    Raises requests.RequestException on network failure.
    Raises ValueError if the API response is malformed.
    """
    headers = {"User-Agent": USER_AGENT}
    response = requests.get(API_URL, timeout=REQUEST_TIMEOUT, headers=headers)
    response.raise_for_status()

    data = response.json()
    past = data.get("radar", {}).get("past", [])

    if not past:
        raise ValueError("RainViewer API returned no radar frames")

    # Take last frame_count frames, oldest first (natural API order)
    selected = past[-frame_count:]

    frames = []
    for entry in selected:
        path = entry["path"]          # e.g. /v2/radar/abc123def456
        hash_val = path.split("/")[-1]
        frames.append(RadarFrame(
            path=path,
            time=entry["time"],
            hash=hash_val,
        ))

    log.debug(f"Fetched {len(frames)} frames from RainViewer (of {len(past)} available)")
    return frames


def tile_url(frame: RadarFrame, zoom: int, x: int, y: int, color: int = 2) -> str:
    """Build the tile URL for a given frame, zoom level, and tile coordinates."""
    return (
        f"{TILE_HOST}{frame.path}/{TILE_SIZE}/{zoom}/{x}/{y}/{color}/1_1.png"
    )
