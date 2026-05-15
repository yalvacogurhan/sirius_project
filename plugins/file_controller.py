# plugins/file_controller.py

import os
import shutil
import platform
from pathlib import Path
from datetime import datetime

try:
    import send2trash
    _SEND2TRASH = True
except ImportError:
    _SEND2TRASH = False

_OS = platform.system() 

class FileController:
    def __init__(self):
        print("📁 Sirius Dosya Yönetim Sistemi Aktif!")
        self.safe_roots = [Path.home()]
        
        self.FILE_TYPE_MAP = {
            "Görseller":   {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".ico", ".heic"},
            "Belgeler":    {".pdf", ".doc", ".docx", ".txt", ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".odt", ".ods", ".odp"},
            "Videolar":      {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".m4v"},
            "Müzikler":       {".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a"},
            "Arşivler":    {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
            "Yazılım":        {".py", ".js", ".ts", ".html", ".css", ".json", ".xml", ".cpp", ".java", ".cs", ".go", ".rs", ".sh"},
        }

    def _is_safe_path(self, target: Path) -> bool:
        try:
            resolved = target.resolve()
            return any(resolved == root.resolve() or resolved.is_relative_to(root.resolve()) for root in self.safe_roots)
        except: return False

    def _get_path_shortcut(self, name: str) -> Path:
        base = Path.home()
        if _OS == "Linux":
            xdg = os.environ.get(f"XDG_{name.upper()}_DIR", "")
            if xdg and Path(xdg).exists(): return Path(xdg)
        
        shortcuts = {
            "desktop": base / "Desktop", "downloads": base / "Downloads",
            "documents": base / "Documents", "pictures": base / "Pictures",
            "music": base / "Music", "videos": base / "Videos", "home": base
        }
        return shortcuts.get(name.lower(), Path(name).expanduser())

    def _format_size(self, b: int) -> str:
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if b < 1024: return f"{b:.1f} {unit}"
            b /= 1024
        return f"{b:.1f} TB"

    def list_files(self, path: str, show_hidden: bool = False) -> str:
        target = self._get_path_shortcut(path)
        if not self._is_safe_path(target): return "Erişim reddedildi."
        if not target.exists() or not target.is_dir(): return "Klasör bulunamadı."
        
        items = []
        for item in sorted(target.iterdir()):
            if not show_hidden and item.name.startswith("."): continue
            if item.is_dir(): items.append(f"📁 {item.name}/")
            else: items.append(f"📄 {item.name} ({self._format_size(item.stat().st_size)})")
            
        if not items: return "Klasör boş."
        return f"Klasör İçeriği ({target.name}/):\n" + "\n".join(items)

    def create_file(self, path: str, name: str, content: str) -> str:
        target = self._get_path_shortcut(path) / name
        if not self._is_safe_path(target): return "Erişim reddedildi."
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Dosya oluşturuldu: {target.name}"

    def create_folder(self, path: str, name: str) -> str:
        target = self._get_path_shortcut(path) / name
        if not self._is_safe_path(target): return "Erişim reddedildi."
        target.mkdir(parents=True, exist_ok=True)
        return f"Klasör oluşturuldu: {target.name}"

    def delete_file(self, path: str, name: str) -> str:
        target = self._get_path_shortcut(path) / name if name else self._get_path_shortcut(path)
        if not self._is_safe_path(target) or not target.exists(): return "Bulunamadı veya erişim yok."
        
        protected = {self._get_path_shortcut(k) for k in ["desktop", "downloads", "documents", "pictures", "home"]}
        if target.resolve() in {p.resolve() for p in protected}: return "Bu temel sistem klasörü silinemez!"
        
        if not _SEND2TRASH: return "Güvenlik gereği 'send2trash' kütüphanesi olmadan silme işlemi yapılamaz."
        send2trash.send2trash(str(target))
        return f"Çöp kutusuna taşındı: {target.name}"

    def move_or_copy(self, path: str, name: str, dest: str, is_copy: bool = False) -> str:
        src = self._get_path_shortcut(path) / name if name else self._get_path_shortcut(path)
        dst = self._get_path_shortcut(dest)
        if not src.exists() or not dest: return "Kaynak bulunamadı veya hedef belirtilmedi."
        
        if dst.is_dir(): dst = dst / src.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        
        if is_copy:
            if src.is_dir(): shutil.copytree(str(src), str(dst))
            else: shutil.copy2(str(src), str(dst))
            return f"Kopyalandı: {src.name} -> {dst.parent.name}/"
        else:
            shutil.move(str(src), str(dst))
            return f"Taşındı: {src.name} -> {dst.parent.name}/"

    def rename_file(self, path: str, name: str, new_name: str) -> str:
        target = self._get_path_shortcut(path) / name
        if not target.exists() or not new_name: return "Dosya bulunamadı veya yeni isim verilmedi."
        new_path = target.parent / new_name
        if new_path.exists(): return "Bu isimde bir dosya zaten var."
        target.rename(new_path)
        return f"İsim değiştirildi: {target.name} -> {new_name}"

    def read_file(self, path: str, name: str) -> str:
        target = self._get_path_shortcut(path) / name
        if not target.is_file(): return "Dosya okunamadı."
        content = target.read_text(encoding="utf-8", errors="ignore")
        return content[:4000] + ("\n\n[Çok uzun olduğu için kırpıldı]" if len(content)>4000 else "")

    def find_files(self, name: str, ext: str, path: str) -> str:
        search_path = self._get_path_shortcut(path)
        results = []
        for item in search_path.rglob("*"):
            if item.is_dir() or (ext and item.suffix.lower() != ext.lower()) or (name and name.lower() not in item.name.lower()): continue
            results.append(f"📄 {item.name} ({self._format_size(item.stat().st_size)}) - {item.parent}")
            if len(results) >= 20: break
        
        if not results: return f"Arama kriterine uygun dosya bulunamadı: {search_path.name}/"
        return f"Bulunan Dosyalar:\n" + "\n".join(results)

    def get_disk_usage(self, path: str) -> str:
        usage = shutil.disk_usage(self._get_path_shortcut(path))
        return f"Disk Kullanımı:\nToplam: {self._format_size(usage.total)}\nKullanılan: {self._format_size(usage.used)} (%{usage.used/usage.total*100:.1f})\nBoş: {self._format_size(usage.free)}"

    def execute(self, action: str, params: dict) -> str:
        path, name = params.get("path", "desktop"), params.get("name", "")
        try:
            if action == "list": return self.list_files(path)
            elif action == "create_file": return self.create_file(path, name, params.get("content", ""))
            elif action == "create_folder": return self.create_folder(path, name)
            elif action == "delete": return self.delete_file(path, name)
            elif action == "move": return self.move_or_copy(path, name, params.get("destination", ""), is_copy=False)
            elif action == "copy": return self.move_or_copy(path, name, params.get("destination", ""), is_copy=True)
            elif action == "rename": return self.rename_file(path, name, params.get("new_name", ""))
            elif action == "read": return self.read_file(path, name)
            elif action == "find": return self.find_files(params.get("name", ""), params.get("extension", ""), path)
            elif action == "disk_usage": return self.get_disk_usage(path)
            else: return f"Bilinmeyen dosya komutu: {action}"
        except Exception as e:
            return f"Dosya işlem hatası: {e}"