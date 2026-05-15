# plugins/travel_manager.py

import os
import re
import sys
import json
import time
import subprocess
import threading
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import quote_plus

class TravelManager:
    def __init__(self):
        print("🚌✈️ Sirius Seyahat ve Ulaşım Yönetim Merkezi Aktif!")
        self.gemini_key = "" # main.py'den beslenecek
        self.month_map = {
            "ocak": 1, "şubat": 2, "mart": 3, "nisan": 4, "mayıs": 5, "haziran": 6,
            "temmuz": 7, "ağustos": 8, "eylül": 9, "ekim": 10, "kasım": 11, "aralık": 12
        }

    def _parse_date(self, raw: str) -> str:
        raw = raw.lower().strip()
        today = datetime.now()
        
        # Basit eşleşmeler
        if "bugün" in raw: return today.strftime("%Y-%m-%d")
        if "yarın" in raw: return (today + timedelta(days=1)).strftime("%Y-%m-%d")
        
        # Gemini ile akıllı tarih çözümleme
        try:
            from google import genai
            client = genai.Client(api_key=self.gemini_key)
            prompt = f"Bugün {today.strftime('%Y-%m-%d')}. Şu ifadeyi YYYY-MM-DD formatına çevir: '{raw}'. Sadece tarih dizisini döndür."
            response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
            result = response.text.strip()
            if re.match(r"\d{4}-\d{2}-\d{2}", result): return result
        except: pass
        
        return today.strftime("%Y-%m-%d")

    def _build_search_url(self, t_type: str, origin: str, dest: str, date: str) -> str:
        query = f"{origin} {dest} {t_type} bileti {date}"
        return f"https://www.google.com/search?q={quote_plus(query)}"

    def _analyze_with_gemini(self, raw_text: str, t_type: str, origin: str, dest: str, date: str) -> list[dict]:
        from google import genai
        try:
            client = genai.Client(api_key=self.gemini_key)
            prompt = (
                f"Şu metinden {date} tarihindeki {origin} - {dest} arası {t_type} seferlerini ayıkla. "
                "Sadece şu formatta JSON döndür: [{'firma':'...', 'kalkis':'HH:MM', 'fiyat':'...', 'sure':'...'}] "
                f"Metin: {raw_text[:10000]}"
            )
            response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
            
            # SİHİRLİ TEMİZLİK SATIRI (Arayüz bug'ını aşmak için)
            marker = chr(96) * 3
            text = response.text.replace(marker + "json", "").replace(marker, "").strip()
            
            data = json.loads(text)
            return data if isinstance(data, list) else []
        except: return []

    def _save_report(self, flights, t_type, origin, dest, date):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"Sirius_Seyahat_{origin}_{dest}_{ts}.txt"
        filepath = Path.home() / "Desktop" / filename
        
        lines = [f"SIRIUS SEYAHAT RAPORU ({t_type.upper()})\n", "─" * 40]
        lines.append(f"Rota: {origin} -> {dest}\nTarih: {date}\n" + "─" * 40 + "\n")
        
        for i, f in enumerate(flights, 1):
            lines.append(f"{i}. Firma: {f.get('firma')} | Kalkış: {f.get('kalkis')} | Fiyat: {f.get('fiyat')} | Süre: {f.get('sure')}")
        
        filepath.write_text("\n".join(lines), encoding="utf-8")
        subprocess.Popen(["notepad.exe", str(filepath)])
        return str(filepath)

    def execute(self, params: dict) -> str:
        t_type = params.get("type", "uçak").lower() # uçak veya otobüs
        origin = params.get("origin", "").strip()
        dest = params.get("destination", "").strip()
        date_raw = params.get("date", "yarın").strip()

        if not origin or not dest: return "Kalkış ve varış noktalarını belirtmelisiniz patron."
        
        date = self._parse_date(date_raw)
        url = self._build_search_url(t_type, origin, dest, date)
        
        print(f"[Seyahat] 🌐 {t_type.title()} aranıyor: {origin} -> {dest} ({date})")
        
        import webbrowser
        webbrowser.open(url)
        
        return f"{origin} ile {dest} arasındaki {t_type} seferlerini tarayıcıda açtım patron. En uygun seçenekleri ekrandan inceleyebilirsiniz."