"""
plugins/plugin_dispatcher.py
==============================
Tüm SIRIUS plugin'lerini Orchestrator'a bağlayan merkezi dispatcher.

Bu dosya make_sirius_tool_executor() fonksiyonunu GENIŞLETIR —
mevcut plugin'lere browser_action, browser_search, browser_extract
tool'larını ekler ve tek noktadan yönetimi sağlar.

Orchestrator entegrasyonu:
    Orchestrator'ın TaskStep.tool alanına yazılabilecek yeni tool adları:
        browser_action     → BrowserAgent.execute(args)
        browser_search     → kısa yol: sadece arama
        browser_navigate   → kısa yol: sadece navigasyon
        browser_extract    → kısa yol: sayfa metni çıkar
        browser_click      → kısa yol: tıkla
        browser_screenshot → kısa yol: ekran görüntüsü

    Tüm mevcut SIRIUS tool'ları korunur:
        open_app, web_search, weather_report, send_message,
        youtube_video, computer_settings, screen_process,
        flight_finder, save_memory, find_on_screen, switch_ai_provider

Dispatcher Mimarisi:
    ┌─────────────────────────────────────────────────────────────┐
    │                    TaskOrchestrator                         │
    │  step.tool = "browser_search"                               │
    │  step.tool_args = {"query": "İstanbul hava durumu"}        │
    │                         │                                  │
    │                         ▼                                  │
    │              PluginDispatcher.dispatch(                     │
    │                  tool_name, tool_args                       │
    │              )                                              │
    │                         │                                  │
    │              ┌──────────┴──────────┐                       │
    │              │                     │                        │
    │      BrowserAgent             SIRIUS Controller            │
    │      .execute(args)           .plugin.execute(args)        │
    └─────────────────────────────────────────────────────────────┘

main.py SiriusController entegrasyonu:
    # __init__ içinde:
    from plugins.plugin_dispatcher import PluginDispatcher
    self.dispatcher = PluginDispatcher(controller=self)

    # orchestrator'a bağla:
    self.orchestrator = TaskOrchestrator(
        gemini_key=gk,
        tool_executor=self.dispatcher.as_executor(),
        ...
    )
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Callable, Coroutine, Optional

from plugins.browser_agent import BrowserAgent, quick_search

logger = logging.getLogger("sirius.plugins.dispatcher")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(
        logging.Formatter("[%(asctime)s] [%(name)s] %(levelname)s — %(message)s", "%H:%M:%S")
    )
    logger.addHandler(_h)
logger.setLevel(logging.DEBUG)

# Tip alias
ToolExecutor = Callable[[str, dict], Coroutine[Any, Any, str]]


# ---------------------------------------------------------------------------
# PluginDispatcher
# ---------------------------------------------------------------------------

class PluginDispatcher:
    """
    SIRIUS'un tüm plugin'leri için tek dispatch noktası.

    Her tool_name → async handler fonksiyonu eşlemesi burada tutulur.
    Yeni plugin eklemek için sadece _build_dispatch_table()'a yeni giriş ekle.

    Kullanım:
        dispatcher = PluginDispatcher(controller=sirius_controller)
        executor   = dispatcher.as_executor()   # orchestrator'a ver

        # Doğrudan çağrı:
        result = await dispatcher.dispatch("browser_search", {"query": "hava"})
    """

    def __init__(
        self,
        controller=None,   # SiriusController instance'ı
        browser_headless: bool = True,
        browser_keep_alive: bool = True,
    ):
        self._controller = controller
        self._browser    = BrowserAgent(
            headless=browser_headless,
            keep_alive=browser_keep_alive,
        )
        self._table: dict[str, Callable] = self._build_dispatch_table()

    # ------------------------------------------------------------------
    # Dispatch Tablosu
    # ------------------------------------------------------------------

    def _build_dispatch_table(self) -> dict[str, Callable]:
        """
        tool_name → async handler eşlemesi.
        Tüm tool'ların tek listesi — ekleme/çıkarma buradan yapılır.
        """
        # ----- Browser Tool'ları (YENİ) -----
        browser_tools = {
            # Tam browser_action — tüm action'lar desteklenir
            "browser_action": self._browser_action,

            # Kısa yollar — orchestrator planlarında daha okunabilir
            "browser_search":     self._browser_search,
            "browser_navigate":   self._browser_navigate,
            "browser_extract":    self._browser_extract,
            "browser_click":      self._browser_click,
            "browser_screenshot": self._browser_screenshot,
            "browser_scroll":     self._browser_scroll,
            "browser_type":       self._browser_type,
            "browser_fill_form":  self._browser_fill_form,
            "browser_get_links":  self._browser_get_links,
            "browser_close":      self._browser_close,
        }

        # ----- Mevcut SIRIUS Tool'ları -----
        sirius_tools = {
            "open_app":           self._open_app,
            "web_search":         self._web_search,
            "weather_report":     self._weather_report,
            "send_message":       self._send_message,
            "youtube_video":      self._youtube_video,
            "computer_settings":  self._computer_settings,
            "screen_process":     self._screen_process,
            "flight_finder":      self._flight_finder,
            "save_memory":        self._save_memory,
            "find_on_screen":     self._find_on_screen,
            "switch_ai_provider": self._switch_ai_provider,
        }

        return {**browser_tools, **sirius_tools}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def dispatch(self, tool_name: str, tool_args: dict) -> str:
        """
        Tool adı ve argümanlarıyla ilgili handler'ı çağırır.

        Orchestrator'ın tool_executor callback'i bu metodu çağırır.
        asyncio.wait_for ile timeout uygulanır (orchestrator zaten de yapar
        ama double safety olarak burada da var).
        """
        name = tool_name.lower().strip()
        handler = self._table.get(name)

        if handler is None:
            # Bilinmeyen tool — yardımcı hata mesajı
            close = self._closest_tool(name)
            suggestion = f" '{close}' demek istediniz mi?" if close else ""
            return (
                f"HATA: Bilinmeyen tool '{name}'.{suggestion} "
                f"Mevcut tool'lar: {', '.join(sorted(self._table.keys()))}"
            )

        logger.info(f"[Dispatcher] → {name}({json.dumps(tool_args, ensure_ascii=False)[:60]})")

        try:
            result = await asyncio.wait_for(
                handler(tool_args),
                timeout=60.0,
            )
            result_str = str(result) if result is not None else "Tamamlandı."
            logger.debug(f"[Dispatcher] ← {name}: {result_str[:80]}")
            return result_str
        except asyncio.TimeoutError:
            return f"HATA [{name}]: 60 saniyelik zaman aşımı."
        except asyncio.CancelledError:
            raise  # Orchestrator iptalini yukarı ilet
        except Exception as e:
            logger.error(f"[Dispatcher] {name} hatası: {e}", exc_info=True)
            return f"HATA [{name}]: {e}"

    def as_executor(self) -> ToolExecutor:
        """
        Orchestrator'ın beklediği ToolExecutor callable'ını döner.

        make_sirius_tool_executor() yerine bu kullanılır:
            self.orchestrator = TaskOrchestrator(
                tool_executor=self.dispatcher.as_executor(),
                ...
            )
        """
        async def _executor(tool_name: str, tool_args: dict) -> str:
            return await self.dispatch(tool_name, tool_args)
        return _executor

    def register(self, tool_name: str, handler: Callable) -> None:
        """
        Çalışma zamanında yeni tool kaydeder.
        Dinamik plugin ekleme (örn: user-defined tools) için kullanılır.

        Örnek:
            dispatcher.register("my_custom_tool", my_async_handler)
        """
        self._table[tool_name.lower()] = handler
        logger.info(f"[Dispatcher] Yeni tool kaydedildi: '{tool_name}'")

    def list_tools(self) -> list[str]:
        """Kayıtlı tüm tool adlarını alfabetik sıralar."""
        return sorted(self._table.keys())

    # ------------------------------------------------------------------
    # Browser Handler'ları
    # ------------------------------------------------------------------

    async def _browser_action(self, args: dict) -> str:
        """
        Tam browser_action — tüm BrowserAgent action'larına erişim.

        Orchestrator TaskStep.tool_args örneği:
            {
                "action": "navigate",
                "url": "https://hepsiburada.com",
                "wait_for": "networkidle"
            }
        """
        return await self._browser.execute(args)

    async def _browser_search(self, args: dict) -> str:
        """
        Google araması — kısa yol.

        Orchestrator tool_args:
            {"query": "Python öğrenme kaynakları"}
        """
        query = args.get("query", args.get("text", ""))
        if not query:
            return "HATA [browser_search]: 'query' parametresi belirtilmedi."
        return await self._browser.execute({"action": "search", "query": query})

    async def _browser_navigate(self, args: dict) -> str:
        """
        URL'ye git.

        Orchestrator tool_args:
            {"url": "https://example.com", "wait_for": "load"}
        """
        url = args.get("url", "")
        if not url:
            return "HATA [browser_navigate]: 'url' parametresi belirtilmedi."
        return await self._browser.execute({
            "action": "navigate",
            "url": url,
            "wait_for": args.get("wait_for", "load"),
        })

    async def _browser_extract(self, args: dict) -> str:
        """
        Sayfa metni çıkar.

        Orchestrator tool_args:
            {"selector": "article"}   # opsiyonel
            {}                         # tüm sayfa
        """
        return await self._browser.execute({
            "action": "extract",
            "selector": args.get("selector", ""),
            "extract_mode": args.get("extract_mode", "text"),
        })

    async def _browser_click(self, args: dict) -> str:
        """
        Element tıkla.

        Orchestrator tool_args:
            {"selector": "button.submit"}
            {"text": "Kaydet"}
        """
        return await self._browser.execute({
            "action": "click",
            "selector": args.get("selector", ""),
            "text": args.get("text", ""),
        })

    async def _browser_screenshot(self, args: dict) -> str:
        """Ekran görüntüsü al."""
        return await self._browser.execute({
            "action": "screenshot",
            "screenshot_path": args.get("path", args.get("screenshot_path", "")),
        })

    async def _browser_scroll(self, args: dict) -> str:
        """Sayfa kaydır."""
        return await self._browser.execute({
            "action": "scroll",
            "scroll_dir": args.get("direction", args.get("scroll_dir", "down")),
            "scroll_px": int(args.get("pixels", args.get("scroll_px", 500))),
        })

    async def _browser_type(self, args: dict) -> str:
        """Input'a metin yaz."""
        return await self._browser.execute({
            "action": "type",
            "selector": args.get("selector", ""),
            "text": args.get("text", ""),
        })

    async def _browser_fill_form(self, args: dict) -> str:
        """Form doldur."""
        return await self._browser.execute({
            "action": "fill_form",
            "form_data": args.get("form_data", args),
        })

    async def _browser_get_links(self, args: dict) -> str:
        """Sayfa linklerini topla."""
        return await self._browser.execute({
            "action": "get_links",
            "max_links": int(args.get("max_links", 20)),
        })

    async def _browser_close(self, args: dict) -> str:
        """Tarayıcıyı kapat."""
        return await self._browser.execute({"action": "close"})

    # ------------------------------------------------------------------
    # Mevcut SIRIUS Plugin Handler'ları
    # ------------------------------------------------------------------

    async def _open_app(self, args: dict) -> str:
        """
        Uygulama açar.
        SiriusController.launcher.execute() çağrısını sarar.
        """
        if self._controller:
            return await asyncio.to_thread(
                self._controller.launcher.execute, args
            )
        return "HATA [open_app]: Controller bağlı değil."

    async def _web_search(self, args: dict) -> str:
        """
        Web araması yapar.
        SiriusController.web.execute() çağrısını sarar.
        """
        if self._controller:
            return await asyncio.to_thread(
                self._controller.web.execute, args
            )
        # Controller yoksa browser ile yedek
        query = args.get("query", args.get("text", ""))
        return await self._browser_search({"query": query})

    async def _weather_report(self, args: dict) -> str:
        if self._controller:
            return await asyncio.to_thread(
                self._controller.weather.execute, args
            )
        return "HATA [weather_report]: Controller bağlı değil."

    async def _send_message(self, args: dict) -> str:
        if self._controller:
            return await asyncio.to_thread(
                self._controller.messenger.execute, args
            )
        return "HATA [send_message]: Controller bağlı değil."

    async def _youtube_video(self, args: dict) -> str:
        if self._controller:
            return await asyncio.to_thread(
                self._controller.yt.execute, args
            )
        # Yedek: browser ile YouTube araması
        query = args.get("query", args.get("text", ""))
        return await self._browser.execute({
            "action": "navigate",
            "url": f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}",
        })

    async def _computer_settings(self, args: dict) -> str:
        if self._controller:
            return await asyncio.to_thread(
                self._controller.sys_set.execute,
                args.get("action"),
                args.get("value"),
            )
        return "HATA [computer_settings]: Controller bağlı değil."

    async def _screen_process(self, args: dict) -> str:
        """
        Ekran işleme — CoordEngine entegrasyonu.
        Faz 2'deki coord_engine.py ile çalışır.
        """
        if not self._controller:
            return "HATA [screen_process]: Controller bağlı değil."

        target = args.get("target", "")
        action = args.get("action", "analyze")

        if target and action == "click":
            try:
                hint = await self._controller.coord_engine.find_and_click(
                    target=target,
                    show_overlay=True,
                )
                if hint:
                    key = self._controller.hint_manager.store(
                        target=target,
                        coord_hint=hint,
                        app_process=self._controller.region_manager
                            .get_ui_context().active_window_process,
                    )
                    self._controller.hint_manager.record_success(key)
                    return f"'{hint.label}' tıklandı: ({hint.x}, {hint.y})"
                return f"'{target}' ekranda bulunamadı."
            except Exception as e:
                return f"HATA [screen_process]: {e}"

        elif target and action == "find":
            try:
                region = self._controller.region_manager.get_safe_search_region()
                hint   = await self._controller.coord_engine.find(target, region=region)
                return (
                    f"'{hint.label}' koordinatı: ({hint.x}, {hint.y})"
                    if hint else f"'{target}' bulunamadı."
                )
            except Exception as e:
                return f"HATA [screen_process/find]: {e}"

        else:
            return await asyncio.to_thread(
                self._controller.vision.execute, args
            )

    async def _flight_finder(self, args: dict) -> str:
        if self._controller:
            return await asyncio.to_thread(
                self._controller.travel.execute, args
            )
        return "HATA [flight_finder]: Controller bağlı değil."

    async def _save_memory(self, args: dict) -> str:
        try:
            from memory.memory_manager import update_memory  # type: ignore
            category = args.get("category", "notes")
            key      = args.get("key", "item")
            value    = args.get("value", "")
            update_memory({category: {key: {"value": value}}})
            return f"Hafızaya kaydedildi: [{category}][{key}] = '{str(value)[:50]}'"
        except Exception as e:
            return f"HATA [save_memory]: {e}"

    async def _find_on_screen(self, args: dict) -> str:
        """
        Ekranda UI öğesi bul ve tıkla.
        Faz 2 CoordEngine ile entegre.
        """
        target = args.get("target", "")
        action = args.get("action", "click")
        return await self._screen_process({"target": target, "action": action})

    async def _switch_ai_provider(self, args: dict) -> str:
        """AI sağlayıcısını değiştir."""
        if not self._controller:
            return "HATA [switch_ai_provider]: Controller bağlı değil."

        provider_name = args.get("provider", "").lower().strip()
        try:
            from ai_core.providers import ProviderType  # type: ignore
            provider_map = {
                "gemini": ProviderType.GEMINI,
                "groq":   ProviderType.GROQ,
                "openai": ProviderType.OPENAI,
                "ollama": ProviderType.OLLAMA,
            }
            pt = provider_map.get(provider_name)
            if pt:
                self._controller.provider_registry.set_active(pt)
                return f"AI sağlayıcı değiştirildi: {provider_name.upper()}"
            return f"Bilinmeyen sağlayıcı: '{provider_name}'"
        except Exception as e:
            return f"HATA [switch_ai_provider]: {e}"

    # ------------------------------------------------------------------
    # Yardımcı
    # ------------------------------------------------------------------

    def _closest_tool(self, name: str, threshold: int = 3) -> Optional[str]:
        """
        Bilinmeyen tool adına en yakın eşleşmeyi bulur (Levenshtein benzeri).
        Kullanıcıya daha iyi hata mesajı için.
        """
        def _dist(a: str, b: str) -> int:
            if len(a) > len(b):
                a, b = b, a
            dists = range(len(a) + 1)
            for c2 in b:
                new_dists = [len(a)]
                for i, c1 in enumerate(a):
                    new_dists.append(
                        dists[i] if c1 == c2
                        else 1 + min(dists[i], dists[i + 1], new_dists[-1])
                    )
                dists = new_dists
            return dists[-1]

        best, best_d = None, threshold + 1
        for tool in self._table:
            d = _dist(name, tool)
            if d < best_d:
                best, best_d = tool, d
        return best if best_d <= threshold else None


