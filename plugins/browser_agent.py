"""
plugins/browser_agent.py
=========================
SIRIUS için Playwright tabanlı tam browser otomasyon plugin'i.

Neden Playwright, Selenium değil?
    - Native asyncio desteği → SIRIUS'un event loop'unu bloke etmez
    - Selenium → threading + WebDriver protokolü → ekstra gecikme
    - Playwright → doğrudan CDP (Chrome DevTools Protocol) → 3-5× hızlı
    - Auto-wait built-in → element görünene kadar otomatik bekler
    - Screenshot, PDF, network intercept yerleşik
    - Headless ve headed mod aynı API

OpenGuider karşılığı:
    OpenGuider'da browser Electron'un webview/Playwright entegrasyonu ile yapılır.
    SIRIUS'ta bu tamamen bağımsız bir Playwright instance'ı olarak çalışır.

Orchestrator entegrasyonu:
    Bu plugin make_sirius_tool_executor() tarafından çağrılır.
    tool_name = "browser_action" → BrowserAgent.execute(args) → str sonuç

    Desteklenen action'lar:
        navigate       → URL'ye git
        click          → selector veya metin ile tıkla
        type           → input'a metin yaz
        extract        → sayfadan metin/veri çıkar
        screenshot     → ekran görüntüsü al (base64)
        scroll         → sayfayı kaydır
        wait           → element bekle
        search         → Google'da ara (navigate + extract birleşimi)
        fill_form      → form doldur
        get_links      → sayfadaki linkleri al
        run_js         → JavaScript çalıştır
        close          → tarayıcıyı kapat

Kurulum:
    pip install playwright
    playwright install chromium
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field as PydanticField

logger = logging.getLogger("sirius.plugins.browser_agent")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(
        logging.Formatter("[%(asctime)s] [%(name)s] %(levelname)s — %(message)s", "%H:%M:%S")
    )
    logger.addHandler(_h)
logger.setLevel(logging.DEBUG)


# ---------------------------------------------------------------------------
# Veri Modelleri
# ---------------------------------------------------------------------------

class BrowserAction(str, Enum):
    NAVIGATE   = "navigate"
    CLICK      = "click"
    TYPE       = "type"
    EXTRACT    = "extract"
    SCREENSHOT = "screenshot"
    SCROLL     = "scroll"
    WAIT       = "wait"
    SEARCH     = "search"
    FILL_FORM  = "fill_form"
    GET_LINKS  = "get_links"
    RUN_JS     = "run_js"
    CLOSE      = "close"
    BACK       = "back"
    FORWARD    = "forward"
    REFRESH    = "refresh"
    NEW_TAB    = "new_tab"
    GET_TEXT   = "get_text"


class BrowserRequest(BaseModel):
    """
    Orchestrator'ın browser plugin'e gönderdiği istek.

    Orchestrator TaskStep.tool_args şöyle doldurulur:
        {
            "action": "navigate",
            "url": "https://google.com",
            "wait_for": "networkidle"
        }
    """
    action:       str                 = "navigate"
    url:          str                 = ""
    selector:     str                 = ""     # CSS selector veya metin
    text:         str                 = ""     # type action için
    query:        str                 = ""     # search action için
    wait_for:     str                 = "load" # "load" | "networkidle" | "domcontentloaded"
    timeout_ms:   int                 = 15_000
    headless:     bool                = True
    extract_mode: str                 = "text" # "text" | "html" | "json"
    scroll_dir:   str                 = "down" # "down" | "up" | "top" | "bottom"
    scroll_px:    int                 = 500
    js_code:      str                 = ""
    form_data:    dict                = PydanticField(default_factory=dict)
    screenshot_path: str              = ""     # kaydetmek için yol
    max_links:    int                 = 20
    new_tab_url:  str                 = ""


class BrowserResult(BaseModel):
    """Browser action'ın sonucu."""
    success:      bool  = True
    action:       str   = ""
    url:          str   = ""
    title:        str   = ""
    text:         str   = ""         # extract/get_text sonucu
    base64_image: str   = ""         # screenshot sonucu
    links:        list[dict] = PydanticField(default_factory=list)
    error:        str   = ""
    latency_ms:   float = 0.0

    def to_string(self) -> str:
        """Orchestrator'a döndürülecek string özet."""
        if not self.success:
            return f"HATA [{self.action}]: {self.error}"

        parts = [f"[{self.action.upper()}] Başarılı"]
        if self.url:
            parts.append(f"URL: {self.url}")
        if self.title:
            parts.append(f"Başlık: {self.title}")
        if self.text:
            preview = self.text[:300].replace("\n", " ")
            parts.append(f"İçerik: {preview}{'...' if len(self.text) > 300 else ''}")
        if self.links:
            parts.append(f"Link sayısı: {len(self.links)}")
        if self.base64_image:
            parts.append(f"Ekran görüntüsü alındı ({len(self.base64_image)} byte base64)")
        return " | ".join(parts)


