# plugins/computer_control.py

import io
import json
import re
import string
import subprocess
import time
import random
from pathlib import Path

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

try:
    import pyperclip
    _PYPERCLIP = True
except ImportError:
    _PYPERCLIP = False

class ComputerController:
    def __init__(self):
        print("💻 Sirius Bilgisayar ve Görme Kontrolcüsü Aktif!")
        self.gemini_key = "" # main.py üzerinden yüklenecek

        # Türkçe sahte veriler için listeler
        self.isimler = ["Ahmet", "Ayşe", "Mehmet", "Fatma", "Mustafa", "Zeynep", "Ali", "Elif", "Can", "Selin"]
        self.soyadlar = ["Yılmaz", "Kaya", "Demir", "Çelik", "Şahin", "Yıldız", "Öztürk", "Aydın", "Özdemir", "Arslan"]
        self.sehirler = ["İstanbul", "Ankara", "İzmir", "Bursa", "Antalya", "Adana", "Konya", "Gaziantep"]
        self.domainler = ["gmail.com", "outlook.com", "yahoo.com", "yandex.com"]

    def _require_pyautogui(self):
        if not _PYAUTOGUI: raise RuntimeError("PyAutoGUI kurulu değil. Terminale yazın: pip install pyautogui")

    def _rastgele_veri(self, veri_tipi: str) -> str:
        dt = veri_tipi.lower().strip()
        if dt == "first_name": return random.choice(self.isimler)
        if dt == "last_name": return random.choice(self.soyadlar)
        if dt == "name": return f"{random.choice(self.isimler)} {random.choice(self.soyadlar)}"
        if dt == "email": return f"{random.choice(self.isimler).lower()}.{random.choice(self.soyadlar).lower()}{random.randint(10,999)}@{random.choice(self.domainler)}"
        if dt == "username": return f"{random.choice(self.isimler).lower()}{random.randint(100, 9999)}"
        if dt == "password":
            chars = string.ascii_letters + string.digits + "!@#$%"
            raw = random.choice(string.ascii_uppercase) + random.choice(string.digits) + random.choice("!@#$%") + "".join(random.choices(chars, k=9))
            return "".join(random.sample(raw, len(raw)))
        if dt == "phone": return f"+905{random.randint(30,59)}{random.randint(1000000, 9999999)}"
        if dt == "city": return random.choice(self.sehirler)
        return f"rastgele_{dt}_{random.randint(1000, 9999)}"

    def _ekranda_bul(self, aciklama: str):
        if not self.gemini_key:
            print("[Bilgisayar] ⚠️ Ekranı görmek için Gemini API Key eksik!")
            return None

        try:
            from google import genai
            from google.genai import types as gtypes

            self._require_pyautogui()
            w, h = pyautogui.size()
            img = pyautogui.screenshot()
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            image_bytes = buf.getvalue()

            client = genai.Client(api_key=self.gemini_key)
            prompt = (
                f"Bu {w}x{h} piksellik bir ekran görüntüsüdür. "
                f"Şu öğeyi bul: '{aciklama}'. "
                f"SADECE merkez koordinatlarını x,y formatında yaz. "
                f"Eğer ekranda göremiyorsan sadece NOT_FOUND yaz."
            )

            response = client.models.generate_content(
                model="gemini-2.5-flash-lite", # Hızlı ve ucuz görme modeli
                contents=[gtypes.Part.from_bytes(data=image_bytes, mime_type="image/png"), prompt],
            )

            text = (response.text or "").strip()
            if "NOT_FOUND" in text.upper(): return None

            match = re.search(r"(\d+)\s*,\s*(\d+)", text)
            if match: return int(match.group(1)), int(match.group(2))
        except Exception as e:
            print(f"[Bilgisayar] ⚠️ Ekran okuma hatası: {e}")
        return None

    def execute(self, action: str, params: dict) -> str:
        self._require_pyautogui()
        try:
            if action == "type":
                text = params.get("text", "")
                pyautogui.typewrite(text, interval=0.03)
                return f"Yazıldı: {text[:30]}"
                
            elif action == "smart_type":
                text = params.get("text", "")
                if params.get("clear_first", True):
                    pyautogui.hotkey("ctrl", "a")
                    pyautogui.press("delete")
                    time.sleep(0.1)
                if _PYPERCLIP and len(text) > 20:
                    pyperclip.copy(text)
                    pyautogui.hotkey("ctrl", "v")
                else:
                    pyautogui.typewrite(text, interval=0.03)
                return f"Akıllı yazıldı: {text[:30]}"

            elif action in ("click", "left_click", "double_click", "right_click"):
                x, y = params.get("x"), params.get("y")
                btn = "right" if action == "right_click" else "left"
                clks = 2 if action == "double_click" else 1
                if x and y: pyautogui.click(x, y, button=btn, clicks=clks)
                else: pyautogui.click(button=btn, clicks=clks)
                return "Tıklandı."

            elif action == "scroll":
                yon = params.get("direction", "down")
                miktar = int(params.get("amount", 3))
                pyautogui.scroll(-miktar * 100 if yon == "down" else miktar * 100)
                return f"{yon} yönüne kaydırıldı."

            elif action == "copy":
                if _PYPERCLIP: return pyperclip.paste()
                pyautogui.hotkey("ctrl", "c")
                return "Kopyalandı."

            elif action == "paste":
                if _PYPERCLIP:
                    pyperclip.copy(params.get("text", ""))
                    pyautogui.hotkey("ctrl", "v")
                    return "Yapıştırıldı."
                return "Pano kütüphanesi eksik."

            elif action == "screen_click":
                desc = params.get("description", "")
                coords = self._ekranda_bul(desc)
                if coords:
                    pyautogui.click(coords[0], coords[1])
                    return f"'{desc}' bulundu ve tıklandı."
                return f"Ekranda '{desc}' bulunamadı."

            elif action == "random_data":
                tip = params.get("type", "name")
                return self._rastgele_veri(tip)
                
            else:
                return f"Bilinmeyen bilgisayar komutu: {action}"

        except Exception as e:
            return f"İşlem hatası: {e}"