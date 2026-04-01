#!/opt/highland/venv/bin/python
"""
daemon.py — Highland weather daemon scheduler.

Wakes every minute, checks each enabled product's cadence, and spawns
product scripts in the background if they are due and not already running.
Uses per-product lockfiles to prevent overlapping runs.

Also manages the base map rebuild check on each cycle.

Reads config from disk on every cycle so changes take effect without restart.

Usage:
    python daemon.py [--config /path/to/radar.json] [--debug]
"""

import argparse
import logging
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.logging_config import configure_logging
from lib.config import load_config, RadarConfig, ProductConfig
from lib.mqtt import MqttPublisher

log = logging.getLogger(__name__)

LOCKS_DIR = "/var/lib/highland/weather/locks"
STATE_DIR = "/var/lib/highland/weather/state"
PRODUCTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "products")
PYTHON = "/opt/highland/venv/bin/python"
SLEEP_INTERVAL = 60  # seconds between ticks
CONFIG_PATH = "/var/lib/highland/weather/config/weather.json"


def lock_path(product_id: str) -> str:
    return os.path.join(LOCKS_DIR, f"{product_id}.lock")


def last_run_path(product_id: str) -> str:
    return os.path.join(STATE_DIR, f"{product_id}.last_run")


def is_running(product_id: str) -> bool:
    """Check if a product script is currently running via its lockfile."""
    lpath = lock_path(product_id)
    if not os.path.exists(lpath):
        return False
    # Read PID from lockfile and check if process is alive
    try:
        with open(lpath) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)  # Signal 0 — just checks if process exists
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        # Stale lockfile — remove it
        log.warning(f"Removing stale lockfile for {product_id}")
        try:
            os.remove(lpath)
        except OSError:
            pass
        return False


def is_due(product: ProductConfig) -> bool:
    """Check if a product is due to run based on its cadence and last run time."""
    lrpath = last_run_path(product.id)
    if not os.path.exists(lrpath):
        return True  # Never run
    try:
        with open(lrpath) as f:
            last_run = float(f.read().strip())
        elapsed = time.time() - last_run
        return elapsed >= product.cadence_minutes * 60
    except (ValueError, OSError):
        return True


def record_run(product_id: str) -> None:
    """Record the current time as the last run time for a product."""
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(last_run_path(product_id), "w") as f:
        f.write(str(time.time()))


def spawn_product(product: ProductConfig, config_path: str) -> None:
    """
    Spawn a product script in the background.
    Writes a lockfile with the child PID before returning.
    """
    script = os.path.join(PRODUCTS_DIR, f"{product.id}.py")
    if not os.path.exists(script):
        log.error(f"Product script not found: {script}")
        return

    os.makedirs(LOCKS_DIR, exist_ok=True)

    log.info(f"Spawning product: {product.id}")

    proc = subprocess.Popen(
        [PYTHON, script, "--config", config_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    # Write PID to lockfile — product script is responsible for removing it on exit
    with open(lock_path(product.id), "w") as f:
        f.write(str(proc.pid))

    record_run(product.id)
    log.info(f"Spawned {product.id} (pid={proc.pid})")


def tick(config_path: str) -> None:
    """Single daemon tick — load config, check products, spawn as needed."""
    try:
        config = load_config(config_path)
    except FileNotFoundError:
        log.warning(f"Config not found at {config_path} — waiting for config listener")
        return
    except ValueError as e:
        log.error(f"Invalid config: {e}")
        return

    for product in config.enabled_products:
        if is_running(product.id):
            log.debug(f"{product.id}: still running — skipping")
            continue
        if not is_due(product):
            log.debug(f"{product.id}: not due yet — skipping")
            continue
        spawn_product(product, config_path)


def run_daemon(config_path: str) -> None:
    """Main daemon loop. Runs until interrupted."""
    log.info("Highland weather daemon starting")
    os.makedirs(LOCKS_DIR, exist_ok=True)
    os.makedirs(STATE_DIR, exist_ok=True)

    while True:
        try:
            tick(config_path)
        except Exception as e:
            log.error(f"Tick failed unexpectedly: {e}", exc_info=True)

        time.sleep(SLEEP_INTERVAL)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Highland weather daemon")
    parser.add_argument("--config", default=CONFIG_PATH, help="Path to radar config JSON")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    configure_logging(level=logging.DEBUG if args.debug else logging.INFO)

    try:
        run_daemon(args.config)
    except KeyboardInterrupt:
        log.info("Daemon stopped by interrupt")
        sys.exit(0)
