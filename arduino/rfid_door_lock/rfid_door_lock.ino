#include <SPI.h>
#include <MFRC522.h>
#include <EEPROM.h>

#define RST_PIN 9
#define SS_PIN 10
#define RELAY_PIN 8

#define EXIT_BTN_PIN 3
#define REG_BTN_PIN  4  // ปุ่มกดเข้า Mode Register
#define SET_BTN_PIN  5  // ปุ่มกดกลับ Mode Idle

MFRC522 mfrc522(SS_PIN, RST_PIN);

// กำหนดสถานะ Mode ของระบบ
enum SystemMode {
  MODE_IDLE,
  MODE_REGISTER
};
SystemMode currentMode = MODE_IDLE;

#define MAX_CARDS 20

// รายชื่อ UID เริ่มต้น (เก็บลง EEPROM ตอนเปิดเครื่องครั้งแรก)
const byte defaultUIDs[][4] = {
  {0xA7, 0x56, 0x5B, 0x06},
  {0xC4, 0x0E, 0xB3, 0x06},
  {0xC6, 0x79, 0x6C, 0x06}
};

void setup() {
  Serial.begin(9600);
  SPI.begin();
  mfrc522.PCD_Init();

  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, HIGH); // ล็อคประตูไว้ (Active Low)

  pinMode(EXIT_BTN_PIN, INPUT_PULLUP);
  pinMode(REG_BTN_PIN, INPUT_PULLUP);
  pinMode(SET_BTN_PIN, INPUT_PULLUP);

  // ตรวจสอบ EEPROM หากยังไม่เคยบันทึก ให้โหลดค่าเริ่มต้น
  if (EEPROM.read(0) == 255 || EEPROM.read(0) > MAX_CARDS) {
    EEPROM.write(0, 3);
    for (int i = 0; i < 3; i++) {
      for (int j = 0; j < 4; j++) {
        EEPROM.write(1 + (i * 4) + j, defaultUIDs[i][j]);
      }
    }
  }

  Serial.println("=== SYSTEM INITIALIZED ===");
  Serial.println("Current Mode: IDLE (Normal Operation)");
}

bool isAuthorized(const MFRC522::Uid &uid) {
  if (uid.size != 4) return false;
  byte totalCards = EEPROM.read(0);

  for (byte i = 0; i < totalCards; i++) {
    bool match = true;
    for (byte j = 0; j < 4; j++) {
      if (EEPROM.read(1 + (i * 4) + j) != uid.uidByte[j]) {
        match = false;
        break;
      }
    }
    if (match) return true;
  }
  return false;
}

void registerNewCard(const MFRC522::Uid &uid) {
  if (uid.size != 4) {
    Serial.println("Invalid Card Size!");
    return;
  }

  if (isAuthorized(uid)) {
    Serial.println("This card is already registered!");
    return;
  }

  byte totalCards = EEPROM.read(0);
  if (totalCards >= MAX_CARDS) {
    Serial.println("EEPROM Full! Cannot register more cards.");
    return;
  }

  int address = 1 + (totalCards * 4);
  for (byte j = 0; j < 4; j++) {
    EEPROM.write(address + j, uid.uidByte[j]);
  }

  EEPROM.write(0, totalCards + 1);
  
  Serial.print("Register Success! Saved UID: ");
  printUID(uid);
  Serial.println();
}

void printUID(const MFRC522::Uid &uid) {
  for (byte i = 0; i < uid.size; i++) {
    if (uid.uidByte[i] < 0x10) Serial.print("0");
    Serial.print(uid.uidByte[i], HEX);
  }
}

void unlockDoor() {
  Serial.println("Access Granted! Unlocking...");
  digitalWrite(RELAY_PIN, LOW); 
  delay(3000); 
  digitalWrite(RELAY_PIN, HIGH); 
  Serial.println("Locked.");
}

void loop() {
  // 1. ตรวจสอบการกดปุ่มเพื่อเปลี่ยน Mode
  if (digitalRead(REG_BTN_PIN) == LOW) {
    currentMode = MODE_REGISTER;
    Serial.println("\n>>> [SWITCH MODE] -> REGISTER MODE (Waiting for new card...) <<<");
    delay(500); // กันกดเบิ้ล (Debounce)
  }

  if (digitalRead(SET_BTN_PIN) == LOW) {
    currentMode = MODE_IDLE;
    Serial.println("\n>>> [SWITCH MODE] -> IDLE MODE (Normal Operation) <<<");
    delay(500); // Debounce
  }

  // ปุ่ม Exit กดเปิดจากด้านในได้ตลอดเวลา
  if (digitalRead(EXIT_BTN_PIN) == LOW) {
    Serial.println("Exit Button Pressed! Unlocking...");
    unlockDoor();
    delay(500);
    return;
  }

  // 2. ตรวจสอบการทาบบัตร RFID (ทำงานแยกตาม Mode ปัจจุบัน)
  if (!mfrc522.PICC_IsNewCardPresent() || !mfrc522.PICC_ReadCardSerial()) {
    return;
  }

  switch (currentMode) {
    case MODE_IDLE:
      Serial.print("[IDLE] Scanned UID: ");
      printUID(mfrc522.uid);
      Serial.println();

      if (isAuthorized(mfrc522.uid)) {
        unlockDoor();
      } else {
        Serial.println("Access Denied!");
        delay(1000);
      }
      break;

    case MODE_REGISTER:
      Serial.print("[REGISTER] Scanning new card to save: ");
      printUID(mfrc522.uid);
      Serial.println();
      registerNewCard(mfrc522.uid);
      delay(1000);
      break;
  }

  // ล้างสถานะการอ่านบัตรเพื่อเตรียมรับรอบถัดไป
  mfrc522.PICC_HaltA();
  mfrc522.PCD_StopCrypto1();
  mfrc522.PCD_Init();
}