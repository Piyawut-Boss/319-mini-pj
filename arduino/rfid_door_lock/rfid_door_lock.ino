#include <SPI.h>
#include <MFRC522.h>
#include <EEPROM.h>

#define RST_PIN 5
#define SS_PIN 10
#define RELAY_PIN A5

// จับคู่ขาสัญญาณและขากราวด์จำลองให้อยู่ใกล้กัน
#define EXIT_BTN_PIN 3
#define EXIT_GND_PIN 2

#define REG_BTN_PIN  4
#define REG_GND_PIN  6

#define SET_BTN_PIN  9
#define SET_GND_PIN  8

MFRC522 mfrc522(SS_PIN, RST_PIN);

enum SystemMode {
  MODE_IDLE,
  MODE_REGISTER
};
SystemMode currentMode = MODE_IDLE;

// true while the Pi is showing any admin screen — card-based unlock is
// refused in this state (the EXIT button is NOT affected, it always works)
bool doorLockedByPi = false;

#define MAX_CARDS 20

const byte defaultUIDs[][4] = {
  {0xA7, 0x56, 0x5B, 0x06}
};
const byte numDefaultUIDs = sizeof(defaultUIDs) / sizeof(defaultUIDs[0]);

// บรรทัดคำสั่งที่กำลังอ่านมาจาก Pi ผ่าน Serial (สร้างทีละตัวอักษรจนเจอ '\n')
String serialLine = "";

void setup() {
  Serial.begin(9600);
  SPI.begin();
  mfrc522.PCD_Init();

  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, HIGH);

  // ตั้งค่าปุ่มกด
  pinMode(EXIT_BTN_PIN, INPUT_PULLUP);
  pinMode(REG_BTN_PIN, INPUT_PULLUP);
  pinMode(SET_BTN_PIN, INPUT_PULLUP);

  // เสกขา Digital เป็นกราวด์จำลอง (จ่ายไฟ 0V)
  pinMode(EXIT_GND_PIN, OUTPUT);
  digitalWrite(EXIT_GND_PIN, LOW);

  pinMode(REG_GND_PIN, OUTPUT);
  digitalWrite(REG_GND_PIN, LOW);

  pinMode(SET_GND_PIN, OUTPUT);
  digitalWrite(SET_GND_PIN, LOW);

  if (EEPROM.read(0) == 255 || EEPROM.read(0) > MAX_CARDS) {
    resetEEPROMToDefault();
  }

  Serial.println("=== SYSTEM INITIALIZED ===");
  Serial.print("Total cards in memory: ");
  Serial.println(EEPROM.read(0));
  Serial.println("Current Mode: IDLE (Normal Operation)");
}

