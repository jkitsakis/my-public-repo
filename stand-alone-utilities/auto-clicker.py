import time
import threading
import random
import pyautogui
from pynput import mouse

CLICK_INTERVAL = 30      # seconds (2 minutes)
JITTER_INTERVAL = 15      # seconds
JITTER_PIXELS = 5

target_position = None
confirmed = False


def on_click(x, y, button, pressed):
    global target_position, confirmed

    if pressed:
        target_position = (x, y)
        print(f"\n📍 Clicked at position: X={x}, Y={y}")
        answer = input("Is this the desired spot? (y/n): ").strip().lower()

        if answer == "y":
            confirmed = True
            print("✅ Position confirmed.")
            return False  # stop listener
        else:
            print("❌ Not confirmed. Click again...")


def capture_position():
    print("🖱️ Click on the desired button on the screen...")
    with mouse.Listener(on_click=on_click) as listener:
        listener.join()


def auto_click():
    while True:
        time.sleep(CLICK_INTERVAL)
        pyautogui.moveTo(*target_position, duration=0.3)
        pyautogui.click()
        print(f"🖱️ Clicked at {target_position}")


def jitter_mouse():
    while True:
        time.sleep(JITTER_INTERVAL)
        dx = random.randint(-JITTER_PIXELS, JITTER_PIXELS)
        dy = random.randint(-JITTER_PIXELS, JITTER_PIXELS)
        pyautogui.moveRel(dx, dy, duration=0.2)


def main():
    global confirmed

    while not confirmed:
        capture_position()

    print("🚀 Automation started. Press CTRL+C to stop.")

    click_thread = threading.Thread(target=auto_click, daemon=True)
    jitter_thread = threading.Thread(target=jitter_mouse, daemon=True)

    click_thread.start()
    jitter_thread.start()

    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
