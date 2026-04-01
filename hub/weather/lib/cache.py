"""
cache.py — Frame cache management for the Highland weather daemon.

Manages the on-disk cache of composited radar frames and interpolated frames.
All cache paths are per-product — different products have isolated cache directories
so different opacity/color settings never collide, and concurrent runs are safe.

Cache structure:
  /var/lib/highland/weather/assets/cache/{product_id}/{hash}.png
  /var/lib/highland/weather/assets/cache/{product_id}/interp_{hash_a}_{hash_b}_{n}.png
"""

import logging
import os
from typing import List, Optional, Set

log = logging.getLogger(__name__)

CACHE_BASE_DIR = "/var/lib/highland/weather/assets/cache"


def product_cache_dir(product_id: str) -> str:
    """Return the cache directory for a specific product."""
    return os.path.join(CACHE_BASE_DIR, product_id)


def ensure_cache_dir(product_id: str) -> None:
    """Create the product cache directory if it doesn't exist."""
    os.makedirs(product_cache_dir(product_id), exist_ok=True)


def cache_path(hash_val: str, product_id: str) -> str:
    """Return the cache path for a real composited frame."""
    return os.path.join(product_cache_dir(product_id), f"{hash_val}.png")


def interp_cache_path(hash_a: str, hash_b: str, n: int, product_id: str) -> str:
    """Return the cache path for an interpolated frame."""
    return os.path.join(product_cache_dir(product_id), f"interp_{hash_a}_{hash_b}_{n}.png")


def is_cached(hash_val: str, product_id: str) -> bool:
    """Check if a real frame is in cache."""
    return os.path.exists(cache_path(hash_val, product_id))


def are_interp_cached(hash_a: str, hash_b: str, n_frames: int, product_id: str) -> bool:
    """Check if all interpolated frames for a pair are in cache."""
    return all(
        os.path.exists(interp_cache_path(hash_a, hash_b, i + 1, product_id))
        for i in range(n_frames)
    )


def get_interp_paths(hash_a: str, hash_b: str, n_frames: int, product_id: str) -> List[str]:
    """Return the list of interp cache paths for a pair, in order."""
    return [interp_cache_path(hash_a, hash_b, i + 1, product_id) for i in range(n_frames)]


def evict_stale(active_hashes: Set[str], product_id: str) -> int:
    """
    Remove cached real frames whose hash is no longer in the active frame list.
    Skips interp_ files — they are managed separately.
    Returns count of evicted files.
    """
    cache_dir = product_cache_dir(product_id)
    evicted = 0
    try:
        for filename in os.listdir(cache_dir):
            if filename.startswith("interp_"):
                continue
            if not filename.endswith(".png"):
                continue
            hash_val = filename[:-4]  # strip .png
            if hash_val not in active_hashes:
                try:
                    os.remove(os.path.join(cache_dir, filename))
                    log.debug(f"Evicted stale frame: {filename}")
                    evicted += 1
                except OSError as e:
                    log.warning(f"Failed to evict {filename}: {e}")
    except OSError as e:
        log.warning(f"Cache eviction failed — cannot list cache dir: {e}")
    return evicted


def evict_stale_interp(active_hashes: Set[str], product_id: str) -> int:
    """
    Remove interp frames whose parent hashes are no longer active.
    Filename format: interp_{hash_a}_{hash_b}_{n}.png
    Returns count of evicted files.
    """
    cache_dir = product_cache_dir(product_id)
    evicted = 0
    try:
        for filename in os.listdir(cache_dir):
            if not filename.startswith("interp_"):
                continue
            # Parse: interp_{hash_a}_{hash_b}_{n}.png
            parts = filename[len("interp_"):-len(".png")].rsplit("_", 1)
            if len(parts) != 2:
                continue
            pair_part, _ = parts
            pair_parts = pair_part.split("_")
            if len(pair_parts) != 2:
                continue
            hash_a, hash_b = pair_parts
            if hash_a not in active_hashes or hash_b not in active_hashes:
                try:
                    os.remove(os.path.join(cache_dir, filename))
                    log.debug(f"Evicted stale interp: {filename}")
                    evicted += 1
                except OSError as e:
                    log.warning(f"Failed to evict interp {filename}: {e}")
    except OSError as e:
        log.warning(f"Interp cache eviction failed: {e}")
    return evicted
