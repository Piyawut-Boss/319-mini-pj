# Door Guard (Arduino Uno R3)

หน้าที่: ระบบความปลอดภัยหน้าประตู ทำงานแบบ real-time แยกอิสระจาก Pi
(รองรับ Local Failover — เปิดประตูด้วยบัตรที่อนุญาตได้แม้ Pi จะ reboot/crash)

## โครงสร้าง

```
blink_serial/
  blink_serial.ino     สเก็ตช์ทดสอบ serial link กับ Pi ผ่าน USB
                        รับคำสั่ง 9600 baud: 1=ไฟติด, 0=ไฟดับ, b=กระพริบ 5 ครั้ง x 5 รอบ

rfid_door_lock/
  rfid_door_lock.ino    อ่านบัตร RFID (RC522) ตรวจสอบ UID กับรายชื่อที่อนุญาต
                        แล้วสั่ง relay ปลดล็อกประตู 3 วินาที — ทำงานอิสระ ไม่พึ่ง Pi
```

## rfid_door_lock.ino — Pin

| อุปกรณ์ | ขา Arduino |
|---|---|
| RC522 RST | 9 |
| RC522 SS/SDA | 10 |
| RC522 SPI (MOSI/MISO/SCK) | 11/12/13 (ฮาร์ดแวร์ SPI ของ Uno) |
| Relay control | 8 (Active LOW — HIGH=ล็อก, LOW=ปลดล็อก) |

แก้รายชื่อ UID บัตรที่อนุญาตได้ที่ array `authorizedUIDs` ในไฟล์ — เปิด Serial Monitor
(9600 baud) แล้วสแกนบัตรจริงเพื่อดู UID ที่ถูกต้องก่อนเพิ่ม/ลบ

## ยังไม่ได้ทำ

- ยังไม่ได้เชื่อม serial protocol กับฝั่ง Pi (ดู branch `pi`) — ตอนนี้ `rfid_door_lock.ino`
  ทำงานอิสระเต็มตัว ไม่ได้รับคำสั่ง OPEN/DENY จาก Pi หรือส่ง event กลับไปที่ Pi
- ยังไม่มี Door Sensor (Reed Switch), Exit Button, Buzzer/LED แจ้งเตือนตามสเปคเต็ม
- ยังไม่มี flyback diode + optocoupler relay ตามสเปค (ตอนนี้ควบคุม relay ตรงจากขา digital)
