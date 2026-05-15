# plugins/game_updater.py

import os
import re
import sys
import json
import time
import subprocess
import threading
import shutil
import winreg
from pathlib import Path
from datetime import datetime

class GameUpdater:
    def __init__(self):
        print("🎮 Sirius Oyun & Güncelleme Yöneticisi (Windows Sürümü) Aktif!")
        self.known_appids = {
            "pubg": ("578080", "PUBG: Battlegrounds"), "cs2": ("730", "Counter-Strike 2"),
            "csgo": ("730", "Counter-Strike 2"), "dota 2": ("570", "Dota 2"),
            "rust": ("252490", "Rust"), "cyberpunk": ("1091500", "Cyberpunk 2077"),
            "elden ring": ("1245620", "ELDEN RING"), "apex": ("1172470", "Apex Legends"),
            "fortnite": ("1517990", "Fortnite"), "gta 5": ("271590", "Grand Theft Auto V"),
            "valorant": ("1818750", "VALORANT"), "rocket league": ("252950", "Rocket League")
        }

    # --- STEAM FONKSİYONLARI ---
    def _find_steam_path(self) -> Path | None:
        try:
            for hive, key_path in [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
                                   (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
                                   (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Valve\Steam")]:
                try:
                    key = winreg.OpenKey(hive, key_path)
                    val, _ = winreg.QueryValueEx(key, "InstallPath")
                    winreg.CloseKey(key)
                    p = Path(val)
                    if p.exists() and (p / "steam.exe").exists(): return p
                except: continue
        except: pass
        
        for p in [Path(os.environ.get("ProgramFiles(x86)", "")) / "Steam", Path("C:/Steam"), Path("D:/Steam")]:
            if p.exists() and (p / "steam.exe").exists(): return p
        return None

    def _get_steam_libraries(self, steam_path: Path) -> list[Path]:
        libraries = [steam_path / "steamapps"]
        vdf_path = steam_path / "steamapps" / "libraryfolders.vdf"
        if not vdf_path.exists(): return libraries
        try:
            content = vdf_path.read_text(encoding="utf-8", errors="ignore")
            for raw_path in re.findall(r'"path"\s+"([^"]+)"', content):
                lib = Path(raw_path.replace("\\\\", "/")) / "steamapps"
                if lib.exists() and lib not in libraries: libraries.append(lib)
        except: pass
        return libraries

    def _get_steam_games(self, steam_path: Path) -> list[dict]:
        games = []
        for lib in self._get_steam_libraries(steam_path):
            for acf in lib.glob("appmanifest_*.acf"):
                try:
                    content = acf.read_text(encoding="utf-8", errors="ignore")
                    app_id = re.search(r'"appid"\s+"(\d+)"', content)
                    name = re.search(r'"name"\s+"([^"]+)"', content)
                    state = re.search(r'"StateFlags"\s+"(\d+)"', content)
                    if app_id and name:
                        games.append({
                            "id": app_id.group(1), "name": name.group(1),
                            "state": int(state.group(1)) if state else 0
                        })
                except: continue
        return games

    def _is_steam_running(self) -> bool:
        try:
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq steam.exe"], capture_output=True, text=True).stdout
            return "steam.exe" in out.lower()
        except: return False

    def _ensure_steam_running(self, steam_path: Path) -> bool:
        if self._is_steam_running(): return True
        exe = steam_path / "steam.exe"
        if not exe.exists(): return False
        
        print("[OyunYöneticisi] 🚀 Steam başlatılıyor...")
        subprocess.Popen([str(exe)])
        for _ in range(20):
            time.sleep(1)
            if self._is_steam_running(): return True
        return False

    def _search_steam_appid(self, game_name: str) -> tuple[str | None, str | None]:
        name_lower = game_name.lower().strip()
        for key, (app_id, canonical) in self.known_appids.items():
            if name_lower in key or key in name_lower: return app_id, canonical
        try:
            import urllib.request, urllib.parse
            url = f"https://store.steampowered.com/api/storesearch/?term={urllib.parse.quote(game_name)}&l=english&cc=US"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                items = json.loads(resp.read().decode()).get("items", [])
            if items: return str(items[0]["id"]), items[0]["name"]
        except: pass
        return None, None

    # --- EPIC GAMES FONKSİYONLARI ---
    def _find_epic_exe(self) -> Path | None:
        try:
            for hive, key_path in [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\EpicGames\EpicGamesLauncher"),
                                   (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\EpicGames\EpicGamesLauncher"),
                                   (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\EpicGames\EpicGamesLauncher")]:
                try:
                    key = winreg.OpenKey(hive, key_path)
                    val, _ = winreg.QueryValueEx(key, "AppDataPath")
                    winreg.CloseKey(key)
                    exe = Path(val) / "Binaries" / "Win64" / "EpicGamesLauncher.exe"
                    if exe.exists(): return exe
                except: continue
        except: pass
        
        for candidate in [
            Path(os.environ.get("ProgramFiles(x86)", "")) / "Epic Games" / "Launcher" / "Portal" / "Binaries" / "Win64" / "EpicGamesLauncher.exe",
            Path(os.environ.get("ProgramFiles", "")) / "Epic Games" / "Launcher" / "Portal" / "Binaries" / "Win64" / "EpicGamesLauncher.exe"
        ]:
            if candidate.exists(): return candidate
        return None

    def _get_epic_games(self) -> list[dict]:
        manifests = Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
        if not manifests.exists(): return []
        games = []
        for item_file in manifests.glob("*.item"):
            try:
                data = json.loads(item_file.read_text(encoding="utf-8"))
                name = data.get("DisplayName") or data.get("AppName", "")
                if name: games.append({"id": data.get("AppName", ""), "name": name})
            except: continue
        return games

    def _is_epic_running(self) -> bool:
        try:
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq EpicGamesLauncher.exe"], capture_output=True, text=True).stdout
            return "epicgameslauncher.exe" in out.lower()
        except: return False

    # --- OTONOM KURULUM VE KAPATMA ---
    def _find_best_drive(self) -> dict | None:
        import string
        drives = []
        for letter in string.ascii_uppercase:
            drive_path = f"{letter}:\\"
            if os.path.exists(drive_path):
                try:
                    free_gb = shutil.disk_usage(drive_path).free / (1024 ** 3)
                    if free_gb > 0: drives.append({"letter": letter, "path": drive_path, "free_gb": free_gb})
                except: continue
        return max(drives, key=lambda d: d["free_gb"]) if drives else None

    def _handle_install_dialog(self, game_name: str) -> str:
        best_drive = self._find_best_drive()
        if not best_drive: return f"Lütfen '{game_name}' için kurulumu manuel tamamlayın."
        print(f"[OyunYöneticisi] 🏆 Hedef sürücü: {best_drive['letter']}:\\ ({best_drive['free_gb']:.1f} GB boş)")
        try:
            import pyautogui
            import pygetwindow as gw
            install_win = None
            for _ in range(30):
                time.sleep(0.5)
                for w in gw.getAllWindows():
                    if ("install" in w.title.lower() or "yükle" in w.title.lower() or "steam" in w.title.lower()) and w.width > 300 and w.visible:
                        install_win = w
                        break
                if install_win: break

            if not install_win: return "Kurulum ekranı bulunamadı."
            try: install_win.activate(); time.sleep(0.4)
            except: pass
            
            wx, wy, ww, wh = install_win.left, install_win.top, install_win.width, install_win.height
            pyautogui.click(wx + int(ww * 0.35), wy + int(wh * 0.45)); time.sleep(0.2)
            pyautogui.typewrite(best_drive["letter"], interval=0.05); time.sleep(0.2)
            pyautogui.click(wx + int(ww * 0.72), wy + int(wh * 0.88))
            return f"{best_drive['letter']}: sürücüsü seçildi ve '{game_name}' kurulumu başlatıldı."
        except Exception as e:
            return f"Otomatik kurulum hatası: {e}"

    def _watch_and_shutdown(self, steam_path: Path):
        print("[OyunYöneticisi] İndirmeler izleniyor...")
        for _ in range(24): 
            time.sleep(5)
            if any(g["state"] == 1026 for g in self._get_steam_games(steam_path)): break
        else: return 
        
        while True:
            time.sleep(30)
            if not any(g["state"] == 1026 for g in self._get_steam_games(steam_path)):
                print("[OyunYöneticisi] İndirme bitti, sistem kapatılıyor.")
                time.sleep(5)
                subprocess.run(["shutdown", "/s", "/t", "10"])
                return

    # --- ZAMANLANMIŞ GÖREVLER (WINDOWS TASK SCHEDULER) ---
    def schedule_update(self, hour: int, minute: int) -> str:
        task_name = "SIRIUS_GameUpdater"
        script_path = Path(__file__).resolve()
        subprocess.run(["schtasks", "/Delete", "/TN", task_name, "/F"], capture_output=True)
        cmd = ["schtasks", "/Create", "/TN", task_name, "/TR", f'"{sys.executable}" "{script_path}" --scheduled', "/SC", "DAILY", "/ST", f"{hour:02d}:{minute:02d}", "/F", "/RL", "HIGHEST"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0: return f"Oyun güncellemeleri her gün {hour:02d}:{minute:02d} saatine zamanlandı."
        return f"Zamanlama başarısız: {result.stderr.strip()}"

    def cancel_schedule(self) -> str:
        result = subprocess.run(["schtasks", "/Delete", "/TN", "SIRIUS_GameUpdater", "/F"], capture_output=True, text=True)
        return "Zamanlanmış oyun güncellemesi iptal edildi." if result.returncode == 0 else "Aktif bir zamanlanmış görev bulunamadı."

    def schedule_status(self) -> str:
        result = subprocess.run(["schtasks", "/Query", "/TN", "SIRIUS_GameUpdater", "/FO", "LIST"], capture_output=True, text=True)
        if result.returncode != 0: return "Zamanlanmış bir oyun güncellemesi yok."
        for line in result.stdout.strip().splitlines():
            if "Next Run" in line or "Sonraki Çalışma Zamanı" in line: return f"Oyun güncellemesi aktif. {line.strip()}"
        return "Oyun güncellemesi zamanlanmış durumda."

    # --- ANA YÜRÜTÜCÜ ---
    def execute(self, params: dict) -> str:
        action = params.get("g_action", "update").lower()
        platform = params.get("platform", "both").lower()
        game_name = params.get("game_name", "")
        shutdown = str(params.get("shutdown_when_done", "false")).lower() == "true"
        hour, minute = int(params.get("hour", 3)), int(params.get("minute", 0))

        if action == "schedule": return self.schedule_update(hour, minute)
        if action == "cancel_schedule": return self.cancel_schedule()
        if action == "schedule_status": return self.schedule_status()

        results = []
        steam_path = self._find_steam_path()

        # STEAM İŞLEMLERİ
        if platform in ("steam", "both") and steam_path:
            if action == "list":
                games = self._get_steam_games(steam_path)
                results.append(f"Steam ({len(games)} oyun): " + ", ".join(g["name"] for g in games[:10]))
            elif action == "download_status":
                games = self._get_steam_games(steam_path)
                active = [g for g in games if g["state"] == 1026]
                if active: results.append(f"Steam İndiriliyor: {', '.join(g['name'] for g in active)}")
            elif action in ("install", "update"):
                if not self._ensure_steam_running(steam_path): results.append("Steam başlatılamadı.")
                else:
                    installed = self._get_steam_games(steam_path)
                    if game_name:
                        already = next((g for g in installed if game_name.lower() in g["name"].lower()), None)
                        if already:
                            if already["state"] in (6, 516):
                                subprocess.Popen([str(steam_path / "steam.exe"), f"steam://update/{already['id']}"])
                                results.append(f"Steam: '{already['name']}' güncelleniyor.")
                            else: results.append(f"Steam: '{already['name']}' zaten kurulu/güncel.")
                        else:
                            app_id, found_name = self._search_steam_appid(game_name)
                            if app_id:
                                subprocess.Popen([str(steam_path / "steam.exe"), f"steam://install/{app_id}"])
                                threading.Thread(target=self._handle_install_dialog, args=(found_name or game_name,), daemon=True).start()
                                results.append(f"Steam: '{found_name or game_name}' kuruluyor.")
                            else: results.append(f"Steam: '{game_name}' bulunamadı.")
                    else:
                        for g in [g for g in installed if g["state"] in (6, 516)]:
                            subprocess.Popen([str(steam_path / "steam.exe"), f"steam://update/{g['id']}"])
                        results.append("Steam: Bekleyen tüm güncellemeler başlatıldı.")

                    if shutdown:
                        threading.Thread(target=self._watch_and_shutdown, args=(steam_path,), daemon=True).start()
                        results.append("(Sistem işlem bitince kapanacak)")

        # EPIC GAMES İŞLEMLERİ
        epic_exe = self._find_epic_exe()
        if platform in ("epic", "both") and epic_exe:
            if action == "list":
                games = self._get_epic_games()
                results.append(f"Epic ({len(games)} oyun): " + ", ".join(g["name"] for g in games[:10]))
            elif action in ("install", "update"):
                games = self._get_epic_games()
                if game_name:
                    matched = [g for g in games if game_name.lower() in g["name"].lower()]
                    if matched:
                        subprocess.Popen([str(epic_exe), f"com.epicgames.launcher://apps/{matched[0]['id']}?action=launch&silent=true"])
                        results.append(f"Epic: '{matched[0]['name']}' açılıyor/güncelleniyor.")
                    else: results.append(f"Epic: '{game_name}' bulunamadı.")
                else:
                    if not self._is_epic_running(): subprocess.Popen([str(epic_exe)])
                    for g in games[:10]:
                        subprocess.Popen([str(epic_exe), f"com.epicgames.launcher://apps/{g['id']}?action=launch&silent=true"])
                        time.sleep(0.5)
                    results.append("Epic: Bekleyen güncellemeler kontrol ediliyor.")

        return " | ".join(results) if results else "Hiçbir platformda işlem yapılamadı."

# Modül kendi başına (Zamanlanmış görev olarak) çalıştırılırsa:
if __name__ == "__main__":
    if "--scheduled" in sys.argv:
        print(f"[OyunYöneticisi] 🕐 Zamanlanmış görev çalışıyor: {datetime.now().strftime('%H:%M')}")
        updater = GameUpdater()
        result = updater.execute({"g_action": "update", "platform": "both"})
        print(f"[OyunYöneticisi] ✅ {result}")