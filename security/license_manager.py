# security/license_manager.py
import os
import time
import requests
from cryptography.fernet import Fernet
from dotenv import load_dotenv
from .hwid import get_hardware_id

load_dotenv()

SERVER_URL = "https://yalvac.pythonanywhere.com"
SECRET_KEY = "D7k2HqL9x_mYzP4wV1aBcN8jF5gT3sR6vK0lE_hXyJc="
TOKEN_FILE = os.path.join(os.getenv("APPDATA") or os.getcwd(), "sirius_license.bin")

class LicenseManager:
    def __init__(self):
        self.hwid = get_hardware_id()
        self.fernet = Fernet(SECRET_KEY.encode()) if SECRET_KEY else None
        self.failed_attempts = 0
        self.lockout_time = 0

    def check_local_license(self) -> bool:
        """Yerel olarak şifrelenmiş lisans dosyasını kontrol eder."""
        if not os.path.exists(TOKEN_FILE) or not self.fernet:
            return False
        
        try:
            with open(TOKEN_FILE, "rb") as f:
                encrypted_data = f.read()
            decrypted_key = self.fernet.decrypt(encrypted_data).decode()
            
            # Arka planda sunucudan doğrula (Sessizce)
            return self.verify_key_online(decrypted_key, silent=True)
        except Exception:
            return False

    def verify_key_online(self, license_key: str, silent: bool = False) -> bool:
        """Anahtarı sunucuya gönderir ve doğrular."""
        # Brute-force koruması
        if time.time() < self.lockout_time:
            if not silent: print("⏳ Çok fazla hatalı deneme. Lütfen bekleyin.")
            return False

        try:
            response = requests.post(f"{SERVER_URL}/api/validate", json={
                "license_key": license_key,
                "hwid": self.hwid
            }, timeout=5)
            
            data = response.json()
            
            if response.status_code == 200 and data.get("valid"):
                self.failed_attempts = 0
                self._save_local_token(license_key)
                return True
            else:
                self._handle_failure(silent, data.get("message", "Geçersiz lisans."))
                return False
                
        except requests.exceptions.RequestException:
            if not silent: print("❌ Lisans sunucusuna ulaşılamıyor.")
            return False

    def _handle_failure(self, silent: bool, msg: str):
        self.failed_attempts += 1
        if self.failed_attempts >= 5:
            self.lockout_time = time.time() + 300  # 5 dakika kilitle
            if not silent: print("⛔ Güvenlik Kilidi: 5 dakika boyunca giriş yapamazsınız.")
        elif not silent:
            print(f"❌ Hata: {msg}")

    def _save_local_token(self, key: str):
        if self.fernet:
            with open(TOKEN_FILE, "wb") as f:
                f.write(self.fernet.encrypt(key.encode()))

    def wipe_license(self):
        """Lisansı sistemden siler (Çıkış/Ban durumu)."""
        if os.path.exists(TOKEN_FILE):
            os.remove(TOKEN_FILE)