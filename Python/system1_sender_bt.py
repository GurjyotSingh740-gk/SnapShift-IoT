# SnapShift — SYSTEM 1 (Sender) — Bluetooth RFCOMM Only
# BTN1 = SELECT active window → start move
# BTN2 = If at right edge → TRANSFER to System 2 | else drop in place
# BTN3 = RESET everything
# Transport: Bluetooth Classic RFCOMM (no Wi-Fi, no UDP, no IP)

import socket
import threading
import time
import sys
import os
import ctypes

import win32gui
import pygame

# ── CONFIG ────────────────────────────────────────────────────────
ESP32_MAC       = "84:1F:E8:69:84:32"   # ← Your ESP32 BT MAC address
RFCOMM_PORT_ESP = 1                      # RFCOMM channel on ESP32

SYSTEM2_MAC     = "00:20:C8:B8:48:E8"   # ← System 2 PC Bluetooth MAC
RFCOMM_PORT_S2  = 4                      # RFCOMM channel on System 2 PC
                                         # (System 2 listens on channel 4)

TRANSFER_DIR    = r"C:\SnapShift\outbox"
SCREEN_W        = 1920
SCREEN_H        = 1080
MOVE_SENS       = 3.5
EDGE_THRESH     = SCREEN_W - 160        # file "at edge" if x >= this
# ─────────────────────────────────────────────────────────────────

grabbed_hwnd   = None
grabbed_title  = ""
grabbed_file   = None
is_grabbed     = False

gyro_z = 0.0
gyro_y = 0.0
drag_x = 0.0
drag_y = 0.0

transfer_done   = False
transfer_active = False
at_edge         = False

# Global BT socket to System 2 (lazy connect when needed)
s2_sock = None
s2_lock = threading.Lock()


# ── BT CONNECT HELPERS ────────────────────────────────────────────

def bt_connect_esp():
    """Blocking connect to ESP32 RFCOMM — called once at startup."""
    while True:
        try:
            print(f"[S1] Connecting to ESP32 BT: {ESP32_MAC} ...")
            sock = socket.socket(
                socket.AF_BLUETOOTH,
                socket.SOCK_STREAM,
                socket.BTPROTO_RFCOMM
            )
            sock.settimeout(15)
            sock.connect((ESP32_MAC, RFCOMM_PORT_ESP))
            sock.settimeout(None)
            print("[S1] ✅ ESP32 BT connected")
            return sock
        except Exception as e:
            print(f"[S1] ⚠️ ESP32 BT connect failed: {e}  — retrying in 5s")
            time.sleep(5)


def bt_connect_s2():
    """Connect to System 2 BT RFCOMM — called when file transfer needed."""
    global s2_sock
    with s2_lock:
        if s2_sock:
            return s2_sock
        try:
            print(f"[S1] Connecting to System 2 BT: {SYSTEM2_MAC} ...")
            sock = socket.socket(
                socket.AF_BLUETOOTH,
                socket.SOCK_STREAM,
                socket.BTPROTO_RFCOMM
            )
            sock.settimeout(20)
            sock.connect((SYSTEM2_MAC, RFCOMM_PORT_S2))
            sock.settimeout(None)
            s2_sock = sock
            print("[S1] ✅ System 2 BT connected")
            return sock
        except Exception as e:
            print(f"[S1] ❌ System 2 BT connect failed: {e}")
            s2_sock = None
            return None


# ── WINDOW HELPERS (unchanged from v5) ───────────────────────────

def get_active_window():
    try:
        hwnd  = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
        if title and "SnapShift" not in title:
            return hwnd, title
    except Exception:
        pass
    return None, ""


def get_rect(hwnd):
    try:
        r = win32gui.GetWindowRect(hwnd)
        return r[0], r[1], r[2] - r[0], r[3] - r[1]
    except Exception:
        return 0, 0, 500, 300


def move_window(hwnd, x, y):
    try:
        _, _, w, h = get_rect(hwnd)
        win32gui.MoveWindow(hwnd, int(x), int(y), w, h, True)
    except Exception:
        pass


def find_file(title):
    """Return first file in outbox fuzzy-matching window title."""
    os.makedirs(TRANSFER_DIR, exist_ok=True)
    for fname in os.listdir(TRANSFER_DIR):
        for word in title.split():
            if len(word) > 3 and word.lower() in fname.lower():
                return os.path.join(TRANSFER_DIR, fname)
    all_files = [
        os.path.join(TRANSFER_DIR, f)
        for f in os.listdir(TRANSFER_DIR)
        if os.path.isfile(os.path.join(TRANSFER_DIR, f))
    ]
    return all_files[0] if all_files else None


