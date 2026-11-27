# ESP32 Sensor Board (beggy)

อ่านค่า KY-015 (DHT11), KY-018 (LDR) และ GP2Y1014AU0F (PM2.5) แล้ว publish ขึ้น AWS IoT Core
ที่ topic `beggy/esp/env` (retain = true) ทุก 15 วินาที ตาม [HANDOFF.md](HANDOFF.md)

```json
{"temperature_c":27.4,"humidity_pct":61.0,"light_lux":320,"dust_pm25":18.5,"device":"esp32-1","unix_ms":1795000000000}
```

## การต่อสาย

| เซนเซอร์ | ขาเซนเซอร์ | ต่อกับ ESP32 |
|---|---|---|
| **KY-015** (DHT11) | S | GPIO 4 |
| | VCC (ขากลาง) | 3V3 |
| | − | GND |
| **KY-018** (LDR) | S | GPIO 5 |
| | VCC (ขากลาง) | **3V3** (ห้ามใช้ 5V เพราะ ADC รับได้สูงสุด 3.3V) |
| | − | GND |
| **GP2Y1014AU0F** | 1 V-LED | 5V ผ่าน R 150Ω และต่อ C 220µF ลง GND |
| | 2 LED-GND | GND |
| | 3 LED | GPIO 6 |
| | 4 S-GND | GND |
| | 5 Vo | ผ่าน **วงจรแบ่งแรงดัน** เข้า GPIO 7 (ดูด้านล่าง) |
| | 6 Vcc | 5V (VIN) |

**วงจรแบ่งแรงดันของ Vo:** `Vo ── 10kΩ ──┬── GPIO7` และ `┴── 20kΩ ── GND`
เพราะ Vo ของเซนเซอร์ฝุ่นขึ้นได้เกิน 3.3V ถ้าใช้ค่า R อื่นให้แก้ `DUST_DIVIDER_RATIO` ใน [include/config.h](include/config.h)
(ถ้าซื้อแบบมีบอร์ด adapter มาแล้ว R 150Ω กับ C 220µF มักอยู่บนบอร์ดนั้นแล้ว)

บอร์ดเป็น **ESP32-S3** เสียบสายที่ช่อง **COM** ขา analog ต้องเป็น GPIO 1-10 (ADC1) เพราะ ADC2 ใช้ไม่ได้ระหว่างที่เปิด Wi-Fi และอย่าใช้ GPIO 0, 3, 45, 46 (strapping pins)

## ตั้งค่า

1. แก้ [include/secrets.h](include/secrets.h): ใส่ `WIFI_SSID`, `WIFI_PASSWORD` (ใส่ Wi-Fi สำรองได้อีก 2 ชุดที่ `WIFI_SSID_2`/`_3` เช่น hotspot มือถือ บอร์ดจะต่อตัวที่เจอเอง) และ `AWS_IOT_ENDPOINT` (ขอจากคนทำ backend
   ดูได้ที่ AWS console > IoT Core > Settings > Device data endpoint)
2. ไฟล์ certificate อยู่ใน `certs/` แล้ว และถูก compile ฝังเข้า firmware อัตโนมัติ
   ถ้าได้ cert ชุดใหม่มา ให้เขียนทับ `certs/device.pem.crt` กับ `certs/private.pem.key`

`certs/` และ `secrets.h` อยู่ใน `.gitignore` แล้ว ห้าม commit ขึ้น git

## Build / Upload

```bash
pio run -e aws -t upload      # ใช้งานจริง: AWS IoT Core port 8883
pio device monitor            # ดู log ที่ 115200
```

ถ้า AWS ยังไม่พร้อม ทดสอบกับ broker บน Pi ได้ (ESP32 ต้องอยู่ Wi-Fi เดียวกับ Pi):

```bash
pio run -e local -t upload    # pi6633101021.local:1883 ไม่มี TLS
```

## ทดสอบ

AWS console > IoT Core > **MQTT test client** แล้ว subscribe `beggy/esp/#` ควรเห็นข้อความทุก 15 วินาที

| log | ความหมาย |
|---|---|
| `[mqtt] ... failed, state=-2` | ต่อ TLS ไม่ได้: endpoint ผิด, cert ผิด, หรือเครือข่ายบล็อก port 8883 |
| ต่อได้แล้วหลุดทันที (`state=-4`/`-1`) | IoT policy ไม่อนุญาต client id `esp32-1` หรือ topic `beggy/esp/*`: แจ้งคนทำ backend |
| `[dht] read failed` | สาย DHT11 หลวม หรือต่อผิดขา (จะไม่ส่ง field อุณหภูมิ/ความชื้นในรอบนั้น) |

## การปรับเทียบ (calibration)

- **แสง:** KY-018 เป็น LDR จึงได้ lux แบบ **ประมาณ** ถ้าเอานิ้วปิดเซนเซอร์แล้วค่า lux **เพิ่มขึ้น** ให้เปลี่ยน
  `LDR_TO_GND` เป็น `false` ถ้าอยากให้แม่นขึ้น ให้เทียบกับแอปวัดแสงในมือถือแล้วปรับ `LDR_R10_OHM`
- **ฝุ่น:** ใช้ค่ามาตรฐานจาก datasheet (Voc 0.6V, 0.5V ต่อ 100 µg/m³) และโค้ดจะลด Voc ลงเองเมื่อเจออากาศที่สะอาดกว่า
  ดูค่า `[dust] Vo=... Voc=...` ใน log ถ้าวางไว้ในอากาศสะอาดแล้ว `dust_pm25` ยังสูง ให้ตั้ง `DUST_VOC_V` เป็นค่า Vo ที่อ่านได้
