import requests
import pyautogui
import pytesseract
import sys
import datetime
from plyer import notification
from pynput import mouse
import threading
from send_email import EmailSender


# Your OpenAI API Key
# api_key = ''
# client = OpenAI(api_key=api_key)
THEME= "Κειμενογράφος Ψηφιακού Περιεχομένου"
OPENROUTER_API_KEY = ""  # put your key here
OPENROUTER_MODEL = "openai/gpt-4.1"  # or gpt-4o, gpt-4.1-mini, etc.

OPENROUTER_HEADERS = {
    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
    "Content-Type": "application/json",
    "HTTP-Referer": "http://localhost",   # required by OpenRouter
    "X-Title": f"{THEME}"
}

prompt= (f"You are an expert in {THEME} certification exam and Azure Machine Learning Studio. "
         f"I will provide you with a screenshot containing  multiple-choice question. "
         f"- Identify the **question and possible answers options** to choose from, related to {THEME}. "
         f"- Please respond **only the correct answer option(s)**, nothing else. Do not include any explanations or extra text."
         f"- If the question asks to match items, please respond with the matched pairs. Only provide the matches, no explanations"
         f"- Please answer the following question **USING ONLY** the content from these study resource links and their sublinks:"
         f"-- https://drive.google.com/file/d/14Rzs_R1zc2sOy4A-4Cha8sVgiOsN4Xr3/view?usp=drive_link"
         )

def log(text):
    with open("assist.log", "a", encoding="utf-8") as f:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"[{timestamp}] {text}\n")



def process_screenshot():
    log("processing screenshot...")
    try:
        screenshot = pyautogui.screenshot()
        custom_config = r'--oem 3 --psm 6'
        text = pytesseract.image_to_string(screenshot, config=custom_config)

        if text.strip() == "":
            log("No text detected in screenshot!")
            print("No text detected in screenshot!")
            return

        question = text
        print(f"Question :\n{question}")
        log(f"Question :\n{question}")

        payload = {
            "model": OPENROUTER_MODEL,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": question}
            ],
            "temperature": 0,
            "max_tokens": 250,
            "top_p": 1
        }

        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=OPENROUTER_HEADERS,
            json=payload,
            timeout=60
        )

        response.raise_for_status()
        data = response.json()

        answer = data["choices"][0]["message"]["content"].strip()
        print(answer)
        log(f"Answer:\n{answer}\n{'-' * 40}")

        EmailSender.send_email(
            f"{THEME} Question",
            f"Question:\n{question}\n\nAnswer:\n{answer}"
        )

    except Exception as e:
        log(f"Error in process_screenshot: {e}")
        print(f"Error in process_screenshot: {e}")


def on_click(x, y, button, pressed):
    if pressed and (button == mouse.Button.middle or button == mouse.Button.right):
        print(f"Mouse  button pressed at ({x}, {y})")
        threading.Thread(target=process_screenshot, daemon=True).start()

def main():
    log("Ready!!! Listening for mouse action...")
    notification.notify(
        title="Assistant",
        message="App is running",
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
