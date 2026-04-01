"""
config.py — Configuration loading and validation for the Highland weather daemon.

Reads the daemon's working config from disk (/var/lib/highland/weather/config/radar.json).
This file is written by the config listener when Node-RED pushes a new config via MQTT.

The config structure mirrors the relevant sections of weather.json from Node-RED,
translated into a flat structure the daemon can consume directly.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import List, Optional

log = logging.getLogger(__name__)

CONFIG_PATH = "/var/lib/highland/weather/config/weather.json"


@dataclass
class LayerConfig:
    type: str
    opacity: float = 1.0


@dataclass
class ProductConfig:
    id: str
    enabled: bool
    cadence_minutes: int
    frame_count: int
    frame_delay_ms: int
    loop_delay_ms: int
    interpolated_frames: int
    output_filename: str
    color_scheme: int = 2
    layers: List[LayerConfig] = field(default_factory=list)

    @property
    def radar_opacity(self) -> float:
        for layer in self.layers:
            if layer.type == "radar":
                return layer.opacity
        return 0.75


@dataclass
class LocationConfig:
    latitude: float
    longitude: float
    timezone: str = "America/New_York"


@dataclass
class StadiaMapsConfig:
    api_key: str
    style: str = "alidade_smooth_dark"
    zoom: int = 8
    grid_size: int = 5
    crop_size: int = 1280


@dataclass
class HaosConfig:
    host: str
    port: int
    username: str
    ssh_key_path: str
    www_path: str = "/config/www/hub.local"


@dataclass
class MqttConfig:
    host: str
    port: int
    username: str
    password: str
    client_id: str = "highland-weather-daemon"


@dataclass
class RadarConfig:
    location: LocationConfig
    stadia: StadiaMapsConfig
    haos: HaosConfig
    mqtt: MqttConfig
    products: List[ProductConfig] = field(default_factory=list)
    base_map_refresh_days: int = 7

    def get_product(self, product_id: str) -> Optional[ProductConfig]:
        for p in self.products:
            if p.id == product_id:
                return p
        return None

    @property
    def enabled_products(self) -> List[ProductConfig]:
        return [p for p in self.products if p.enabled]


def load_config(path: str = CONFIG_PATH) -> RadarConfig:
    """Load and parse the daemon config from disk. Raises on missing or invalid file."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path, "r") as f:
        raw = json.load(f)

    try:
        location = LocationConfig(
            latitude=float(raw["location"]["latitude"]),
            longitude=float(raw["location"]["longitude"]),
            timezone=raw["location"].get("timezone", "America/New_York"),
        )

        stadia = StadiaMapsConfig(
            api_key=raw["stadia"]["api_key"],
            style=raw["stadia"].get("style", "alidade_smooth_dark"),
            zoom=raw["stadia"].get("zoom", 8),
            grid_size=raw["stadia"].get("grid_size", 5),
            crop_size=raw["stadia"].get("crop_size", 1280),
        )

        haos = HaosConfig(
            host=raw["haos"]["host"],
            port=raw["haos"].get("port", 22),
            username=raw["haos"]["username"],
            ssh_key_path=raw["haos"]["ssh_key_path"],
            www_path=raw["haos"].get("www_path", "/config/www/hub.local"),
        )

        mqtt = MqttConfig(
            host=raw["mqtt"]["host"],
            port=raw["mqtt"].get("port", 1883),
            username=raw["mqtt"]["username"],
            password=raw["mqtt"]["password"],
            client_id=raw["mqtt"].get("client_id", "highland-weather-daemon"),
        )

        # radar section is nested under "radar" key
        radar_raw = raw.get("radar", {})

        products = []
        for p in radar_raw.get("products", []):
            layers = [
                LayerConfig(type=l["type"], opacity=l.get("opacity", 1.0))
                for l in p.get("layers", [])
            ]
            products.append(ProductConfig(
                id=p["id"],
                enabled=p.get("enabled", True),
                cadence_minutes=p.get("cadence_minutes", 5),
                frame_count=p.get("frame_count", 10),
                frame_delay_ms=p.get("frame_delay_ms", 500),
                loop_delay_ms=p.get("loop_delay_ms", 2000),
                interpolated_frames=p.get("interpolated_frames", 1),
                output_filename=p.get("output_filename", f"{p['id']}.gif"),
                color_scheme=p.get("color_scheme", 2),
                layers=layers,
            ))

        return RadarConfig(
            location=location,
            stadia=stadia,
            haos=haos,
            mqtt=mqtt,
            products=products,
            base_map_refresh_days=radar_raw.get("base_map_refresh_days", 7),
        )

    except KeyError as e:
        raise ValueError(f"Missing required config field: {e}") from e
