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

def save_api_keys(gemini_api_key: str, serpapi_key: str = "", groq_api_key: str = "") -> None:
    ensure_config_dir()

    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}

    # Gelen anahtarları sözlüğe ekle veya güncelle
    data["gemini_key"] = gemini_api_key.strip()
    data["serpapi_key"] = serpapi_key.strip()
    data["api_key"] = groq_api_key.strip()

    CONFIG_FILE.write_text(
        json.dumps(data, indent=4),
        encoding="utf-8"
    )
    print("[ConfigManager] 💾 Sirius ayarları güncellendi.")

def get_config() -> dict:
    """Ayar dosyasını okur. Dosya yoksa varsayılan boş bir yapı döndürür."""
    if not CONFIG_FILE.exists():
        return {"api_key": "", "serpapi_key": "", "gemini_key": ""}
        
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[ConfigManager] ⚠️ api_keys.json okunamadı: {e}")
        return {"api_key": "", "serpapi_key": "", "gemini_key": ""}

def get_gemini_key() -> str | None:
    return get_config().get("gemini_key")

def is_configured() -> bool:
    key = get_gemini_key()
    return bool(key and len(key) > 15)

def is_windows() -> bool: 
    """Sistem artık sadece Windows odaklı olduğu için her zaman True döner."""
    return True