// GP2Y1014AU0F (dust) reading, shared by main.cpp and test_dust.cpp.
#pragma once

#include <Arduino.h>
#include "config.h"

struct DustReading {
  bool ok;      // false = unstable / sensor not connected: don't publish, don't calibrate
  int n;        // valid samples out of DUST_SAMPLES
  float vo;     // average sensor output (V, before the divider)
  float vMin, vMax;
  float vocV;   // clean-air zero point in use
  float pm25;   // ug/m3, only meaningful when ok
};

inline void dustBegin() {
  pinMode(PIN_DUST_LED, OUTPUT);
  digitalWrite(PIN_DUST_LED, HIGH);  // LED off
  analogSetPinAttenuation(PIN_DUST_VO, ADC_11db);  // 0-3.3 V range
}

// One reading, using the datasheet pulse timing:
// LED on, sample after 0.28 ms, LED off after 0.32 ms, 10 ms period.
inline float dustReadVoltsOnce() {
  digitalWrite(PIN_DUST_LED, LOW);
  delayMicroseconds(280);
  uint32_t mv = analogReadMilliVolts(PIN_DUST_VO);
  delayMicroseconds(40);
  digitalWrite(PIN_DUST_LED, HIGH);
  delayMicroseconds(9680);
  return mv / 1000.0f * DUST_DIVIDER_RATIO;
}

// Averages DUST_SAMPLES pulses. For a few seconds after boot (or with a loose wire) the
// output jumps around and drops to 0 V; such a batch is marked !ok so it is neither sent
// nor used to move Voc, which would otherwise stick at that low value until reboot.
inline DustReading dustRead() {
  static float vocV = DUST_VOC_V;
  DustReading r{};
  float sum = 0;
  r.vMin = 99;
  for (int i = 0; i < DUST_SAMPLES; i++) {
    float s = dustReadVoltsOnce();
    if (s < DUST_MIN_VALID_V) continue;
    sum += s;
    r.n++;
    r.vMin = min(r.vMin, s);
    r.vMax = max(r.vMax, s);
  }
  r.vo = r.n ? sum / r.n : 0;
  r.ok = r.n >= DUST_SAMPLES * 3 / 4 && (r.vMax - r.vMin) <= DUST_MAX_SPREAD_V;

  // Clean air gives the lowest voltage; track it (from good batches only) so the zero
  // point follows the sensor's drift.
  if (r.ok && r.vo < vocV) vocV = r.vo;
  r.vocV = vocV;
  float ug = (r.vo - vocV) / DUST_V_PER_100UG * 100.0f;
  r.pm25 = ug < 0 ? 0 : ug;
  return r;
}
