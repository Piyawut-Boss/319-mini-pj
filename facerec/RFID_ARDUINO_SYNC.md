# RFID Card Management & Pi↔Arduino Sync

เอกสารนี้อธิบายว่าการ์ด RFID ถูกเก็บ/จัดการ/ซิงค์ระหว่าง Pi (`app.py`) กับ Arduino
(`arduino/rfid_door_lock/rfid_door_lock.ino`) อย่างไร หลังจากเพิ่มฟีเจอร์จัดการบัตร
แบบละเอียด (เพิ่ม/ลบ/แก้ไขทีละใบ, auto-sync ตอน save, reset-and-sync ทั้งชุด)

## ใครเก็บอะไร

| | เก็บอะไร | รู้จักเจ้าของบัตรไหม |
|---|---|---|
| **Pi** (`people.json`) | ชื่อ → `{user_id, role, rfid: [uid, ...], face_count, registered_at}` | ✅ รู้ — เป็นแหล่งความจริงเดียวเรื่องเจ้าของบัตร |
| **Arduino** (EEPROM) | รายการ UID ดิบๆ (byte 0 = จำนวนบัตร, ตามด้วย UID ทีละ 4 byte) | ❌ ไม่รู้ — แค่บอกได้ว่า UID นี้ "เข้าได้" หรือ "เข้าไม่ได้" |

Arduino ทำหน้าที่เป็นแค่ "ยาม" ตรวจ UID เท่านั้น ส่วนความสัมพันธ์ ชื่อ↔บัตร ทั้งหมด
อยู่บน Pi ฝ่ายเดียว เชื่อมกันด้วย UID string (ตัวพิมพ์ใหญ่ 8 หลัก hex) ที่ต้องตรงกัน
ทุกตัวอักษรระหว่างสองฝั่ง

## โปรโตคอล Serial (9600 baud)

### คำสั่งจาก Pi → Arduino

| คำสั่ง | ทำอะไร | ขึ้นกับ currentMode ไหม |
|---|---|---|
| `REGISTER` | เข้าโหมดลงทะเบียน รอแตะบัตรจริงที่เครื่องอ่าน | เปลี่ยนเป็น `MODE_REGISTER` |
| `IDLE` | กลับโหมดปกติ (สแกนบัตรเพื่อปลดล็อกได้) | เปลี่ยนเป็น `MODE_IDLE` |
| `ADD:<uid>` | เขียน UID ลง EEPROM ตรงๆ **ไม่ต้องแตะบัตรจริง** | ไม่ขึ้นกับ mode — ใช้ได้ตลอด |
| `REMOVE:<uid>` | ลบ UID ออกจาก EEPROM | ไม่ขึ้นกับ mode |
| `CLEAR` | ล้างบัตรทั้งหมดออกจาก EEPROM จริงๆ (count = 0) | ไม่ขึ้นกับ mode, รีเซ็ตเป็น `MODE_IDLE` |
| `ADMIN_LOCK` | ปิดการปลดล็อกด้วยบัตร (ปุ่ม EXIT ยังทำงานปกติ) | ไม่ขึ้นกับ mode |
| `ADMIN_UNLOCK` | เปิดการปลดล็อกด้วยบัตรกลับมาปกติ | ไม่ขึ้นกับ mode |
| `OPEN` | เปิดประตูชั่วคราว (เหมือนแตะบัตร/กด EXIT) จากปุ่ม admin | ไม่ขึ้นกับ mode/ADMIN_LOCK |
| `HOLD_ON` | เปิดประตูค้างไว้ ไม่ล็อกอัตโนมัติจนกว่าจะสั่ง `HOLD_OFF` | ไม่ขึ้นกับ mode/ADMIN_LOCK |
| `HOLD_OFF` | ปล่อยประตูค้าง กลับสู่การล็อกปกติ | ไม่ขึ้นกับ mode/ADMIN_LOCK |

### ข้อความตอบจาก Arduino → Pi

| ข้อความ | ความหมาย |
|---|---|
| `REGISTERED:<uid>:<total>` | บันทึกบัตรสำเร็จ (จากการแตะจริงตอน REGISTER หรือจาก `ADD`) |
| `DUPLICATE:<uid>:<total>` | บัตรนี้มีอยู่ใน EEPROM แล้ว |
| `FULL:<total>` / `FULL` | EEPROM เต็ม (MAX_CARDS = 20) |
| `REMOVED:<uid>:<total>` | ลบบัตรสำเร็จ |
| `NOTFOUND:<uid>` | ไม่พบบัตรนี้ตอนสั่งลบ |
| `CLEARED:0` | ล้างบัตรทั้งหมดสำเร็จ |

