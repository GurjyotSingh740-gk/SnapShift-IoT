/*
 * SnapShift v5.1 — Multi-System (ESP32 code unchanged from v5.0)
 *
 * ALL ORIGINAL v5.0 LOGIC INTACT — only added:
 *   1. Second UDP target (System 2) for MOTION broadcast
 *   2. PC_IP2 constant (System 2 IP)
 *
 * Button 1 (GPIO25) = SELECT window
 * Button 2 (GPIO26) = RELEASE/DROP / SEND to System 2
 * Button 3 (GPIO27) = RESET / cancel
 * IMU: MPU9250 @ I2C 0x68 | SDA=21, SCL=22
 * Transport: Wi-Fi UDP → Python on both PCs
 * Serial: 115200 baud
 */

#include <Wire.h>
#include "WiFi.h"
#include <WiFiUdp.h>

// ── Wi-Fi Credentials ────────────────────────────────────
const char* ssid     = "ACTFIBERNET";
const char* password = "act12345";

// ── System 1 PC IP (primary — all commands go here first) ──
const char* PC_IP    = "192.168.0.101";
const int   UDP_PORT = 5005;

// ── System 2 PC IP (receives MOTION broadcast only) ─────
const char* PC_IP2     = "192.168.0.100";   // <-- Change to System 2 IP
const int   UDP_PORT2  = 5007;              // System 2 motion port

// ── IMU Config ───────────────────────────────────────────
#define IMU_ADDR  0x68
#define SDA_PIN   21
#define SCL_PIN   22

// ── Push Button Pins ─────────────────────────────────────
#define BTN_SELECT  25
#define BTN_RELEASE 26
#define BTN_RESET   27

// ── Shared IMU data ───────────────────────────────────────
volatile float gGy = 0.0f;
volatile float gGz = 0.0f;

WiFiUDP udp;

// ─────────────────────────────────────────────────────────
//  SEND TO SYSTEM 1 ONLY
// ─────────────────────────────────────────────────────────
void sendToPC(const char* msg) {
  udp.beginPacket(PC_IP, UDP_PORT);
  udp.print(msg);
  udp.endPacket();
  Serial.print("📤 Sent SYS1: ");
  Serial.println(msg);
}

// ─────────────────────────────────────────────────────────
//  SEND MOTION TO BOTH SYSTEMS SIMULTANEOUSLY
// ─────────────────────────────────────────────────────────
void sendMotionBoth(const char* msg) {
  // System 1
  udp.beginPacket(PC_IP, UDP_PORT);
  udp.print(msg);
  udp.endPacket();

  // System 2 (motion only — for window animation on arrival)
  udp.beginPacket(PC_IP2, UDP_PORT2);
  udp.print(msg);
  udp.endPacket();
}

// ─────────────────────────────────────────────────────────
//  IMU INIT  (unchanged from v5)
// ─────────────────────────────────────────────────────────
bool imuBegin() {
  Wire.beginTransmission(IMU_ADDR);
  Wire.write(0x6B);
  Wire.write(0x00);
  if (Wire.endTransmission() != 0) {
    Serial.println("❌ IMU: Cannot reach 0x68 — check wiring!");
    return false;
  }
  delay(100);

  Wire.beginTransmission(IMU_ADDR);
  Wire.write(0x75);
  Wire.endTransmission(false);
  Wire.requestFrom(IMU_ADDR, 1);
  if (!Wire.available()) return false;

  uint8_t whoAmI = Wire.read();
  Serial.print("WHO_AM_I: 0x");
  Serial.println(whoAmI, HEX);

  if (whoAmI == 0x71 || whoAmI == 0xEA || whoAmI == 0x70 || whoAmI == 0xAF) {
    Serial.println("✅ IMU confirmed: MPU9250 / ICM-20948");
  } else {
    Serial.println("⚠️  Unknown chip ID — continuing anyway");
  }
  return true;
}

// ─────────────────────────────────────────────────────────
//  IMU READ  (unchanged from v5)
// ─────────────────────────────────────────────────────────
void imuRead() {
  Wire.beginTransmission(IMU_ADDR);
  Wire.write(0x3B);
  Wire.endTransmission(false);
  if (Wire.requestFrom(IMU_ADDR, 14) < 14) return;

  Wire.read(); Wire.read();  // ax
  Wire.read(); Wire.read();  // ay
  Wire.read(); Wire.read();  // az
  Wire.read(); Wire.read();  // temp

  int16_t gx_raw = (Wire.read() << 8) | Wire.read();
  int16_t gy_raw = (Wire.read() << 8) | Wire.read();
  int16_t gz_raw = (Wire.read() << 8) | Wire.read();

  float gy = gy_raw / 131.0f;
  float gz = gz_raw / 131.0f;

  gGy = (fabs(gy) > 0.8f) ? gy : 0.0f;
  gGz = (fabs(gz) > 0.8f) ? gz : 0.0f;
}

