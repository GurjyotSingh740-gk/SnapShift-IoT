# SnapShift-IoT


---

## 🚀 Setup & Usage

### Step 1 — Flash ESP32

1. Open `esp32/snapshift_wand.ino` in **Arduino IDE**
2. Install board: `esp32 by Espressif` via Board Manager
3. Set your Wi-Fi credentials:
   ```cpp
   const char* ssid     = "YOUR_WIFI_SSID";
   const char* password = "YOUR_WIFI_PASSWORD";
   const char* PC_IP    = "YOUR_PC_IPV4_ADDRESS";
   ```
4. Upload to ESP32

### Step 2 — Find Your MACs

- **ESP32 MAC**: Upload `utils/mac_finder.ino` → open Serial Monitor at 115200 baud
- **PC Bluetooth MAC**: Device Manager → Bluetooth → Adapter Properties → Details →
  *Bluetooth radio address* (take first 6 octets only)

### Step 3 — Install Python Dependencies

```bash
pip install pywin32 pygame
```

### Step 4 — Run Receiver on PC

```bash
cd python
python snapshift_receiver.py
```

### Step 5 — Use the Wand

| Action | Result |
|---|---|
| **Single tap** TTP223 | Grabs the currently active/focused window |
| **Tilt wand left/right** | Moves the grabbed window horizontally |
| **Tilt wand up/down** | Moves the grabbed window vertically |
| **Double tap** TTP223 | Drops/releases the window at current position |

---

## 🔒 Security Model

- ESP32 wand identity verified via **Wi-Fi MAC whitelist** in Python receiver
- UDP port `5005` restricted to **local network only** (no internet exposure)
- Motion commands rejected if source IP does not match registered ESP32
- File operations require **UUID-based one-time tokens** (planned for v2)

---

## 📦 Dependencies

### ESP32 (Arduino IDE)
- `WiFi.h` — built-in with ESP32 core
- `WiFiUdp.h` — built-in with ESP32 core
- `Wire.h` — built-in (I2C for MPU9250)
- `esp_mac.h` — built-in (ESP32 core 3.x+)

### Python (PC)
