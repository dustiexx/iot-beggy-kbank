# Handoff: ESP32 sensor board

From the Pi (control board) team. This is everything the ESP32 must do so the Pi, AWS and the dashboard can use its data.
Full system topic list: `backend/HANDOFF.md` > "All MQTT topics". Questions -> Pi team.

## What to send

Publish to **`beggy/esp/env`**, **retain = true** (so the Pi gets the latest values right after it restarts), every 10-30 s:

```json
{"temperature_c": 27.4, "humidity_pct": 61.0, "light_lux": 320, "dust_pm25": 18.5, "device": "esp32-1"}
```

| field | unit | note |
|---|---|---|
| `temperature_c` | degrees C | |
| `humidity_pct` | % RH | |
| `light_lux` | lux | |
| `dust_pm25` | ug/m3 (PM2.5) | |
| `device` | text | which board, e.g. `esp32-1` |
| `unix_ms` | ms | optional; leave it out if the ESP32 has no clock (the Pi stamps it on arrival, the IoT Rule uses `timestamp()`) |

Field names must be exactly these: Gemini reads them to answer "how dusty is it?", and the History API uses them as `metric`. Extra fields are fine.

## Where to connect: AWS IoT Core

The broker is **AWS IoT Core**. The ESP32 and the Pi both connect to AWS, never to each other, so the ESP32 code has no Pi IP in it.

| | value |
|---|---|
| broker | the IoT Core endpoint `xxxx-ats.iot.<region>.amazonaws.com` (from the backend person; it never changes) |
| port | `8883` (MQTT over TLS) |
| auth | mutual TLS: the ESP32's own `certificate.pem.crt` + `private.pem.key` + `AmazonRootCA1.pem` (from the backend person) |
| client id | `esp32-1` (must match the ESP32's IoT policy on AWS) |

- The ESP32 may only publish to `beggy/esp/*`. Don't use other topics.
- Never commit the certificate / private key to git.
- Test: in the AWS console, **IoT Core > MQTT test client**, subscribe to `beggy/esp/#`.

### Optional, temporary: test before AWS is ready

Only if you want to test before the backend person has created AWS IoT Core. The Pi runs a local test broker; use its **name**, not an IP:

| broker | port | auth |
|---|---|---|
| `pi6633101021.local` (same Wi-Fi as the Pi) | `1883` | none (plain MQTT) |

```cpp
#include <ESPmDNS.h>
MDNS.begin("esp32-1");
IPAddress pi = MDNS.queryHost("pi6633101021");   // no ".local" here
mqtt.setServer(pi, 1883);
```

Remove this once AWS works.

## Checklist

- [ ] Publish `beggy/esp/env` every 10-30 s with **retain = true**
- [ ] Field names exactly `temperature_c`, `humidity_pct`, `light_lux`, `dust_pm25` (+ `device`)
- [ ] Connect to AWS IoT Core (port 8883, certificates + endpoint from the backend person)
- [ ] Data shows up in the IoT Core MQTT test client
