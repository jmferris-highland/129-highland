"""
mqtt.py — MQTT publish helpers for the Highland weather daemon.

Handles publishing state, events, and log messages to the MQTT broker.
Uses a simple synchronous publish pattern — no persistent connection needed
for fire-and-forget event publishing from product scripts.
"""

import json
import logging
import time
from typing import Any, Optional

import paho.mqtt.publish as publish

log = logging.getLogger(__name__)

# Topic templates
TOPIC_STATUS = "highland/status/weather/radar/{product}"
TOPIC_STATE = "highland/state/weather/radar/{product}/last_updated"
TOPIC_EVENT_RENDERED = "highland/event/weather/radar/{product}/rendered"
TOPIC_EVENT_ERROR = "highland/event/weather/radar/{product}/error"
TOPIC_EVENT_BASE_MAP = "highland/event/weather/radar/base_map/rendered"
TOPIC_LOG = "highland/event/log"


class MqttPublisher:
    """Thin wrapper around paho publish.single for fire-and-forget MQTT publishing."""

    def __init__(self, host: str, port: int, username: str, password: str):
        self.host = host
        self.port = port
        self.auth = {"username": username, "password": password}

    def _publish(self, topic: str, payload: Any, retain: bool = False) -> None:
        """Publish a single message. Logs but does not raise on failure."""
        try:
            if isinstance(payload, dict):
                payload = json.dumps(payload)
            publish.single(
                topic=topic,
                payload=payload,
                hostname=self.host,
                port=self.port,
                auth=self.auth,
                retain=retain,
            )
        except Exception as e:
            log.warning(f"MQTT publish failed [{topic}]: {e}")

    def publish_status(self, product: str, status: str) -> None:
        """Publish retained status for a product: idle / running / error."""
        topic = TOPIC_STATUS.format(product=product)
        self._publish(topic, status, retain=True)

    def publish_rendered(self, product: str, output_path: str) -> None:
        """Publish non-retained event when a product GIF is successfully written."""
        topic = TOPIC_EVENT_RENDERED.format(product=product)
        payload = {
            "product": product,
            "output_path": output_path,
            "timestamp": int(time.time()),
        }
        self._publish(topic, payload)

        # Also update retained last_updated state
        state_topic = TOPIC_STATE.format(product=product)
        self._publish(state_topic, str(int(time.time())), retain=True)

    def publish_error(self, product: str, message: str) -> None:
        """Publish non-retained error event for a product."""
        topic = TOPIC_EVENT_ERROR.format(product=product)
        payload = {
            "product": product,
            "error": message,
            "timestamp": int(time.time()),
        }
        self._publish(topic, payload)

    def publish_base_map_rendered(self) -> None:
        """Publish non-retained event when the base map is regenerated."""
        payload = {"timestamp": int(time.time())}
        self._publish(TOPIC_EVENT_BASE_MAP, payload)

    def publish_log(
        self,
        level: str,
        message: str,
        source: str = "weather-daemon",
        product: Optional[str] = None,
    ) -> None:
        """Publish a log entry to the standard Highland log topic."""
        payload = {
            "level": level,
            "source": source,
            "message": message,
            "timestamp": int(time.time()),
        }
        if product:
            payload["product"] = product
        self._publish(TOPIC_LOG, payload)
