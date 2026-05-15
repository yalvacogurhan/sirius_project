# plugins/weather.py

import webbrowser
from urllib.parse import quote_plus

class WeatherManager:
    def __init__(self):
        print("🌤️ Sirius Hava Durumu Gösterge Modülü Aktif!")

    def execute(self, params: dict) -> str:
        city = params.get("city", "").strip()
        when = params.get("time", "bugün").strip()

        if not city:
            return "Hangi şehrin hava durumunu öğrenmek istediğinizi belirtmediniz."

        # Google'ın direkt hava durumu widget'ını açacak arama sorgusu
        search_query = f"{city} hava durumu {when}"
        url = f"https://www.google.com/search?q={quote_plus(search_query)}"

        print(f"[HavaDurumu] 🔍 Aranıyor: {city} - {when}")

        try:
            opened = webbrowser.open(url)
            if not opened:
                raise RuntimeError("Tarayıcı tetiklenemedi.")
            return f"{city} bölgesi için {when} gününün hava durumu haritası ekrana getirildi efendim."
        except Exception as e:
            return f"Hava durumu için tarayıcı açılamadı: {e}"