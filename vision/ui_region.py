"""
vision/ui_region.py
===================
Windows'ta aktif pencere, uygulama bölgesi ve dialog tespiti.

Bu modül CoordEngine'e "nereye bakacağını" söyler:
    - Tüm ekran yerine sadece aktif pencere bölgesini tara
    - Açık pencereleri listele, başlıklarına göre bul
    - Dialog / popup bölgesini otomatik tespit et
    - Çoklu monitörde doğru ekranı seç

OpenGuider JS karşılığı:
    electron.screen.getAllDisplays()   → ScreenRegionManager.get_monitors()
    BrowserWindow.getBounds()          → WindowInfo.region
    desktopCapturer filtered by app    → get_window_region(title)

Windows API erişimi:
    win32gui.EnumWindows()             → açık pencereleri listele
    win32gui.GetWindowRect()           → pencere koordinatları
    win32gui.GetForegroundWindow()     → aktif pencere
    win32process.GetWindowThreadProcessId() → PID → uygulama adı
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from pydantic import BaseModel, Field

# win32 isteğe bağlı — yoksa graceful degradation
try:
    import win32gui    # type: ignore
    import win32con    # type: ignore
    import win32process  # type: ignore
    import psutil      # type: ignore
    _WIN32_OK = True
except ImportError:
    _WIN32_OK = False

from vision.coord_engine import ScreenRegion

# ---------------------------------------------------------------------------
logger = logging.getLogger("sirius.vision.ui_region")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(asctime)s] [%(name)s] %(levelname)s — %(message)s", "%H:%M:%S"))
    logger.addHandler(_h)
logger.setLevel(logging.DEBUG)


# ---------------------------------------------------------------------------
# Veri Modelleri
# ---------------------------------------------------------------------------

class WindowInfo(BaseModel):
    """Tek bir Windows penceresi hakkında bilgi."""
    hwnd:          int          = 0
    title:         str          = ""
    process_name:  str          = ""
    pid:           int          = 0
    region:        ScreenRegion = Field(default_factory=ScreenRegion)
    is_visible:    bool         = True
    is_minimized:  bool         = False
    is_foreground: bool         = False
    z_order:       int          = 0      # 0 = en üstte


class MonitorInfo(BaseModel):
    """Tek bir fiziksel monitör bilgisi."""
    index:       int          = 1
    region:      ScreenRegion = Field(default_factory=ScreenRegion)
    is_primary:  bool         = False
    dpi_scale:   float        = 1.0


class UIContext(BaseModel):
    """
    Anlık ekran bağlamı — orchestrator planner'a verilir.
    coord_engine.get_screen_context() bunu genişletir.
    """
    active_window_title:   str                = ""
    active_window_process: str                = ""
    active_region:         Optional[ScreenRegion] = None
    visible_windows:       list[str]          = Field(default_factory=list)
    monitor_count:         int                = 1
    primary_monitor:       Optional[ScreenRegion] = None
    captured_at:           float             = Field(default_factory=time.time)


# ---------------------------------------------------------------------------
# WindowEnumerator — win32gui ile pencere listesi
# ---------------------------------------------------------------------------

class WindowEnumerator:
    """
    Win32 API ile açık pencereleri listeler ve sorgular.

    JS karşılığı:
        const wins = await desktopCapturer.getSources({types:['window']})

    Python:
        win32gui.EnumWindows(callback, results_list)
    """

    def _enum_callback(self, hwnd: int, results: list) -> None:
        """EnumWindows callback'i — her pencere için çağrılır."""
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return

        try:
            rect = win32gui.GetWindowRect(hwnd)
        except Exception:
            return

        left, top, right, bottom = rect
        w = right - left
        h = bottom - top

        # Boyutsuz veya ekran dışı pencereleri atla
        if w <= 0 or h <= 0:
            return

        # PID ve process adı
        pid = 0
        process_name = ""
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            proc = psutil.Process(pid)
            process_name = proc.name()
        except Exception:
            pass

        region = ScreenRegion(left=left, top=top, width=w, height=h)
        foreground_hwnd = win32gui.GetForegroundWindow()

        info = WindowInfo(
            hwnd=hwnd,
            title=title,
            process_name=process_name,
            pid=pid,
            region=region,
            is_visible=True,
            is_minimized=win32gui.IsIconic(hwnd),
            is_foreground=(hwnd == foreground_hwnd),
        )
        results.append(info)

    def get_all_windows(self) -> list[WindowInfo]:
        """Görünür tüm pencereleri listeler."""
        if not _WIN32_OK:
            logger.warning("[UIRegion] pywin32/psutil bulunamadı — boş pencere listesi.")
            return []
        results: list[WindowInfo] = []
        try:
            win32gui.EnumWindows(self._enum_callback, results)
        except Exception as e:
            logger.error(f"[UIRegion] EnumWindows hatası: {e}")
        # Z-sırasına göre sırala (foreground önce)
        results.sort(key=lambda w: (0 if w.is_foreground else 1, w.z_order))
        return results

    def get_foreground_window(self) -> Optional[WindowInfo]:
        """Aktif (ön planda) pencereyi döner."""
        if not _WIN32_OK:
            return None
        try:
            hwnd = win32gui.GetForegroundWindow()
            title = win32gui.GetWindowText(hwnd)
            rect = win32gui.GetWindowRect(hwnd)
            left, top, right, bottom = rect

            pid = 0
            process_name = ""
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                proc = psutil.Process(pid)
                process_name = proc.name()
            except Exception:
                pass

            return WindowInfo(
                hwnd=hwnd,
                title=title,
                process_name=process_name,
                pid=pid,
                region=ScreenRegion(
                    left=left, top=top,
                    width=right - left, height=bottom - top,
                ),
                is_foreground=True,
            )
        except Exception as e:
            logger.error(f"[UIRegion] GetForegroundWindow hatası: {e}")
            return None

    def find_window_by_title(
        self, title_fragment: str, case_sensitive: bool = False
    ) -> Optional[WindowInfo]:
        """
        Başlık içinde verilen metni içeren pencereyi bulur.
        Örnek: find_window_by_title("Chrome") → Chrome penceresini döner.
        """
        needle = title_fragment if case_sensitive else title_fragment.lower()
        for win in self.get_all_windows():
            haystack = win.title if case_sensitive else win.title.lower()
            if needle in haystack:
                return win
        return None

    def find_window_by_process(self, process_name: str) -> list[WindowInfo]:
        """Belirli bir uygulamanın (process adına göre) tüm pencerelerini döner."""
        needle = process_name.lower()
        return [
            w for w in self.get_all_windows()
            if needle in w.process_name.lower()
        ]

    def bring_to_front(self, hwnd: int) -> bool:
        """Pencereyi ön plana getirir."""
        if not _WIN32_OK:
            return False
        try:
            win32gui.SetForegroundWindow(hwnd)
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            return True
        except Exception as e:
            logger.error(f"[UIRegion] BringToFront hatası: {e}")
            return False