`app.py`'s `_poll_arduino()` แยกการจัดการข้อความเหล่านี้เป็น 3 กลุ่มอิสระต่อกัน
(ไม่ให้ผลลัพธ์ปนกัน):
1. `REMOVED`/`NOTFOUND` — จัดการได้ตลอดเวลา ไม่ว่าจะอยู่ flow ไหน
2. `syncing_cards` flow (ปุ่ม reset-and-sync) — คุม `_sync_queue`/`_sync_progress` ของตัวเอง
3. `registering_rfid` flow (ลงทะเบียนบัตรเดี่ยวจากหน้าแก้ไขผู้ใช้) — คุม `pending_rfids` ของฟอร์มที่เปิดอยู่

## หน้าจอ Admin — จัดการบัตรทีละใบ

หน้าแก้ไขผู้ใช้ (`_show_admin_form`) แสดงบัตรของคนนั้นเป็นแถวแยกทีละใบ
(`_refresh_pending_rfid_label`) แต่ละแถวมีปุ่ม **✕** กดลบบัตรใบนั้นออกจากรายการที่
กำลังแก้ไข (`_remove_pending_rfid`) — ยังไม่ส่งอะไรไป Arduino จนกว่าจะกด "บันทึกผู้ใช้"

### ตอนกด "บันทึกผู้ใช้" (`save_person`)

1. เทียบรายการบัตร**เก่า** (ก่อนแก้ไข) กับรายการ**ใหม่** (`pending_rfids` หลังกด ✕/เพิ่ม) —
   บัตรที่หายไปจะถูกสั่ง `REMOVE:<uid>` ไปที่ Arduino
2. บันทึก `people.json`
3. สั่ง `ADD:<uid>` ให้ทุกบัตรที่เหลืออยู่ใน `pending_rfids` ของคนนี้ — **อัตโนมัติ ไม่ต้อง
   กดปุ่มซิงค์แยก** (บัตรที่มีอยู่แล้วจาก flow แตะบัตรจะได้ `DUPLICATE` เฉยๆ ไม่มีผลเสีย)
4. คำนวณ embedding ใหม่**เฉพาะคนนี้** (`_update_person_embeddings`) และ**เฉพาะตอนที่ถ่าย
   รูปใหม่จริงในรอบนี้** (`self.captured_count > 0`) — ไม่แตะ embeddings ของคนอื่นเลย
   (เดิมเรียก `retrain()` ทำ full rebuild ทุกคนทุกครั้ง ตอนนี้เอาออกแล้วเพราะไม่มีจุดเรียกใช้เหลือ)

## ปุ่ม "🔄 รีเซ็ตและซิงค์บัตร RFID กับ Arduino" (หน้ารายชื่อผู้ใช้)

ใช้ตอน Arduino เปลี่ยนเครื่อง/EEPROM มีบัตรกำพร้า (ไม่มีเจ้าของใน `people.json`) หรือ
อยากให้แน่ใจว่า EEPROM ตรงกับ `people.json` เป๊ะๆ:

1. ยืนยันก่อน (destructive — ล้างของเดิมทั้งหมด)
2. ส่ง `CLEAR` → รอ Arduino ตอบ `CLEARED` ก่อนค่อยเริ่มขั้นต่อไป
3. ไล่ส่ง `ADD:<uid>` ให้ทุกบัตรที่มีเจ้าของใน `people.json` ทีละใบ (รอ confirm ก่อนส่งใบถัดไป)
4. จบแล้ว Arduino จะเหลือ**เฉพาะบัตรที่มีเจ้าของ** เท่านั้น ไม่มีบัตรกำพร้าหลงเหลือ

## ล็อกประตูระหว่างอยู่หน้า Admin

- Pi ส่ง `ADMIN_LOCK` ตอนเข้า admin (`_unlock_admin`), `ADMIN_UNLOCK` ตอนออก (`_lock_admin`)
- Arduino เก็บ flag `doorLockedByPi` — ถ้า true จะปฏิเสธการปลดล็อกด้วยบัตรใน `MODE_IDLE`
  (ยังสแกนบัตรและตอบสนองคำสั่งอื่นได้ปกติ แค่ไม่สั่ง relay เปิด)
