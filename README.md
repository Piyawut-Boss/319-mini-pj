# Door Access System

ระบบควบคุมการเข้าออกหน้าร้าน แบ่งหน้าที่ 2 บอร์ด:

## สถาปัตยกรรม

**Raspberry Pi 5 — "The Brain": AI & Logic Management**
- Face Recognition (YOLO26n เทรนเองสำหรับตรวจจับหน้า + OpenCV SFace สำหรับจดจำตัวตน), จอสแกนหน้าแบบ kiosk, จัดการฐานข้อมูลผู้ใช้
- อุปกรณ์: Camera Module 3

**Arduino (Nano โคลนชิป CH340) — "The Guard": Safety & Door Controller**
- ระบบความปลอดภัยหน้าประตู real-time แยกอิสระจาก Pi (Local Failover — เปิดประตูด้วยบัตร
  ที่อนุญาตได้แม้ Pi reboot/crash) เก็บรายชื่อ UID ที่อนุญาตไว้ใน EEPROM ของตัวเอง
- อุปกรณ์: RFID Reader (RC522), Relay ควบคุมล็อกประตู, ปุ่ม REG/SET/EXIT
- เชื่อมกับ Pi ผ่านสาย USB (serial, 9600 baud) — โปรโตคอลคำสั่งเต็มดูที่
  [`facerec/RFID_ARDUINO_SYNC.md`](facerec/RFID_ARDUINO_SYNC.md)
- Flash firmware ได้ตรงจาก Pi เลยด้วย `arduino-cli` ไม่ต้องใช้ Arduino IDE
  (วิธีทำอยู่ในเอกสารเดียวกัน)

## โครงสร้างโฟลเดอร์

```
facerec/                 รันบน Pi 5 ที่ /home/<user>/facerec/
  app.py                   GUI หลัก — หน้าสแกน + หน้าผู้ดูแลระบบ (Tkinter)
  face_engine.py           ตัวตรวจจับ (YOLO26n) + ตัวจดจำใบหน้า (SFace) ใช้ร่วมกันทุกสคริปต์
  capture_faces.py         เก็บรูปฝึกหน้าคนจาก command line
  train_model.py           สร้าง embeddings.json จากรูปที่เก็บทั้งหมด (SFace)
  recognize.py             รันจดจำใบหน้าจาก command line (ไม่มี GUI)
  models/                  yolo26n_face.onnx (เทรนเองจาก WIDER FACE), face_recognition_sface_2021dec.onnx (official OpenCV Zoo)
  RFID_ARDUINO_SYNC.md     โปรโตคอล serial เต็ม + วิธี flash Arduino จาก Pi
  .gitignore               กัน people.json/dataset/รูปหน้าคน/embeddings.json/รหัสผ่านหลุดขึ้น git
facerec-app.desktop       shortcut เปิดแอปจาก desktop icon บน Pi

arduino/
  blink_serial/            สเก็ตช์ทดสอบ serial link (1=ไฟติด, 0=ไฟดับ, b=กระพริบ 5x5)
  rfid_door_lock/           อ่าน RFID (RC522) เทียบ UID ที่อนุญาต แล้วสั่ง relay ปลดล็อก
                            เชื่อม protocol เต็มกับ Pi แล้ว (ดู RFID_ARDUINO_SYNC.md)
```

## ฟีเจอร์หลักของ app.py (Pi)

- หน้าจอ Standby (นาฬิกา+วันที่ พ.ศ., แตะปลุก, auto กลับเองถ้าไม่มีการแตะ — เปิด/ปิดได้ในเมนู admin)
- หน้าสแกนเต็มจอ กรอบตรวจจับสีเขียว(จำได้)/แดง(stranger)
- เมนูผู้ดูแลระบบต้องใส่รหัสผ่านก่อนถึงเพิ่ม/ลบ/ดูรายชื่อผู้ใช้ได้
- ลงทะเบียนผู้ใช้: ชื่อ, User ID auto, สิทธิ์, ถ่ายรูป 10 รูป, แตะบัตร RFID ที่เครื่องอ่านได้เลย
  (จัดการทีละใบ เพิ่ม/ลบแยกแต่ละใบได้ในหน้าแก้ไขผู้ใช้), sync ไป Arduino อัตโนมัติตอนบันทึก
- หน้ารายชื่อผู้ใช้แยกจากหน้าเพิ่ม/แก้ไขผู้ใช้ + ปุ่มรีเซ็ตและซิงค์บัตรทั้งหมดกับ Arduino
- on-screen keyboard สำหรับจอทัชสกรีน

รันแอป:
```bash
python3 -m venv venv --system-site-packages
source venv/bin/activate
pip install opencv-contrib-python-headless
sudo apt install python3-pil.imagetk

DISPLAY=:0 venv/bin/python3 app.py
```
รหัสผ่าน admin เริ่มต้น: `1234` (hash เก็บที่ `admin.json` ซึ่งไม่ถูก commit)

## rfid_door_lock.ino — Pin

| อุปกรณ์ | ขา Arduino |
|---|---|
| RC522 RST | 5 |
| RC522 SS/SDA | 10 |
| RC522 SPI (MOSI/MISO/SCK) | 11/12/13 |
| Relay control | A5 (Active LOW) |
| ปุ่ม EXIT (สัญญาณ/กราวด์จำลอง) | 3 / 2 |
| ปุ่ม REG — กดค้าง 5 วิ = factory reset ฉุกเฉิน (สัญญาณ/กราวด์จำลอง) | 4 / 6 |
| ปุ่ม SET — กลับโหมด IDLE (สัญญาณ/กราวด์จำลอง) | 9 / 8 |

บัตรที่อนุญาตเก็บใน EEPROM ไม่ใช่ array ในโค้ดอีกต่อไป — เพิ่ม/ลบบัตรทำผ่านหน้า admin
บน Pi เท่านั้น (ดูโปรโตคอลเต็มที่ [`facerec/RFID_ARDUINO_SYNC.md`](facerec/RFID_ARDUINO_SYNC.md))
กดปุ่ม REG ค้าง 5 วิ ใช้กู้คืนฉุกเฉินกลับเป็นบัตร default เท่านั้น ไม่ใช่ทางเข้าโหมดลงทะเบียนปกติ

## ยังไม่ได้ทำ

- Door Sensor (Reed Switch), Buzzer/LED, flyback diode + optocoupler relay ตามสเปคเต็ม
- Web Dashboard, Access Logs
