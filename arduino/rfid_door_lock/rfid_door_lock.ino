#include <SPI.h>
#include <MFRC522.h>

#define RST_PIN 9
#define SS_PIN 10
#define RELAY_PIN 8
#define EXIT_BTN_PIN 3

MFRC522 mfrc522(SS_PIN, RST_PIN);

const byte authorizedUIDs[][4] = {
  {0xA7, 0x56, 0x5B, 0x06},
  {0xC4, 0x0E, 0xB3, 0x06},
  {0xC6, 0x79, 0x6C, 0x06}
};
const byte numAuthorizedUIDs = sizeof(authorizedUIDs) / sizeof(authorizedUIDs[0]);

void setup() {
  Serial.begin(9600);
  SPI.begin();
  mfrc522.PCD_Init();

  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, HIGH); // ล็อคประตูไว้ (Active Low)

  pinMode(EXIT_BTN_PIN, INPUT_PULLUP);
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
  // 1. ตรวจสอบการกดปุ่ม Exit ด้านใน
  if (digitalRead(EXIT_BTN_PIN) == LOW) {
    Serial.println("Exit Button Pressed! Unlocking...");
    digitalWrite(RELAY_PIN, LOW);  
    delay(3000);                   
    digitalWrite(RELAY_PIN, HIGH); 
    Serial.println("Locked.");
    delay(500); 
    return; // ข้ามการเช็ค RFID ในรอบนี้ไปก่อน
  }

  // 2. ตรวจสอบว่ามีบัตรมาทาบหรือไม่
  if (!mfrc522.PICC_IsNewCardPresent() || !mfrc522.PICC_ReadCardSerial()) {
    return;
  }

  Serial.print("Scanned UID: ");
  printUID(mfrc522.uid);
  Serial.println();

  if (isAuthorized(mfrc522.uid)) {
    Serial.println("Access Granted! Unlocking...");
    digitalWrite(RELAY_PIN, LOW); 
    delay(3000); 
    digitalWrite(RELAY_PIN, HIGH); 
    Serial.println("Locked.");
  } else {
    Serial.println("Access Denied!");
    delay(1000); 
  } // ปิดวงเล็บของบล็อก else ให้เรียบร้อย

  // หยุดการสื่อสารกับบัตร
  mfrc522.PICC_HaltA();
  mfrc522.PCD_StopCrypto1();
  mfrc522.PCD_Init();

  Serial.println("System Ready. Please scan your card.");
}