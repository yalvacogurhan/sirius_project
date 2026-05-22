# plugins/window_manager.py

import sys
import comtypes.client

# PyInstaller (.exe) comtypes önbellek çökme hatasını (WinError 183) engeller
comtypes.client.gen_dir = None 

import pywinauto
import pyautogui
import time
# ... (kodun geri kalanı aynı şekilde devam edecek)
from pywinauto import Desktop
import pyautogui
import time
import os
import screen_brightness_control as sbc

class WindowManager:
    def __init__(self):
        print("🪟 Sirius Pencere ve Donanım Yöneticisi Başlatıldı.")

    def smart_open(self, app_name):
        print(f"🚀 {app_name} başlatılıyor...")
        pyautogui.hotkey('win')
        time.sleep(0.5)
        pyautogui.write(app_name, interval=0.05)
        time.sleep(1)
        pyautogui.press('enter')
        time.sleep(2)
        return True

    def manage_window(self, app_title, command):
        if not app_title:
            return False
            
        # 🛡️ GÜVENLİK DUVARI: Sirius'un intihar etmesini (kendini kapatmasını) engeller Amk!
        hedef_kucuk = app_title.lower()
        if "sirius" in hedef_kucuk or "assistant" in hedef_kucuk:
            print("🛡️ Sirius: Kendime zarar vermeyi reddediyorum!")
            return False
            
        try:
            # Masaüstündeki tüm açık pencereleri tarar
            windows = Desktop(backend="uia").windows()
            target_win = None
            
            # Aranan kelimeyi pencere isimleri arasında spesifik olarak bulur
            for w in windows:
                title = w.window_text().lower()
                if hedef_kucuk in title and title != "":
                    target_win = w
                    break

            if not target_win:
                print(f"❌ '{app_title}' adında açık bir pencere bulunamadı.")
                return False

            print(f"🎯 Hedef Pencere Yakalandı: '{target_win.window_text()}' -> İşlem: {command}")

            # SADECE hedeflenen X veya Y penceresine komutu uygular, diğerlerine dokunmaz
            if command == "close":
                target_win.close()
            elif command == "maximize":
                target_win.maximize()
            elif command == "minimize":
                target_win.minimize()
            elif command == "focus":
                target_win.set_focus()
            
            return True
            
        except Exception as e:
            print(f"❌ Pencere yönetim hatası: {e}")
            return False

    def system_control(self, command, value=None):
        print(f"⚙️ Sistem Ayarı: {command} -> {value}")
        try:
            if command == "volume_up":
                pyautogui.press('volumeup', presses=value if value else 5)
            elif command == "volume_down":
                pyautogui.press('volumedown', presses=value if value else 5)
            elif command == "volume_mute":
                pyautogui.press('volumemute')
            elif command == "brightness":
                sbc.set_brightness(value if value else 50)
            elif command == "wifi":
                os.system("start ms-settings:network-wifi")
            elif command == "bluetooth":
                os.system("start ms-settings:bluetooth")
            return True
        except Exception as e:
            print(f"❌ Sistem kontrol hatası: {e}")
            return False