# ── BT NOTIFY SYSTEM 2 (short control message) ───────────────────

def bt_notify_s2(msg):
    """Send short control text to System 2 over BT."""
    sock = bt_connect_s2()
    if sock:
        try:
            sock.sendall((msg + "\n").encode())
        except Exception as e:
            print(f"[S1] Notify error: {e}")
            global s2_sock
            with s2_lock:
                s2_sock = None


# ── BT FILE TRANSFER TO SYSTEM 2 ─────────────────────────────────

def bt_send_file(filepath):
    """Send file to System 2 over BT RFCOMM with header."""
    global transfer_active, transfer_done, s2_sock

    sock = bt_connect_s2()
    if not sock:
        print("[S1] ❌ Cannot reach System 2 BT")
        transfer_active = False
        return

    try:
        filename = os.path.basename(filepath)
        filesize = os.path.getsize(filepath)

        print(f"[S1] Sending via BT: {filename}  ({filesize} bytes)")

        # Header line: HANDOFF|filename|filesize\n
        header = f"HANDOFF|{filename}|{filesize}\n"
        sock.sendall(header.encode())

        sent = 0
        with open(filepath, "rb") as f:
            while True:
                chunk = f.read(4096)
                if not chunk:
                    break
                sock.sendall(chunk)
                sent += len(chunk)
                pct = int(sent / filesize * 100)
                print(f"\r[S1] Uploading via BT... {pct}%", end="", flush=True)

        print(f"\n[S1] ✅ BT Transfer complete: {filename}")
        transfer_done   = True
        transfer_active = False

    except Exception as e:
        print(f"\n[S1] ❌ BT Transfer failed: {e}")
        transfer_active = False
        with s2_lock:
            s2_sock = None


def do_transfer():
    """Trigger BT file transfer in background thread."""
    global transfer_active, grabbed_file
    if grabbed_file and not transfer_done:
        transfer_active = True
        bt_notify_s2(f"INCOMING|{grabbed_title}|{os.path.basename(grabbed_file)}")
        threading.Thread(target=bt_send_file, args=(grabbed_file,), daemon=True).start()


def reset_all():
    global grabbed_hwnd, grabbed_title, grabbed_file
    global is_grabbed, transfer_done, transfer_active, at_edge
    is_grabbed      = False
    transfer_done   = False
    transfer_active = False
    at_edge         = False
    grabbed_hwnd    = None
    grabbed_title   = ""
    grabbed_file    = None
    print("[S1] RESET")
    bt_notify_s2("RESET")


# ── BT LISTENER (reads from ESP32 RFCOMM) ────────────────────────

def bt_listener():
    global grabbed_hwnd, grabbed_title, grabbed_file
    global is_grabbed, gyro_z, gyro_y, drag_x, drag_y
    global transfer_done, at_edge

    while True:
        esp_sock = bt_connect_esp()
        buffer   = b""

        try:
            while True:
                chunk = esp_sock.recv(256)
                if not chunk:
                    print("[S1] ⚠️ ESP32 BT dropped — reconnecting")
                    break

                buffer += chunk

                # Parse newline-delimited messages
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    msg = line.decode("utf-8", errors="ignore").strip()
                    if not msg:
                        continue

                    # ── BTN1 → SELECT ────────────────────────
                    if msg == "SELECT":
                        hwnd, title = get_active_window()
                        if hwnd:
                            grabbed_hwnd  = hwnd
                            grabbed_title = title
                            is_grabbed    = True
                            transfer_done = False
                            drag_x, drag_y, _, _ = get_rect(hwnd)
                            grabbed_file  = find_file(title)
                            print(f"[S1] ✅ Grabbed : {grabbed_title}")
                            print(f"[S1]    File    : {grabbed_file}")
                            print(f"[S1]    Pos     : ({drag_x:.0f}, {drag_y:.0f})")
                        else:
                            print("[S1] No window found — click target window first")

                    # ── BTN2 → RELEASE / SEND ────────────────
                    elif msg == "RELEASE":
                        if is_grabbed:
                            if at_edge and not transfer_done:
                                print("[S1] 📤 Edge reached → SENDING TO SYSTEM 2")
                                do_transfer()
                                is_grabbed = False
                            else:
                                is_grabbed = False
                                print(f"[S1] Dropped at ({drag_x:.0f}, {drag_y:.0f})")
                        else:
                            print("[S1] Nothing grabbed")

                    # ── BTN3 → RESET ─────────────────────────
                    elif msg == "RESET":
                        reset_all()

                    # ── MOTION ───────────────────────────────
                    elif msg.startswith("MOTION:"):
                        parts = msg.split(":")
                        if len(parts) == 3:
                            gyro_z = float(parts[1])
                            gyro_y = float(parts[2])

        except Exception as e:
            print(f"[S1] BT read error: {e}")

        try:
            esp_sock.close()
        except Exception:
            pass
        print("[S1] 🔄 Reconnecting to ESP32 BT in 5s...")
        time.sleep(5)


