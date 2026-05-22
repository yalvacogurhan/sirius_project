# plugins/computer_settings.py

import json
import re
import time
import subprocess
import platform

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

_OS = platform.system() 

class ComputerSettings:
    def __init__(self):
        print("⚙️ Sirius Sistem ve Donanım Ayarları Modülü Aktif!")
        self.gemini_key = "" 
        
        self.ACTION_MAP = {
            "volume_up": self.volume_up, "volume_down": self.volume_down, "mute": self.volume_mute,
            "brightness_up": self.brightness_up, "brightness_down": self.brightness_down,
            "sleep_display": self.sleep_display, "close_app": self.close_app, "close_window": self.close_window,
            "full_screen": self.full_screen, "minimize": self.minimize_window, "maximize": self.maximize_window,
            "show_desktop": self.show_desktop, "task_manager": self.open_task_manager,
            "copy": self.copy, "paste": self.paste, "cut": self.cut, "undo": self.undo,
            "screenshot": self.take_screenshot, "lock_screen": self.lock_screen,
            "open_settings": self.open_system_settings, "file_explorer": self.open_file_explorer,
            "dark_mode": self.dark_mode, "toggle_wifi": self.toggle_wifi,
            "restart": self.restart_computer, "shutdown": self.shutdown_computer
        }

    # --- SES KONTROLLERİ ---
    def volume_up(self):
        if _OS == "Windows": [pyautogui.press("volumeup") for _ in range(5)]
        elif _OS == "Darwin": subprocess.run(["osascript", "-e", "set volume output volume (output volume of (get volume settings) + 10)"])

    def volume_down(self):
        if _OS == "Windows": [pyautogui.press("volumedown") for _ in range(5)]
        elif _OS == "Darwin": subprocess.run(["osascript", "-e", "set volume output volume (output volume of (get volume settings) - 10)"])

    def volume_mute(self):
        if _OS == "Windows": pyautogui.press("volumemute")
        elif _OS == "Darwin": subprocess.run(["osascript", "-e", "set volume with output muted"])

    def volume_set(self, value: int):
        value = max(0, min(100, int(value)))
        if _OS == "Windows":
            try:
                import math
                from ctypes import cast, POINTER
                from comtypes import CLSCTX_ALL
                from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
                devices = AudioUtilities.GetSpeakers()
                interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                vol = cast(interface, POINTER(IAudioEndpointVolume))
                vol_db = -65.25 if value == 0 else max(-65.25, 20 * math.log10(value / 100))
                vol.SetMasterVolumeLevel(vol_db, None)
            except Exception as e:
                print(f"Ses ayarı hatası (pycaw kurun): {e}")
                [pyautogui.press("volumemute") for _ in range(2)]

    # --- PARLAKLIK KONTROLLERİ ---
    def brightness_up(self):
        if _OS == "Windows":
            subprocess.run(["powershell", "-Command", "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1, [math]::Min(100, (Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightness).CurrentBrightness + 10))"])

    def brightness_down(self):
        if _OS == "Windows":
            subprocess.run(["powershell", "-Command", "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1, [math]::Max(0, (Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightness).CurrentBrightness - 10))"])

    # --- SİSTEM DONANIM VE GÜÇ KONTROLLERİ ---
    def dark_mode(self):
        if _OS == "Windows":
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize", 0, winreg.KEY_ALL_ACCESS)
            current, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            winreg.SetValueEx(key, "AppsUseLightTheme", 0, winreg.REG_DWORD, 1 - current)
            winreg.SetValueEx(key, "SystemUsesLightTheme", 0, winreg.REG_DWORD, 1 - current)
            winreg.CloseKey(key)

    def toggle_wifi(self):
        if _OS == "Windows":
            subprocess.run(["powershell", "-Command", "$adapter = Get-NetAdapter | Where-Object {$_.PhysicalMediaType -eq 'Native 802.11'}; if ($adapter.Status -eq 'Up') { Disable-NetAdapter -Name $adapter.Name -Confirm:$false } else { Enable-NetAdapter -Name $adapter.Name -Confirm:$false }"])

    def lock_screen(self):
        if _OS == "Windows": pyautogui.hotkey("win", "l")
        
    def sleep_display(self):
        if _OS == "Windows":
            import ctypes
            ctypes.windll.user32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2)

    def restart_computer(self):
        if _OS == "Windows": subprocess.run(["shutdown", "/r", "/t", "5"])
        
    def shutdown_computer(self):
        if _OS == "Windows": subprocess.run(["shutdown", "/s", "/t", "5"])

    # --- PENCERE VE GENEL KISAYOLLAR ---
    def close_app(self): pyautogui.hotkey("alt", "f4") if _OS == "Windows" else pyautogui.hotkey("command", "q")
    def close_window(self): pyautogui.hotkey("ctrl", "w") if _OS == "Windows" else pyautogui.hotkey("command", "w")
    def full_screen(self): pyautogui.press("f11") if _OS == "Windows" else pyautogui.hotkey("ctrl", "command", "f")
    def minimize_window(self): pyautogui.hotkey("win", "down")
    def maximize_window(self): pyautogui.hotkey("win", "up")
    def show_desktop(self): pyautogui.hotkey("win", "d")
    def open_task_manager(self): pyautogui.hotkey("ctrl", "shift", "esc")
    def take_screenshot(self): pyautogui.hotkey("win", "shift", "s")
    def open_system_settings(self): pyautogui.hotkey("win", "i")
    def open_file_explorer(self): pyautogui.hotkey("win", "e")
    def copy(self): pyautogui.hotkey("ctrl", "c")
    def paste(self): pyautogui.hotkey("ctrl", "v")
    def cut(self): pyautogui.hotkey("ctrl", "x")
    def undo(self): pyautogui.hotkey("ctrl", "z")

    def execute(self, action: str, value=None) -> str:
        if not _PYAUTOGUI: return "PyAutoGUI kurulu değil."
        
        # Değer Gerektiren Özel Eylemler
        if action == "volume_set":
            self.volume_set(int(value or 50))
            return f"Ses seviyesi %{value} olarak ayarlandı."
        
        # Haritalanmış Standart Eylemler
        func = self.ACTION_MAP.get(action)
        if not func:
            # Eğer tam eşleşme yoksa LLM ile niyet tespiti yap
            return self._ai_detect_and_execute(action)

        try:
            func()
            return f"Sistem komutu çalıştırıldı: {action.replace('_', ' ')}"
        except Exception as e:
            return f"Sistem komutu hatası: {e}"

def _ai_detect_and_execute(self, desc: str) -> str:
        if not self.gemini_key: return "Komut anlaşılamadı. Lütfen daha net ifade edin."
        try:
            import google.generativeai as genai
            genai.configure(api_key=self.gemini_key)
            model = genai.GenerativeModel("gemini-2.5-flash-lite")
            
            available = ", ".join(self.ACTION_MAP.keys()) + ", volume_set"
            prompt = f"Kullanıcı komutu: '{desc}'. Mevcut komutlar: {available}. SADECE JSON döndür: {{\"action\": \"komut_adi\", \"value\": 50}}"
            
            resp = model.generate_content(prompt)
            text = resp.text.replace("```json", "").replace("```", "").strip()
            veri = json.loads(text)
            
            action = veri.get("action")
            val = veri.get("value")
            
            if action in self.ACTION_MAP:
                self.ACTION_MAP[action]()
                return f"Algılanan komut uygulandı: {action}"
            elif action == "volume_set":
                self.volume_set(int(val or 50))
                return f"Ses seviyesi algılandı ve ayarlandı: %{val}"
            return "Anlaşılamayan veya desteklenmeyen sistem komutu."
        except Exception as e:
            return f"Yapay zeka niyet algılama hatası: {e}"