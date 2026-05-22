# plugins/youtube_manager.py

import json
import re
import time
import subprocess
import webbrowser
import tkinter as tk
from tkinter import simpledialog
from pathlib import Path
from datetime import datetime
from urllib.parse import quote_plus

try:
    import requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False

try:
    from youtube_transcript_api import YouTubeTranscriptApi
    _TRANSCRIPT_OK = True
except ImportError:
    _TRANSCRIPT_OK = False

class YouTubeManager:
    def __init__(self):
        print("▶️ Sirius YouTube ve Video Analiz Motoru Aktif!")
        self.gemini_key = "" # main.py'den gelecek
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        }

    def _scrape_first_video_url(self, query: str) -> str | None:
        if not _REQUESTS_OK: return None
        search_url = f"https://www.youtube.com/results?search_query={quote_plus(query)}&sp=EgIQAQ%3D%3D"
        try:
            r = requests.get(search_url, headers=self.headers, timeout=10)
            video_ids = re.findall(r'"videoId":"([A-Za-z0-9_-]{11})"', r.text)
            seen = set()
            for vid in video_ids:
                if vid in seen: continue
                seen.add(vid)
                if f'/shorts/{vid}' in r.text: continue
                return f"https://www.youtube.com/watch?v={vid}"
        except Exception as e:
            print(f"[YouTube] ⚠️ Arama hatası: {e}")
        return None

    def _extract_video_id(self, url: str) -> str | None:
        match = re.search(r"(?:v=|\/v\/|youtu\.be\/|\/embed\/|\/shorts\/)([A-Za-z0-9_-]{11})", url)
        return match.group(1) if match else None

    def _ask_for_url(self, prompt_text: str = "YouTube video URL'sini girin:") -> str | None:
        try:
            root = tk._default_root or tk.Tk()
            if not tk._default_root: root.withdraw()
            url = simpledialog.askstring("Sirius YouTube", prompt_text, parent=root)
            return url.strip() if url else None
        except: return None

    def _get_transcript(self, video_id: str) -> str | None:
        if not _TRANSCRIPT_OK: return None
        try:
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
            lang_priority = ["tr", "en", "de", "fr", "es", "ru"]
            transcript = None

            try: transcript = transcript_list.find_manually_created_transcript(lang_priority)
            except: pass

            if not transcript:
                try: transcript = transcript_list.find_generated_transcript(lang_priority)
                except:
                    for t in transcript_list:
                        transcript = t; break

            if not transcript: return None
            return " ".join(entry["text"] for entry in transcript.fetch())
        except Exception as e:
            print(f"[YouTube] ⚠️ Altyazı çekilemedi: {e}")
            return None

    def _summarize_with_gemini(self, transcript: str, video_url: str) -> str:
        from google import genai
        client = genai.Client(api_key=self.gemini_key)
        
        prompt = (
            "Sen Sirius'sun. Şu YouTube videosunun altyazısını okuyup özetle. "
            "Önce tek cümlelik genel bir özet yap, ardından 3-5 maddelik en önemli detayları listele. "
            "Kullanıcıya 'Patron' diye hitap et. Dili Türkçe kullan.\n\n"
            f"Altyazı: {transcript[:80000]}"
        )
        response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        return response.text.strip()

    def _save_summary(self, content: str, video_url: str) -> str:
        filename = f"Sirius_VideoOzeti_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        filepath = Path.home() / "Desktop" / filename
        header = f"SIRIUS — YouTube Video Özeti\n{'─' * 50}\nURL: {video_url}\nTarih: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n{'─' * 50}\n\n"
        filepath.write_text(header + content, encoding="utf-8")
        try: subprocess.Popen(["notepad.exe", str(filepath)])
        except: pass
        return str(filepath)

    def _scrape_video_info(self, video_id: str) -> dict:
        if not _REQUESTS_OK: return {}
        try:
            r = requests.get(f"https://www.youtube.com/watch?v={video_id}", headers=self.headers, timeout=12)
            html = r.text
            info = {}
            patterns = [
                ("title", r'"title":\{"runs":\[\{"text":"([^"]+)"'),
                ("channel", r'"ownerChannelName":"([^"]+)"'),
                ("views", r'"viewCount":"(\d+)"'),
                ("duration", r'"lengthSeconds":"(\d+)"')
            ]
            for key, pattern in patterns:
                match = re.search(pattern, html)
                if match:
                    raw = match.group(1)
                    if key == "views": info["İzlenme"] = f"{int(raw):,}"
                    elif key == "duration": info["Süre"] = f"{int(raw) // 60}:{int(raw) % 60:02d}"
                    elif key == "title": info["Başlık"] = raw
                    elif key == "channel": info["Kanal"] = raw
            return info
        except: return {}

    def _scrape_trending(self, region: str = "TR") -> str:
        if not _REQUESTS_OK: return "Web kazıma kütüphanesi eksik."
        try:
            r = requests.get(f"https://www.youtube.com/feed/trending?gl={region}", headers=self.headers, timeout=12)
            titles = re.findall(r'"title":\{"runs":\[\{"text":"([^"]+)"\}\]', r.text)
            channels = re.findall(r'"ownerText":\{"runs":\[\{"text":"([^"]+)"', r.text)
            
            lines, seen = [f"Türkiye Trend Videolar:"], set()
            for i, title in enumerate(titles):
                if title in seen or len(title) < 5: continue
                seen.add(title)
                channel = channels[i] if i < len(channels) else "Bilinmiyor"
                lines.append(f"{len(lines)}. {title} ({channel})")
                if len(lines) > 8: break
            return "\n".join(lines)
        except Exception as e:
            return f"Trendler çekilemedi: {e}"

    def execute(self, params: dict) -> str:
        action = params.get("yt_action", "play").lower().strip()
        query = params.get("query", "").strip()
        url = params.get("url", "").strip()

        if action == "play":
            if not query: return "Ne izlemek istediğinizi belirtmediniz patron."
            print(f"[YouTube] 🔍 Aranıyor: {query}")
            video_url = self._scrape_first_video_url(query)
            if video_url:
                webbrowser.open(video_url)
                return f"İstediğiniz video açılıyor: {query}"
            else:
                webbrowser.open(f"https://www.youtube.com/results?search_query={quote_plus(query)}")
                return f"Video bulunamadı, arama sayfası açıldı."

        elif action == "summarize":
            if not url: url = self._ask_for_url("Özetlenecek YouTube URL'si:")
            if not url: return "URL girilmediği için işlem iptal edildi."
            vid = self._extract_video_id(url)
            if not vid: return "Geçerli bir YouTube linki değil."
            
            transcript = self._get_transcript(vid)
            if not transcript: return "Bu videonun altyazısı okunamadı, özet çıkaramıyorum."
            
            summary = self._summarize_with_gemini(transcript, url)
            if params.get("save", True):
                self._save_summary(summary, url)
            return "Video özeti çıkarıldı ve masaüstüne not defteri olarak kaydedildi."

        elif action == "get_info":
            if not url: url = self._ask_for_url("Bilgi alınacak YouTube URL'si:")
            vid = self._extract_video_id(url) if url else None
            if not vid: return "Geçerli bir link verilmedi."
            info = self._scrape_video_info(vid)
            if not info: return "Video bilgileri çekilemedi."
            return " | ".join(f"{k}: {v}" for k, v in info.items())

        elif action == "trending":
            return self._scrape_trending()

        return f"Geçersiz YouTube komutu: {action}"