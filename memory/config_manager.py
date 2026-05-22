# memory/config_manager.py

import json
import sys
from pathlib import Path

def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR    = get_base_dir()
# Ayar dosyası ana dizindeki 'config' klasörüne kaydedilir (Güvenlik için)
CONFIG_DIR  = BASE_DIR / "config"
CONFIG_FILE = CONFIG_DIR / "api_keys.json"

def ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

def config_exists() -> bool:
    return CONFIG_FILE.exists()

def save_api_keys(gemini_api_key: str = "", serpapi_key: str = "", groq_api_key: str = "", openai_api_key: str = "") -> None:
    ensure_config_dir()

    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}

    # Gelen anahtarları sözlüğe ekle veya güncelle (Mevcut olanları ezmez)
    if gemini_api_key:
        data["gemini_key"] = gemini_api_key.strip()
    if serpapi_key:
        data["serpapi_key"] = serpapi_key.strip()
    if groq_api_key:
        data["groq_api_key"] = groq_api_key.strip()  # İsimlendirme hatası düzeltildi
    if openai_api_key:
        data["openai_api_key"] = openai_api_key.strip()

    CONFIG_FILE.write_text(
        json.dumps(data, indent=4),
        encoding="utf-8"
    )
    print("[ConfigManager] 💾 Sirius ayarları güncellendi.")

def get_config() -> dict:
    """Ayar dosyasını okur. Dosya yoksa varsayılan boş bir yapı döndürür."""
    default_config = {
        "gemini_key": "", 
        "serpapi_key": "", 
        "groq_api_key": "", 
        "openai_api_key": ""
    }
    
    if not CONFIG_FILE.exists():
        return default_config
        
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        # Okunan veride eksik anahtar varsa varsayılan boş değerle tamamla
        for key, value in default_config.items():
            if key not in data:
                data[key] = value
        return data
    except Exception as e:
        print(f"[ConfigManager] ⚠️ api_keys.json okunamadı: {e}")
        return default_config

def get_gemini_key() -> str | None:
    return get_config().get("gemini_key")

def is_configured() -> bool:
    key = get_gemini_key()
    return bool(key and len(key) > 15)

def is_windows() -> bool: 
    """Sistem artık sadece Windows odaklı olduğu için her zaman True döner."""
    return True