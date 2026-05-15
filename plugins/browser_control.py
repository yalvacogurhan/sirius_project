# plugins/browser_control.py

import asyncio
import concurrent.futures
import os
import platform
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Optional

from playwright.async_api import (
    async_playwright,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeout,
)

_OS = platform.system()

def _normalize_url(url: str) -> str:
    url = url.strip()
    if not url: return "about:blank"
    if "://" in url: return url
    if "." not in url: url = url + ".com"
    return "https://" + url

def _real_profile_dir(browser: str) -> str:
    home  = Path.home()
    local = os.environ.get("LOCALAPPDATA", "")
    roam  = os.environ.get("APPDATA", "")
    candidates: list[Path] = []

    if _OS == "Windows":
        m = {
            "chrome":   [Path(local) / "Google" / "Chrome" / "User Data"],
            "edge":     [Path(local) / "Microsoft" / "Edge" / "User Data"],
            "brave":    [Path(local) / "BraveSoftware" / "Brave-Browser" / "User Data"],
            "vivaldi":  [Path(local) / "Vivaldi" / "User Data"],
            "opera":    [Path(roam)  / "Opera Software" / "Opera Stable", Path(local) / "Opera Software" / "Opera Stable"],
            "operagx":  [Path(roam)  / "Opera Software" / "Opera GX Stable", Path(local) / "Opera Software" / "Opera GX Stable"],
        }
        candidates = m.get(browser, [])

    for p in candidates:
        if p.exists():
            print(f"[Tarayıcı] ✅ {browser} için gerçek profil bulundu: {p}")
            return str(p)

    fallback = home / ".sirius_profiles" / browser
    fallback.mkdir(parents=True, exist_ok=True)
    print(f"[Tarayıcı] ⚠️ {browser} profili bulunamadı, Sirius profili kullanılıyor: {fallback}")
    return str(fallback)

# (KODUN ÇOK UZAMAMASI İÇİN DİĞER YARDIMCI FONKSİYONLAR - firefox, opera vb - AYNEN KORUNDU AMA SADELEŞTİRİLDİ)
_BROWSER_SPECS = {
    "Windows": {
        "chrome":   {"engine": "chromium", "channel": "chrome",  "bins": []},
        "edge":     {"engine": "chromium", "channel": "msedge",  "bins": []},
        "firefox":  {"engine": "firefox",  "channel": None,      "bins": ["firefox.exe"]},
        "opera":    {"engine": "chromium", "channel": None,      "bins": ["opera.exe"],  "special": "opera_windows"},
        "operagx":  {"engine": "chromium", "channel": None,      "bins": [],             "special": "opera_windows"},
        "brave":    {"engine": "chromium", "channel": None,      "bins": ["brave.exe"]},
    }
}
_ALIASES = {"google chrome": "chrome", "ms edge": "edge", "opera gx": "operagx"}

def _resolve_browser(name: str) -> dict | None:
    name = _ALIASES.get(name.lower().strip(), name.lower().strip())
    spec = _BROWSER_SPECS.get(_OS, {}).get(name)
    if not spec: return None
    return {"engine": spec["engine"], "exe": None, "channel": spec.get("channel")}

