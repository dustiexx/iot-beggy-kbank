// ESP32 sensor board for the beggy system.
// Reads KY-015 (DHT11), KY-018 (LDR) and GP2Y1014AU0F (PM2.5), then publishes
//   {"temperature_c":..,"humidity_pct":..,"light_lux":..,"dust_pm25":..,"device":"esp32-1","unix_ms":..}
// to beggy/esp/env with retain = true, on AWS IoT Core (or the Pi test broker in the "local" env).

#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include <math.h>
#include <sys/time.h>

#include "config.h"
#include "secrets.h"
#include "dust.h"

#ifdef USE_LOCAL_BROKER
#include <ESPmDNS.h>
WiFiClient net;
#else
#include <WiFiClientSecure.h>
WiFiClientSecure net;
// Embedded from certs/ by board_build.embed_txtfiles in platformio.ini
extern const char root_ca_pem[]     asm("_binary_certs_AmazonRootCA1_pem_start");
extern const char device_cert_pem[] asm("_binary_certs_device_pem_crt_start");
extern const char private_key_pem[] asm("_binary_certs_private_pem_key_start");
#endif

PubSubClient mqtt(net);
DHT dht(PIN_DHT, DHT11);

static uint32_t lastPublishMs = 0;
static uint32_t lastMqttAttemptMs = 0;

// ---------------------------------------------------------------- sensors

static float round1(float v) { return roundf(v * 10.0f) / 10.0f; }

static float readLux() {
  uint32_t sum = 0;
  const int n = 16;
  for (int i = 0; i < n; i++) {
    sum += analogReadMilliVolts(PIN_LDR);
    delay(2);
  }
  float mv = (float)sum / n;
  mv = constrain(mv, 1.0f, LDR_VCC_MV - 1.0f);

  float rLdr = LDR_TO_GND ? LDR_FIXED_OHM * mv / (LDR_VCC_MV - mv)
                          : LDR_FIXED_OHM * (LDR_VCC_MV - mv) / mv;
  float lux = 10.0f * powf(LDR_R10_OHM / rLdr, 1.0f / LDR_GAMMA);
  return constrain(lux, 0.0f, 100000.0f);
}

static float readDustPm25() {
  DustReading d = dustRead();
  if (!d.ok) {
    Serial.printf("[dust] unstable (Vo=%.3f V, %d/%d ok, spread %.3f V): dust_pm25 left out\n",
                  d.vo, d.n, DUST_SAMPLES, d.n ? d.vMax - d.vMin : 0.0f);
    return NAN;
  }
  Serial.printf("[dust] Vo=%.3f V  Voc=%.3f V\n", d.vo, d.vocV);
  return d.pm25;
}

// ---------------------------------------------------------------- network

static void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.printf("[wifi] connecting to %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 20000) {
    delay(500);
    Serial.print(".");
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("\n[wifi] ok, ip %s\n", WiFi.localIP().toString().c_str());
  } else {
    Serial.println("\n[wifi] failed, will retry");
  }
}

static bool timeIsSet() { return time(nullptr) > 1700000000; }

static uint64_t unixMs() {
  struct timeval tv;
  gettimeofday(&tv, nullptr);
  return (uint64_t)tv.tv_sec * 1000ULL + tv.tv_usec / 1000;
}

static void setupBroker() {
#ifdef USE_LOCAL_BROKER
  MDNS.begin(DEVICE_ID);
  IPAddress pi = MDNS.queryHost(LOCAL_BROKER_HOST);
  Serial.printf("[mdns] %s.local -> %s\n", LOCAL_BROKER_HOST, pi.toString().c_str());
  mqtt.setServer(pi, LOCAL_BROKER_PORT);
#else
  net.setCACert(root_ca_pem);
  net.setCertificate(device_cert_pem);
  net.setPrivateKey(private_key_pem);
  mqtt.setServer(AWS_IOT_ENDPOINT, AWS_PORT);
#endif
  mqtt.setBufferSize(1024);
  mqtt.setKeepAlive(30);
}

static void connectMqtt() {
  if (mqtt.connected() || WiFi.status() != WL_CONNECTED) return;
  if (lastMqttAttemptMs && millis() - lastMqttAttemptMs < 5000) return;
  lastMqttAttemptMs = millis();

#ifdef USE_LOCAL_BROKER
  setupBroker();  // the Pi's address may have changed; look it up again
#endif
  Serial.print("[mqtt] connecting... ");
  if (mqtt.connect(DEVICE_ID)) {
    Serial.println("ok");
  } else {
    // -2 = TCP/TLS failed (endpoint, certs, port 8883 blocked)
    // -4/-1 right after connecting = usually the IoT policy rejected the client id
    Serial.printf("failed, state=%d\n", mqtt.state());
  }
}

// ---------------------------------------------------------------- publish

static void publishReading() {
  float t = dht.readTemperature();
  float h = dht.readHumidity();
  float lux = readLux();
#if ENABLE_DUST
  float pm = readDustPm25();
#endif

  JsonDocument doc;
  if (!isnan(t)) doc["temperature_c"] = round1(t);
  if (!isnan(h)) doc["humidity_pct"] = round1(h);
  doc["light_lux"] = (int)roundf(lux);
#if ENABLE_DUST
  if (!isnan(pm)) doc["dust_pm25"] = round1(pm);
#endif
  doc["device"] = DEVICE_ID;
  if (timeIsSet()) doc["unix_ms"] = unixMs();  // left out until NTP has synced
  if (isnan(t) || isnan(h)) Serial.println("[dht] read failed, fields left out");

  char payload[256];
  size_t len = serializeJson(doc, payload, sizeof(payload));
  bool ok = mqtt.publish(TOPIC_ENV, (const uint8_t*)payload, len, /*retained=*/true);
  Serial.printf("[pub] %s %s %s\n", ok ? "sent" : "FAILED", TOPIC_ENV, payload);
}

// ---------------------------------------------------------------- main

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.printf("\n[boot] %s\n", DEVICE_ID);

  analogReadResolution(12);
  analogSetPinAttenuation(PIN_LDR, ADC_11db);      // 0-3.3 V range
  dustBegin();
  dht.begin();

  connectWiFi();
  configTime(0, 0, "pool.ntp.org", "time.google.com");  // UTC, for unix_ms
  setupBroker();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) connectWiFi();
  connectMqtt();
  mqtt.loop();

  if (mqtt.connected() && (lastPublishMs == 0 || millis() - lastPublishMs >= PUBLISH_INTERVAL_MS)) {
    lastPublishMs = millis();
    publishReading();
  }
  delay(10);
}