// ─────────────────────────────────────────────────────────
//  Wi-Fi CONNECT  (unchanged from v5)
// ─────────────────────────────────────────────────────────
void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.begin(ssid, password);

  Serial.print("📶 Connecting to WiFi");
  uint32_t t0 = millis();

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
    if (millis() - t0 > 20000) {
      Serial.println("\n⚠️  WiFi timeout!");
      return;
    }
  }

  Serial.println("\n✅ WiFi Connected!");
  Serial.print("   ESP32 IP: ");
  Serial.println(WiFi.localIP());
  Serial.print("   System 1 PC: ");
  Serial.println(PC_IP);
  Serial.print("   System 2 PC: ");
  Serial.println(PC_IP2);
}

// ─────────────────────────────────────────────────────────
//  MAIN TASK  (v5 logic unchanged — only sendMotionBoth added)
// ─────────────────────────────────────────────────────────
void taskMain(void* pv) {
  pinMode(BTN_SELECT,  INPUT_PULLUP);
  pinMode(BTN_RELEASE, INPUT_PULLUP);
  pinMode(BTN_RESET,   INPUT_PULLUP);

  bool lastBtn1 = HIGH, lastBtn2 = HIGH, lastBtn3 = HIGH;
  uint32_t lastMotionSend = 0;
  uint32_t lastWiFiCheck  = 0;

  Serial.println("[Task] Running — Buttons + IMU + UDP (Multi-System)");

  for (;;) {
    uint32_t now = millis();

    bool btn1 = digitalRead(BTN_SELECT);
    bool btn2 = digitalRead(BTN_RELEASE);
    bool btn3 = digitalRead(BTN_RESET);

    // Button 1 — SELECT (goes to System 1 only)
    if (btn1 == LOW && lastBtn1 == HIGH) {
      delay(30);
      if (digitalRead(BTN_SELECT) == LOW) {
        Serial.println("🟢 BTN1 → SELECT");
        sendToPC("SELECT");
      }
    }

    // Button 2 — RELEASE / SEND (goes to System 1 — it decides to send or drop)
    if (btn2 == LOW && lastBtn2 == HIGH) {
      delay(30);
      if (digitalRead(BTN_RELEASE) == LOW) {
        Serial.println("🔴 BTN2 → RELEASE");
        sendToPC("RELEASE");
      }
    }

    // Button 3 — RESET (broadcast to both systems)
    if (btn3 == LOW && lastBtn3 == HIGH) {
      delay(30);
      if (digitalRead(BTN_RESET) == LOW) {
        Serial.println("🔵 BTN3 → RESET");
        sendToPC("RESET");
        // Also reset System 2 via its control port
        udp.beginPacket(PC_IP2, 5006);
        udp.print("RESET");
        udp.endPacket();
      }
    }

    lastBtn1 = btn1;
    lastBtn2 = btn2;
    lastBtn3 = btn3;

    // ── Read IMU and send MOTION to BOTH systems at ~50Hz ─
    imuRead();

    if (now - lastMotionSend >= 20) {
      lastMotionSend = now;
      char motionMsg[64];
      snprintf(motionMsg, sizeof(motionMsg),
        "MOTION:%.2f:%.2f", (float)gGz, (float)gGy);
      sendMotionBoth(motionMsg);
    }

    // ── WiFi health check (unchanged from v5) ─────────────
    if (now - lastWiFiCheck >= 15000) {
      lastWiFiCheck = now;
      if (WiFi.status() != WL_CONNECTED) {
        Serial.println("⚠️  WiFi lost — reconnecting...");
        WiFi.reconnect();
      } else {
        Serial.printf("📶 WiFi OK | RSSI: %d dBm\n", WiFi.RSSI());
      }
    }

    vTaskDelay(pdMS_TO_TICKS(10));
  }
}

// ─────────────────────────────────────────────────────────
//  SETUP  (unchanged from v5)
// ─────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println("\n========================================");
  Serial.println("  SnapShift v5.1 — Multi-System Test");
  Serial.println("  MPU9250 + 3 Buttons + WiFi UDP");
  Serial.println("========================================\n");

  Wire.begin(SDA_PIN, SCL_PIN);
  delay(300);

  if (!imuBegin()) {
    Serial.println("❌ IMU failed — check connections");
  }

  connectWiFi();
  udp.begin(UDP_PORT);
  Serial.println("✅ UDP socket open");

  xTaskCreatePinnedToCore(
    taskMain,
    "SnapShift_Main",
    8192,
    nullptr,
    1,
    nullptr,
    1
  );
}

void loop() {
  vTaskDelay(pdMS_TO_TICKS(1000));
}