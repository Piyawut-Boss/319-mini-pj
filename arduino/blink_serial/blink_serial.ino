void setup() {
  Serial.begin(9600);
  pinMode(LED_BUILTIN, OUTPUT);
}

void loop() {
  if (Serial.available()) {
    char c = Serial.read();
    if (c == '1') {
      digitalWrite(LED_BUILTIN, HIGH);
      Serial.println("LED ON");
    } else if (c == '0') {
      digitalWrite(LED_BUILTIN, LOW);
      Serial.println("LED OFF");
    } else if (c == 'b') {
      for (int round = 0; round < 5; round++) {
        for (int i = 0; i < 5; i++) {
          digitalWrite(LED_BUILTIN, HIGH);
          delay(200);
          digitalWrite(LED_BUILTIN, LOW);
          delay(200);
        }
        delay(500);
      }
      Serial.println("BLINKED 5x5");
    }
  }
}