# ---------------------------------------------------------------------------
# BrowserSession — Playwright oturumu
# ---------------------------------------------------------------------------

class BrowserSession:
    """
    Tek bir Playwright browser + sayfa oturumu.
    Oturum boyunca aynı browser instance'ını kullanır (performans).

    JS'deki:
        const browser = await playwright.chromium.launch()
        const page = await browser.newPage()

    Python:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            page = await browser.new_page()
    """

    def __init__(self, headless: bool = True, timeout_ms: int = 15_000):
        self.headless   = headless
        self.timeout_ms = timeout_ms
        self._playwright = None
        self._browser    = None
        self._context    = None
        self._page       = None
        self._started    = False

    async def start(self) -> None:
        """Browser'ı başlatır. İlk action öncesinde otomatik çağrılır."""
        if self._started:
            return
        try:
            from playwright.async_api import async_playwright  # type: ignore
        except ImportError:
            raise RuntimeError(
                "Playwright bulunamadı.\n"
                "Kurulum: pip install playwright && playwright install chromium"
            )

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",  # bot tespitini azalt
                "--disable-infobars",
            ],
        )
        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="tr-TR",
        )
        self._page = await self._context.new_page()
        self._page.set_default_timeout(self.timeout_ms)
        self._started = True
        logger.info(f"[BrowserSession] Başlatıldı (headless={self.headless})")

    async def stop(self) -> None:
        """Browser'ı kapatır ve kaynakları serbest bırakır."""
        if not self._started:
            return
        try:
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception as e:
            logger.warning(f"[BrowserSession] Kapatma hatası: {e}")
        finally:
            self._started    = False
            self._page       = None
            self._context    = None
            self._browser    = None
            self._playwright = None
        logger.info("[BrowserSession] Kapatıldı.")

    @property
    def page(self):
        """Playwright Page nesnesi."""
        if not self._page:
            raise RuntimeError("Browser başlatılmadı. start() çağır.")
        return self._page

    @property
    def is_running(self) -> bool:
        return self._started and self._page is not None

    async def new_tab(self, url: str = "") -> None:
        """Yeni sekme açar ve odağı ona taşır."""
        if not self._context:
            raise RuntimeError("Browser başlatılmadı.")
        self._page = await self._context.new_page()
        self._page.set_default_timeout(self.timeout_ms)
        if url:
            await self._page.goto(url)

    async def current_url(self) -> str:
        return self._page.url if self._page else ""

    async def current_title(self) -> str:
        try:
            return await self._page.title() if self._page else ""
        except Exception:
            return ""


# ---------------------------------------------------------------------------
# Action Handler'ları
# ---------------------------------------------------------------------------

