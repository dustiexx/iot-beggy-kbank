# Beggy: TODO (อัปเดต 23 พ.ย. 2026)

**Deadline:** สไลด์ + วิดีโอ ส่ง myCourseVille **2 ธ.ค. 2026, 9:00 น.**
โค้ด Pi อยู่ที่ branch [`raspi`](https://github.com/dustiexx/iot-beggy-kbank/tree/raspi) วิธีใช้ทั้งหมดอยู่ใน `raspi/README.md`

---

## ✅ ส่วน Pi (Control board) ที่เสร็จแล้ว

- **Posture detection** (MediaPipe บน Pi): good / slouch / head_down / too_close / uneven_shoulders, กรองการจับผิดตอนไม่มีคน
- **Server กลาง** `beggy_main.py`: รวม posture, voice, servo, battery, MQTT, HTTP API ไว้ใน process เดียว
- **Voice:** wake word → STT (Gemini 3.1 Flash Lite) → Gemini + tools → TTS (edge-tts → gTTS → espeak-ng offline)
- **Gemini tools:** หันกล้องหาคน, อ่านค่า sensor, ท่านั่ง, แบต, Google Calendar (ดู/สร้าง/ลบ), ประวัติย้อนหลัง (ผ่าน backend)
- **Servo pan/tilt:** หันกล้องให้คนอยู่กลางเฟรม (ทดสอบด้วยหัวจำลองแล้ว)
- **แบต UPS HAT (E) ผ่าน I2C:** เตือนแบบ iPhone ที่ 20% และ 10%
- **systemd:** เปิดเองตอนบูต, restart เองถ้าล่ม
- **Evaluation script:** วัด fps + accuracy + confusion matrix + ภาพกรณีถูก/ผิด
- **Tests:** 38 ข้อผ่าน

## 📡 MQTT topics (สัญญากลางของทั้งทีม)

ผู้ส่ง = publish ข้อความ, ผู้รับ = subscribe ข้อความนั้น ("AWS" = IoT Rule ที่เก็บลง DynamoDB + backend)

| topic | ผู้ส่ง (publish) | ผู้รับ (subscribe) | ใช้ทำอะไร |
|---|---|---|---|
| `beggy/esp/env` | ESP32 | Pi, AWS | temp, humi, light, ฝุ่น: Pi เก็บค่าล่าสุดไว้ตอบคำถามเสียง, AWS เก็บลง DB + แจ้งเตือนฝุ่น |
| `beggy/pi/posture` | Pi | AWS | ท่านั่ง (ตอน state เปลี่ยน + status ทุก 5 วิ) → DB + แจ้งเตือน posture |
| `beggy/pi/battery` | Pi | AWS | แบตทุก 30 วิ + `battery_low` → DB + dashboard |
| `beggy/pi/voice` | Pi | AWS | สิ่งที่ผู้ใช้พูด + คำตอบของเบกกี้ → DB + dashboard |
| `beggy/pi/head` | Pi | AWS | ผลการหันกล้องหาคน → dashboard |
| `beggy/pi/calendar` | Pi | AWS (backend) | นัดใน 24 ชม. ข้างหน้า ทุก 5 นาที → แจ้งเตือน calendar deadline |
| `beggy/pi/status` | Pi | AWS (dashboard) | online / offline (retained + last will) |
| `beggy/pi/cmd` | AWS (backend / dashboard) | Pi | สั่งงานหุ่น: `center`, `home`, `say`, `ask` |
| `beggy/pi/cmd_result` | Pi | AWS (backend / dashboard) | ผลของคำสั่งที่ส่งมาทาง `beggy/pi/cmd` |
| `beggy/pi/settings` | AWS (backend / dashboard) | Pi | เปลี่ยน setting จากหน้า Setting ของ dashboard |
| `beggy/pi/settings_result` | Pi | AWS (backend / dashboard) | ยืนยันว่า setting ไหนถูกใช้ / ถูกปฏิเสธ |

ทุก message เป็น JSON มี `type`, `unix_ms`, `device`

---

## 🔴 ต้องทำ (สำคัญ: มีผลต่อคะแนน)

- [ ] **AWS IoT Core (broker):** สร้าง thing + certificate + policy แล้วส่ง endpoint + cert ให้ฝั่ง Pi ใส่ `.env` (โค้ดรองรับแล้ว ตอนนี้ใช้ Mosquitto บน Pi ไปก่อน) **ใครถือบัญชี AWS?**
- [ ] **ทีม backend: History API** ให้เบกกี้ตอบคำถามย้อนหลังได้ เช่น "วันนี้นั่งค่อมนานแค่ไหน" หรือ "ฝุ่นสัปดาห์นี้เป็นยังไง" Pi ทำฝั่งตัวเองเสร็จแล้ว รอ backend ทำ 2 endpoint ตามสเปกใน `raspi/README.md` หัวข้อ "History API" (มีแบบตาราง DynamoDB ที่แนะนำด้วย) เสร็จแล้วส่ง URL + API key ให้ฝั่ง Pi ใส่ `.env`
- [ ] **ทีม ESP32:** (1) ยืนยันชื่อ topic และ field JSON ตอนนี้ Pi รออ่าน `beggy/esp/env` เป็น `{"temperature_c", "humidity_pct", "light_lux", "dust_pm25"}` ถ้าใช้ชื่ออื่นบอกฝั่ง Pi (2) publish ด้วย `retain=true` Pi จะได้ค่าล่าสุดทันทีหลังรีสตาร์ท
- [ ] **วัดผล posture (เกณฑ์ข้อ 9, 2 คะแนน):** นั่งหน้ากล้อง calibrate แล้วรัน `eval_posture.py` ใช้เวลาประมาณ 3 นาที/รอบ ได้ตัวเลข + ภาพใส่สไลด์เลย
- [ ] **commit + push** งานรอบล่าสุดของ Pi (ยังไม่ได้ commit)

## 🟡 รอของ / ต้องมีคนทำ

- [ ] **กล้อง Pi Camera Module 3:** รอปริ้นต์ตัวยึด แล้ว calibrate ใหม่ + ทดสอบ (ตอนนี้ใช้ USB webcam อยู่)
- [ ] **Servo:** ต่อสาย (ไฟจาก 5V ของ UPS HAT, ใส่ capacitor 470–1000 µF), ทดสอบทิศหมุน, เช็กไฟตก `vcgencmd get_throttled`
- [ ] **ลำโพง:** รอของ แล้วตั้ง `AUDIO_OUT` ในไฟล์ `.env`
- [ ] **ทดสอบเสียงครบวงจรด้วยไมค์จริง** (ใช้ quota Gemini 2–3 คำถาม)
- [ ] **Wake word "hey Beggy":** ระบบเทรนพร้อมแล้ว ต้องอัดเสียงจริง:
  - ทุกคนในทีมพูด "hey Beggy" คนละ 20–40 ครั้ง: `wakeword_train.py record-pos`
  - อัดเสียงคุยปกติประมาณ 3 นาที: `wakeword_train.py record-neg`
  - (ตอนนี้ยังใช้ "hey jarvis" แทน)
- [ ] **Google Calendar:** ตั้ง OAuth ใน Google Cloud แล้ว login บนจอ Pi ครั้งเดียว (4 ขั้น ใน README)

## 🎤 สไลด์ + วิดีโอ (7 คะแนน)

- [ ] Objectives + SDGs 2–3 ข้อ
- [ ] System architecture (ใช้ภาพ diagram + ตาราง MQTT ด้านบน)
- [ ] Input / Output
- [ ] Strengths / Limitations เช่น:
  - Gemini ฟรีจำกัด 500 ครั้ง/วัน
  - TTS ฟรีไม่เสถียร (มี offline fallback)
  - ต้องใช้เน็ต
  - posture ใช้ได้เฉพาะตำแหน่งหัวที่ calibrate
- [ ] เทียบกับสินค้าจริงในตลาด
- [ ] วิดีโอประมาณ 5 นาที: hardware + app + system

## ⚠️ หมายเหตุ

- **Gemini free tier:** ใช้แต่ **3.1 Flash Lite** (500 ครั้ง/วัน) เพราะ 3.6/3.8 Flash ได้แค่ 20 ครั้ง/วันและช้า ทดสอบอย่าเปลือง: คำถามเสียง 1 ครั้งใช้ประมาณ 3 requests
- **DynamoDB:** เกณฑ์เขียนว่า "time-series database" ต้องอธิบายในสไลด์ว่าออกแบบ key เป็น time-series (partition ตาม device/metric, sort ตามเวลา)
- **หลังจบโปรเจกต์:** สร้าง Gemini API key ใหม่ (key ปัจจุบันเคยถูกส่งในแชท)
