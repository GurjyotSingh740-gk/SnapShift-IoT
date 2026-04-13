# SnapShift — SYSTEM 2 (Receiver)
# Receives file from System 1 over TCP
# After file lands:  BTN2 = Finalize (keep + open)  |  BTN3 = Cancel (delete)
#
# Run: python system2_receiver.py

import socket
import threading
import time
import sys
import os
import ctypes
import subprocess

import pygame

# ── CONFIG ────────────────────────────────────────────────────────
SAVE_DIR      = r"C:\SnapShift\inbox"
HANDOFF_PORT  = 6000    # TCP — file bytes arrive here
NOTIFY_PORT   = 6001    # UDP — APPROACHING / INCOMING / RESET from S1
ESP_UDP_PORT  = 5007    # MOTION from ESP32 (not used for movement, just logging)
SCREEN_W      = 1920
SCREEN_H      = 1080
# ─────────────────────────────────────────────────────────────────

incoming_title   = ""
incoming_fname   = ""
file_received    = False
received_path    = ""

# States: "idle" | "approaching" | "receiving" | "arrived" | "finalizing"
state = "idle"

# Animation
anim_x   = float(SCREEN_W + 160)   # file slides in from RIGHT edge
anim_y   = float(SCREEN_H / 2)
anim_vel = 0.0
SLIDE_TARGET = SCREEN_W * 0.38

# Progress bar (0.0 → 1.0)
recv_progress = 0.0


def reset_all():
    global state, incoming_title, incoming_fname
    global file_received, received_path, recv_progress
    global anim_x, anim_vel
    state         = "idle"
    incoming_title  = ""
    incoming_fname  = ""
    file_received   = False
    received_path   = ""
    recv_progress   = 0.0
    anim_x          = float(SCREEN_W + 160)
    anim_vel        = 0.0
    print("[S2] RESET")


def tcp_file_receiver():
    """Accept one TCP connection, read HANDOFF header, save file."""
    global state, file_received, received_path, incoming_fname
    global recv_progress, anim_x, anim_y, anim_vel

    os.makedirs(SAVE_DIR, exist_ok=True)
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", HANDOFF_PORT))
    server.listen(3)
    print(f"[S2] TCP listening on port {HANDOFF_PORT}")

    while True:
        try:
            conn, addr = server.accept()
            print(f"[S2] Connection from {addr[0]}")
            state = "receiving"
            recv_progress = 0.0

            # Read header: HANDOFF|filename|filesize\n
            header = b""
            while b"\n" not in header:
                c = conn.recv(1)
                if not c:
                    break
                header += c

            parts = header.decode().strip().split("|")
            if len(parts) != 3 or parts[0] != "HANDOFF":
                print(f"[S2] Bad header: {header}")
                conn.close()
                continue

            filename = parts[1]
            filesize = int(parts[2])
            save_path = os.path.join(SAVE_DIR, filename)

            print(f"[S2] Receiving: {filename}  ({filesize} bytes)")

            received_bytes = 0
            with open(save_path, "wb") as f:
                while received_bytes < filesize:
                    chunk = conn.recv(min(4096, filesize - received_bytes))
                    if not chunk:
                        break
                    f.write(chunk)
                    received_bytes += len(chunk)
                    recv_progress = received_bytes / filesize
                    print(f"\r[S2]   {int(recv_progress*100):3d}%  {received_bytes}/{filesize}", end="", flush=True)

            conn.close()

            if received_bytes == filesize:
                print(f"\n[S2] ✅ Saved: {save_path}")
                incoming_fname = filename
                received_path  = save_path
                file_received  = True
                state          = "arrived"
                anim_x         = float(SCREEN_W + 160)   # reset animation start
                anim_y         = float(SCREEN_H / 2)
                anim_vel       = -28.0                    # slide in from right
            else:
                print(f"\n[S2] ❌ Incomplete: {received_bytes}/{filesize}")
                state = "idle"

        except Exception as e:
            print(f"[S2] TCP error: {e}")