async def _action_navigate(page, req: BrowserRequest) -> BrowserResult:
    """
    URL'ye gider.

    Orchestrator tool_args:
        {"action": "navigate", "url": "https://example.com", "wait_for": "networkidle"}
    """
    if not req.url:
        return BrowserResult(success=False, action="navigate", error="URL belirtilmedi.")

    start = time.time()
    try:
        wait_until = req.wait_for if req.wait_for in (
            "load", "networkidle", "domcontentloaded", "commit"
        ) else "load"

        response = await page.goto(req.url, wait_until=wait_until, timeout=req.timeout_ms)
        title    = await page.title()
        status   = response.status if response else 0

        logger.info(f"[Browser] Navigasyon: {req.url} → {status} '{title}'")
        return BrowserResult(
            success=True,
            action="navigate",
            url=page.url,
            title=title,
            text=f"HTTP {status}",
            latency_ms=round((time.time() - start) * 1000, 1),
        )
    except Exception as e:
        return BrowserResult(success=False, action="navigate", error=str(e))


async def _action_click(page, req: BrowserRequest) -> BrowserResult:
    """
    Element'e tıklar. CSS selector veya görünür metin ile bulur.

    Orchestrator tool_args:
        {"action": "click", "selector": "button#submit"}
        {"action": "click", "text": "Kaydet"}       # görünür metin ile
    """
    start = time.time()
    try:
        if req.selector:
            # CSS selector ile
            await page.wait_for_selector(req.selector, timeout=req.timeout_ms)
            await page.click(req.selector)
            label = req.selector
        elif req.text:
            # Görünür metin ile (OpenGuider'ın text-based click mantığı)
            locator = page.get_by_text(req.text, exact=False)
            await locator.first.click()
            label = f"'{req.text}'"
        else:
            return BrowserResult(success=False, action="click", error="selector veya text belirtilmedi.")

        title = await page.title()
        logger.info(f"[Browser] Tıklandı: {label}")
        return BrowserResult(
            success=True,
            action="click",
            url=page.url,
            title=title,
            text=f"{label} tıklandı.",
            latency_ms=round((time.time() - start) * 1000, 1),
        )
    except Exception as e:
        return BrowserResult(success=False, action="click", error=str(e))


async def _action_type(page, req: BrowserRequest) -> BrowserResult:
    """
    Input alanına metin yazar.

    Orchestrator tool_args:
        {"action": "type", "selector": "input[name='q']", "text": "hava durumu"}
    """
    if not req.text:
        return BrowserResult(success=False, action="type", error="Yazılacak metin belirtilmedi.")

    start = time.time()
    try:
        target = req.selector or "input:visible, textarea:visible"
        await page.wait_for_selector(target, timeout=req.timeout_ms)

        # Önce mevcut içeriği temizle, sonra yaz
        await page.fill(target, "")
        await page.type(target, req.text, delay=30)  # 30ms gecikme — daha doğal

        logger.info(f"[Browser] Yazıldı: '{req.text[:30]}' → {target}")
        return BrowserResult(
            success=True,
            action="type",
            url=page.url,
            text=f"'{req.text[:50]}' yazıldı.",
            latency_ms=round((time.time() - start) * 1000, 1),
        )
    except Exception as e:
        return BrowserResult(success=False, action="type", error=str(e))


async def _action_extract(page, req: BrowserRequest) -> BrowserResult:
    """
    Sayfadan metin veya HTML çıkarır.

    Orchestrator tool_args:
        {"action": "extract", "selector": "article", "extract_mode": "text"}
        {"action": "extract"}  → tüm sayfa metni
    """
    start = time.time()
    try:
        if req.selector:
            await page.wait_for_selector(req.selector, timeout=req.timeout_ms)
            element = page.locator(req.selector).first
            if req.extract_mode == "html":
                content = await element.inner_html()
            else:
                content = await element.inner_text()
        else:
            # Tüm sayfa — script/style hariç
            content = await page.evaluate("""() => {
                const scripts = document.querySelectorAll('script, style, nav, footer, header');
                scripts.forEach(el => el.remove());
                return document.body ? document.body.innerText : document.documentElement.innerText;
            }""")

        # Fazla boşlukları temizle
        content = re.sub(r"\n{3,}", "\n\n", content).strip()
        title   = await page.title()

        logger.info(f"[Browser] Çıkarıldı: {len(content)} karakter — '{title}'")
        return BrowserResult(
            success=True,
            action="extract",
            url=page.url,
            title=title,
            text=content[:5000],  # max 5000 karakter
            latency_ms=round((time.time() - start) * 1000, 1),
        )
    except Exception as e:
        return BrowserResult(success=False, action="extract", error=str(e))


