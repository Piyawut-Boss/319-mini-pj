#include <SPI.h>
#include <MFRC522.h>

#define RST_PIN 9
#define SS_PIN 10
#define RELAY_PIN 8

MFRC522 mfrc522(SS_PIN, RST_PIN);

// รายชื่อ UID บัตรที่อนุญาต (4 ไบต์ต่อใบ) เพิ่มบัตรใหม่ได้โดยเติมแถวในนี้
// TODO: บัตรใบที่ 2 มี 2 ค่าที่ไม่ตรงกันจากโค้ดเดิม (C40EB306 / C6796C06)
// ใส่ไว้ทั้งคู่ชั่วคราว — เปิด Serial Monitor แล้วสแกนบัตรจริงเพื่อดูค่าที่ถูกต้อง
// แล้วลบแถวที่ไม่ใช่ทิ้ง
const byte authorizedUIDs[][4] = {
  {0xA7, 0x56, 0x5B, 0x06},
  {0xC4, 0x0E, 0xB3, 0x06},
  {0xC6, 0x79, 0x6C, 0x06}
};
const byte numAuthorizedUIDs = sizeof(authorizedUIDs) / sizeof(authorizedUIDs[0]);

void setup() {
  Serial.begin(9600);
  SPI.begin();
  mfrc522.PCD_Init(); // เริ่มต้นการทำงานของโมดูล

  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, HIGH); // ล็อคประตูไว้ (Active Low)

  Serial.println("System Ready. Please scan your card.");
}

bool isAuthorized(const MFRC522::Uid &uid) {
  if (uid.size != 4) return false;
  for (byte i = 0; i < numAuthorizedUIDs; i++) {
    if (memcmp(uid.uidByte, authorizedUIDs[i], 4) == 0) return true;
  }
  return false;
}

void printUID(const MFRC522::Uid &uid) {
  for (byte i = 0; i < uid.size; i++) {
    if (uid.uidByte[i] < 0x10) Serial.print("0");
    Serial.print(uid.uidByte[i], HEX);
  }
}

void loop() {
  // ตรวจสอบว่ามีบัตรมาทาบหรือไม่
  if (!mfrc522.PICC_IsNewCardPresent() || !mfrc522.PICC_ReadCardSerial()) {
    return;
  }

  Serial.print("Scanned UID: ");
  printUID(mfrc522.uid);
  Serial.println();

  if (isAuthorized(mfrc522.uid)) {
    Serial.println("Access Granted! Unlocking...");
    digitalWrite(RELAY_PIN, LOW); // สั่ง Relay ปลดล็อค

    delay(3000); // เปิดประตูค้างไว้ 3 วินาที

    digitalWrite(RELAY_PIN, HIGH); // สั่งล็อคประตูเหมือนเดิม
    Serial.println("Locked.");
  } else {
    Serial.println("Access Denied!");
    delay(1000); // หน่วงเวลาเฉพาะตอนสแกนไม่ผ่าน
  }

  // หยุดการสื่อสารกับบัตรและล้างสถานะการเข้ารหัส เพื่อเตรียมรับรอบต่อไป
  mfrc522.PICC_HaltA();
  mfrc522.PCD_StopCrypto1();

  // รีเซ็ตตัวอ่าน RC522 ทั้งหมดกลับไปเหมือนตอนเพิ่งบูตเครื่อง (คำสั่งเดียวกับใน setup())
  // การตัดแค่สายอากาศ (AntennaOff/On) ไม่พอ เพราะรีจิสเตอร์อื่นๆ ของตัวอ่านยังค้าง
  // อยู่ในสถานะเดิมของรอบก่อนหน้า ทำให้บางครั้งบัตรใบเดิมสแกนซ้ำไม่ติด
  mfrc522.PCD_Init();

  Serial.println("System Ready. Please scan your card.");
}