def udp_notify_receiver():
    """Receive control messages from System 1."""
    global state, incoming_title, incoming_fname, anim_y

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", NOTIFY_PORT))
    sock.settimeout(0.5)
    print(f"[S2] UDP notify on port {NOTIFY_PORT}")

    while True:
        try:
            data, _ = sock.recvfrom(1024)
            msg = data.decode("utf-8").strip()

            if msg.startswith("APPROACHING|"):
                title = msg.split("|", 1)[1]
                incoming_title = title
                if state == "idle":
                    state = "approaching"
                print(f"[S2] Approaching: {title}")

            elif msg.startswith("INCOMING|"):
                parts = msg.split("|")
                incoming_title = parts[1] if len(parts) > 1 else "File"
                incoming_fname = parts[2] if len(parts) > 2 else ""
                state = "approaching"
                print(f"[S2] Incoming: {incoming_title}")

            elif msg == "RESET":
                reset_all()

        except socket.timeout:
            pass
        except Exception as e:
            print(f"[S2] UDP error: {e}")


def esp_udp_listener():
    """Listen for BTN2/BTN3 from ESP32 on its System 2 port."""
    global state, received_path

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", ESP_UDP_PORT))
    sock.settimeout(0.5)
    print(f"[S2] ESP32 button port on UDP {ESP_UDP_PORT}")

    while True:
        try:
            data, _ = sock.recvfrom(256)
            msg = data.decode("utf-8").strip()

            if state == "arrived" or state == "finalizing":

                if msg == "RELEASE":     # BTN2 → Finalize
                    state = "finalizing"
                    print(f"[S2] ✅ FINALIZED: {received_path}")
                    if received_path and os.path.exists(received_path):
                        subprocess.Popen(f'explorer /select,"{received_path}"')

                elif msg == "RESET":     # BTN3 → Cancel / delete
                    if received_path and os.path.exists(received_path):
                        os.remove(received_path)
                        print(f"[S2] 🗑️  Deleted: {received_path}")
                    reset_all()

        except socket.timeout:
            pass
        except Exception as e:
            print(f"[S2] ESP UDP error: {e}")


def animation_ticker():
    """Slide-in physics for the file icon."""
    global anim_x, anim_vel

    while True:
        if state == "arrived":
            if anim_x > SLIDE_TARGET:
                anim_vel -= 3.5          # accelerate left
                anim_x   += anim_vel
                if anim_x <= SLIDE_TARGET:
                    anim_x   = SLIDE_TARGET
                    anim_vel = 0.0
        time.sleep(0.016)