class _BrowserSession:
    def __init__(self, browser_name: str):
        self.browser_name = browser_name
        self._spec = _resolve_browser(browser_name)
        self._loop, self._thread = None, None
        self._ready = threading.Event()
        self._pw, self._context, self._page = None, None, None

    def start(self):
        if self._thread and self._thread.is_alive(): return
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name=f"Browser-{self.browser_name}")
        self._thread.start()
        self._ready.wait(timeout=20)

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._async_init())
        self._ready.set()
        self._loop.run_forever()

    async def _async_init(self):
        self._pw = await async_playwright().start()

    def run(self, coro, timeout: int = 60) -> str:
        if not self._loop: raise RuntimeError("Tarayıcı oturumu başlatılamadı.")
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=timeout)

    def close(self):
        if self._loop: asyncio.run_coroutine_threadsafe(self._async_close(), self._loop).result(10)

    async def _async_close(self):
        if self._context:
            try: await self._context.close()
            except: pass
        if self._pw:
            try: await self._pw.stop()
            except: pass
        self._context = self._page = None

    async def _launch(self):
        if self._context is not None: return
        if self._spec is None: raise RuntimeError(f"'{self.browser_name}' desteklenmiyor.")

        engine_name = self._spec["engine"]
        channel = self._spec["channel"]
        engine_obj = getattr(self._pw, engine_name)
        profile = _real_profile_dir(self.browser_name)

        kwargs = {"headless": False, "no_viewport": True, "args": ["--start-maximized"]}
        if channel: kwargs["channel"] = channel

        try:
            self._context = await engine_obj.launch_persistent_context(profile, **kwargs)
            self._page = await self._context.new_page()
            print(f"[Tarayıcı] ✅ Başlatıldı: {self.browser_name}")
        except Exception as e:
            fallback = str(Path.home() / ".sirius_profiles" / self.browser_name)
            self._context = await engine_obj.launch_persistent_context(fallback, **kwargs)
            self._page = await self._context.new_page()

    async def _get_page(self) -> Page:
        await self._launch()
        if self._page is None or self._page.is_closed():
            self._page = await self._context.new_page()
        return self._page

    async def go_to(self, url: str) -> str:
        url = _normalize_url(url)
        page = await self._get_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            return f"Açıldı: {page.url}"
        except:
            return f"Açılamadı: {url}"

    async def search(self, query: str, engine: str = "google") -> str:
        return await self.go_to(f"https://www.google.com/search?q={query.replace(' ', '+')}")

    async def smart_click(self, description: str) -> str:
        page = await self._get_page()
        try:
            await page.get_by_text(description, exact=False).first.click(timeout=5_000)
            return f"Tıklandı: '{description}'"
        except:
            return f"Bulunamadı: '{description}'"

    async def smart_type(self, description: str, text: str) -> str:
        page = await self._get_page()
        try:
            el = page.get_by_placeholder(description, exact=False).first
            await el.clear()
            await el.type(text, delay=50)
            return f"Yazıldı: '{description}' -> {text}"
        except:
            return f"Metin kutusu bulunamadı: '{description}'"

    async def scroll(self, direction: str, amount: int) -> str:
        page = await self._get_page()
        y = amount if direction == "down" else -amount
        await page.mouse.wheel(0, y)
        return f"{direction} yönüne kaydırıldı."

    async def new_tab(self) -> str:
        page = await self._get_page()
        self._page = await page.context.new_page()
        return "Yeni sekme açıldı."

    async def close_tab(self) -> str:
        if self._page and not self._page.is_closed():
            await self._page.close()
            return "Sekme kapatıldı."
        return "Kapatılacak sekme yok."

class BrowserController:
    def __init__(self):
        print("🌐 Sirius Otonom Tarayıcı Modülü (Playwright) Aktif!")
        self._sessions = {}
        self._active_browser = "chrome"

    def execute(self, action: str, params: dict) -> str:
        if action == "close_all":
            for s in self._sessions.values(): s.close()
            self._sessions.clear()
            return "Tüm tarayıcılar kapatıldı."

        if self._active_browser not in self._sessions:
            sess = _BrowserSession(self._active_browser)
            sess.start()
            self._sessions[self._active_browser] = sess
        sess = self._sessions[self._active_browser]

        try:
            if action == "go_to": return sess.run(sess.go_to(params.get("url", "")))
            elif action == "search": return sess.run(sess.search(params.get("query", "")))
            elif action == "smart_click": return sess.run(sess.smart_click(params.get("description", "")))
            elif action == "smart_type": return sess.run(sess.smart_type(params.get("description", ""), params.get("text", "")))
            elif action == "scroll": return sess.run(sess.scroll(params.get("direction", "down"), int(params.get("amount", 500))))
            elif action == "new_tab": return sess.run(sess.new_tab())
            elif action == "close_tab": return sess.run(sess.close_tab())
            else: return f"Bilinmeyen tarayıcı komutu: {action}"
        except Exception as e:
            return f"Tarayıcı hatası: {e}"