async def _action_screenshot(page, req: BrowserRequest) -> BrowserResult:
    """
    Ekran görüntüsü alır, base64 döner.

    Orchestrator tool_args:
        {"action": "screenshot"}
        {"action": "screenshot", "screenshot_path": "C:/shots/page.png"}
    """
    start = time.time()
    try:
        png_bytes = await page.screenshot(full_page=False, type="png")
        b64       = base64.b64encode(png_bytes).decode("utf-8")
        title     = await page.title()

        # İsteğe bağlı diske kaydet
        if req.screenshot_path:
            os.makedirs(os.path.dirname(req.screenshot_path), exist_ok=True)
            with open(req.screenshot_path, "wb") as f:
                f.write(png_bytes)
            logger.info(f"[Browser] Screenshot kaydedildi: {req.screenshot_path}")

        return BrowserResult(
            success=True,
            action="screenshot",
            url=page.url,
            title=title,
            base64_image=b64,
            text=f"Screenshot alındı ({len(png_bytes)} byte)",
            latency_ms=round((time.time() - start) * 1000, 1),
        )
    except Exception as e:
        return BrowserResult(success=False, action="screenshot", error=str(e))


async def _action_scroll(page, req: BrowserRequest) -> BrowserResult:
    """
    Sayfayı kaydırır.

    Orchestrator tool_args:
        {"action": "scroll", "scroll_dir": "down", "scroll_px": 800}
        {"action": "scroll", "scroll_dir": "bottom"}   → sayfanın sonuna
    """
    start = time.time()
    try:
        if req.scroll_dir == "bottom":
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        elif req.scroll_dir == "top":
            await page.evaluate("window.scrollTo(0, 0)")
        elif req.scroll_dir == "up":
            await page.evaluate(f"window.scrollBy(0, -{req.scroll_px})")
        else:  # down
            await page.evaluate(f"window.scrollBy(0, {req.scroll_px})")

        await page.wait_for_timeout(300)  # kaydırma animasyonu
        return BrowserResult(
            success=True,
            action="scroll",
            url=page.url,
            text=f"{req.scroll_dir} yönünde {req.scroll_px}px kaydırıldı.",
            latency_ms=round((time.time() - start) * 1000, 1),
        )
    except Exception as e:
        return BrowserResult(success=False, action="scroll", error=str(e))


async def _action_wait(page, req: BrowserRequest) -> BrowserResult:
    """
    Element veya ağ durumu bekler.

    Orchestrator tool_args:
        {"action": "wait", "selector": "#result", "timeout_ms": 10000}
        {"action": "wait", "wait_for": "networkidle"}
    """
    start = time.time()
    try:
        if req.selector:
            await page.wait_for_selector(
                req.selector,
                state="visible",
                timeout=req.timeout_ms,
            )
            text = f"'{req.selector}' göründü."
        elif req.wait_for in ("networkidle", "load", "domcontentloaded"):
            await page.wait_for_load_state(req.wait_for, timeout=req.timeout_ms)
            text = f"'{req.wait_for}' durumuna ulaşıldı."
        else:
            ms = min(req.timeout_ms, 5000)
            await page.wait_for_timeout(ms)
            text = f"{ms}ms beklendi."

        return BrowserResult(
            success=True,
            action="wait",
            url=page.url,
            text=text,
            latency_ms=round((time.time() - start) * 1000, 1),
        )
    except Exception as e:
        return BrowserResult(success=False, action="wait", error=str(e))


