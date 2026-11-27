// Pins, timing and sensor calibration. Change these to match your wiring.
#pragma once

// ---------- MQTT ----------
#define DEVICE_ID          "esp32-1"         // also the MQTT client id; must match the AWS IoT policy
#define TOPIC_ENV          "beggy/esp/env"
#define PUBLISH_INTERVAL_MS 5000             // 5 s: dashboard must look realtime (rubric item 13)
// Report-on-change: sensors are read every CHECK_INTERVAL_MS and a reading is sent right away
// (instead of waiting for the 5 s tick) when it moved more than these thresholds since the last
// publish, so the dashboard reacts within ~1 s (lights off, dust puff) without sending more
// messages while the room is steady. Thresholds sit above each sensor's normal jitter.
#define CHECK_INTERVAL_MS   1000             // DHT11 can't be read faster than once per second
#define MIN_GAP_MS          1000             // never publish more often than this
#define CHANGE_TEMP_C       1.0f
#define CHANGE_HUMI_PCT     5.0f
#define CHANGE_LUX_FRAC     0.30f            // 30 % of the last sent value...
#define CHANGE_LUX_MIN      5.0f             // ...and at least 5 lux (dark rooms jitter by a few)
#define CHANGE_PM25         30.0f            // the dust sensor jitters ~20 ug/m3 between reads

#define AWS_PORT   8883
#define LOCAL_BROKER_HOST "pi6633101021"     // mDNS name, no ".local"
#define LOCAL_BROKER_PORT 1883

// ---------- Pins ----------
// Board: ESP32-S3. Analog inputs must be ADC1 pins (GPIO1-10): ADC2 stops working while
// Wi-Fi is on. Avoid the strapping pins GPIO0, 3, 45, 46.
#define PIN_DHT        4     // KY-015 "S"
#define PIN_LDR        5     // KY-018 "S"            (ADC1_CH4)
#define PIN_DUST_LED   6     // GP2Y1014AU0F pin 3 (LED), LOW = LED on
#define PIN_DUST_VO    7     // GP2Y1014AU0F pin 5 (Vo), through the voltage divider (ADC1_CH6)

// ---------- KY-018 (LDR) ----------
// The module is a divider: LDR + fixed 10k resistor. Power it from 3.3 V.
#define LDR_VCC_MV        3300.0f
#define LDR_FIXED_OHM     10000.0f
// true  = LDR sits between S and GND  -> voltage FALLS as light rises
// false = LDR sits between S and VCC  -> voltage RISES as light rises
// Cover the sensor with a finger: if the logged lux goes UP, flip this.
#define LDR_TO_GND        true
// GL55xx-style curve: R = R10 * (lux / 10)^-GAMMA. Typical GL5528: R10 ~ 10-20k, gamma ~ 0.7.
// This gives an approximate lux, not a lab-grade one.
#define LDR_R10_OHM       15000.0f
#define LDR_GAMMA         0.7f

// ---------- GP2Y1014AU0F (dust) ----------
// 1 = read the sensor and send dust_pm25. Set to 0 to leave dust_pm25 out of the message
// (e.g. while the sensor is unplugged) instead of sending noise.
#define ENABLE_DUST        1
// The sensor runs on 5 V and its output can exceed 3.3 V, so Vo goes through a divider
// into the ESP32: Vo -- 10k -- PIN_DUST_VO -- 20k -- GND  ->  ratio = 30k / 20k = 1.5
#define DUST_DIVIDER_RATIO 1.5f
// Output with clean air (Voc). Datasheet typical is 0.6 V, but each unit differs.
// Calibrated 2026-11-26: room Vo was ~1.833 V while a reference meter showed ~8 ug/m3,
// so Voc = 1.833 - 8/100 * 0.5 = 1.79 V. Voc auto-adjusts downward if a cleaner
// reading than this is ever seen.
#define DUST_VOC_V         1.79f
// Samples below this are dropped: a connected, powered sensor never reads ~0 V.
#define DUST_MIN_VALID_V   0.10f
// A batch whose samples spread wider than this is unstable (warming up right after
// boot, or a loose wire) and is dropped. A settled sensor spreads ~0.05 V.
#define DUST_MAX_SPREAD_V  0.20f
#define DUST_V_PER_100UG   0.50f             // 0.5 V per 0.1 mg/m3
#define DUST_SAMPLES       20                // readings averaged per publish