def draw_file_card(screen, x, y, label, font_sm, arrived, progress=1.0):
    """Draw animated file card with glow, label, and progress bar."""
    W, H = 170, 110
    cx = int(x - W / 2)
    cy = int(y - H / 2)

    # glow
    glow = pygame.Surface((W + 40, H + 40), pygame.SRCALPHA)
    gc   = (50, 200, 100, 55) if arrived else (80, 140, 255, 50)
    pygame.draw.rect(glow, gc, (0, 0, W + 40, H + 40), border_radius=22)
    screen.blit(glow, (cx - 20, cy - 20))

    # card body
    card_col = (20, 110, 55) if arrived else (25, 65, 155)
    pygame.draw.rect(screen, card_col, (cx, cy, W, H), border_radius=13)
    pygame.draw.rect(screen, (200, 230, 255, 100), (cx, cy, W, H), 2, border_radius=13)

    # file icon
    pygame.draw.rect(screen, (190, 215, 255), (cx + 18, cy + 12, 52, 64), border_radius=5)
    for i, ly in enumerate([cy + 24, cy + 34, cy + 44]):
        pygame.draw.line(screen, (100, 140, 200), (cx + 26, ly), (cx + 56, ly), 2)

    # progress bar (shown while receiving)
    if not arrived and progress < 1.0:
        bar_w = int((W - 20) * progress)
        pygame.draw.rect(screen, (40, 40, 80), (cx + 10, cy + H - 18, W - 20, 8), border_radius=4)
        pygame.draw.rect(screen, (80, 160, 255), (cx + 10, cy + H - 18, bar_w, 8), border_radius=4)

    # label
    short = (label[:16] + "…") if len(label) > 16 else label
    txt   = font_sm.render(short, True, (240, 240, 255))
    screen.blit(txt, (cx + W // 2 - txt.get_width() // 2, cy + H - 18))

    # motion trail
    if not arrived:
        for i in range(1, 6):
            r = max(1, 9 - i * 2)
            s = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
            pygame.draw.circle(s, (80, 160, 255, 160 - i * 30), (r, r), r)
            screen.blit(s, (cx + W + i * 22, cy + H // 2 - r))


def run_overlay():
    pygame.init()
    screen = pygame.display.set_mode((SCREEN_W, SCREEN_H),
                                     pygame.NOFRAME | pygame.HWSURFACE | pygame.DOUBLEBUF)
    pygame.display.set_caption("SnapShift S2")
    clock    = pygame.time.Clock()
    font_big = pygame.font.SysFont("Segoe UI", 22, bold=True)
    font_sm  = pygame.font.SysFont("Segoe UI", 15)

    hwnd_over = pygame.display.get_wm_info()["window"]
    ctypes.windll.user32.SetWindowPos(hwnd_over, -1, 0, 0, 0, 0, 0x0003)

    print("[S2] Overlay active — waiting for file from System 1")

    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                pygame.quit(); sys.exit()

        # ── Background ──────────────────────────────────────
        active = state not in ("idle",)
        if active:
            bg = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
            bg.fill((0, 0, 0, 80))
            screen.fill((10, 10, 20))
            screen.blit(bg, (0, 0))
        else:
            screen.fill((12, 12, 22))

        # ── Status bar ──────────────────────────────────────
        bar_h = 56
        if state == "approaching":
            pygame.draw.rect(screen, (40, 30, 8), (0, 0, SCREEN_W, bar_h))
            txt = font_big.render(
                f"📡  Incoming: {incoming_title[:46]}  —  Get ready!",
                True, (255, 200, 60)
            )

        elif state == "receiving":
            pygame.draw.rect(screen, (10, 18, 40), (0, 0, SCREEN_W, bar_h))
            pct = int(recv_progress * 100)
            txt = font_big.render(
                f"⬇  Receiving: {incoming_fname[:40]}  {pct}%",
                True, (80, 180, 255)
            )
            # global progress bar across top
            bar_w = int(SCREEN_W * recv_progress)
            pygame.draw.rect(screen, (20, 40, 80), (0, bar_h - 6, SCREEN_W, 6))
            pygame.draw.rect(screen, (80, 160, 255), (0, bar_h - 6, bar_w, 6))

        elif state == "arrived":
            pygame.draw.rect(screen, (10, 35, 18), (0, 0, SCREEN_W, bar_h))
            txt = font_big.render(
                f"✅  {incoming_fname}  —  Press BTN2 to Open | BTN3 to Delete",
                True, (80, 230, 110)
            )

        elif state == "finalizing":
            pygame.draw.rect(screen, (10, 35, 18), (0, 0, SCREEN_W, bar_h))
            txt = font_big.render(
                f"📂  Opened: {received_path}",
                True, (120, 255, 140)
            )

        else:
            pygame.draw.rect(screen, (14, 14, 24), (0, 0, SCREEN_W, bar_h))
            txt = font_sm.render(
                "SnapShift S2 — Waiting for file from System 1",
                True, (60, 65, 100)
            )

        screen.blit(txt, (20, 16))

        # ── File card animation ──────────────────────────────
        if state in ("arrived", "finalizing"):
            draw_file_card(screen, anim_x, anim_y,
                           incoming_fname, font_sm,
                           arrived=True)

        elif state == "receiving":
            draw_file_card(screen, anim_x, anim_y,
                           incoming_fname or incoming_title, font_sm,
                           arrived=False, progress=recv_progress)

        pygame.display.flip()
        clock.tick(60)


if __name__ == "__main__":
    print("=" * 52)
    print("  SnapShift System 2 — Receiver")
    print(f"  Save dir : {SAVE_DIR}")
    print(f"  TCP port : {HANDOFF_PORT}  (file bytes)")
    print(f"  UDP port : {NOTIFY_PORT}   (notify from S1)")
    print(f"  ESP port : {ESP_UDP_PORT}  (BTN2/BTN3 from ESP32)")
    print("  BTN2 = Finalize/Open  |  BTN3 = Cancel/Delete")
    print("=" * 52)

    os.makedirs(SAVE_DIR, exist_ok=True)

    threading.Thread(target=tcp_file_receiver,   daemon=True).start()
    threading.Thread(target=udp_notify_receiver, daemon=True).start()
    threading.Thread(target=esp_udp_listener,    daemon=True).start()
    threading.Thread(target=animation_ticker,    daemon=True).start()
    run_overlay()