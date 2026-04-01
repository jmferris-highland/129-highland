"""
tiles.py — Tile math for base map and radar tile grid computation.

Translates the JavaScript tile math from Node-RED's Compute Tile Grid function node
into Python. All geographic projections use Web Mercator (EPSG:3857 / slippy map).

This module is pure math — no I/O, no side effects.
"""

import math
from dataclasses import dataclass
from typing import List, Tuple


@dataclass
class TileCoord:
    x: int
    y: int
    z: int


@dataclass
class CropParams:
    crop_x: int
    crop_y: int
    crop_size: int
    canvas_x: int
    canvas_y: int


@dataclass
class RadarTileGrid:
    tiles: List[TileCoord]
    origin_x: int
    origin_y: int
    crop_x: int
    crop_y: int
    crop_w: int
    crop_h: int
    grid_w: int
    grid_h: int


@dataclass
class TileGrid:
    """Complete tile grid computation result for a given location."""
    # Base map (zoom 8)
    center_tile: TileCoord
    tile_grid: List[TileCoord]
    crop_params: CropParams
    # Radar overlay (zoom 7)
    radar: RadarTileGrid
    # Geographic bounds of the cropped output
    lon_nw: float
    lat_nw: float
    lon_se: float
    lat_se: float


def _lon_to_tile_x(lon: float, zoom: int) -> float:
    """Convert longitude to fractional tile X at given zoom."""
    return (lon + 180.0) / 360.0 * (2 ** zoom)


def _lat_to_tile_y(lat: float, zoom: int) -> float:
    """Convert latitude to fractional tile Y at given zoom (Web Mercator)."""
    lat_rad = math.radians(lat)
    return (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * (2 ** zoom)


def _tile_x_to_lon(tx: float, zoom: int) -> float:
    """Convert fractional tile X to longitude."""
    return tx / (2 ** zoom) * 360.0 - 180.0


def _tile_y_to_lat(ty: float, zoom: int) -> float:
    """Convert fractional tile Y to latitude (Web Mercator)."""
    n = math.pi - 2.0 * math.pi * ty / (2 ** zoom)
    return math.degrees(math.atan(math.sinh(n)))


def compute_tile_grid(lat: float, lon: float) -> TileGrid:
    """
    Compute the full tile grid for a given location.

    Base map: zoom 8, 5x5 Stadia Maps tile grid, 512px tiles (@2x), 1280px crop.
    Radar overlay: zoom 7 (RainViewer max), grid and crop projected from base map bounds.
    """
    ZOOM_BASE = 8
    ZOOM_RADAR = 7
    TILE_SIZE = 512      # @2x tiles
    HALF_GRID = 2        # 5x5 grid = center ± 2
    CROP_SIZE = 1280

    num_tiles_base = 2 ** ZOOM_BASE

    # --- Base map center tile ---
    x_center = int(_lon_to_tile_x(lon, ZOOM_BASE))
    y_center = int(_lat_to_tile_y(lat, ZOOM_BASE))

    # --- Pixel position of exact coordinates within the stitched canvas ---
    x_frac = _lon_to_tile_x(lon, ZOOM_BASE) - x_center

    # Mercator Y fraction within center tile
    merc_nw = math.pi * (1 - 2 * y_center / num_tiles_base)
    merc_se = math.pi * (1 - 2 * (y_center + 1) / num_tiles_base)
    lat_rad = math.radians(lat)
    merc_coord = math.log(math.tan(math.pi / 4 + lat_rad / 2))
    y_frac = (merc_nw - merc_coord) / (merc_nw - merc_se)

    canvas_x = round(HALF_GRID * TILE_SIZE + x_frac * TILE_SIZE)
    canvas_y = round(HALF_GRID * TILE_SIZE + y_frac * TILE_SIZE)

    # Crop centered on location
    crop_x = max(0, canvas_x - CROP_SIZE // 2)
    crop_y = max(0, canvas_y - CROP_SIZE // 2)

    crop_params = CropParams(
        crop_x=crop_x,
        crop_y=crop_y,
        crop_size=CROP_SIZE,
        canvas_x=canvas_x,
        canvas_y=canvas_y,
    )

    # --- Base map tile grid (5x5) ---
    x_min = x_center - HALF_GRID
    y_min = y_center - HALF_GRID
    tile_grid = [
        TileCoord(x=x, y=y, z=ZOOM_BASE)
        for y in range(y_min, y_min + 5)
        for x in range(x_min, x_min + 5)
    ]

    center_tile = TileCoord(x=x_center, y=y_center, z=ZOOM_BASE)

    # --- Geographic bounds of cropped output ---
    # NW corner
    tx_nw = x_min + crop_x / TILE_SIZE
    ty_nw = y_min + crop_y / TILE_SIZE
    lon_nw = _tile_x_to_lon(tx_nw, ZOOM_BASE)
    lat_nw = _tile_y_to_lat(ty_nw, ZOOM_BASE)

    # SE corner
    tx_se = x_min + (crop_x + CROP_SIZE) / TILE_SIZE
    ty_se = y_min + (crop_y + CROP_SIZE) / TILE_SIZE
    lon_se = _tile_x_to_lon(tx_se, ZOOM_BASE)
    lat_se = _tile_y_to_lat(ty_se, ZOOM_BASE)

    # --- Radar tile grid (zoom 7) ---
    xf_nw = _lon_to_tile_x(lon_nw, ZOOM_RADAR)
    yf_nw = _lat_to_tile_y(lat_nw, ZOOM_RADAR)
    xf_se = _lon_to_tile_x(lon_se, ZOOM_RADAR)
    yf_se = _lat_to_tile_y(lat_se, ZOOM_RADAR)

    rx_min = int(xf_nw)
    ry_min = int(yf_nw)
    rx_max = int(xf_se)
    ry_max = int(yf_se)

    r_crop_x = round((xf_nw - rx_min) * TILE_SIZE)
    r_crop_y = round((yf_nw - ry_min) * TILE_SIZE)
    r_crop_w = round((xf_se - xf_nw) * TILE_SIZE)
    r_crop_h = round((yf_se - yf_nw) * TILE_SIZE)

    grid_w = rx_max - rx_min + 1
    grid_h = ry_max - ry_min + 1

    radar_tiles = [
        TileCoord(x=rx, y=ry, z=ZOOM_RADAR)
        for ry in range(ry_min, ry_max + 1)
        for rx in range(rx_min, rx_max + 1)
    ]

    radar = RadarTileGrid(
        tiles=radar_tiles,
        origin_x=rx_min,
        origin_y=ry_min,
        crop_x=r_crop_x,
        crop_y=r_crop_y,
        crop_w=r_crop_w,
        crop_h=r_crop_h,
        grid_w=grid_w,
        grid_h=grid_h,
    )

    return TileGrid(
        center_tile=center_tile,
        tile_grid=tile_grid,
        crop_params=crop_params,
        radar=radar,
        lon_nw=lon_nw,
        lat_nw=lat_nw,
        lon_se=lon_se,
        lat_se=lat_se,
    )


def home_pixel(crop_params: CropParams) -> Tuple[int, int]:
    """
    Return the pixel coordinates of the home location within the cropped output image.
    Used for crosshair placement in the static overlay.
    """
    return (
        crop_params.canvas_x - crop_params.crop_x,
        crop_params.canvas_y - crop_params.crop_y,
    )
