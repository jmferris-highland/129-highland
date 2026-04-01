#!/opt/highland/venv/bin/python
"""
config_listener.py — MQTT config subscriber for the Highland weather daemon.

Listens on highland/command/radar/config for configuration pushes from Node-RED.
When a message arrives, validates it and writes it to the daemon's working config
file on disk. The daemon picks up the new config on its next tick.

Also listens on highland/command/radar/{product}/enable for runtime
enable/disable of individual products.

Runs as a persistent systemd service alongside the daemon.

Usage:
    python config_listener.py [--config /path/to/radar.json] [--debug]
"""

import argparse
import json
import logging
import os
import sys
import time

import paho.mqtt.client as mqtt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.logging_config import configure_logging

log = logging.getLogger(__name__)

CONFIG_PATH = "/var/lib/highland/weather/config/weather.json"
TOPIC_CONFIG = "highland/command/weather/config"
TOPIC_ENABLE = "highland/command/weather/radar/+/enable"  # e.g. highland/command/weather/radar/reflectivity/enable

# MQTT broker connection — read from environment or use defaults
MQTT_HOST = os.environ.get("MQTT_HOST", "hub.local")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USERNAME = os.environ.get("MQTT_USERNAME", "svc_scripts")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "")
MQTT_CLIENT_ID = "highland-weather-config-listener"


def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        log.info("Connected to MQTT broker")
        client.subscribe(TOPIC_CONFIG, qos=1)
        client.subscribe(TOPIC_ENABLE, qos=1)
        log.info(f"Subscribed to {TOPIC_CONFIG} and {TOPIC_ENABLE}")
        log.info("Waiting for config push from Node-RED")
    else:
        log.error(f"MQTT connection failed: rc={rc}")


def on_message(client, userdata, message):
    topic = message.topic
    payload_str = message.payload.decode("utf-8", errors="replace")

    log.debug(f"Received message on {topic}")

    if topic == TOPIC_CONFIG:
        _handle_config(payload_str, userdata["config_path"])
    elif "/enable" in topic:
        _handle_enable(topic, payload_str, userdata["config_path"])


def _handle_config(payload_str: str, config_path: str) -> None:
    """Validate and write a full config payload to disk."""
    try:
        data = json.loads(payload_str)
    except json.JSONDecodeError as e:
        log.error(f"Config payload is not valid JSON: {e}")
        return

    # Basic structure validation
    required = ["location", "stadia", "haos", "mqtt", "products"]
    missing = [k for k in required if k not in data]
    if missing:
        log.error(f"Config payload missing required fields: {missing}")
        return

    # Write atomically
    tmp_path = config_path + ".tmp"
    try:
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(tmp_path, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, config_path)
        log.info(f"Config written to {config_path}")
    except OSError as e:
        log.error(f"Failed to write config: {e}")
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def _handle_enable(topic: str, payload_str: str, config_path: str) -> None:
    """
    Handle a runtime enable/disable message for a product.
    Topic format: highland/command/radar/{product}/enable
    Payload: "true" or "false"
    """
    # Extract product ID from topic
    # Format: highland/command/weather/radar/{product}/enable
    parts = topic.split("/")
    if len(parts) < 5:
        log.warning(f"Malformed enable topic: {topic}")
        return
    product_id = parts[4]

    enabled = payload_str.strip().lower() in ("true", "1", "yes")
    log.info(f"Setting {product_id} enabled={enabled}")

    if not os.path.exists(config_path):
        log.warning(f"Config not found — cannot update enable state for {product_id}")
        return

    try:
        with open(config_path) as f:
            data = json.load(f)

        updated = False
        for product in data.get("products", []):
            if product.get("id") == product_id:
                product["enabled"] = enabled
                updated = True
                break

        if not updated:
            log.warning(f"Product '{product_id}' not found in config")
            return

        # Write atomically
        tmp_path = config_path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, config_path)
        log.info(f"Updated {product_id} enabled={enabled} in {config_path}")

    except (OSError, json.JSONDecodeError) as e:
        log.error(f"Failed to update enable state: {e}")


def run_listener(config_path: str) -> None:
    """Main listener loop. Runs until interrupted."""
    log.info("Highland weather config listener starting")

    client = mqtt.Client(
        client_id=MQTT_CLIENT_ID,
        protocol=mqtt.MQTTv5,
    )
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.on_connect = on_connect
    client.on_message = on_message
    client.user_data_set({"config_path": config_path})

    # Retry connection loop
    while True:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            client.loop_forever()
        except ConnectionRefusedError:
            log.warning("MQTT connection refused — retrying in 30s")
            time.sleep(30)
        except OSError as e:
            log.warning(f"MQTT connection error: {e} — retrying in 30s")
            time.sleep(30)
        except KeyboardInterrupt:
            log.info("Config listener stopped by interrupt")
            break


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Highland weather config listener")
    parser.add_argument("--config", default=CONFIG_PATH, help="Path to radar config JSON")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    configure_logging(level=logging.DEBUG if args.debug else logging.INFO)

    run_listener(args.config)