- **ปุ่ม EXIT (เปิดประตูจากข้างใน) ไม่ถูกล็อกด้วย flag นี้เลย ทำงานตลอดเวลาไม่ว่า Pi จะอยู่
  โหมดไหน** — เพื่อความปลอดภัย/ทางหนีไฟ

## ควบคุมประตูจากหน้า Admin (หน้ารายชื่อผู้ใช้ → ตั้งค่าระบบ)

- **"เปิดประตูตอนนี้"** — ส่ง `OPEN` เปิดชั่วคราว 3 วิ แล้วล็อกกลับเอง เหมือนแตะบัตร/กด EXIT
- **"เปิดประตูค้าง" (เปิด/ปิด แบบ toggle เหมือน Standby)** — ส่ง `HOLD_ON`/`HOLD_OFF`
  ตั้ง flag `doorHeldOpen` บน Arduino ที่ทำให้ `unlockDoor()` **ไม่รีล็อกอัตโนมัติ**หลัง 3 วิ
  ไม่ว่าจะมีการแตะบัตร/กด EXIT ระหว่างนั้นกี่ครั้งก็ตาม
- ทั้งสองคำสั่งทำงาน**อิสระจาก `doorLockedByPi`** เพราะเป็นคำสั่งเปิดประตูโดยตรงจาก admin เอง
  ไม่ใช่การปลดล็อกด้วยบัตรที่ ADMIN_LOCK ตั้งใจจะกัน
- **ข้อจำกัด:** สถานะเปิดค้างอยู่ไหมเก็บไว้บน Pi เท่านั้น (`self.door_held_open`) ไม่มีคำสั่ง
  query สถานะกลับจาก Arduino — ถ้า `app.py` restart ระหว่างเปิดค้างอยู่ ปุ่มจะแสดงผิดจนกว่า
  จะกดใหม่อีกที (Arduino เองยังจำ `doorHeldOpen` ถูกต้อง แค่ Pi ลืม)

## Firmware deploy จาก Pi โดยตรง

ไม่ต้องใช้ Arduino IDE บนเครื่องอื่นแล้ว — jocasta มี `arduino-cli` ติดตั้งไว้ที่
`/home/jocasta/bin/arduino-cli` พร้อม core `arduino:avr` และ library `MFRC522`:

```bash
# หยุด app.py ก่อน (ปล่อย serial port ให้ avrdude ใช้)
pkill -f 'venv/bin/python3.*app.py'

# compile
/home/jocasta/bin/arduino-cli compile --fqbn arduino:avr:nano:cpu=atmega328 /home/jocasta/rfid_door_lock

# upload (บอร์ดที่ใช้เป็น Nano โคลนชิป CH340 — ไม่ต้อง sudo)
/home/jocasta/bin/arduino-cli upload -p /dev/ttyUSB0 --fqbn arduino:avr:nano:cpu=atmega328 /home/jocasta/rfid_door_lock
```

โฟลเดอร์สเก็ตช์ต้นทางสำหรับ flash อยู่ที่ `/home/jocasta/rfid_door_lock/rfid_door_lock.ino`
(คัดลอกมาจาก `arduino/rfid_door_lock/rfid_door_lock.ino` ในโปรเจกต์ทุกครั้งที่แก้โค้ด)

## จุดที่ต้องระวังถ้าจะแก้ต่อ

- UID ต้องเป็น**ตัวพิมพ์ใหญ่ 8 หลัก hex เป๊ะ**ทั้งสองฝั่ง (`uidToHex()` ใน `.ino` ใช้
  `toUpperCase()`) ถ้าไม่ตรงเคสจะหาเจ้าของบัตรไม่เจอ (`_find_rfid_owner` เทียบ string ตรงๆ)
- MAX_CARDS = 20 (จำกัดด้วยขนาด EEPROM ของ ATmega328) — ถ้าจะเพิ่มต้องคำนวณพื้นที่ EEPROM ใหม่
- ห้าม flash sketch ที่ไม่มี `handleSerialCommands()` ครบทุกคำสั่งข้างต้น เพราะ Pi ฝั่งนี้
  design ไว้ให้คาดหวังว่า Arduino รองรับครบทุกคำสั่งเสมอ (ไม่มี feature detection)