async def _action_search(page, req: BrowserRequest) -> BrowserResult:
    """
    Google'da arama yapar ve ilk sonuçları döner.
    navigate + type + extract birleşimi.

    Orchestrator tool_args:
        {"action": "search", "query": "Python asyncio tutorial"}
    """
    if not req.query:
        return BrowserResult(success=False, action="search", error="Arama sorgusu belirtilmedi.")

    start = time.time()
    try:
        # Google'a git
        search_url = f"https://www.google.com/search?q={req.query.replace(' ', '+')}&hl=tr"
        await page.goto(search_url, wait_until="domcontentloaded", timeout=req.timeout_ms)

        # Cookie banner varsa kapat
        try:
            accept_btn = page.get_by_role("button", name=re.compile(r"(kabul|accept|agree)", re.I))
            if await accept_btn.count() > 0:
                await accept_btn.first.click()
                await page.wait_for_timeout(500)
        except Exception:
            pass

        # Sonuç snippetlerini çek
        results_text = await page.evaluate("""() => {
            const items = [];
            // Organik sonuç başlıkları + açıklamalar
            document.querySelectorAll('h3').forEach(h => {
                const parent = h.closest('a') || h.parentElement;
                const desc = parent?.nextElementSibling?.innerText || '';
                const href = h.closest('a')?.href || '';
                if (h.innerText && href.startsWith('http')) {
                    items.push(h.innerText + (desc ? ': ' + desc.slice(0, 150) : '') + ' [' + href + ']');
                }
            });
            return items.slice(0, 5).join('\\n\\n');
        }""")

        title = await page.title()
        logger.info(f"[Browser] Arama: '{req.query}' → {len(results_text)} karakter")

        return BrowserResult(
            success=True,
            action="search",
            url=page.url,
            title=title,
            text=results_text or "Sonuç bulunamadı.",
            latency_ms=round((time.time() - start) * 1000, 1),
        )
    except Exception as e:
        return BrowserResult(success=False, action="search", error=str(e))


async def _action_fill_form(page, req: BrowserRequest) -> BrowserResult:
    """
    Form alanlarını doldurur.

    Orchestrator tool_args:
        {
            "action": "fill_form",
            "form_data": {
                "input[name='username']": "kullanici",
                "input[name='password']": "sifre123",
                "textarea#mesaj": "Merhaba dünya"
            }
        }
    """
    if not req.form_data:
        return BrowserResult(success=False, action="fill_form", error="form_data boş.")

    start = time.time()
    filled = []
    errors = []

    for selector, value in req.form_data.items():
        try:
            await page.wait_for_selector(selector, timeout=5000)
            tag = await page.eval_on_selector(selector, "el => el.tagName.toLowerCase()")
            input_type = await page.eval_on_selector(
                selector, "el => el.type || ''"
            )

            if tag == "select":
                await page.select_option(selector, value=str(value))
            elif input_type in ("checkbox", "radio"):
                if str(value).lower() in ("true", "1", "yes", "evet"):
                    await page.check(selector)
                else:
                    await page.uncheck(selector)
            else:
                await page.fill(selector, str(value))

            filled.append(selector)
            logger.debug(f"[Browser] Form dolduruldu: {selector} = '{str(value)[:20]}'")
        except Exception as e:
            errors.append(f"{selector}: {e}")
            logger.warning(f"[Browser] Form alanı hatası: {selector} — {e}")

    summary = f"{len(filled)}/{len(req.form_data)} alan dolduruldu."
    if errors:
        summary += f" Hatalar: {'; '.join(errors[:3])}"

    return BrowserResult(
        success=len(errors) == 0,
        action="fill_form",
        url=page.url,
        text=summary,
        latency_ms=round((time.time() - start) * 1000, 1),
    )