# ---------------------------------------------------------------------------
# SIRIUS Tool Declarations Eki (main.py TOOL_DECLARATIONS'a eklenecek)
# ---------------------------------------------------------------------------

BROWSER_TOOL_DECLARATIONS = [
    {
        "name": "browser_action",
        "description": (
            "Playwright tabanlı tarayıcı otomasyonu. "
            "Bir web sayfasında gezinme, tıklama, form doldurma, metin çıkarma "
            "ve ekran görüntüsü alma işlemlerini gerçekleştirir."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": (
                        "Yapılacak işlem: navigate, click, type, extract, screenshot, "
                        "scroll, wait, search, fill_form, get_links, run_js, close"
                    ),
                },
                "url":      {"type": "STRING", "description": "Gidilecek URL (navigate için)"},
                "selector": {"type": "STRING", "description": "CSS seçici"},
                "text":     {"type": "STRING", "description": "Tıklanacak veya yazılacak metin"},
                "query":    {"type": "STRING", "description": "Arama sorgusu (search için)"},
                "wait_for": {"type": "STRING", "description": "load | networkidle | domcontentloaded"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "browser_search",
        "description": "Web'de arama yapar ve ilk sonuçları getirir.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "Arama sorgusu"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "browser_navigate",
        "description": "Belirtilen URL'ye gider.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "url": {"type": "STRING", "description": "Gidilecek URL"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "browser_extract",
        "description": "Açık sayfanın metnini veya belirli bir bölümün içeriğini çıkarır.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "selector": {"type": "STRING", "description": "CSS seçici (opsiyonel, boşsa tüm sayfa)"},
            },
        },
    },
    {
        "name": "browser_click",
        "description": "Sayfada görünür metne veya CSS seçicisine göre tıklar.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "text":     {"type": "STRING", "description": "Tıklanacak görünür metin"},
                "selector": {"type": "STRING", "description": "CSS seçici"},
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Bağımsız Test
# ---------------------------------------------------------------------------

async def _demo() -> None:
    print("\n🔌  PluginDispatcher Demo Başlatılıyor...")
    print("─" * 50)

    dispatcher = PluginDispatcher(controller=None)

    print(f"\n1. Kayıtlı tool'lar ({len(dispatcher.list_tools())} adet):")
    for t in dispatcher.list_tools():
        print(f"   • {t}")

    print("\n2. Browser search testi...")
    try:
        from playwright.async_api import async_playwright  # type: ignore
        result = await dispatcher.dispatch(
            "browser_search", {"query": "Python Playwright kullanımı"}
        )
        print(f"   {result[:200]}")
    except ImportError:
        print("   ⚠️  Playwright kurulu değil — browser testleri atlandı.")
        print("   Kurulum: pip install playwright && playwright install chromium")

    print("\n3. Bilinmeyen tool hata mesajı testi...")
    result = await dispatcher.dispatch("brwser_serch", {})
    print(f"   {result[:120]}")

    print("\n4. save_memory testi...")
    result = await dispatcher.dispatch("save_memory", {
        "category": "test",
        "key": "demo_key",
        "value": "dispatcher çalışıyor",
    })
    print(f"   {result}")

    # Browser'ı kapat
    await dispatcher._browser_close({})

    print("\n✅ PluginDispatcher demo tamamlandı.")


if __name__ == "__main__":
    asyncio.run(_demo())