# ── WINDOW MOVER (unchanged logic from v5) ───────────────────────

def window_mover():
    global drag_x, drag_y, at_edge

    while True:
        if is_grabbed and grabbed_hwnd:
            dx = gyro_z * MOVE_SENS
            dy = gyro_y * MOVE_SENS
            if abs(dx) < 1.5: dx = 0
            if abs(dy) < 1.5: dy = 0

            drag_x += dx
            drag_y += dy

            drag_x = max(-200, min(SCREEN_W + 250, drag_x))
            drag_y = max(0,    min(SCREEN_H - 80,  drag_y))

            move_window(grabbed_hwnd, drag_x, drag_y)

            at_edge = drag_x >= EDGE_THRESH

            if drag_x >= SCREEN_W - 280 and not transfer_done:
                bt_notify_s2(f"APPROACHING|{grabbed_title}")

        time.sleep(0.016)


# ── PYGAME OVERLAY (unchanged from v5) ───────────────────────────

def run_overlay():
    pygame.init()
    screen  = pygame.display.set_mode((660, 68), pygame.NOFRAME)
    pygame.display.set_caption("SnapShift S1")
    clock   = pygame.time.Clock()
    font    = pygame.font.SysFont("Segoe UI", 17)
    font_sm = pygame.font.SysFont("Segoe UI", 13)

    hwnd_bar = pygame.display.get_wm_info()["window"]
    ctypes.windll.user32.SetWindowPos(hwnd_bar, -1, 10, 10, 0, 0, 0x0001)

    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                pygame.quit(); sys.exit()

        screen.fill((12, 12, 20))

        if transfer_active:
            pygame.draw.rect(screen, (30, 80, 200), (0, 0, 660, 68))
            txt  = font.render(
                f"📤  Uploading via BT → System 2 ...  {grabbed_title[:30]}",
                True, (180, 220, 255)
            )
            hint = font_sm.render("Bluetooth transfer in progress ...", True, (120, 160, 220))

        elif transfer_done:
            pygame.draw.rect(screen, (10, 50, 20), (0, 0, 660, 68))
            txt  = font.render(
                f"✅  Sent: {grabbed_title[:35]}  →  System 2 (BT)",
                True, (80, 220, 100)
            )
            hint = font_sm.render("BTN3 = Reset  |  BTN1 = Grab another", True, (60, 160, 80))

        elif is_grabbed and at_edge:
            pygame.draw.rect(screen, (80, 50, 10), (0, 0, 660, 68))
            txt  = font.render(
                f"→ EDGE  {grabbed_title[:30]}  |  BTN2 = SEND TO SYSTEM 2 (BT)",
                True, (255, 190, 50)
            )
            hint = font_sm.render("Tilt right, press BTN2 to fire BT transfer", True, (200, 160, 60))

        elif is_grabbed:
            txt  = font.render(
                f"[MOVING]  {grabbed_title[:38]}  |  BTN2=Drop  BTN3=Reset",
                True, (255, 170, 30)
            )
            hint = font_sm.render(
                f"Pos: ({drag_x:.0f}, {drag_y:.0f})  |  Tilt right to reach edge",
                True, (160, 120, 30)
            )

        else:
            txt  = font.render(
                "SnapShift S1  |  Click a window → Press BTN1 to grab",
                True, (80, 210, 100)
            )
            hint = font_sm.render("BTN2 = Send/Drop  |  BTN3 = Reset", True, (60, 140, 80))

        screen.blit(txt,  (10, 8))
        screen.blit(hint, (10, 46))
        pygame.display.flip()
        clock.tick(30)


# ── MAIN ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 52)
    print("  SnapShift System 1 — Bluetooth RFCOMM Only")
    print(f"  ESP32 BT  : {ESP32_MAC}  ch{RFCOMM_PORT_ESP}")
    print(f"  System 2  : {SYSTEM2_MAC}  ch{RFCOMM_PORT_S2}")
    print(f"  Outbox    : {TRANSFER_DIR}")
    print("  BTN1=Grab  BTN2=Send/Drop  BTN3=Reset")
    print("=" * 52)

    os.makedirs(TRANSFER_DIR, exist_ok=True)

    threading.Thread(target=bt_listener,  daemon=True).start()
    threading.Thread(target=window_mover, daemon=True).start()
    run_overlay()