async def _action_get_links(page, req: BrowserRequest) -> BrowserResult:
    """
    Sayfadaki linkleri toplar.

    Orchestrator tool_args:
        {"action": "get_links", "max_links": 15}
    """
    start = time.time()
    try:
        links = await page.evaluate(f"""() => {{
            const seen = new Set();
            const items = [];
            document.querySelectorAll('a[href]').forEach(a => {{
                const href = a.href;
                const text = (a.innerText || a.title || '').trim().slice(0, 80);
                if (href && href.startsWith('http') && !seen.has(href)) {{
                    seen.add(href);
                    items.push({{url: href, text: text}});
                }}
                if (items.length >= {req.max_links}) return;
            }});
            return items.slice(0, {req.max_links});
        }}""")

        title = await page.title()
        text_summary = "\n".join(
            f"• {lnk['text'] or '(isimsiz)'}: {lnk['url']}" for lnk in links
        )

        return BrowserResult(
            success=True,
            action="get_links",
            url=page.url,
            title=title,
            text=text_summary,
            links=links,
            latency_ms=round((time.time() - start) * 1000, 1),
        )
    except Exception as e:
        return BrowserResult(success=False, action="get_links", error=str(e))


async def _action_run_js(page, req: BrowserRequest) -> BrowserResult:
    """
    Sayfada JavaScript çalıştırır.

    Orchestrator tool_args:
        {"action": "run_js", "js_code": "document.title"}
    """
    if not req.js_code:
        return BrowserResult(success=False, action="run_js", error="js_code belirtilmedi.")

    start = time.time()
    try:
        result = await page.evaluate(req.js_code)
        result_str = str(result) if result is not None else "null"
        logger.info(f"[Browser] JS çalıştırıldı: {req.js_code[:50]}")
        return BrowserResult(
            success=True,
            action="run_js",
            url=page.url,
            text=result_str[:1000],
            latency_ms=round((time.time() - start) * 1000, 1),
        )
    except Exception as e:
        return BrowserResult(success=False, action="run_js", error=str(e))


async def _action_get_text(page, req: BrowserRequest) -> BrowserResult:
    """Sayfanın görünür metnini döner (extract'ın kısaltılmış hali)."""
    return await _action_extract(page, req)


async def _action_navigate_history(page, req: BrowserRequest, direction: str) -> BrowserResult:
    """Geri/ileri/yenile navigasyon."""
    start = time.time()
    try:
        if direction == "back":
            await page.go_back(timeout=req.timeout_ms)
        elif direction == "forward":
            await page.go_forward(timeout=req.timeout_ms)
        else:
            await page.reload(timeout=req.timeout_ms)

        title = await page.title()
        return BrowserResult(
            success=True,
            action=direction,
            url=page.url,
            title=title,
            text=f"{direction} navigasyonu başarılı.",
            latency_ms=round((time.time() - start) * 1000, 1),
        )
    except Exception as e:
        return BrowserResult(success=False, action=direction, error=str(e))


# ---------------------------------------------------------------------------
# BrowserAgent — Ana Plugin Sınıfı
# ---------------------------------------------------------------------------

