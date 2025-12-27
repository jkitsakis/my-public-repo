import base64
import io

import requests
import sys
import datetime
from plyer import notification
from pynput import mouse
import threading
from mss import mss
from PIL import Image
from send_email import EmailSender

THEME= "Digital Content Writer"
LANGUAGE= "GREEK"
OPENROUTER_API_KEY = ""  # put your key here
OPENROUTER_MODEL = "openai/gpt-4.1"  # or gpt-4o, gpt-4.1-mini, etc.

OPENROUTER_HEADERS = {
    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
    "Content-Type": "application/json",
    "HTTP-Referer": "http://localhost",   # required by OpenRouter
    "X-Title": f"{THEME}"
}

prompt = (
    f"You are an expert in {THEME}. and in sections : \n"
    f"- Copywriting \n"
    f"- SEO Copywriting \n"
    f"- Audience Analysis and Understanding \n"
    f"It is about online training program in {LANGUAGE}. Answer in {LANGUAGE}.\n"
    f"Do this IN ORDER:\n"
    f"1) Silently reconstruct the clean question and the answer options from the OCR text.\n"
    f"2) **READ AND USE** the content from these resources and their sublinks/files:\n"
    f"    -- https://drive.google.com/file/d/14Rzs_R1zc2sOy4A-4Cha8sVgiOsN4Xr3/view?usp=drive_link\n"
    f"\n"
    f"Output rules (strict):\n"
    f"- Show the reconstructed question with answers .\n"
    f"- The final answer phrase or option letter(s) **NOTHING ELSE** \n"
    f"- Prompt final answer as \"Answer:\"\n"
    f"- If it’s a matching question, response should contain matched pairs (e.g., A→III, B→I, C→II).\n"
    f"- If a numeric/algebraic result is requested, return ONLY the final value/expression (fractions allowed), no steps.\n"
)



def log(text):
    with open("assist.log", "a", encoding="utf-8") as f:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"[{timestamp}] {text}\n")



def process_screenshot():
    log("processing screenshot (vision)...")
    try:
        # 1) Take screenshot using mss (Python 3.12 safe)
        with mss() as sct:
            monitor = sct.monitors[1]  # primary monitor
            sct_img = sct.grab(monitor)
            screenshot = Image.frombytes(
                "RGB",
                sct_img.size,
                sct_img.rgb
            )

        # 2) Save screenshot to memory (PNG)
        buf = io.BytesIO()
        screenshot.save(buf, format="PNG")
        img_bytes = buf.getvalue()
        buf.close()

        # 3) Base64 encode as data URL
        b64 = base64.b64encode(img_bytes).decode("ascii")
        data_url = f"data:image/png;base64,{b64}"

        # 4) OpenRouter Vision request
        payload = {
            "model": OPENROUTER_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": prompt
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "This screenshot contains an exam question. "
                                "Read the question and answer choices from the image and follow the output rules."
                            )
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": data_url
                            }
                        }
                    ]
                }
            ],
            "temperature": 0,
            "max_tokens": 300
        }

        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=OPENROUTER_HEADERS,
            json=payload,
            timeout=60
        )

        if response.status_code != 200:
            raise Exception(f"OpenRouter error {response.status_code}: {response.text}")

        data = response.json()
        answer = data["choices"][0]["message"]["content"].strip()

        print(f"Answer: {answer}")
        log(f"Answer:\n{answer}\n{'-' * 40}")

        # Optional email
        EmailSender.send_email(
            f"{THEME}",
            f"Answer:\n{answer}"
        )

    except Exception as e:
        log(f"Error in process_screenshot (vision): {e}")
        print(f"Error in process_screenshot (vision): {e}")


def on_click(x, y, button, pressed):
    if pressed and (button == mouse.Button.middle or button == mouse.Button.right):
        print(f"Mouse  button pressed at ({x}, {y})")
        threading.Thread(target=process_screenshot, daemon=True).start()

def main():
    log("Ready!!! Listening for mouse action...")
    notification.notify(
        title="TutorAssistant",
        message="App is running ...",
        timeout=5
    )

    listener = mouse.Listener(on_click=on_click)
    listener.start()

    try:
        listener.join()
    except KeyboardInterrupt:
        print("Exiting...")
        log("TutorAssistant exiting...")
        listener.stop()
        sys.exit(0)


if __name__ == "__main__":
    main()
