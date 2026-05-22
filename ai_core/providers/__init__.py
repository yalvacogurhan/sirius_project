"""
ai_core/providers/__init__.py
==============================
ProviderRegistry — tüm sağlayıcıları yönetir ve SIRIUS'a tek giriş noktası sunar.

Bu dosya:
    1. Tüm sağlayıcıları içe aktarır
    2. SIRIUS'un config_data'sından anahtarları okuyarak başlatır
    3. En iyi mevcut sağlayıcıyı otomatik seçer
    4. Orchestrator ve diğer modüller için tek .complete() ve .complete_json() noktası sağlar

SIRIUS entegrasyon noktası (main.py SiriusController.__init__):

    from ai_core.providers import ProviderRegistry

    self.provider_registry = ProviderRegistry(self.config_data)

    # apply_keys() içinde:
    self.provider_registry.update_keys(self.config_data)

    # orchestrator.py artık doğrudan Gemini çağırmak yerine:
    registry.complete_json(prompt)  →  aktif provider ne ise onu kullanır
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from ai_core.providers.base_provider import (
    BaseProvider, ProviderType, ProviderRequest, ProviderResponse,
    ChatMessage, ImageAttachment, MessageRole,
)
from ai_core.providers.gemini_provider  import GeminiProvider
from ai_core.providers.openai_provider  import OpenAIProvider, GroqProvider
from ai_core.providers.ollama_provider  import OllamaProvider

logger = logging.getLogger("sirius.providers")

# Public API — dışarıdan import edilecekler
__all__ = [
    "ProviderRegistry",
    "BaseProvider",
    "ProviderType",
    "ProviderRequest",
    "ProviderResponse",
    "ChatMessage",
    "MessageRole",
    "ImageAttachment",
    "GeminiProvider",
    "OpenAIProvider",
    "GroqProvider",
    "OllamaProvider",
]


class ProviderRegistry:
    """
    SIRIUS'un AI sağlayıcı yöneticisi.

    OpenGuider'ın src/ai/ klasöründeki provider switcher mantığının Python portu.
    SIRIUS'u tek bir sağlayıcıya (Gemini) bağımlılıktan kurtarır.

    Öncelik sırası (otomatik seçimde):
        1. Gemini  — SIRIUS'un mevcut sağlayıcısı, en öncelikli
        2. Groq    — SIRIUS'ta zaten groq_key var
        3. OpenAI  — openai_key varsa
        4. Ollama  — yerel, anahtar gerektirmez, internet yok

    Kullanım:
        registry = ProviderRegistry(config_data)

        # Basit metin çağrısı:
        resp = await registry.complete("Merhaba!")

        # JSON çağrısı:
        resp = await registry.complete_json(prompt)

        # Belirli sağlayıcıyı zorla:
        resp = await registry.complete(prompt, force=ProviderType.GROQ)

        # Vision:
        resp = await registry.complete_vision(prompt, base64_png)
    """

    def __init__(self, config_data: dict):
        """
        config_data: SIRIUS'un memory/config_manager.get_config() sonucu.
        Beklenen anahtarlar: gemini_key, groq_key, openai_key (opsiyonel)
        """
        self._providers: dict[ProviderType, BaseProvider] = {}
        self._active: Optional[ProviderType] = None
        self._build(config_data)

    # ------------------------------------------------------------------
    # Kurulum
    # ------------------------------------------------------------------

    def _build(self, config: dict) -> None:
        """Config'den anahtarları okuyarak sağlayıcıları başlatır."""
        gemini_key = config.get("gemini_key", "")
        groq_key   = config.get("groq_key", "")
        openai_key = config.get("openai_key", "")

        # Gemini — her zaman kayıt et, anahtar sonradan set edilebilir
        self._providers[ProviderType.GEMINI] = GeminiProvider(api_key=gemini_key)

        # Groq — SIRIUS'ta zaten var
        self._providers[ProviderType.GROQ] = GroqProvider(api_key=groq_key)

        # OpenAI — opsiyonel
        self._providers[ProviderType.OPENAI] = OpenAIProvider(api_key=openai_key)

        # Ollama — her zaman ekle (yerel, anahtar gerektirmez)
        self._providers[ProviderType.OLLAMA] = OllamaProvider()

        # Aktif sağlayıcıyı belirle
        forced = config.get("active_provider", "")
        if forced:
            try:
                self._active = ProviderType(forced)
                logger.info(f"[Registry] Zorunlu aktif sağlayıcı: {self._active}")
                return
            except ValueError:
                logger.warning(f"[Registry] Bilinmeyen active_provider: {forced}")

        # Otomatik seçim
        self._active = self._pick_best(gemini_key, groq_key, openai_key)
        logger.info(f"[Registry] Aktif sağlayıcı: {self._active}")

    def _pick_best(self, gemini_key: str, groq_key: str, openai_key: str) -> ProviderType:
        """Mevcut anahtarlara göre en iyi sağlayıcıyı seçer."""
        if gemini_key:
            return ProviderType.GEMINI
        if groq_key:
            logger.info("[Registry] Gemini anahtarı yok, Groq kullanılıyor.")
            return ProviderType.GROQ
        if openai_key:
            logger.info("[Registry] Groq anahtarı yok, OpenAI kullanılıyor.")
            return ProviderType.OPENAI
        logger.warning("[Registry] Hiç bulut anahtarı yok! Ollama (yerel) deneniyor.")
        return ProviderType.OLLAMA

    def update_keys(self, config: dict) -> None:
        """
        SIRIUS'un apply_keys() çağrısında anahtarları günceller.
        Mevcut sağlayıcı nesnelerini yeniden oluşturmadan günceller.

        main.py SiriusController.apply_keys() içine ekle:
            self.provider_registry.update_keys(self.config_data)
        """
        gemini_key = config.get("gemini_key", "")
        groq_key   = config.get("groq_key", "")
        openai_key = config.get("openai_key", "")

        self._providers[ProviderType.GEMINI].api_key = gemini_key   # type: ignore
        self._providers[ProviderType.GROQ].api_key   = groq_key     # type: ignore
        self._providers[ProviderType.OPENAI].api_key = openai_key   # type: ignore

        # Aktif sağlayıcıyı yeniden seç (anahtar eklendi/silindi olabilir)
        forced = config.get("active_provider", "")
        if not forced:
            self._active = self._pick_best(gemini_key, groq_key, openai_key)
            logger.info(f"[Registry] Anahtarlar güncellendi, aktif: {self._active}")

    # ------------------------------------------------------------------
    # Sağlayıcı Erişimi
    # ------------------------------------------------------------------

    def get(self, provider_type: Optional[ProviderType] = None) -> BaseProvider:
        """Belirli bir sağlayıcıyı veya aktifi döner."""
        pt = provider_type or self._active or ProviderType.GEMINI
        provider = self._providers.get(pt)
        if provider is None:
            raise ValueError(f"Sağlayıcı bulunamadı: {pt}")
        return provider

    @property
    def active_provider(self) -> BaseProvider:
        """Şu an aktif olan sağlayıcı."""
        return self.get(self._active)

    @property
    def active_type(self) -> ProviderType:
        """Şu an aktif olan sağlayıcı tipi."""
        return self._active or ProviderType.GEMINI

    def set_active(self, provider_type: ProviderType) -> None:
        """Aktif sağlayıcıyı manuel olarak değiştirir."""
        if provider_type not in self._providers:
            raise ValueError(f"Bilinmeyen sağlayıcı: {provider_type}")
        self._active = provider_type
        logger.info(f"[Registry] Aktif sağlayıcı değiştirildi: {provider_type}")

    # ------------------------------------------------------------------
    # Yüksek Seviye Çağrı API'si
    # ------------------------------------------------------------------

    async def complete(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.2,
        max_tokens: int = 2048,
        force: Optional[ProviderType] = None,
    ) -> ProviderResponse:
        """
        Tek satırlık metin tamamlama.

        Örnek:
            resp = await registry.complete("İstanbul'un nüfusu nedir?")
            print(resp.text)
        """
        provider = self.get(force)
        request = ProviderRequest(
            messages=[ChatMessage.user(prompt)],
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        resp = await provider.complete(request)
        if not resp.success:
            logger.warning(f"[Registry] {provider.provider_type} başarısız, fallback deneniyor...")
            resp = await self._fallback_complete(request, exclude=provider.provider_type)
        return resp

    async def complete_json(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.2,
        max_tokens: int = 2048,
        force: Optional[ProviderType] = None,
    ) -> ProviderResponse:
        """
        JSON modunda tamamlama — orchestrator.py'nin _call_gemini_json()
        yerine artık bu kullanılır.

        Örnek:
            resp = await registry.complete_json(planning_prompt)
            data = json.loads(resp.text)
        """
        provider = self.get(force)
        request = ProviderRequest(
            messages=[ChatMessage.user(prompt)],
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=True,
        )
        resp = await provider.complete_json(request)
        if not resp.success:
            logger.warning(f"[Registry] JSON {provider.provider_type} başarısız, fallback...")
            resp = await self._fallback_complete(request, exclude=provider.provider_type)
        return resp

    async def complete_vision(
        self,
        prompt: str,
        base64_image: str,
        mime_type: str = "image/png",
        system: str = "",
        force: Optional[ProviderType] = None,
    ) -> ProviderResponse:
        """
        Görsel + metin çağrısı.
        Vision destekleyen sağlayıcıyı otomatik seçer.

        Örnek:
            b64 = await coord_engine.screenshot_for_ai()
            resp = await registry.complete_vision("Ekranda ne var?", b64)
        """
        # Vision destekleyen sağlayıcıyı seç
        provider = self._get_vision_provider(force)
        request = ProviderRequest(
            messages=[ChatMessage.user(prompt)],
            system=system,
            images=[ImageAttachment(base64_data=base64_image, mime_type=mime_type)],
        )
        return await provider.complete_with_image(
            request, request.images[0]
        )

    async def complete_with_history(
        self,
        messages: list[ChatMessage],
        system: str = "",
        json_mode: bool = False,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        force: Optional[ProviderType] = None,
    ) -> ProviderResponse:
        """
        Çok turlu sohbet geçmişiyle tamamlama.
        CouncilManager gibi çok mesajlı yapılar için.
        """
        provider = self.get(force)
        request = ProviderRequest(
            messages=messages,
            system=system,
            json_mode=json_mode,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if json_mode:
            return await provider.complete_json(request)
        return await provider.complete(request)

    # ------------------------------------------------------------------
    # Yardımcı
    # ------------------------------------------------------------------

    def _get_vision_provider(
        self, force: Optional[ProviderType] = None
    ) -> BaseProvider:
        """Vision destekleyen sağlayıcıyı döner."""
        if force:
            p = self.get(force)
            if p.capabilities.vision:
                return p
            logger.warning(f"[Registry] {force} vision desteklemiyor, Gemini'ye fallback.")

        # Önce aktif sağlayıcıyı dene
        active = self.active_provider
        if active.capabilities.vision:
            return active

        # Vision destekleyen ilkini bul
        for p in self._providers.values():
            if p.capabilities.vision:
                return p

        # Yoksa aktifi döndür (hata mesajı içinde bildirsin)
        return active

    async def _fallback_complete(
        self,
        request: ProviderRequest,
        exclude: ProviderType,
    ) -> ProviderResponse:
        """
        Aktif sağlayıcı başarısız olduğunda sıradakini dener.
        Öncelik: Gemini → Groq → OpenAI → Ollama
        """
        fallback_order = [
            ProviderType.GEMINI,
            ProviderType.GROQ,
            ProviderType.OPENAI,
            ProviderType.OLLAMA,
        ]
        for pt in fallback_order:
            if pt == exclude:
                continue
            provider = self._providers.get(pt)
            if provider is None:
                continue
            # Anahtarı olan sağlayıcıyı dene
            api_key = getattr(provider, "api_key", "ollama")
            if not api_key or api_key == "ollama":
                # Ollama için çalışıp çalışmadığını kontrol et
                if pt == ProviderType.OLLAMA:
                    is_running = await provider.health_check()
                    if not is_running:
                        continue
                elif not api_key:
                    continue

            logger.info(f"[Registry] Fallback: {pt} deneniyor...")
            if request.json_mode:
                resp = await provider.complete_json(request)
            else:
                resp = await provider.complete(request)
            if resp.success:
                return resp

        return ProviderResponse(
            success=False,
            error="Tüm sağlayıcılar başarısız oldu.",
            provider="none",
        )

    async def check_all(self) -> dict[ProviderType, bool]:
        """
        Tüm sağlayıcıların sağlık durumunu paralel kontrol eder.
        Ayarlar ekranında göstermek için kullanılabilir.

        Kullanım:
            status = await registry.check_all()
            # → {ProviderType.GEMINI: True, ProviderType.GROQ: False, ...}
        """
        tasks = {
            pt: provider.health_check()
            for pt, provider in self._providers.items()
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        return {
            pt: (r is True)
            for pt, r in zip(tasks.keys(), results)
        }

    def summary(self) -> str:
        """Kayıtlı sağlayıcıların özet string'i — loglama için."""
        lines = [f"Aktif: {self._active}"]
        for pt, p in self._providers.items():
            has_key = bool(getattr(p, "api_key", "") and getattr(p, "api_key", "") != "ollama")
            status  = "✓ anahtar var" if has_key else ("yerel" if pt == ProviderType.OLLAMA else "✗ anahtar yok")
            lines.append(f"  {pt.value:<10} {status}  model={p.default_model}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Bağımsız Test
# ---------------------------------------------------------------------------

async def _demo() -> None:
    import os

    print("\n🤖  SIRIUS ProviderRegistry Demo Başlatılıyor...")
    print("─" * 50)

    # Gerçek anahtarlar yoksa mock config
    config = {
        "gemini_key":  os.environ.get("GEMINI_KEY", ""),
        "groq_key":    os.environ.get("GROQ_KEY", ""),
        "openai_key":  os.environ.get("OPENAI_KEY", ""),
    }

    registry = ProviderRegistry(config)
    print(f"\n1. Sağlayıcı özeti:\n{registry.summary()}")

    # Sağlık kontrolleri
    print("\n2. Sağlık kontrolleri (paralel)...")
    statuses = await registry.check_all()
    for pt, ok in statuses.items():
        icon = "✅" if ok else "❌"
        print(f"   {icon} {pt.value}")

    # Gerçek çağrı (anahtar varsa)
    active = registry.active_type
    active_provider = registry.active_provider
    has_key = bool(getattr(active_provider, "api_key", "") not in ("", "ollama"))

    if has_key or active == ProviderType.OLLAMA:
        print(f"\n3. Test çağrısı ({active.value})...")
        resp = await registry.complete(
            "Türkiye'nin başkenti neredir? Tek cümle ile cevapla.",
            max_tokens=50,
        )
        if resp.success:
            print(f"   ✅ Yanıt: {resp.text.strip()}")
            print(f"   📊 Token: {resp.total_tokens} | Gecikme: {resp.latency_ms}ms")
        else:
            print(f"   ❌ Hata: {resp.error}")
    else:
        print(f"\n3. Test çağrısı atlandı (API anahtarı yok).")
        print("   GEMINI_KEY, GROQ_KEY veya OPENAI_KEY ortam değişkeni set edin.")

    print("\n✅ ProviderRegistry demo tamamlandı.")


if __name__ == "__main__":
    asyncio.run(_demo())