class BrowserAgent:
    """
    SIRIUS'un browser otomasyon plugin'i.

    Orchestrator entegrasyonu (make_sirius_tool_executor dispatch tablosuna ekle):

        "browser_action": lambda a: self.browser_agent.execute(a),

    Veya doğrudan:

        result = await browser_agent.execute({
            "action": "search",
            "query": "Python Playwright tutorial"
        })

    Oturum yönetimi:
        - İlk çağrıda otomatik başlatılır
        - keep_alive=True: oturumu çağrılar arasında açık tutar (daha hızlı)
        - keep_alive=False: her çağrıda yeni browser açıp kapar (daha güvenli)
    """

    # Action → handler fonksiyonu eşlemesi
    _HANDLERS = {
        BrowserAction.NAVIGATE:   _action_navigate,
        BrowserAction.CLICK:      _action_click,
        BrowserAction.TYPE:       _action_type,
        BrowserAction.EXTRACT:    _action_extract,
        BrowserAction.SCREENSHOT: _action_screenshot,
        BrowserAction.SCROLL:     _action_scroll,
        BrowserAction.WAIT:       _action_wait,
        BrowserAction.SEARCH:     _action_search,
        BrowserAction.FILL_FORM:  _action_fill_form,
        BrowserAction.GET_LINKS:  _action_get_links,
        BrowserAction.RUN_JS:     _action_run_js,
        BrowserAction.GET_TEXT:   _action_get_text,
    }

    def __init__(
        self,
        headless:   bool  = True,
        keep_alive: bool  = True,
        timeout_ms: int   = 15_000,
    ):
        """
        headless:   True = görünmez tarayıcı (production), False = görünür (debug)
        keep_alive: True = oturumu aç tut, False = her çağrıda yenile
        timeout_ms: Varsayılan element bekleme süresi (ms)
        """
        self.headless   = headless
        self.keep_alive = keep_alive
        self.timeout_ms = timeout_ms
        self._session: Optional[BrowserSession] = None

    # ------------------------------------------------------------------
    # Public API — Orchestrator bu metodu çağırır
    # ------------------------------------------------------------------

    async def execute(self, args: dict) -> str:
        """
        Orchestrator'ın çağırdığı tek giriş noktası.

        args örnek:
            {"action": "search", "query": "İstanbul hava durumu"}
            {"action": "navigate", "url": "https://hepsiburada.com"}
            {"action": "click", "text": "Sepete Ekle"}

        Döner: str — orchestrator'ın işleyebileceği insan okunabilir sonuç.
        """
        try:
            req = BrowserRequest(**args)
        except Exception as e:
            return f"HATA [browser_action]: Geçersiz argümanlar — {e}"

        # Tarayıcıyı başlat (gerekirse)
        session = await self._get_session(req.headless)

        # close action özel işlem
        if req.action == BrowserAction.CLOSE or req.action == "close":
            await self._close_session()
            return "[CLOSE] Tarayıcı kapatıldı."

        # new_tab action
        if req.action == BrowserAction.NEW_TAB or req.action == "new_tab":
            await session.new_tab(req.new_tab_url)
            return f"[NEW_TAB] Yeni sekme açıldı: {req.new_tab_url or '(boş)'}"

        # back/forward/refresh
        if req.action in ("back", "forward", "refresh"):
            result = await _action_navigate_history(session.page, req, req.action)
            return result.to_string()

        # Normal handler dispatch
        try:
            action_enum = BrowserAction(req.action)
        except ValueError:
            return f"HATA [browser_action]: Bilinmeyen action '{req.action}'. Geçerliler: {[a.value for a in BrowserAction]}"

        handler = self._HANDLERS.get(action_enum)
        if handler is None:
            return f"HATA [browser_action]: '{req.action}' için handler bulunamadı."

        try:
            result = await handler(session.page, req)
        except Exception as e:
            logger.error(f"[BrowserAgent] Handler hatası ({req.action}): {e}")
            result = BrowserResult(success=False, action=req.action, error=str(e))

        # keep_alive=False ise her çağrıdan sonra kapat
        if not self.keep_alive:
            await self._close_session()

        logger.debug(
            f"[BrowserAgent] {req.action}: "
            f"{'✅' if result.success else '❌'} — {result.to_string()[:80]}"
        )
        return result.to_string()

    async def execute_sequence(self, steps: list[dict]) -> list[str]:
        """
        Birden fazla action'ı sırayla çalıştırır.
        Bir action başarısız olursa sonrakini yine dener.

        Orchestrator TaskStep listesini doğrudan bu metoda verebilir.
        """
        results = []
        for step in steps:
            result = await self.execute(step)
            results.append(result)
            # Kritik hata varsa dur
            if "HATA" in result and step.get("stop_on_error", False):
                logger.warning(f"[BrowserAgent] stop_on_error tetiklendi: {result}")
                break
        return results

    async def get_current_context(self) -> dict:
        """
        Mevcut sayfanın bağlamını döner.
        Orchestrator planner'a ekran bağlamı olarak verebilir.
        """
        if not self._session or not self._session.is_running:
            return {"url": "", "title": "", "text": ""}

        try:
            page   = self._session.page
            url    = await self._session.current_url()
            title  = await self._session.current_title()
            text   = await page.evaluate("""() => {
                return document.body ? document.body.innerText.slice(0, 1000) : '';
            }""")
            return {"url": url, "title": title, "text": text.strip()}
        except Exception as e:
            return {"url": "", "title": "", "error": str(e)}

    # ------------------------------------------------------------------
    # Oturum Yönetimi (İç)
    # ------------------------------------------------------------------

    async def _get_session(self, headless: bool = True) -> BrowserSession:
        """Var olan oturumu döner veya yeni oluşturur."""
        if self._session and self._session.is_running:
            return self._session

        self._session = BrowserSession(
            headless=headless if headless is not None else self.headless,
            timeout_ms=self.timeout_ms,
        )
        await self._session.start()
        return self._session

    async def _close_session(self) -> None:
        """Mevcut oturumu kapatır."""
        if self._session:
            await self._session.stop()
            self._session = None

    async def __aenter__(self) -> "BrowserAgent":
        """async with BrowserAgent() as agent: desteği."""
        return self

    async def __aexit__(self, *_) -> None:
        await self._close_session()


