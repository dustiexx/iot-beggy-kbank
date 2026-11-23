"""MQTT link between the control board and the broker (local Mosquitto or AWS IoT Core).

Topics (prefix "beggy"):
  publish   beggy/pi/status    online/offline (retained, last-will) + heartbeat with fps/uptime
            beggy/pi/posture   posture state changes and periodic status
            beggy/pi/battery   UPS HAT battery readings and low/critical alerts
            beggy/pi/voice     what the user said, Beggy's reply, voice errors
            beggy/pi/head      camera centering results / head position
            beggy/pi/calendar  upcoming events / created / deleted / errors
            beggy/pi/settings_result   which settings were applied
  subscribe beggy/esp/+        sensor board data (temp, humidity, light, dust)
            beggy/pi/settings  runtime settings from the dashboard
"""
from __future__ import annotations

import json
import logging
import ssl
import time

import paho.mqtt.client as mqtt

log = logging.getLogger("mqtt")


class MqttLink:
    def __init__(self, env):
        self.env = env
        p = env.topic_prefix
        self.t_status = f"{p}/pi/status"
        self.t_settings = f"{p}/pi/settings"
        self.t_esp = f"{p}/esp/+"
        self.handlers = {}   # topic filter -> fn(topic, payload_dict)
        self.connected = False

        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=env.mqtt_client_id)
        if env.mqtt_cert and env.mqtt_key:   # AWS IoT Core: mutual TLS with the thing's certificate
            c.tls_set(ca_certs=env.mqtt_ca, certfile=env.mqtt_cert, keyfile=env.mqtt_key,
                      tls_version=ssl.PROTOCOL_TLS_CLIENT)
        c.will_set(self.t_status, json.dumps({"type": "status", "online": False}), qos=1, retain=True)
        c.on_connect = self._on_connect
        c.on_disconnect = self._on_disconnect
        c.on_message = self._on_message
        c.reconnect_delay_set(1, 30)
        self.client = c

    def topic(self, name: str) -> str:
        return f"{self.env.topic_prefix}/pi/{name}"

    def subscribe(self, topic_filter: str, fn):
        self.handlers[topic_filter] = fn
        if self.connected:
            self.client.subscribe(topic_filter, qos=1)

    def start(self):
        self.client.connect_async(self.env.mqtt_host, self.env.mqtt_port, keepalive=30)
        self.client.loop_start()

    def stop(self):
        if self.connected:
            self.publish("status", {"type": "status", "online": False}, retain=True).wait_for_publish(2)
        self.client.loop_stop()
        self.client.disconnect()

    def publish(self, name: str, payload: dict, retain: bool = False, qos: int = 0):
        payload.setdefault("unix_ms", int(time.time() * 1000))
        payload.setdefault("device", self.env.mqtt_client_id)
        return self.client.publish(self.topic(name), json.dumps(payload, ensure_ascii=False), qos=qos, retain=retain)

    # --- callbacks (paho network thread) ---
    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.warning("MQTT connect failed: %s", reason_code)
            return
        self.connected = True
        log.info("MQTT connected to %s:%s", self.env.mqtt_host, self.env.mqtt_port)
        for f in self.handlers:
            client.subscribe(f, qos=1)
        self.publish("status", {"type": "status", "online": True}, retain=True, qos=1)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        self.connected = False
        log.warning("MQTT disconnected (%s), retrying...", reason_code)

    def _on_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            log.warning("ignoring non-JSON message on %s", msg.topic)
            return
        for f, fn in self.handlers.items():
            if mqtt.topic_matches_sub(f, msg.topic):
                try:
                    fn(msg.topic, data)
                except Exception:
                    log.exception("handler for %s failed", msg.topic)