# ---------------------------------------------------------------------------
# MonitorDetector — çoklu monitör desteği
# ---------------------------------------------------------------------------

class MonitorDetector:
    """
    Bağlı monitörleri tespit eder.

    JS karşılığı:
        electron.screen.getAllDisplays()

    Python:
        mss ile monitor listesi veya win32api.EnumDisplayMonitors()
    """

    def get_all_monitors(self) -> list[MonitorInfo]:
        """Tüm monitörlerin bilgisini döner."""
        try:
            import mss
            with mss.mss() as sct:
                monitors: list[MonitorInfo] = []
                # sct.monitors[0] = tüm ekranlar birleşik
                # sct.monitors[1..n] = bireysel monitörler
                for i, m in enumerate(sct.monitors[1:], start=1):
                    monitors.append(MonitorInfo(
                        index=i,
                        region=ScreenRegion(
                            left=m["left"],
                            top=m["top"],
                            width=m["width"],
                            height=m["height"],
                        ),
                        is_primary=(i == 1),
                    ))
                return monitors
        except Exception as e:
            logger.error(f"[UIRegion] Monitör tespiti hatası: {e}")
            return [MonitorInfo(
                index=1,
                region=ScreenRegion(left=0, top=0, width=1920, height=1080),
                is_primary=True,
            )]

    def get_primary_monitor(self) -> MonitorInfo:
        monitors = self.get_all_monitors()
        for m in monitors:
            if m.is_primary:
                return m
        return monitors[0] if monitors else MonitorInfo(
            index=1,
            region=ScreenRegion(left=0, top=0, width=1920, height=1080),
            is_primary=True,
        )

    def get_monitor_for_point(self, x: int, y: int) -> Optional[MonitorInfo]:
        """Verilen koordinatın hangi monitörde olduğunu döner."""
        for m in self.get_all_monitors():
            r = m.region
            if r.left <= x < r.right and r.top <= y < r.bottom:
                return m
        return None


# ---------------------------------------------------------------------------
# DialogDetector — popup ve dialog bölgesi tespiti
# ---------------------------------------------------------------------------

