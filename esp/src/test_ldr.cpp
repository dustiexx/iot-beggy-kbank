// KY-018 (LDR) + KY-015 (DHT11) test: no Wi-Fi, no MQTT.
// Build/upload with:  pio run -e ldr_test -t upload && pio device monitor
// Check: cover the LDR with a finger -> lux must DROP. If it rises, flip LDR_TO_GND in config.h.

#include <Arduino.h>
#include <DHT.h>
#include <math.h>
#include "config.h"

DHT dht(PIN_DHT, DHT11);

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.printf("\n[test] KY-018 on GPIO %d, KY-015 on GPIO %d\n", PIN_LDR, PIN_DHT);
  analogReadResolution(12);
  analogSetPinAttenuation(PIN_LDR, ADC_11db);  // 0-3.3 V range
  dht.begin();
  delay(2000);
}

void loop() {
  uint32_t sum = 0;
  for (int i = 0; i < 16; i++) {
    sum += analogReadMilliVolts(PIN_LDR);
    delay(2);
  }
  float mv = constrain((float)sum / 16, 1.0f, LDR_VCC_MV - 1.0f);
  float rLdr = LDR_TO_GND ? LDR_FIXED_OHM * mv / (LDR_VCC_MV - mv)
                          : LDR_FIXED_OHM * (LDR_VCC_MV - mv) / mv;
  float lux = constrain(10.0f * powf(LDR_R10_OHM / rLdr, 1.0f / LDR_GAMMA), 0.0f, 100000.0f);

  float t = dht.readTemperature();
  float h = dht.readHumidity();

  Serial.printf("[ldr] %4.0f mV  R=%7.0f ohm  light_lux=%6.0f   ", mv, rLdr, lux);
  if (isnan(t) || isnan(h)) Serial.println("[dht] read FAILED");
  else Serial.printf("[dht] temperature_c=%.1f humidity_pct=%.1f\n", t, h);

  delay(1000);
}
