# plugins/messenger.py

import time
import subprocess
import webbrowser

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.06
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

try:
    import pyperclip
    _PYPERCLIP = True
except ImportError:
    _PYPERCLIP = False

class Messenger:
    def __init__(self):
        print("✉️ Sirius Otonom Mesajlaşma Asistanı Aktif!")

    def _require_pyautogui(self):
        if not _PYAUTOGUI:
            raise RuntimeError("PyAutoGUI kurulu değil. Lütfen kurun: pip install pyautogui")

    def _paste_text(self, text: str) -> None:
        self._require_pyautogui()
        if _PYPERCLIP:
            pyperclip.copy(text)
            time.sleep(0.15)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.1)
        else:
            pyautogui.write(text, interval=0.03)

    def _clear_and_paste(self, text: str) -> None:
        self._require_pyautogui()
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.1)
        pyautogui.press("delete")
        time.sleep(0.1)
        self._paste_text(text)

    def _open_app(self, app_name: str) -> bool:
        self._require_pyautogui()
        try:
            pyautogui.press("win")
            time.sleep(0.5)
            self._paste_text(app_name)
            time.sleep(0.6)
            pyautogui.press("enter")
            time.sleep(2.5)
            return True
        except Exception as e:
            print(f"[Mesajcı] ⚠️ {app_name} açılamadı: {e}")
            return False

    def _open_browser_url(self, url: str) -> bool:
        try:
            webbrowser.open(url)
            time.sleep(4.0) 
            return True
        except Exception as e:
            print(f"[Mesajcı] ⚠️ Tarayıcı açılamadı: {e}")
            return False

    def _search_in_app(self, query: str) -> None:
        self._require_pyautogui()
        pyautogui.hotkey("ctrl", "f")
        time.sleep(0.5)
        self._clear_and_paste(query)
        time.sleep(1.0)

    def _desktop_send(self, app_name: str, receiver: str, message: str) -> str:
        if not self._open_app(app_name):
            return f"{app_name} uygulaması açılamadı."

        time.sleep(1.0)
        self._search_in_app(receiver)
        pyautogui.press("enter")
        time.sleep(0.8)

        self._paste_text(message)
        time.sleep(0.2)
        pyautogui.press("enter")
        time.sleep(0.3)
        return f"Mesaj {app_name} üzerinden {receiver} adlı kişiye gönderildi."

    def _send_instagram(self, receiver: str, message: str) -> str:
        self._require_pyautogui()
        if not self._open_browser_url("https://www.instagram.com/direct/new/"):
            return "Instagram tarayıcıda açılamadı."

        self._paste_text(receiver)
        time.sleep(1.5)

        pyautogui.press("down")
        time.sleep(0.3)
        pyautogui.press("enter")   
        time.sleep(0.4)

        for _ in range(4):
            pyautogui.press("tab")
            time.sleep(0.15)
        pyautogui.press("enter")
        time.sleep(2.0)

        self._paste_text(message)
        time.sleep(0.2)
        pyautogui.press("enter")
        time.sleep(0.3)

        return f"Mesaj Instagram üzerinden {receiver} adlı kişiye gönderildi."

    def _send_messenger(self, receiver: str, message: str) -> str:
        self._require_pyautogui()
        if not self._open_browser_url("https://www.messenger.com/"):
            return "Messenger tarayıcıda açılamadı."

        self._search_in_app(receiver)
        time.sleep(0.5)
        pyautogui.press("down")
        time.sleep(0.3)
        pyautogui.press("enter")
        time.sleep(1.0)

        self._paste_text(message)
        time.sleep(0.2)
        pyautogui.press("enter")
        time.sleep(0.3)

        return f"Mesaj Messenger üzerinden {receiver} adlı kişiye gönderildi."

    def execute(self, params: dict) -> str:
        receiver = params.get("receiver", "").strip()
        message_text = params.get("message", "").strip()
        platform = params.get("platform", "whatsapp").strip().lower()

        if not receiver: return "Lütfen bir alıcı belirtin."
        if not message_text: return "Lütfen gönderilecek mesajı belirtin."
        if not _PYAUTOGUI: return "PyAutoGUI kurulu değil, masaüstü kontrol edilemiyor."

        print(f"[Mesajcı] 📨 {platform.title()} -> {receiver}: {message_text[:30]}...")

        try:
            if platform in ["whatsapp", "wp", "wapp"]:
                return self._desktop_send("WhatsApp", receiver, message_text)
            elif platform in ["telegram", "tg"]:
                return self._desktop_send("Telegram", receiver, message_text)
            elif platform in ["discord", "dc"]:
                return self._desktop_send("Discord", receiver, message_text)
            elif platform in ["signal"]:
                return self._desktop_send("Signal", receiver, message_text)
            elif platform in ["instagram", "ig", "insta"]:
                return self._send_instagram(receiver, message_text)
            elif platform in ["messenger", "facebook", "fb"]:
                return self._send_messenger(receiver, message_text)
            else:
                return self._desktop_send(platform.title(), receiver, message_text)
        except Exception as e:
            return f"Mesaj gönderilemedi: {e}"