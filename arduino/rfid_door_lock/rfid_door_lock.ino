#include <SPI.h>
#include <MFRC522.h>
#include <EEPROM.h>

#define RST_PIN 9
#define SS_PIN 10
#define RELAY_PIN 8

#define EXIT_BTN_PIN 3
#define REG_BTN_PIN  4  
#define SET_BTN_PIN  5  

MFRC522 mfrc522(SS_PIN, RST_PIN);

enum SystemMode {
  MODE_IDLE,
  MODE_REGISTER
};
SystemMode currentMode = MODE_IDLE;

#define MAX_CARDS 20

// กำหนด UID เริ่มต้น (คุณสามารถเพิ่มหรือลดจำนวนในนี้ได้ตามต้องการ)
const byte defaultUIDs[][4] = {
  {0xA7, 0x56, 0x5B, 0x06}
};
// คำนวณจำนวนการ์ดเริ่มต้นอัตโนมัติจากขนาดอาเรย์
const byte numDefaultUIDs = sizeof(defaultUIDs) / sizeof(defaultUIDs[0]);

void setup() {
  Serial.begin(9600);
  SPI.begin();
  mfrc522.PCD_Init();

  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, HIGH); 

  pinMode(EXIT_BTN_PIN, INPUT_PULLUP);
  pinMode(REG_BTN_PIN, INPUT_PULLUP);
  pinMode(SET_BTN_PIN, INPUT_PULLUP);

  // ตรวจสอบ EEPROM หากยังไม่เคยบันทึก ให้โหลดค่าเริ่มต้น
  if (EEPROM.read(0) == 255 || EEPROM.read(0) > MAX_CARDS) {
    resetEEPROMToDefault();
  }

  Serial.println("=== SYSTEM INITIALIZED ===");
  Serial.print("Total cards in memory: ");
  Serial.println(EEPROM.read(0));
  Serial.println("Current Mode: IDLE (Normal Operation)");
}

// ฟังก์ชันโหลดค่าเริ่มต้นลง EEPROM แบบคำนวณขนาดอัตโนมัติ
void resetEEPROMToDefault() {
  EEPROM.write(0, numDefaultUIDs); // บันทึกจำนวนตามจริง
  for (int i = 0; i < numDefaultUIDs; i++) {
    for (int j = 0; j < 4; j++) {
      EEPROM.write(1 + (i * 4) + j, defaultUIDs[i][j]);
    }
  }
  Serial.println(">>> EEPROM Reset to Default UIDs! <<<");
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
  Serial.print(" | Total cards now: ");
  Serial.println(EEPROM.read(0));
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
  // 1. กดปุ่ม REG_BTN ค้างไว้ 5 วินาที เพื่อ Clear Memory (Factory Reset) กลับมาเป็นค่าเริ่มต้น
  if (digitalRead(REG_BTN_PIN) == LOW) {
    unsigned long pressTime = millis();
    bool isHeld = true;
    
    while (digitalRead(REG_BTN_PIN) == LOW) {
      if (millis() - pressTime > 5000) { 
        resetEEPROMToDefault();
        Serial.println(">>> MEMORY CLEARED & RESET TO DEFAULT SUCCESSFUL! <<<");
        delay(1000);
        isHeld = false;
        break;
      }
    }

    if (isHeld && (millis() - pressTime <= 5000)) {
      currentMode = MODE_REGISTER;
      Serial.println("\n>>> [SWITCH MODE] -> REGISTER MODE (Waiting for new card...) <<<");
      delay(3000); 
    }
  }

  // 2. กดปุ่ม Set เพื่อกลับสู่ Mode Idle
  if (digitalRead(SET_BTN_PIN) == LOW) {
    currentMode = MODE_IDLE;
    Serial.println("\n>>> [SWITCH MODE] -> IDLE MODE (Normal Operation) <<<");
    delay(500); 
  }

  // ปุ่ม Exit กดเปิดจากด้านในได้ตลอดเวลา
  if (digitalRead(EXIT_BTN_PIN) == LOW) {
    Serial.println("Exit Button Pressed! Unlocking...");
    unlockDoor();
    delay(500);
    return;
  }

  // 3. อ่านบัตรตาม Mode ปัจจุบัน
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

  mfrc522.PICC_HaltA();
  mfrc522.PCD_StopCrypto1();
  mfrc522.PCD_Init();
}