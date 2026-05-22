# plugins/desktop.py

import os
import sys
import json
import shutil
import subprocess
import tempfile
import platform
from pathlib import Path
from datetime import datetime

try:
    import pyautogui
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"

class DesktopManager:
    def __init__(self):
        print("🖥️ Sirius Masaüstü Yöneticisi ve Otomasyon Aktif!")
        self.gemini_key = "" # main.py'den yüklenecek
        
        self.FILE_TYPE_MAP = {
            "Görseller":   {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".ico", ".heic"},
            "Belgeler":    {".pdf", ".doc", ".docx", ".txt", ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".odt", ".ods", ".odp"},
            "Videolar":      {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".m4v"},
            "Müzikler":       {".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a"},
            "Arşivler":    {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
            "Yazılım":        {".py", ".js", ".ts", ".html", ".css", ".json", ".xml", ".cpp", ".java", ".cs", ".go", ".rs", ".sh", ".php"},
            "Uygulamalar": {".exe", ".msi", ".bat", ".cmd", ".sh", ".appimage", ".deb", ".rpm"},
        }

        self._SKIP_EXTENSIONS = {
            "Windows": {".lnk", ".url", ".ini"},
            "Darwin":  {".webloc"},
            "Linux":   {".desktop"},
        }

    def _get_desktop(self) -> Path:
        if _OS == "Linux":
            xdg = os.environ.get("XDG_DESKTOP_DIR", "")
            if xdg and Path(xdg).exists(): return Path(xdg)
        return Path.home() / "Desktop"

    def _build_sandbox(self) -> dict:
        import time
        safe_builtins = {
            "print": print, "len": len, "str": str, "int": int, "float": float,
            "bool": bool, "list": list, "dict": dict, "tuple": tuple,
            "range": range, "enumerate": enumerate, "sorted": sorted,
            "isinstance": isinstance, "hasattr": hasattr, "getattr": getattr,
            "max": max, "min": min, "sum": sum, "abs": abs,
            "zip": zip, "map": map, "filter": filter,
        }
        sandbox = {
            "__builtins__": safe_builtins,
            "Path": Path, "time": time,
            "shutil": type("shutil", (), {
                "copy2": shutil.copy2, "copytree": shutil.copytree, "disk_usage": shutil.disk_usage,
            })(),
            "os_path": os.path,  
        }
        if _PYAUTOGUI: sandbox["pyautogui"] = pyautogui
        if _OS == "Windows":
            try:
                import ctypes, winreg
                sandbox["ctypes"] = ctypes
                sandbox["winreg"] = type("winreg", (), {
                    "OpenKey": winreg.OpenKey, "QueryValueEx": winreg.QueryValueEx, "HKEY_CURRENT_USER": winreg.HKEY_CURRENT_USER,
                })()
            except ImportError: pass
        return sandbox

    def _execute_generated_code(self, code: str) -> str:
        if not code or code.strip() == "UNSAFE": return "Bu işlem güvenlik gereği (dosya silme vb.) yapılamaz."
        if code.startswith("```"):
            lines = code.split("\n")
            code  = "\n".join(lines[1:-1]).strip()

        sandbox = self._build_sandbox()
        output_lines = []
        sandbox["__builtins__"]["print"] = lambda *a: output_lines.append(" ".join(str(x) for x in a))

        try:
            exec(compile(code, "<sirius_desktop>", "exec"), sandbox)
            return "\n".join(output_lines) if output_lines else "Özel görev tamamlandı."
        except Exception as e:
            return f"Kod çalıştırma hatası: {e}"

    def _ask_gemini_for_desktop_action(self, task: str) -> str:
        if not self.gemini_key: return "Görev oluşturmak için Gemini API anahtarı gereklidir."
        try:
            import google.generativeai as genai
            genai.configure(api_key=self.gemini_key)
            model = genai.GenerativeModel("gemini-2.5-flash")

            desktop = str(self._get_desktop())
            os_specific = "- ctypes ve winreg (Sadece Okuma)" if _OS == "Windows" else "- subprocess YASAK"

            prompt = f"""Sen bir masaüstü otomasyon asistanısın. İşletim Sistemi: {_OS}. Masaüstü yolu: {desktop}
Aşağıdaki görev için GÜVENLİ bir Python kodu yaz. 
İzin verilen kütüphaneler: pyautogui, pathlib.Path, shutil (sadece copy2, copytree, disk_usage), os_path, time.sleep. {os_specific}

KESİN KURALLAR:
- Dosya SİLMEK YASAKTIR (unlink, rmtree, remove vb. ASLA KULLANMA).
- Subprocess YASAKTIR.
- Eğer görev bu araçlarla GÜVENLİ bir şekilde yapılamıyorsa (örneğin dosya silme isteniyorsa) sadece "UNSAFE" yaz.
SADECE kodu yaz. Açıklama yapma.

Görev: {task}"""
            response = model.generate_content(prompt)
            return response.text.strip()
        except Exception as e:
            return f"Yapay Zeka Hatası: {e}"

    def set_wallpaper(self, image_path: str) -> str:
        path = Path(image_path).expanduser().resolve()
        if not path.exists(): return f"Görsel bulunamadı: {image_path}"
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}: return f"Desteklenmeyen format: {path.suffix}"

        try:
            if _OS == "Windows":
                import ctypes
                ctypes.windll.user32.SystemParametersInfoW(20, 0, str(path), 3)
                return "Duvar kağıdı değiştirildi."
            elif _OS == "Darwin":
                subprocess.run(["osascript", "-e", f'tell application "System Events" to tell every desktop to set picture to POSIX file "{path}"'], capture_output=True)
                return "Duvar kağıdı değiştirildi."
            else:
                return "Linux masaüstü arka plan değiştirme şimdilik desteklenmiyor."
        except Exception as e:
            return f"Duvar kağıdı ayarlanamadı: {e}"

    def organize_desktop(self, mode: str = "by_type") -> str:
        desktop = self._get_desktop()
        skip_exts = self._SKIP_EXTENSIONS.get(_OS, set())
        moved = 0

        for item in desktop.iterdir():
            if item.is_dir() or item.name.startswith(".") or item.suffix.lower() in skip_exts: continue

            if mode == "by_date":
                mtime = datetime.fromtimestamp(item.stat().st_mtime)
                folder_name = mtime.strftime("%Y-%m")
            else:
                ext = item.suffix.lower()
                folder_name = "Diğerleri"
                for folder, exts in self.FILE_TYPE_MAP.items():
                    if ext in exts:
                        folder_name = folder
                        break

            target_dir = desktop / folder_name
            target_dir.mkdir(exist_ok=True)
            new_path = target_dir / item.name

            if not new_path.exists():
                shutil.move(str(item), str(new_path))
                moved += 1

        return f"Masaüstü düzenlendi. {moved} dosya klasörlere ayrıldı."

    def clean_desktop(self) -> str:
        desktop = self._get_desktop()
        skip_exts = self._SKIP_EXTENSIONS.get(_OS, set())
        today = datetime.now().strftime("%Y-%m-%d")
        archive_dir = desktop / f"Arşiv_{today}"
        archive_dir.mkdir(exist_ok=True)

        moved = 0
        for item in desktop.iterdir():
            if item.is_dir() or item.name.startswith(".") or item.suffix.lower() in skip_exts: continue
            new_path = archive_dir / item.name
            if not new_path.exists():
                shutil.move(str(item), str(new_path))
                moved += 1

        return f"Masaüstü temizlendi. {moved} dosya '{archive_dir.name}' klasörüne taşındı."

    def list_desktop(self) -> str:
        desktop = self._get_desktop()
        items = []
        for item in sorted(desktop.iterdir()):
            if item.name.startswith("."): continue
            if item.is_dir(): items.append(f"📁 {item.name}/")
            else: items.append(f"📄 {item.name}")

        if not items: return "Masaüstü boş."
        return f"Masaüstünde {len(items)} öğe var:\n" + "\n".join(items[:15]) + ("\n..." if len(items)>15 else "")

    def execute(self, action: str, params: dict) -> str:
        try:
            if action == "wallpaper": return self.set_wallpaper(params.get("path", ""))
            elif action == "organize": return self.organize_desktop(params.get("mode", "by_type"))
            elif action == "clean": return self.clean_desktop()
            elif action == "list": return self.list_desktop()
            elif action == "task":
                task_desc = params.get("description", "").strip()
                if not task_desc: return "Ne yapmak istediğinizi belirtin."
                code = self._ask_gemini_for_desktop_action(task_desc)
                return self._execute_generated_code(code)
            else:
                return f"Bilinmeyen masaüstü komutu: {action}"
        except Exception as e:
            return f"Masaüstü yönetim hatası: {e}"