class DialogDetector:
    """
    Ekranda dialog, popup veya modal pencere tespit eder.

    OpenGuider'da bu yoktu — SIRIUS'a özgü ekleme.
    Görev planlarken "önce açık dialogu kapat" gibi kararlar için kullanılır.
    """

    # Dialog olduğunu gösteren tipik başlık anahtar kelimeleri
    DIALOG_KEYWORDS = [
        "uyarı", "hata", "onay", "emin misiniz", "evet", "hayır",
        "tamam", "iptal", "warning", "error", "confirm", "dialog",
        "alert", "yes", "no", "ok", "cancel", "save", "delete",
    ]

    def __init__(self):
        self._enumerator = WindowEnumerator()

    def find_active_dialog(self) -> Optional[WindowInfo]:
        """
        Ön planda bir dialog/popup penceresi varsa döner.
        Küçük pencereler ve bilinen dialog anahtar kelimeleri kullanılır.
        """
        foreground = self._enumerator.get_foreground_window()
        if foreground is None:
            return None

        # Boyut kontrolü: dialoglar genellikle küçüktür
        r = foreground.region
        is_small = r.width < 700 and r.height < 500

        # Başlık kontrolü
        title_lower = foreground.title.lower()
        has_dialog_keyword = any(kw in title_lower for kw in self.DIALOG_KEYWORDS)

        if is_small or has_dialog_keyword:
            logger.info(f"[UIRegion] Dialog tespit edildi: '{foreground.title}' — {r.width}×{r.height}")
            return foreground

        return None

    def get_dialog_region(self) -> Optional[ScreenRegion]:
        """Aktif dialog varsa bölgesini döner, yoksa None."""
        dialog = self.find_active_dialog()
        return dialog.region if dialog else None

    def has_blocking_dialog(self) -> bool:
        """
        Görev akışını bloklayacak açık bir dialog var mı?
        orchestrator adım çalıştırmadan önce bunu kontrol eder.
        """
        return self.find_active_dialog() is not None


# ---------------------------------------------------------------------------
# ScreenRegionManager — Ana Sınıf
# ---------------------------------------------------------------------------

