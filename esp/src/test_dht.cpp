// KY-015 (DHT11) test only: no Wi-Fi, no MQTT.
// Build/upload with:  pio run -e dht_test -t upload && pio device monitor

#include <Arduino.h>
#include <DHT.h>
#include "config.h"

DHT dht(PIN_DHT, DHT11);

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.printf("\n[test] KY-015 / DHT11 on GPIO %d\n", PIN_DHT);
  dht.begin();
  delay(2000);  // DHT11 needs ~1-2 s after power-up
}

void loop() {
  float t = dht.readTemperature();
  float h = dht.readHumidity();
  if (isnan(t) || isnan(h)) {
    Serial.println("[dht] read FAILED - check wiring: S->GPIO4, middle->3V3, '-'->GND");
  } else {
    Serial.printf("[dht] temperature_c=%.1f  humidity_pct=%.1f\n", t, h);
  }
  delay(2000);  // DHT11 can't be read faster than once per second
}
