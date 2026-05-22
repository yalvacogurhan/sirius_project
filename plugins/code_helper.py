# plugins/code_helper.py

import subprocess
import sys
import json
import re
import time
from pathlib import Path

class CodeHelper:
    def __init__(self):
        print("💻 Sirius Otonom Yazılım ve Hata Ayıklama Asistanı Aktif!")
        self.gemini_key = "" # main.py üzerinden gelecek
        self.desktop = Path.home() / "Desktop"
        self.max_build_attempts = 3

    def _get_gemini(self):
        import google.generativeai as genai
        if not self.gemini_key:
            raise ValueError("Gemini API Key eksik!")
        genai.configure(api_key=self.gemini_key)
        return genai.GenerativeModel("gemini-2.5-flash")

    def _clean_code(self, text: str) -> str:
        text = text.strip()
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
        return text.strip()

    def _resolve_save_path(self, output_path: str, language: str) -> Path:
        ext_map = {
            "python": ".py", "py": ".py", "javascript": ".js", "js": ".js",
            "html": ".html", "css": ".css", "bash": ".sh", "json": ".json"
        }
        if output_path:
            p = Path(output_path)
            return p if p.is_absolute() else self.desktop / p
        ext = ext_map.get((language or "python").lower(), ".py")
        return self.desktop / f"sirius_code{ext}"

    def _save_file(self, path: Path, content: str) -> str:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return f"Kaydedildi: {path}"
        except Exception as e:
            return f"Kaydetme hatası: {e}"

    def _read_file(self, file_path: str) -> tuple[str, str]:
        if not file_path: return "", "Dosya yolu belirtilmedi."
        p = Path(file_path)
        if not p.exists(): return "", f"Dosya bulunamadı: {file_path}"
        try: return p.read_text(encoding="utf-8"), ""
        except Exception as e: return "", f"Dosya okunamadı: {e}"

    def write_action(self, desc: str, lang: str, out_path: str) -> str:
        if not desc: return "Ne yazmamı istediğinizi belirtin efendim."
        model = self._get_gemini()
        prompt = f"""Sen uzman bir {lang} geliştiricisisin.
Aşağıdaki açıklama için temiz, çalışan ve yorum satırı içeren {lang} kodu yaz.
SADECE kodu yaz. Açıklama veya markdown (```) kullanma.
Açıklama: {desc}
Kod:"""
        try:
            response = model.generate_content(prompt)
            code = self._clean_code(response.text)
            path = self._resolve_save_path(out_path, lang)
            self._save_file(path, code)
            return f"Kod başarıyla yazıldı ve şuraya kaydedildi: {path}"
        except Exception as e:
            return f"Kod üretilirken hata oluştu: {e}"

    def run_action(self, file_path: str, args: list, timeout: int) -> str:
        if not file_path: return "Lütfen çalıştırılacak dosya yolunu belirtin."
        p = Path(file_path)
        if not p.exists(): return f"Dosya bulunamadı: {file_path}"
        
        interpreters = {".py": [sys.executable], ".js": ["node"], ".sh": ["bash"]}
        interp = interpreters.get(p.suffix.lower())
        if not interp: return f"Bu dosya türü ({p.suffix}) için yorumlayıcı bulunamadı."

        try:
            result = subprocess.run(interp + [str(p)] + (args or []), capture_output=True, text=True, timeout=timeout, cwd=str(p.parent))
            out, err = result.stdout.strip(), result.stderr.strip()
            if err: return f"HATA ALINDI:\n{err}"
            return f"ÇIKTI:\n{out}" if out else "Dosya başarıyla çalıştırıldı ancak çıktı vermedi."
        except Exception as e:
            return f"Çalıştırma hatası: {e}"

    def screen_debug_action(self, desc: str) -> str:
        try:
            import pyautogui
            screenshot_path = self.desktop / f"sirius_debug_{int(time.time())}.png"
            pyautogui.screenshot().save(str(screenshot_path))
            
            from google import genai
            from google.genai import types
            import base64
            
            client = genai.Client(api_key=self.gemini_key)
            image_bytes = screenshot_path.read_bytes()
            
            prompt = f"""Sen uzman bir hata ayıklayıcısın (debugger). 
Kullanıcının sorusu: '{desc or "Ekranda ne hatası var ve nasıl çözerim?"}'
Lütfen ekrandaki hatayı analiz et, nedenini Türkçe ve basitçe açıkla, ardından çözüm yolunu veya düzeltilmiş kodu ver."""
            
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[types.Part.from_bytes(data=image_bytes, mime_type="image/png"), prompt]
            )
            
            screenshot_path.unlink(missing_ok=True)
            return response.text.strip()
        except Exception as e:
            return f"Ekran analizi başarısız oldu: {e}"

    def execute(self, action: str, params: dict) -> str:
        desc = params.get("description", "").strip()
        lang = params.get("language", "python").strip()
        out_path = params.get("output_path", "").strip()
        file_path = params.get("file_path", "").strip()
        args = params.get("args", [])
        timeout = int(params.get("timeout", 30))

        try:
            if action == "write": return self.write_action(desc, lang, out_path)
            elif action == "run": return self.run_action(file_path, args, timeout)
            elif action == "screen_debug": return self.screen_debug_action(desc)
            # Daha fazla özellik (optimize, edit) eklenebilir, şimdilik en temel 3'ü bağlandı
            else: return f"Bilinmeyen kod komutu: {action}"
        except Exception as e:
            return f"Kod Asistanı Hatası: {e}"