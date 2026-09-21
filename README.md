# Door Access System

ระบบควบคุมการเข้าออกหน้าร้าน/แล็บ ด้วยการจดจำใบหน้า + บัตร RFID แบ่งงานเป็น 2 บอร์ด
ทำงานคนละหน้าที่แต่เชื่อมกันผ่าน serial:

- **Raspberry Pi 5** — จดจำใบหน้า, จอสแกนแบบ kiosk, จัดการฐานข้อมูลผู้ใช้/บัตร
- **Arduino (Nano โคลนชิป CH340)** — อ่าน RFID, คุม relay ล็อกประตูจริง, ทำงานอิสระได้แม้ Pi ดับ

## สารบัญ

- [สถาปัตยกรรม](#สถาปัตยกรรม)
- [โครงสร้างโฟลเดอร์](#โครงสร้างโฟลเดอร์)
- [วิธีติดตั้ง/รันบน Pi](#วิธีติดตั้งรันบน-pi)
- [ฟีเจอร์ของ app.py](#ฟีเจอร์ของ-apppy)
- [รูปแบบข้อมูล](#รูปแบบข้อมูล)
- [ความปลอดภัย](#ความปลอดภัย)
- [Arduino — rfid_door_lock.ino](#arduino--rfid_door_lockino)
- [ยังไม่ได้ทำ](#ยังไม่ได้ทำ)

## สถาปัตยกรรม

**Raspberry Pi 5 — "The Brain": AI & Logic Management**
- Face Recognition สองขั้นตอน: **YOLO26n** (เทรนเองจาก WIDER FACE dataset) หาตำแหน่งหน้าในภาพ
  → **OpenCV SFace** (official model จาก OpenCV Zoo) แปลงเป็น embedding แล้วเทียบว่าเป็นใคร
  (รายละเอียดเหตุผลที่ย้ายมาจาก Haar Cascade + LBPH เดิม อยู่ที่
  [`facerec/MIGRATION_PLAN.md`](facerec/MIGRATION_PLAN.md))
- จอสแกนเต็มจอแบบ kiosk (Tkinter), หน้าผู้ดูแลระบบแยกจากหน้าสแกน
- อุปกรณ์: Camera Module 3, จอสัมผัส HDMI 1024x600

**Arduino (Nano โคลนชิป CH340) — "The Guard": Safety & Door Controller**
- ระบบความปลอดภัยหน้าประตู real-time แยกอิสระจาก Pi (**Local Failover** — เปิดประตูด้วยบัตร
  ที่อนุญาตได้แม้ Pi reboot/crash/ปิดเครื่อง)
- เก็บรายชื่อ UID ที่อนุญาตไว้ใน **EEPROM ของตัวเอง** ไม่พึ่ง Pi ตอนตัดสินใจปลดล็อก
- อุปกรณ์: RFID Reader (RC522), Relay ควบคุมล็อกประตู, ปุ่มกายภาพ REG/SET/EXIT
- เชื่อมกับ Pi ผ่านสาย USB (serial, 9600 baud) — Pi ↔ Arduino คุยกันด้วยโปรโตคอลข้อความ
  บรรทัดต่อบรรทัด (เช่น `REGISTER`, `ADD:<uid>`, `OPEN`) โปรโตคอลเต็มทุกคำสั่ง/ทุกข้อความตอบกลับ
  อยู่ที่ [`facerec/RFID_ARDUINO_SYNC.md`](facerec/RFID_ARDUINO_SYNC.md)
- Flash firmware ได้ตรงจาก Pi เลยด้วย `arduino-cli` (ติดตั้งไว้ที่ `/home/jocasta/bin/arduino-cli`
  บนตัว Pi จริง) ไม่ต้องใช้ Arduino IDE เครื่องแยก — คำสั่งเต็มอยู่ในเอกสารเดียวกัน

### Flow การทำงานหลัก

```
กล้อง → YOLO26n หาตำแหน่งหน้า → SFace แปลงเป็น embedding → เทียบกับฐานข้อมูล
  ├─ จำได้ (กรอบเขียว) + อยู่หน้าสแกน → Pi ส่ง "OPEN" ไป Arduino → ปลดล็อกประตู 3 วิ
  └─ จำไม่ได้ (กรอบแดง / "stranger") → ไม่ทำอะไร

แตะบัตร RFID → Arduino เช็ค UID ใน EEPROM เอง (ไม่ต้องถาม Pi) → ปลดล็อกถ้าอนุญาต
  (ยกเว้นตอน Pi อยู่หน้า admin — ส่ง ADMIN_LOCK ไปกันไว้ก่อน)

ปุ่ม EXIT ที่ตัว Arduino → ปลดล็อกเสมอ ไม่ขึ้นกับ Pi/โหมดไหนทั้งสิ้น (ทางหนีไฟ)
```

## โครงสร้างโฟลเดอร์

```
facerec/                       รันบน Pi 5 ที่ /home/<user>/facerec/
  app.py                         GUI หลัก (Tkinter) — หน้าสแกน, หน้ารายชื่อผู้ใช้,
                                  หน้าเพิ่ม/แก้ไขผู้ใช้, หน้า standby
  face_engine.py                 FaceDetector (YOLO26n ผ่าน cv2.dnn) + FaceIdentifier (SFace)
                                  ใช้ร่วมกันทุกสคริปต์ในโฟลเดอร์นี้
  capture_faces.py               เก็บรูปฝึกหน้าคนจาก command line (headless, ไม่ใช้ GUI)
  train_model.py                 สร้าง/rebuild embeddings.json จากรูปทั้งหมดใน dataset/ (SFace)
  recognize.py                   รันจดจำใบหน้าจาก command line แบบ live (ไม่มี GUI)
  models/
    yolo26n_face.onnx              ตัวตรวจจับหน้า เทรนเองจาก WIDER FACE (ดู MIGRATION_PLAN.md)
    face_recognition_sface_2021dec.onnx   ตัวจดจำใบหน้า official จาก OpenCV Zoo
  setup/
    99-arduino-rfid.rules          udev rule ผูก /dev/arduino_rfid ให้คงที่ไม่ว่า Arduino
                                    จะ enumerate เป็น ttyACM*/ttyUSB* เลขอะไร
    README.md                      วิธีติดตั้ง udev rule + วิธีอัปเดตตอนเปลี่ยนบอร์ด
  RFID_ARDUINO_SYNC.md           โปรโตคอล serial เต็ม, การจัดการบัตรทีละใบ, วิธี flash Arduino
  MIGRATION_PLAN.md              แผน/เหตุผลการย้าย Haar+LBPH → YOLO26n+SFace
  .gitignore                     กัน people.json/dataset/รูปหน้าคน/embeddings.json/admin.json
                                  หลุดขึ้น git (ข้อมูลส่วนตัว/ไบโอเมตริกซ์ของผู้ใช้จริง)
facerec-app.desktop            shortcut เปิดแอปจาก desktop icon บน Pi

arduino/
  blink_serial/                  สเก็ตช์ทดสอบ serial link (1=ไฟติด, 0=ไฟดับ, b=กระพริบ 5x5)
  rfid_door_lock/
    rfid_door_lock.ino             เฟิร์มแวร์เต็ม เชื่อม protocol กับ Pi ครบ (ดู RFID_ARDUINO_SYNC.md)
```

ไฟล์ที่**ไม่ถูก commit** (gitignore ไว้เพราะเป็นข้อมูลส่วนตัว/รันไทม์ของเครื่องจริง):
`people.json` (รายชื่อ+ข้อมูลผู้ใช้), `dataset/` (รูปหน้าคนจริง), `embeddings.json`
(biometric embeddings), `admin.json` (password hash), `settings.json`

## วิธีติดตั้ง/รันบน Pi

```bash
cd facerec
python3 -m venv venv --system-site-packages
source venv/bin/activate
pip install opencv-contrib-python-headless
sudo apt install python3-pil.imagetk

DISPLAY=:0 venv/bin/python3 app.py
```

ต้องมี `--system-site-packages` เพราะ `picamera2` ผูกกับ libcamera ของระบบ ติดตั้งผ่าน pip
ธรรมดาในเวอร์ชันที่ตรงกันไม่ได้

**udev rule สำหรับ Arduino** (ทำครั้งเดียว ก่อนรัน `app.py` ครั้งแรก) — ดูขั้นตอนที่
[`facerec/setup/README.md`](facerec/setup/README.md)

**รหัสผ่าน admin เริ่มต้น:** `1234` (hash เก็บที่ `admin.json` ซึ่งไม่ถูก commit — เปลี่ยนได้โดย
ลบไฟล์นี้แล้วรอให้ app.py สร้างใหม่ด้วยรหัสผ่านที่ตั้งใน `DEFAULT_ADMIN_PASSWORD` ใน `app.py`)

**เปิดแอปอัตโนมัติตอนเปิดเครื่อง:** ใช้ `facerec-app.desktop` (shortcut icon บน desktop ของ Pi)
ปัจจุบันยังต้องกดเปิดเองผ่าน icon นี้ ยังไม่มี systemd service/autostart อัตโนมัติเต็มรูปแบบ

## ฟีเจอร์ของ app.py

**หน้าสแกน (เต็มจอ)**
- ภาพสดจากกล้อง พร้อมกรอบตรวจจับหน้า — เขียว = จำได้ (พร้อมชื่อ), แดง = "stranger"
- จำหน้าได้ที่หน้านี้ **ปลดล็อกประตูอัตโนมัติทันที** (ส่ง `OPEN` ไป Arduino) มี cooldown 5 วิ
  กันสั่งปลดล็อกซ้ำถี่ๆตอนคนยืนอยู่หน้ากล้องนิ่งๆ — ทำงานเฉพาะหน้านี้เท่านั้น ไม่ทำงานตอนอยู่
  หน้า admin แม้ตัวจดจำใบหน้าจะยังคำนวณอยู่เบื้องหลังก็ตาม
- ปุ่มเฟือง (มุมขวาบน) เปิด dialog ใส่รหัสผ่าน admin

**หน้าจอ Standby**
- นาฬิกา + วันที่ พ.ศ., แตะหน้าจอเพื่อปลุก, กลับเองอัตโนมัติถ้าไม่มีการแตะ (20 วิ)
- เปิด/ปิดได้จากเมนู admin (ปิดได้ถ้าไม่ต้องการให้จอ standby เลย)

**หน้ารายชื่อผู้ใช้ (admin, ต้องใส่รหัสผ่านก่อน)**
- ลิสต์ผู้ใช้ทั้งหมด พร้อม User ID/สิทธิ์/บัตร RFID/จำนวนรูปที่ถ่าย — แถบสีสลับอ่านง่าย
- กดที่รายชื่อ → ไปหน้าแก้ไขผู้ใช้คนนั้น
- ปุ่ม "➕ เพิ่มผู้ใช้ใหม่" → ไปหน้าฟอร์มเปล่า
- ปุ่ม "ลบผู้ใช้ที่เลือก" → ลบทั้งคน + สั่งลบบัตรของคนนั้นออกจาก Arduino ด้วย
- ควบคุมประตู: "เปิดประตูตอนนี้" (ชั่วคราว 3 วิ), "เปิดประตูค้าง" (toggle ไม่ล็อกอัตโนมัติ
  จนกว่าจะกดปิด)
- ตั้งค่าระบบ: เปิด/ปิด standby, ปุ่ม "รีเซ็ตและซิงค์บัตร RFID กับ Arduino" (ล้าง EEPROM
  ทั้งหมดแล้วเขียนใหม่จาก `people.json` เท่านั้น — กันบัตรกำพร้าที่ไม่มีเจ้าของค้างอยู่)

**หน้าเพิ่ม/แก้ไขผู้ใช้**
- กรอกชื่อ, User ID (auto), สิทธิ์ (ผู้ใช้ทั่วไป/ผู้ดูแลระบบ)
- ถ่ายรูปหน้า 10 รูป (ใช้เทรน embedding ของคนนั้นโดยเฉพาะ ไม่กระทบคนอื่น)
- แตะบัตร RFID ที่เครื่องอ่านเพื่อลงทะเบียน — จัดการได้ทีละใบ มีปุ่ม ✕ ลบบัตรแต่ละใบออกจาก
  รายการได้ก่อนบันทึกจริง
- กด "บันทึกผู้ใช้" → sync บัตรไป Arduino อัตโนมัติ (`ADD` ใบที่เพิ่ม, `REMOVE` ใบที่เอาออก)
  และคำนวณ embedding ใหม่เฉพาะคนนี้เท่านั้น (ไม่ retrain ทุกคนทุกครั้ง)
- ปุ่ม "← กลับ" ไม่บันทึกอะไร กลับไปหน้ารายชื่อเฉยๆ

**อื่นๆ**
- on-screen keyboard (คีย์บอร์ดจำลอง) สำหรับกรอกข้อความบนจอสัมผัสที่ไม่มีคีย์บอร์ดจริง
- FrameGrabber แยกเธรดดึงเฟรมกล้อง กัน UI ค้างถ้า `Picamera2` เกิด deadlock ภายใน
- ArduinoLink auto-reconnect ถ้าสาย USB หลุด/บอร์ด USB re-enumerate

## รูปแบบข้อมูล

ความสัมพันธ์ "ชื่อคน ↔ บัตร RFID" **อยู่บน Pi ฝ่ายเดียว** ผ่าน `people.json`:

```json
{
  "boss": {
    "user_id": "0002",
    "role": "ผู้ใช้ทั่วไป",
    "rfid": ["A7565B06", "3F2C1B90"],
    "face_count": 10,
    "registered_at": "2026-09-01T16:17:52"
  }
}
```

ฝั่ง **Arduino ไม่รู้จักชื่อคนเลย** — EEPROM เก็บแค่รายการ UID ดิบๆ (byte 0 = จำนวนบัตร ตามด้วย
UID ทีละ 4 byte) ทำหน้าที่แค่ตอบว่า UID นี้ "เข้าได้"/"เข้าไม่ได้" เท่านั้น เชื่อมกับ Pi ด้วย UID
string ที่ต้องตรงกันทุกตัวอักษร (พิมพ์ใหญ่ 8 หลัก hex) — รายละเอียดเต็มอยู่ที่
[`facerec/RFID_ARDUINO_SYNC.md`](facerec/RFID_ARDUINO_SYNC.md)

## ความปลอดภัย

- เมนู admin ต้องใส่รหัสผ่านก่อนเสมอ (hash เก็บแยกไฟล์ ไม่ commit ขึ้น git)
- ระหว่างอยู่หน้า admin ใดๆ (Pi ส่ง `ADMIN_LOCK`) **การปลดล็อกด้วยบัตร RFID จะถูกปิดไว้ชั่วคราว**
  กันไม่ให้มีใครแตะบัตรเข้ามาตอนแอดมินกำลังยุ่งกับหน้าจออยู่ — กลับหน้าสแกนแล้วจะเปิดกลับอัตโนมัติ
- **ปุ่ม EXIT ที่ตัว Arduino ไม่ถูกล็อกด้วยกลไกไหนทั้งสิ้น ทำงานได้เสมอ** ไม่ว่า Pi จะอยู่โหมด
  ไหนหรือแม้ Pi จะดับไปเลยก็ตาม — ออกแบบไว้เพื่อความปลอดภัย/ทางหนีไฟโดยเฉพาะ
- โมเดลตรวจจับหน้าเทรนเองจาก dataset ที่ตรวจสอบแหล่งที่มาได้ (WIDER FACE, official) แทนที่จะ
  ใช้ไฟล์โมเดลสำเร็จรูปจาก repo บุคคลที่สามที่ตรวจสอบไม่ได้ — ดูเหตุผลเต็มใน MIGRATION_PLAN.md

## Arduino — rfid_door_lock.ino

| อุปกรณ์ | ขา Arduino |
|---|---|
| RC522 RST | 5 |
| RC522 SS/SDA | 10 |
| RC522 SPI (MOSI/MISO/SCK) | 11/12/13 |
| Relay control | A5 (Active LOW) |
| ปุ่ม EXIT (สัญญาณ/กราวด์จำลอง) | 3 / 2 |
| ปุ่ม REG — กดค้าง 5 วิ = factory reset ฉุกเฉิน (สัญญาณ/กราวด์จำลอง) | 4 / 6 |
| ปุ่ม SET — กลับโหมด IDLE (สัญญาณ/กราวด์จำลอง) | 9 / 8 |

บัตรที่อนุญาตเก็บใน **EEPROM** ไม่ใช่ array ในโค้ดอีกต่อไป — เพิ่ม/ลบบัตรทำผ่านหน้า admin บน
Pi เท่านั้น (ดูโปรโตคอลเต็มที่ [`facerec/RFID_ARDUINO_SYNC.md`](facerec/RFID_ARDUINO_SYNC.md))
กดปุ่ม REG ค้าง 5 วิ ใช้กู้คืนฉุกเฉินกลับเป็นบัตร default เท่านั้น ไม่ใช่ทางเข้าโหมดลงทะเบียนปกติ
(ลงทะเบียนบัตรใหม่ต้องผ่าน Pi เสมอ กัน EEPROM กับ `people.json` ไม่ตรงกัน)

**Flash firmware จาก Pi โดยตรง** (ไม่ต้องใช้ Arduino IDE):

```bash
pkill -f 'venv/bin/python3.*app.py'   # ปล่อย serial port ก่อน
/home/jocasta/bin/arduino-cli compile --fqbn arduino:avr:nano:cpu=atmega328 arduino/rfid_door_lock
/home/jocasta/bin/arduino-cli upload -p /dev/ttyUSB0 --fqbn arduino:avr:nano:cpu=atmega328 arduino/rfid_door_lock
```

## ยังไม่ได้ทำ

- Door Sensor (Reed Switch), Buzzer/LED, flyback diode + optocoupler relay ตามสเปคเต็ม
- Web Dashboard, Access Logs (ประวัติการเข้าออก)
- systemd service สำหรับ autostart `app.py` ตอนเปิดเครื่อง (ตอนนี้ยังต้องเปิดเองผ่าน desktop icon)
- Query สถานะ "เปิดประตูค้างอยู่ไหม" กลับจาก Arduino — ถ้า `app.py` restart ระหว่างเปิดค้างอยู่
  ปุ่มในหน้า admin จะแสดงผิดจนกว่าจะกดใหม่อีกที
