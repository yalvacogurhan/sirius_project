# config/__init__.py

import json
from pathlib import Path

# Dosyanın bulunduğu klasörü (config klasörü) baz alır
_CONFIG_PATH = Path(__file__).parent / "api_keys.json"

def get_config() -> dict:
    """Ayar dosyasını okur. Dosya yoksa varsayılan boş bir yapı döndürür."""
    if not _CONFIG_PATH.exists():
        return {"api_key": "", "serpapi_key": "", "gemini_key": ""}
        
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[Config] ⚠️ Ayar dosyası okunamadı: {e}")
        return {}

def save_config(data: dict) -> None:
    """Ayarları api_keys.json dosyasına güvenli bir şekilde kaydeder."""
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
    print("[Config] 💾 Sirius ayarları güncellendi.")

def is_windows() -> bool: 
    """Sistem artık sadece Windows odaklı olduğu için her zaman True döner."""
    return True