class ScreenRegionManager:
    """
    UIRegion modülünün tek giriş noktası.

    CoordEngine ve Orchestrator bu sınıfı kullanır:
        - Nereye bakacağını öğrenir (pencere bölgesi, monitör)
        - Dialog engelini tespit eder
        - UI bağlamını Pydantic modeli olarak döner

    SIRIUS entegrasyon noktası:
        # SiriusController.__init__() içinde:
        from vision.ui_region import ScreenRegionManager
        self.region_manager = ScreenRegionManager()

        # coord_engine.find() çağrısından önce:
        region = self.region_manager.get_active_region()
        hint = await self.coord_engine.find(target, region=region)
    """

    def __init__(self):
        self.windows  = WindowEnumerator()
        self.monitors = MonitorDetector()
        self.dialogs  = DialogDetector()

    # ------------------------------------------------------------------
    # Bölge Alma
    # ------------------------------------------------------------------

    def get_active_region(self) -> Optional[ScreenRegion]:
        """
        Aktif pencere bölgesini döner.
        Pencere minimize ise None döner → tam ekran kullanılır.
        """
        win = self.windows.get_foreground_window()
        if win is None or win.is_minimized:
            return None
        return win.region

    def get_window_region(self, title_fragment: str) -> Optional[ScreenRegion]:
        """
        Başlıkta verilen metni içeren pencerenin bölgesini döner.
        Örnek: get_window_region("Notepad") → Notepad penceresinin ScreenRegion'ı
        """
        win = self.windows.find_window_by_title(title_fragment)
        return win.region if win else None

    def get_primary_region(self) -> ScreenRegion:
        """Birincil monitörün tam bölgesi."""
        return self.monitors.get_primary_monitor().region

    def get_dialog_region(self) -> Optional[ScreenRegion]:
        """Varsa açık dialog bölgesi."""
        return self.dialogs.get_dialog_region()

    def get_safe_search_region(self) -> ScreenRegion:
        """
        Arama için en uygun bölgeyi seç:
        1. Varsa aktif pencere
        2. Varsa dialog
        3. Tüm birincil ekran
        """
        dialog = self.dialogs.get_dialog_region()
        if dialog:
            return dialog

        active = self.get_active_region()
        if active:
            return active

        return self.get_primary_region()

    # ------------------------------------------------------------------
    # Bağlam Toplama
    # ------------------------------------------------------------------

    def get_ui_context(self) -> UIContext:
        """
        Anlık UI durumunun özeti — orchestrator planner'a geçilir.
        TaskPlan.context alanına string olarak eklenir.
        """
        foreground = self.windows.get_foreground_window()
        all_windows = self.windows.get_all_windows()
        primary = self.monitors.get_primary_monitor()

        visible_titles = [
            w.title for w in all_windows
            if not w.is_minimized and w.title
        ][:10]  # ilk 10

        return UIContext(
            active_window_title=foreground.title if foreground else "",
            active_window_process=foreground.process_name if foreground else "",
            active_region=foreground.region if foreground else None,
            visible_windows=visible_titles,
            monitor_count=len(self.monitors.get_all_monitors()),
            primary_monitor=primary.region,
        )

    def context_as_string(self) -> str:
        """
        UIContext'i AI prompt'una eklenecek string formatına çevirir.
        orchestrator.py'deki _build_planning_prompt'a geçilir.
        """
        ctx = self.get_ui_context()
        lines = [
            f"Aktif Pencere: {ctx.active_window_title} ({ctx.active_window_process})",
            f"Monitör Sayısı: {ctx.monitor_count}",
            f"Açık Pencereler: {', '.join(ctx.visible_windows[:5])}",
        ]
        if self.dialogs.has_blocking_dialog():
            lines.append("⚠️ UYARI: Ekranda açık bir dialog/uyarı penceresi var!")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Pencere Yönetim Yardımcıları
    # ------------------------------------------------------------------

    def focus_window(self, title_fragment: str) -> bool:
        """
        Başlıkta verilen metni içeren pencereyi ön plana getirir.
        Göreve başlamadan önce doğru uygulamaya odaklanmak için kullanılır.
        """
        win = self.windows.find_window_by_title(title_fragment)
        if win is None:
            logger.warning(f"[UIRegion] '{title_fragment}' penceresi bulunamadı.")
            return False
        success = self.windows.bring_to_front(win.hwnd)
        if success:
            logger.info(f"[UIRegion] '{win.title}' ön plana getirildi.")
        return success

    async def focus_window_async(self, title_fragment: str) -> bool:
        """asyncio uyumlu versiyon."""
        return await asyncio.to_thread(self.focus_window, title_fragment)

    def list_open_apps(self) -> list[str]:
        """
        Açık uygulamaların process adlarını döner (tekrarsız).
        Örnek çıktı: ["chrome.exe", "notepad.exe", "explorer.exe"]
        """
        seen: set[str] = set()
        result: list[str] = []
        for win in self.windows.get_all_windows():
            if win.process_name and win.process_name not in seen:
                seen.add(win.process_name)
                result.append(win.process_name)
        return result


# ---------------------------------------------------------------------------
# Bağımsız Test
# ---------------------------------------------------------------------------

async def _demo() -> None:
    print("\n🪟  SIRIUS UIRegion Demo Başlatılıyor...")
    print("─" * 50)

    manager = ScreenRegionManager()

    # 1. Aktif pencere
    ctx = manager.get_ui_context()
    print(f"\n1. Aktif Pencere : '{ctx.active_window_title}' ({ctx.active_window_process})")
    if ctx.active_region:
        r = ctx.active_region
        print(f"   Bölge         : x={r.left}, y={r.top}, {r.width}×{r.height}")

    # 2. Monitörler
    monitors = manager.monitors.get_all_monitors()
    print(f"\n2. Monitörler    : {len(monitors)} adet")
    for m in monitors:
        r = m.region
        tag = " [BİRİNCİL]" if m.is_primary else ""
        print(f"   Monitör {m.index}{tag}: {r.width}×{r.height} @ ({r.left}, {r.top})")

    # 3. Açık pencereler
    wins = manager.windows.get_all_windows()
    print(f"\n3. Açık Pencereler: {len(wins)} adet")
    for w in wins[:5]:
        print(f"   • '{w.title[:50]}' [{w.process_name}]")

    # 4. Dialog kontrolü
    dialog = manager.dialogs.find_active_dialog()
    print(f"\n4. Dialog Durumu : {'⚠️  AÇIK DIALOG: ' + dialog.title if dialog else '✅ Dialog yok'}")

    # 5. Context string (planner'a verilecek format)
    print(f"\n5. Planner Bağlamı:\n{manager.context_as_string()}")

    # 6. Güvenli arama bölgesi
    region = manager.get_safe_search_region()
    print(f"\n6. Güvenli Arama Bölgesi: {region.width}×{region.height} @ ({region.left}, {region.top})")

    print("\n✅ UIRegion demo tamamlandı.")


if __name__ == "__main__":
    asyncio.run(_demo())
