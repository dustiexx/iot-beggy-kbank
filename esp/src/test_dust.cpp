// GP2Y1014AU0F (dust) test: no Wi-Fi, no MQTT. Uses the same dust.h as the main firmware.
// Build/upload with:  pio run -e dust_test -t upload && pio device monitor
// Check: in clean air Vo settles (this sensor: ~1.83 V) and dust_pm25 stays near 0;
// blow dust / smoke into the hole and Vo + dust_pm25 must jump up.
// "unstable" for the first few seconds after boot is normal. If it never settles:
// 0/20 ok -> check 5 V, the Vo wire and the divider; wide spread -> loose wire.

#include <Arduino.h>
#include "config.h"
#include "dust.h"

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.printf("\n[test] GP2Y1014AU0F: LED on GPIO %d, Vo on GPIO %d (divider ratio %.2f)\n",
                PIN_DUST_LED, PIN_DUST_VO, DUST_DIVIDER_RATIO);
  analogReadResolution(12);
  dustBegin();
}

void loop() {
  DustReading d = dustRead();
  Serial.printf("[dust] %-8s Vo=%.3f V (min %.3f max %.3f, %d/%d ok)  Voc=%.3f V  dust_pm25=%.1f ug/m3\n",
                d.ok ? "ok" : "unstable", d.vo, d.n ? d.vMin : 0.0f, d.vMax, d.n, DUST_SAMPLES,
                d.vocV, d.pm25);
  delay(1000);
}
