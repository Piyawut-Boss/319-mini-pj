# Face Recognition Kiosk (Raspberry Pi)

หน้าที่: AI Face Recognition, จัดการฐานข้อมูลผู้ใช้, จอสแกนหน้าแบบ kiosk (portrait/landscape ปรับได้)
รันบน Raspberry Pi 5 + Camera Module 3

## โครงสร้าง

```
facerec/
  app.py                 GUI หลัก — หน้าสแกน + หน้าผู้ดูแลระบบ (Tkinter)
  capture_faces.py        เก็บรูปฝึกหน้าคนจาก command line
  train_model.py           เทรน LBPH recognizer จากรูปที่เก็บทั้งหมด
  recognize.py              รันจดจำใบหน้าจาก command line (ไม่มี GUI)
  haarcascade_frontalface_default.xml   ไฟล์ตรวจจับใบหน้า (จาก opencv GitHub)
facerec-app.desktop        shortcut เปิดแอปจาก desktop icon
```

## ฟีเจอร์หลัก (app.py)

- **หน้าจอ Standby**: นาฬิกา + วันที่ (พ.ศ.) เต็มจอ แตะเพื่อปลุก, auto กลับเองถ้าไม่มีการแตะ 20 วิ — เปิด/ปิดได้จากเมนูผู้ดูแลระบบ
- **หน้าสแกน**: กล้องสดเต็มจอ, กรอบตรวจจับใบหน้าสีเขียว (จำได้) / สีแดง (stranger/จำไม่ได้)
- **เมนูผู้ดูแลระบบ**: ต้องใส่รหัสผ่านก่อนถึงจะเพิ่ม/ลบผู้ใช้และดูรายชื่อได้ (ปุ่ม "⚙" มุมขวาบนของหน้าสแกน)
- **ลงทะเบียนผู้ใช้**: ชื่อ, User ID (auto-increment), สิทธิ์ (ผู้ใช้ทั่วไป/ผู้ดูแลระบบ), ถ่ายรูปหน้า 20 รูป, ช่อง RFID (กรอกเองชั่วคราว — ยังไม่ได้ต่อเครื่องอ่านจริง), เทรนโมเดลอัตโนมัติหลังบันทึก
- **on-screen keyboard**: เด้งขึ้นเมื่อแตะช่องกรอกข้อมูล (ใช้กับจอทัชสกรีนที่ไม่มีคีย์บอร์ดจริง)

## รันแอป

```bash
python3 -m venv venv --system-site-packages
source venv/bin/activate
pip install opencv-contrib-python-headless
sudo apt install python3-pil.imagetk

DISPLAY=:0 venv/bin/python3 app.py
```

รหัสผ่านผู้ดูแลระบบเริ่มต้น: `1234` (เก็บ hash ไว้ที่ `admin.json` ซึ่งไม่ถูกเก็บเข้า git — ดู `.gitignore`)

## ยังไม่ได้ทำ

- ยังไม่ได้เชื่อม RFID reader จริงเข้ากับ Pi (ดูฝั่ง Arduino ที่ branch `arduino` — มีสเก็ตช์ RFID+relay แยกทำงานอิสระอยู่แล้ว รอเชื่อม protocol)
- ยังไม่มี Grant/Deny flow ที่สั่งเปิดประตูจริงผ่าน serial ไปหา Arduino
- Web Dashboard / Access Logs
