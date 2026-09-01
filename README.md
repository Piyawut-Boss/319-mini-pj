# Door Access System

ระบบควบคุมการเข้าออกหน้าร้าน แบ่งหน้าที่ 2 บอร์ด:

## สถาปัตยกรรม

**Raspberry Pi 5 — "The Brain": AI & Logic Management**
- Face Recognition (OpenCV LBPH), จอสแกนหน้าแบบ kiosk, จัดการฐานข้อมูลผู้ใช้
- อุปกรณ์: Camera Module 3

**Arduino Uno R3 — "The Guard": Safety & Door Controller**
- ระบบความปลอดภัยหน้าประตู real-time แยกอิสระจาก Pi (Local Failover — เปิดประตูด้วยบัตร
  ที่อนุญาตได้แม้ Pi reboot/crash)
- อุปกรณ์: RFID Reader (RC522), Relay ควบคุมล็อกประตู
- เชื่อมกับ Pi ผ่านสาย USB (serial)

## โครงสร้างโฟลเดอร์

```
facerec/                 รันบน Pi 5 ที่ /home/<user>/facerec/
  app.py                   GUI หลัก — หน้าสแกน + หน้าผู้ดูแลระบบ (Tkinter)
  capture_faces.py         เก็บรูปฝึกหน้าคนจาก command line
  train_model.py           เทรน LBPH recognizer จากรูปที่เก็บทั้งหมด
  recognize.py             รันจดจำใบหน้าจาก command line (ไม่มี GUI)
  haarcascade_frontalface_default.xml
  .gitignore               กัน people.json/dataset/รูปหน้าคน/รหัสผ่านหลุดขึ้น git
facerec-app.desktop       shortcut เปิดแอปจาก desktop icon บน Pi

arduino/
  blink_serial/            สเก็ตช์ทดสอบ serial link (1=ไฟติด, 0=ไฟดับ, b=กระพริบ 5x5)
  rfid_door_lock/           อ่าน RFID (RC522) เทียบ UID ที่อนุญาต แล้วสั่ง relay ปลดล็อก
                            (ทำงานอิสระ ยังไม่เชื่อม protocol กับ Pi)
```

## ฟีเจอร์หลักของ app.py (Pi)

- หน้าจอ Standby (นาฬิกา+วันที่ พ.ศ., แตะปลุก, auto กลับเองถ้าไม่มีการแตะ — เปิด/ปิดได้ในเมนู admin)
- หน้าสแกนเต็มจอ กรอบตรวจจับสีเขียว(จำได้)/แดง(stranger)
- เมนูผู้ดูแลระบบต้องใส่รหัสผ่านก่อนถึงเพิ่ม/ลบ/ดูรายชื่อผู้ใช้ได้
- ลงทะเบียนผู้ใช้: ชื่อ, User ID auto, สิทธิ์, ถ่ายรูป 20 รูป, ช่อง RFID (manual ชั่วคราว), เทรนโมเดลอัตโนมัติ
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
| RC522 RST | 9 |
| RC522 SS/SDA | 10 |
| RC522 SPI (MOSI/MISO/SCK) | 11/12/13 |
| Relay control | 8 (Active LOW) |

แก้รายชื่อ UID บัตรที่อนุญาตได้ที่ array `authorizedUIDs` — เปิด Serial Monitor (9600 baud)
แล้วสแกนบัตรจริงเพื่อดู UID ก่อนเพิ่ม/ลบ

## ยังไม่ได้ทำ

- Serial protocol จริงระหว่าง Pi กับ Arduino (คำสั่ง OPEN/DENY จาก Pi + event จาก Arduino)
- Grant/Deny flow ที่สั่งเปิดประตูจริง (เสียง, เครื่องหมายถูก/กากบาท)
- Door Sensor (Reed Switch), Exit Button, Buzzer/LED, flyback diode + optocoupler relay ตามสเปคเต็ม
- Web Dashboard, Access Logs