void resetEEPROMToDefault() {
  EEPROM.write(0, numDefaultUIDs);
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

// แปลง hex string 8 ตัวอักษร (จาก Pi) กลับเป็น 4 ไบต์ UID — คืนค่า false ถ้ารูปแบบผิด
bool hexToUidBytes(const String &hex, byte *out) {
  if (hex.length() != 8) return false;
  for (int i = 0; i < 4; i++) {
    out[i] = (byte) strtol(hex.substring(i * 2, i * 2 + 2).c_str(), NULL, 16);
  }
  return true;
}

// เขียนบัตรลง EEPROM ตรงๆจาก UID ที่ Pi ส่งมา โดยไม่ต้องแตะบัตรจริงที่เครื่องอ่าน
// (ต่างจาก registerNewCard ที่ต้องรอสแกนบัตรจริงในโหมด MODE_REGISTER) — ใช้กู้คืน
// ตอน Arduino เปลี่ยนเครื่อง/EEPROM โดนล้าง แต่ people.json บน Pi ยังมี UID อยู่ครบ
// ทำงานอิสระจาก currentMode เลย ไม่ต้องสลับโหมดก่อนเรียกใช้
void addCardByHex(const String &hex) {
  byte target[4];
  if (!hexToUidBytes(hex, target)) {
    Serial.println("INVALID_UID:" + hex);
    return;
  }

  byte totalCards = EEPROM.read(0);
  for (byte i = 0; i < totalCards; i++) {
    bool match = true;
    for (byte j = 0; j < 4; j++) {
      if (EEPROM.read(1 + (i * 4) + j) != target[j]) {
        match = false;
        break;
      }
    }
    if (match) {
      Serial.println("DUPLICATE:" + hex + ":" + String(totalCards));
      return;
    }
  }

  if (totalCards >= MAX_CARDS) {
    Serial.println("FULL:" + String(totalCards));
    return;
  }

  int address = 1 + (totalCards * 4);
  for (byte j = 0; j < 4; j++) {
    EEPROM.write(address + j, target[j]);
  }
  EEPROM.write(0, totalCards + 1);

  Serial.println("REGISTERED:" + hex + ":" + String(EEPROM.read(0)));
}

// ลบบัตร 1 ใบออกจาก EEPROM ตาม UID ที่ Pi สั่งมา (ใช้ตอนลบผู้ใช้ฝั่ง Pi ให้
// Arduino ไม่ยอมรับบัตรของคนที่ถูกลบไปแล้วต่อ)
void removeCardByHex(const String &hex) {
  byte target[4];
  if (!hexToUidBytes(hex, target)) {
    Serial.println("INVALID_UID:" + hex);
    return;
  }

  byte totalCards = EEPROM.read(0);
  int foundIndex = -1;
  for (byte i = 0; i < totalCards; i++) {
    bool match = true;
    for (byte j = 0; j < 4; j++) {
      if (EEPROM.read(1 + (i * 4) + j) != target[j]) {
        match = false;
        break;
      }
    }
    if (match) {
      foundIndex = i;
      break;
    }
  }

  if (foundIndex == -1) {
    Serial.println("NOTFOUND:" + hex);
    return;
  }

  // เลื่อนรายการที่เหลือมาปิดช่องว่างที่ลบไป
  for (byte i = foundIndex; i < totalCards - 1; i++) {
    for (byte j = 0; j < 4; j++) {
      byte nextVal = EEPROM.read(1 + ((i + 1) * 4) + j);
      EEPROM.write(1 + (i * 4) + j, nextVal);
    }
  }
  EEPROM.write(0, totalCards - 1);

  Serial.println("REMOVED:" + hex + ":" + String(EEPROM.read(0)));
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

  mfrc522.PCD_Init();
}

// อ่านคำสั่งจาก Pi ทีละบรรทัด: "REGISTER" เข้าโหมดลงทะเบียน, "IDLE" กลับโหมดปกติ,
// "REMOVE:<hex uid>" ลบบัตรใบนั้นออกจาก EEPROM, "ADD:<hex uid>" เขียนบัตรลง
// EEPROM ตรงๆโดยไม่ต้องแตะบัตรจริง (กู้คืนตอนเปลี่ยน Arduino/EEPROM โดนล้าง),
// "CLEAR" ล้างบัตรทั้งหมดออกจาก EEPROM จริงๆ
void handleSerialCommands() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      serialLine.trim();
      if (serialLine == "REGISTER") {
        currentMode = MODE_REGISTER;
        Serial.println("\n>>> [PI] -> REGISTER MODE (Waiting for new card...) <<<");
      } else if (serialLine == "IDLE") {
        currentMode = MODE_IDLE;
        Serial.println("\n>>> [PI] -> IDLE MODE (Normal Operation) <<<");
      } else if (serialLine.startsWith("REMOVE:")) {
        removeCardByHex(serialLine.substring(7));
      } else if (serialLine.startsWith("ADD:")) {
        addCardByHex(serialLine.substring(4));
      } else if (serialLine == "CLEAR") {
        // ล้างบัตรทั้งหมดจริงๆ (ต่างจาก factory reset ที่ยังเหลือบัตร default 1 ใบ) —
        // ใช้ก่อนซิงค์ทั้งชุดจาก Pi ให้ Arduino เหลือแต่บัตรที่มีเจ้าของใน people.json เท่านั้น
        EEPROM.write(0, 0);
        currentMode = MODE_IDLE;
        Serial.println("\n>>> [PI] -> ALL CARDS CLEARED <<<");
        Serial.println("CLEARED:0");
      } else if (serialLine == "ADMIN_LOCK") {
        doorLockedByPi = true;
        Serial.println("\n>>> [PI] -> DOOR LOCKED (admin screen open on Pi) <<<");
      } else if (serialLine == "ADMIN_UNLOCK") {
        doorLockedByPi = false;
        Serial.println("\n>>> [PI] -> DOOR UNLOCKED (admin screen closed) <<<");
      }
      serialLine = "";
    } else if (c != '\r') {
      serialLine += c;
    }
  }
}

void loop() {
  handleSerialCommands();

  // กดปุ่ม REG ค้าง 5 วินาที = Factory Reset ฉุกเฉินเท่านั้น (กู้คืนกลับเป็นบัตร
  // default) — กดสั้นๆ จะไม่เข้าโหมด Register ตรงๆ อีกต่อไป ต้องสั่งผ่าน Pi เท่านั้น
  // เพราะถ้าลงทะเบียนผ่านปุ่มจริงตรงๆ EEPROM ของ Arduino จะมีบัตรที่ Pi ไม่รู้จักชื่อ
  // เจ้าของ (ไม่ได้อยู่ใน people.json) ทำให้ข้อมูลสองฝั่งไม่ตรงกัน
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

  if (digitalRead(SET_BTN_PIN) == LOW) {
    currentMode = MODE_IDLE;
    Serial.println("\n>>> [SWITCH MODE] -> IDLE MODE (Normal Operation) <<<");
    delay(500);
  }

  if (digitalRead(EXIT_BTN_PIN) == LOW) {
    Serial.println("Exit Button Pressed! Unlocking...");
    unlockDoor();
    delay(500);
    return;
  }

  if (!mfrc522.PICC_IsNewCardPresent() || !mfrc522.PICC_ReadCardSerial()) {
    return;
  }

  switch (currentMode) {
    case MODE_IDLE:
      Serial.print("[IDLE] Scanned UID: ");
      printUID(mfrc522.uid);
      Serial.println();

      if (isAuthorized(mfrc522.uid)) {
        if (doorLockedByPi) {
          Serial.println("Access Denied! (locked - admin screen open on Pi)");
          delay(1000);
        } else {
          unlockDoor();
        }
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
