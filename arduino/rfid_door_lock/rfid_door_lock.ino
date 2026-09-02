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

// บรรทัดคำสั่งที่กำลังอ่านมาจาก Pi ผ่าน Serial (สร้างทีละตัวอักษรจนเจอ '\n')
String serialLine = "";

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

// hex string ของ UID (ตัวพิมพ์ใหญ่ ไม่มี space) ไว้ให้ Pi parse บรรทัดโปรโตคอลได้ง่าย
String uidToHex(const MFRC522::Uid &uid) {
  String s = "";
  for (byte i = 0; i < uid.size; i++) {
    if (uid.uidByte[i] < 0x10) s += "0";
    s += String(uid.uidByte[i], HEX);
  }
  s.toUpperCase();
  return s;
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
    // ส่งจำนวนรวมปัจจุบันไปด้วย ให้ Pi โชว์ยอดที่ยืนยันจาก EEPROM จริง ไม่ใช่นับเองฝั่ง Pi
    Serial.println("DUPLICATE:" + uidToHex(uid) + ":" + String(EEPROM.read(0)));
    return;
  }

  byte totalCards = EEPROM.read(0);
  if (totalCards >= MAX_CARDS) {
    Serial.println("EEPROM Full! Cannot register more cards.");
    Serial.println("FULL:" + String(EEPROM.read(0)));
    return;
  }

  int address = 1 + (totalCards * 4);
  for (byte j = 0; j < 4; j++) {
    EEPROM.write(address + j, uid.uidByte[j]);
  }

  EEPROM.write(0, totalCards + 1);

  // อ่านค่ากลับจาก EEPROM จริง (ไม่ใช่ค่าที่คำนวณไว้ก่อนเขียน) เพื่อยืนยันว่าบันทึกสำเร็จจริง
  byte confirmedTotal = EEPROM.read(0);

  Serial.print("Register Success! Saved UID: ");
  printUID(uid);
  Serial.print(" | Total cards now: ");
  Serial.println(confirmedTotal);
  // บรรทัดสำหรับ Pi parse: REGISTERED:<hex UID>:<จำนวนบัตรรวมที่ยืนยันจาก EEPROM>
  Serial.println("REGISTERED:" + uidToHex(uid) + ":" + String(confirmedTotal));
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

// อ่านคำสั่งจาก Pi ทีละบรรทัด: "REGISTER" เข้าโหมดลงทะเบียน, "IDLE" กลับโหมดปกติ
// (ทำงานคู่ขนานกับปุ่มจริงบนบอร์ด ไม่ได้แทนที่ — เผื่อ Pi ไม่ได้เชื่อมต่อ/ค้าง)
void handleSerialCommands() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      serialLine.trim();
      if (serialLine == "REGISTER") {
        currentMode = MODE_REGISTER;
        Serial.println("\n>>> [PI] -> REGISTER MODE (Waiting for new card...) <<<");
        Serial.println("MODE:REGISTER");
      } else if (serialLine == "IDLE") {
        currentMode = MODE_IDLE;
        Serial.println("\n>>> [PI] -> IDLE MODE (Normal Operation) <<<");
        Serial.println("MODE:IDLE");
      } else if (serialLine == "CLEAR") {
        // ล้างบัตรทั้งหมดออกจริงๆ (ต่างจาก factory reset ที่ยังเหลือบัตร default 1 ใบ)
        EEPROM.write(0, 0);
        currentMode = MODE_IDLE;
        Serial.println("\n>>> [PI] -> CLEARED ALL CARDS <<<");
        Serial.println("CLEARED:0");
        Serial.println("MODE:IDLE");
      }
      serialLine = "";
    } else if (c != '\r') {
      serialLine += c;
    }
  }
}

void loop() {
  handleSerialCommands();

  // 1. กดปุ่ม REG_BTN ค้างไว้ 5 วินาที เพื่อ Clear Memory (Factory Reset) กลับมาเป็นค่าเริ่มต้น
  // (เหลือไว้แค่ฟังก์ชันกู้คืนฉุกเฉินนี้ — การเข้าโหมด Register ต้องสั่งผ่าน Pi เท่านั้น
  // ปุ่มกดสั้นบนบอร์ดจะไม่เข้าโหมด Register ตรงๆ อีกต่อไป กัน EEPROM กับ people.json
  // บน Pi ไม่ตรงกัน)
  if (digitalRead(REG_BTN_PIN) == LOW) {
    unsigned long pressTime = millis();

    while (digitalRead(REG_BTN_PIN) == LOW) {
      if (millis() - pressTime > 5000) {
        resetEEPROMToDefault();
        Serial.println(">>> MEMORY CLEARED & RESET TO DEFAULT SUCCESSFUL! <<<");
        delay(1000);
        break;
      }
    }
  }

  // 2. กดปุ่ม Set เพื่อกลับสู่ Mode Idle
  if (digitalRead(SET_BTN_PIN) == LOW) {
    currentMode = MODE_IDLE;
    Serial.println("\n>>> [SWITCH MODE] -> IDLE MODE (Normal Operation) <<<");
    Serial.println("MODE:IDLE");
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