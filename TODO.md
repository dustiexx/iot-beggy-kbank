# Beggy: TODO (อัปเดต 27 พ.ย. 2026)

**Deadline:** สไลด์ + วิดีโอ ส่ง myCourseVille **2 ธ.ค. 2026, 9:00 น.**
โค้ด Pi อยู่ที่ branch [`raspi`](https://github.com/dustiexx/iot-beggy-kbank/tree/raspi) วิธีใช้ทั้งหมดอยู่ใน `raspi/README.md`

---

## ✅ ส่วน Pi (Control board) ที่เสร็จแล้ว

- **Posture detection** (MediaPipe บน Pi): good / slouch / head_down / too_close / uneven_shoulders, กรองการจับผิดตอนไม่มีคน
- **Server กลาง** `beggy_main.py`: รวม posture, voice, servo, battery, MQTT, HTTP API ไว้ใน process เดียว
- **Voice:** "hey Beggy" (wake word ที่เทรนเอง) → Gemini 3.1 Flash Lite ฟังเสียงคำถามโดยตรง + tools (STT ทำคู่กันเพื่อเก็บข้อความ, ถ้าเน็ต/Gemini ล่มใช้ Whisper offline) → TTS (edge-tts กับ gTTS แข่งกัน, espeak-ng offline)
  - ถ้าเบกกี้ถามกลับ จะฟังคำตอบต่อ 6 วิโดยไม่ต้องเรียกชื่อใหม่ / เข้าใจเวลาแบบไทย (บ่ายโมง = 13:00)
- **Gemini tools:** หันกล้องหาคน, อ่านค่า sensor, ท่านั่ง, แบต, Google Calendar (ดู/สร้าง/แก้/ลบ), ประวัติย้อนหลัง (ผ่าน backend)
- **Servo pan/tilt:** หันกล้องให้คนอยู่กลางเฟรม (ทดสอบด้วยหัวจำลองแล้ว)
- **แบต UPS HAT (E) ผ่าน I2C:** เตือนแบบ iPhone ที่ 20% และ 10%
- **systemd:** เปิดเองตอนบูต, restart เองถ้าล่ม
- **Evaluation script:** วัด fps + accuracy + confusion matrix + ภาพกรณีถูก/ผิด
- **Google Calendar:** ตั้ง OAuth + login แล้ว (23 พ.ย.) ทดสอบอ่าน/สร้าง/ลบนัดบนปฏิทินจริงผ่าน
- **Tests:** 70 ข้อผ่าน

## 📡 MQTT topics (สัญญากลางของทั้งทีม)

ผู้ส่ง = publish ข้อความ, ผู้รับ = subscribe ข้อความนั้น ("AWS" = IoT Rule ที่เก็บลง DynamoDB + backend)

| topic | ผู้ส่ง (publish) | ผู้รับ (subscribe) | ใช้ทำอะไร |
|---|---|---|---|
| `beggy/esp/env` | ESP32 | Pi, AWS | temp, humi, light, ฝุ่น: Pi เก็บค่าล่าสุดไว้ตอบคำถามเสียง, AWS เก็บลง DB + แจ้งเตือนฝุ่น |
| `beggy/pi/posture` | Pi | AWS | ท่านั่ง (ตอน state เปลี่ยน + status ทุก 5 วิ) → DB + แจ้งเตือน posture |
| `beggy/pi/battery` | Pi | AWS | แบตทุก 30 วิ + `battery_low` → DB + dashboard |
| `beggy/pi/voice` | Pi | AWS | สิ่งที่ผู้ใช้พูด + คำตอบของเบกกี้ → DB + dashboard |
| `beggy/pi/head` | Pi | AWS | ผลการหันกล้องหาคน → dashboard |
| `beggy/pi/calendar` | Pi | AWS (backend / dashboard) | ทุก 5 นาที: `calendar_upcoming` (นัดใน 24 ชม. ข้างหน้า) หรือ `calendar_error` (เช่น login หมดอายุ) + `calendar_created` / `calendar_updated` / `calendar_deleted` ทันทีเมื่อเบกกี้สร้าง/แก้/ลบนัด → แจ้งเตือน calendar deadline (ดูรายละเอียดด้านล่าง) |
| `beggy/pi/status` | Pi | AWS (dashboard) | online / offline (retained + last will) |
| `beggy/pi/settings` | AWS (backend / dashboard) | Pi | เปลี่ยน setting จากหน้า Setting ของ dashboard |
| `beggy/pi/settings_result` | Pi | AWS (backend / dashboard) | ยืนยันว่า setting ไหนถูกใช้ / ถูกปฏิเสธ |

ทุก message เป็น JSON มี `type`, `unix_ms`, `device`

### รายละเอียด `beggy/pi/calendar`

Pi ส่งข้อความ 5 แบบ:

| `type` | ส่งเมื่อ | ข้อมูล |
|---|---|---|
| `calendar_upcoming` | ทุก 5 นาที | `events`: รายการนัดทั้งหมดใน 24 ชม. ข้างหน้า (ส่งทั้งรายการทุกครั้ง, ไม่มีนัด = `[]`) |
| `calendar_created` | ทันทีที่เบกกี้สร้างนัดจากคำสั่งเสียง | `id`, `title`, `start`, `end`, `all_day`, `location` |
| `calendar_updated` 🆕 | ทันทีที่เบกกี้เลื่อนเวลา/เปลี่ยนชื่อนัด (`id` เดิม) | ข้อมูลใหม่ของนัด: `id`, `title`, `start`, `end`, `all_day`, `location` → ถ้าแจ้งเตือนนัดนี้ไปแล้ว ให้ใช้เวลาใหม่ |
| `calendar_deleted` | ทันทีที่เบกกี้ลบนัดจากคำสั่งเสียง (หลังผู้ใช้ยืนยัน) | ข้อมูลของนัดที่ถูกลบ: `id`, `title`, `start`, `end`, `all_day`, `location` |
| `calendar_error` | ทุก 5 นาทีแทน `calendar_upcoming` ถ้าอ่านปฏิทินไม่ได้ | `reason`: `not_connected` (ยังไม่ login), `login_expired` (ครบ 7 วัน ต้อง login ใหม่), `api_error` (เน็ต/Google มีปัญหา) + `error` ข้อความ |

แต่ละ event: `{"id", "title", "start", "end", "all_day", "location"}` เวลาเป็น ISO 8601 `+07:00`

**ฝั่ง backend ต้องทำ:**
- เก็บรายการล่าสุดไว้
- แจ้งเตือนเมื่อนัดใกล้ถึง เช่นเหลือ ≤ 30 นาที
- **จำ `id` ที่แจ้งไปแล้ว** เพราะนัดเดิมจะมาซ้ำทุก 5 นาที
- ถ้าได้ `calendar_error` ที่ `reason = login_expired` ให้ dashboard ขึ้นเตือนให้ login ใหม่

## ⚙️ Settings บน Pi (สำหรับหน้า Setting ของ dashboard)

ส่ง JSON เฉพาะค่าที่จะเปลี่ยนไปที่ `beggy/pi/settings` เช่น `{"posture_bad_after_s": 5, "tts_voice": "th-TH-NiwatNeural"}` แล้ว Pi จะตอบกลับที่ `beggy/pi/settings_result` ว่าค่าไหนใช้แล้ว (`applied`) และค่าไหนถูกปฏิเสธ (`ignored`) ค่าที่ตั้งจะถูกจำไว้ แม้รีสตาร์ทก็ไม่หาย

| setting | ค่าเริ่มต้น | ทำอะไร |
|---|---|---|
| `posture_enabled` | `true` | เปิด/ปิดการตรวจท่านั่ง |
| `posture_bad_after_s` | `10` | ท่าไม่ดีต่อเนื่องกี่วินาทีถึงเตือน |
| `posture_status_every_s` | `5` | ส่ง posture status ขึ้น AWS ทุกกี่วินาที |
| `head_auto_home_s` | `30` | หลังหันหาคน กลับท่าปกติ (ตำแหน่งตรวจท่านั่ง) ภายในกี่วินาที |
| `voice_enabled` | `true` | เปิด/ปิดระบบเสียง |
| `wakeword` | `"models/hey_beggy.joblib"` | คำปลุก "hey Beggy" (สำรอง: `"hey_jarvis"` คู่กับ threshold 0.4) ⚠️ มีผลหลังรีสตาร์ท service |
| `wake_threshold` | `0.98` | ความไวของคำปลุก (0–1, ต่ำ = ไวขึ้นแต่ปลุกผิดบ่อยขึ้น) |
| `tts_voice` | `"th-TH-PremwadeeNeural"` | เสียงพูด: หญิง / `"th-TH-NiwatNeural"` ชาย |
| `battery_every_s` | `30` | อ่านแบตทุกกี่วินาที |
| `battery_low_pct` | `20` | เตือนแบตต่ำครั้งแรกที่กี่ % |
| `battery_very_low_pct` | `10` | เตือนแบตต่ำครั้งที่สองที่กี่ % |

ค่าฮาร์ดแวร์ เช่น broker, กล้อง, servo, ลำโพง และ backend URL อยู่ในไฟล์ `raspi/.env` บน Pi ตั้งได้จากที่ Pi เท่านั้น เปลี่ยนจาก dashboard ไม่ได้

---

## 🔴 ต้องทำ (สำคัญ: มีผลต่อคะแนน)

- [x] **AWS IoT Core (broker):** Pi ต่อ AWS ได้แล้ว (26 พ.ย.)
- [ ] **ทีม backend: เพิ่ม `iot:RetainPublish` ใน policy ของ thing** ตอนนี้ AWS ตัดการเชื่อมต่อถ้า publish แบบ retain (รวม last will) Pi เลยปิด retain ไว้ก่อน (`MQTT_RETAIN=false`) ทำให้ `beggy/pi/status` ไม่ค้างค่าไว้ **ทีม ESP32 ก็จะโดนเหมือนกันถ้า publish `beggy/esp/env` แบบ retain**
- [ ] **ทีม backend: History API** ให้เบกกี้ตอบคำถามย้อนหลังได้ เช่น "วันนี้นั่งค่อมนานแค่ไหน" หรือ "ฝุ่นสัปดาห์นี้เป็นยังไง" Pi ทำฝั่งตัวเองเสร็จแล้ว รอ backend ทำ 2 endpoint ตามสเปกด้านบนของไฟล์ `raspi/beggy/history.py` เสร็จแล้วส่ง URL + API key ให้ฝั่ง Pi ใส่ `.env`
- [ ] **ทีม ESP32:** (1) ยืนยันชื่อ topic และ field JSON ตอนนี้ Pi รออ่าน `beggy/esp/env` เป็น `{"temperature_c", "humidity_pct", "light_lux", "dust_pm25"}` ถ้าใช้ชื่ออื่นบอกฝั่ง Pi (2) publish ด้วย `retain=true` Pi จะได้ค่าล่าสุดทันทีหลังรีสตาร์ท
- [ ] **วัดผล posture (เกณฑ์ข้อ 9, 2 คะแนน):** นั่งหน้ากล้อง calibrate แล้วรัน `eval_posture.py` ใช้เวลาประมาณ 3 นาที/รอบ ได้ตัวเลข + ภาพใส่สไลด์เลย

## 🟡 รอของ / ต้องมีคนทำ

- [ ] **กล้อง Pi Camera Module 3:** ใช้งานแล้ว (~7.6 fps, 26 พ.ย.) เหลือยึดให้ตรง (ตอนนี้เอียง ~30–40°) แล้ว calibrate ท่านั่งใหม่ (`calibration.json` ยังเป็นของ webcam ตัวเก่า)
- [x] **ลำโพง (MAX98357A):** ต่อสายแล้ว เสียงออก ตั้ง `AUDIO_OUT` แล้ว (26 พ.ย.)
- [x] **Servo:** ต่อสายแล้ว ใช้ hardware PWM (ไม่สั่น) (26 พ.ย.)
- [ ] **Servo pan เป็นแบบหมุน 360°** (สั่งเป็นความเร็ว ไม่ใช่มุม) โค้ดใช้วิธีหมุนตามเวลาแทน มุมเป็นค่าประมาณ ไม่แม่นเท่าแบบ 180° ถ้าหาซื้อ SG90 แบบ 180° ได้ เปลี่ยนแล้วตั้ง `pan_continuous = False` ใน `raspi/head/config.py` ได้เลย
- [ ] **ประกอบหัวเสร็จแล้ว:** หา limit / home / ทิศ (invert) ของ pan + tilt ด้วย `head_main.py`, หันหน้าตรงแล้วรัน `head_main.py pan-zero`
- [ ] **Capacitor 470–1000 µF ที่ราง 5V ของ servo** (ตอนนี้ไฟ servo มาจาก 5V header ของ Pi, ยังไม่ใส่) เช็ก `vcgencmd get_throttled` ต้องได้ `0x0`
- [x] **ทดสอบเสียงครบวงจร** STT → Gemini → ลำโพง ใช้ได้แล้ว (26 พ.ย.) ถอดเสียงถูก ~2 วิ, ตอบ ~6–8 วิ ถ้า Gemini ไม่ว่าง (503) จะสลับไปรุ่น lite ตัวอื่นเอง
- [x] **คุยกับเบกกี้ด้วยไมค์จริง** ใช้ได้แล้ว (26–27 พ.ย.): ถามเวลา, สร้าง/แก้/ลบนัด ตอบได้ ช้าสุดตอน Gemini ไม่ว่าง (503/504)
- [x] **Wake word "hey Beggy":** ใช้งานแล้ว (model v5 อยู่ใน git, threshold 0.98) ทดสอบสด: ปลุกติด 31/31 ครั้ง, ปลุกผิดเองประมาณ 1 ครั้ง/10 นาทีตอนมีคนคุย/เปิดทีวี
- [ ] **ให้ทุกคนในทีมอัดเสียง "hey Beggy"** คนละ 20–40 ครั้ง (ตอนนี้เทรนจากเสียงคนเดียว อาจไม่ติดกับเสียงคนอื่น/กรรมการ): หยุด service ก่อน `sudo systemctl stop beggy` แล้ว `wakeword_train.py record-pos` เสร็จแล้ว `sudo systemctl start beggy` จากนั้นฝั่ง Pi เทรนใหม่
- [ ] **ไมค์ USB ค้างเองประมาณทุก 15 นาที** (ไม่มี error จาก kernel) โค้ดเปิดใหม่ให้อัตโนมัติใน 2 วิแล้ว ถ้ามีไมค์ตัวอื่นลองเปลี่ยนดู

## 🔁 ต้องทำซ้ำเป็นระยะ

- [ ] **Login Google Calendar ใหม่ทุก 7 วัน** (แอปอยู่โหมด Testing, Google ตัด login อัตโนมัติ)
  - login ล่าสุด **23 พ.ย.** → หมดอายุประมาณ **30 พ.ย.**
  - **ต้อง login ใหม่ก่อนอัดวิดีโอ/พรีเซนต์**
  - วิธี: บนจอ Pi รัน `cd ~/iot-beggy-kbank/raspi && .venv/bin/python -m beggy.gcal login` แล้วกด Allow ใน browser
  - ถ้าลืม: เบกกี้จะบอกว่า login หมดอายุ และ dashboard จะได้ `calendar_error` ที่ `reason = login_expired` ส่วนอื่นของหุ่นยังทำงานปกติ

## 🎤 สไลด์ + วิดีโอ (7 คะแนน)

- [ ] Objectives + SDGs 2–3 ข้อ
- [ ] System architecture (ใช้ภาพ diagram + ตาราง MQTT ด้านบน)
- [ ] Input / Output
- [ ] Strengths / Limitations เช่น:
  - Gemini ฟรีจำกัด 500 ครั้ง/วัน
  - TTS ฟรีไม่เสถียร (มี offline fallback)
  - ต้องใช้เน็ต (ถอดเสียงสำรองด้วย Whisper offline ได้ แต่การตอบยังต้องใช้ Gemini)
  - wake word เทรนจากเสียงคนเดียว
  - posture ใช้ได้เฉพาะตำแหน่งหัวที่ calibrate
- [ ] เทียบกับสินค้าจริงในตลาด
- [ ] วิดีโอประมาณ 5 นาที: hardware + app + system

## ⚠️ หมายเหตุ

- **Gemini free tier:** ใช้แต่ **3.1 Flash Lite** (500 ครั้ง/วัน) เพราะ 3.6/3.8 Flash ได้แค่ 20 ครั้ง/วันและช้า ทดสอบอย่าเปลือง: คำถามเสียง 1 ครั้งใช้ 2 requests (+1 ต่อการเรียก tool แต่ละครั้ง)
- **DynamoDB:** เกณฑ์เขียนว่า "time-series database" ต้องอธิบายในสไลด์ว่าออกแบบ key เป็น time-series (partition ตาม device/metric, sort ตามเวลา)
- **หลังจบโปรเจกต์:** สร้าง Gemini API key ใหม่ (key ปัจจุบันเคยถูกส่งในแชท)