# ---------------------------------------------------------------------------
# Yüksek Seviye Yardımcı Fonksiyonlar
# ---------------------------------------------------------------------------

async def quick_search(query: str, headless: bool = True) -> str:
    """
    Tek satırda Google araması yapar.

    Orchestrator'dan bağımsız hızlı kullanım:
        result = await quick_search("Python asyncio")
    """
    async with BrowserAgent(headless=headless, keep_alive=False) as agent:
        return await agent.execute({"action": "search", "query": query})


async def quick_navigate_and_extract(url: str, headless: bool = True) -> str:
    """URL'ye git ve metni çıkar."""
    async with BrowserAgent(headless=headless, keep_alive=False) as agent:
        nav = await agent.execute({"action": "navigate", "url": url})
        if "HATA" in nav:
            return nav
        return await agent.execute({"action": "extract"})


# ---------------------------------------------------------------------------
# Bağımsız Test
# ---------------------------------------------------------------------------

async def _demo() -> None:
    print("\n🌐  BrowserAgent Demo Başlatılıyor...")
    print("─" * 50)

    try:
        from playwright.async_api import async_playwright  # type: ignore
        print("✅ Playwright kurulu")
    except ImportError:
        print("❌ Playwright bulunamadı: pip install playwright && playwright install chromium")
        return

    async with BrowserAgent(headless=True, keep_alive=True) as agent:

        # 1. Google'da ara
        print("\n1. Google araması: 'Python asyncio'")
        result = await agent.execute({"action": "search", "query": "Python asyncio tutorial"})
        print(f"   {result[:150]}")

        # 2. Wikipedia'ya git
        print("\n2. Wikipedia navigasyon...")
        result = await agent.execute({
            "action": "navigate",
            "url": "https://tr.wikipedia.org/wiki/Python",
            "wait_for": "load",
        })
        print(f"   {result[:100]}")

        # 3. Metin çıkar
        print("\n3. Sayfa metni çıkarılıyor...")
        result = await agent.execute({
            "action": "extract",
            "selector": "p",
            "extract_mode": "text",
        })
        print(f"   {result[:150]}")

        # 4. Screenshot
        print("\n4. Screenshot alınıyor...")
        result = await agent.execute({"action": "screenshot"})
        print(f"   {result[:100]}")

        # 5. Link listesi
        print("\n5. Linkler toplanıyor...")
        result = await agent.execute({"action": "get_links", "max_links": 5})
        print(f"   {result[:200]}")

        # 6. Scroll
        print("\n6. Sayfa aşağı kaydırılıyor...")
        result = await agent.execute({"action": "scroll", "scroll_dir": "down", "scroll_px": 500})
        print(f"   {result}")

        # 7. Bağlam
        ctx = await agent.get_current_context()
        print(f"\n7. Bağlam: URL={ctx['url'][:50]} | Başlık={ctx['title'][:40]}")

    print("\n✅ BrowserAgent demo tamamlandı.")


if __name__ == "__main__":
    asyncio.run(_demo())
