// Pins, timing and sensor calibration. Change these to match your wiring.
#pragma once

// ---------- MQTT ----------
#define DEVICE_ID          "esp32-1"         // also the MQTT client id; must match the AWS IoT policy
#define TOPIC_ENV          "beggy/esp/env"
#define PUBLISH_INTERVAL_MS 15000            // handoff asks for 10-30 s

#define AWS_PORT   8883
#define LOCAL_BROKER_HOST "pi6633101021"     // mDNS name, no ".local"
#define LOCAL_BROKER_PORT 1883

// ---------- Pins ----------
// Board: ESP32-S3. Analog inputs must be ADC1 pins (GPIO1-10): ADC2 stops working while
// Wi-Fi is on. Avoid the strapping pins GPIO0, 3, 45, 46.
#define PIN_DHT        4     // KY-015 "S"
#define PIN_LDR        5     // KY-018 "S"            (ADC1_CH4)
#define PIN_DUST_VO    6     // GP2Y1014AU0F pin 5 (Vo), through the voltage divider (ADC1_CH5)
#define PIN_DUST_LED   7     // GP2Y1014AU0F pin 3 (LED), LOW = LED on

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
// 0 = sensor not wired yet: dust_pm25 is left out of the message instead of sending noise.
// Set to 1 once the sensor and its voltage divider are connected.
#define ENABLE_DUST        0
// The sensor runs on 5 V and its output can exceed 3.3 V, so Vo goes through a divider
// into the ESP32: Vo -- 10k -- PIN_DUST_VO -- 20k -- GND  ->  ratio = 30k / 20k = 1.5
#define DUST_DIVIDER_RATIO 1.5f
// Output with clean air (Voc) and sensitivity, from the datasheet (typical values).
// Voc auto-adjusts downward if a cleaner reading than this is ever seen.
#define DUST_VOC_V         0.60f
#define DUST_V_PER_100UG   0.50f             // 0.5 V per 0.1 mg/m3
#define DUST_SAMPLES       20                // readings averaged per publish
