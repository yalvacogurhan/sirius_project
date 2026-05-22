"""
ai_core/providers/gemini_provider.py
=====================================
SIRIUS'un mevcut google.genai SDK'sı üzerine inşa edilmiş Gemini sağlayıcısı.

ÖNEMLİ: SIRIUS zaten `from google import genai` kullanıyor (Google Gen AI SDK v2).
Bu dosya aynı SDK'yı kullanır — `google.generativeai` (v1) KULLANMAZ.
Bu sayede mevcut main.py ve Planner.py ile tam uyumlu.

Desteklenen modeller:
    gemini-2.5-flash   → varsayılan (hızlı, ucuz)
    gemini-2.5-pro     → karmaşık görevler
    gemini-2.0-flash   → orchestrator'da JSON çağrıları için

Vision: ✅  JSON Mode: ✅  Streaming: ✅  Max Context: 1M token
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from ai_core.providers.base_provider import (
    BaseProvider, ProviderCapabilities, ProviderRequest,
    ProviderResponse, ProviderType, ImageAttachment,
)

logger = logging.getLogger("sirius.providers.gemini")


class GeminiProvider(BaseProvider):
    """
    Google Gemini sağlayıcısı.

    SIRIUS'taki mevcut doğrudan `genai.Client` çağrılarını
    BaseProvider arayüzüne sarar.

    main.py'deki SiriusController.apply_keys() çağrısında:
        provider_registry.get("gemini").api_key = gk
    ile anahtar güncellenir.
    """

    provider_type = ProviderType.GEMINI

    # Mevcut SIRIUS'un kullandığı modelle aynı aile
    DEFAULT_MODEL      = "gemini-2.5-flash"
    DEFAULT_JSON_MODEL = "gemini-2.5-flash"  # JSON mode için ayrı çağrı gerekmiyor

    def __init__(
        self,
        api_key: str = "",
        model: Optional[str] = None,
        json_model: Optional[str] = None,
    ):
        self.api_key    = api_key
        self._model     = model or self.DEFAULT_MODEL
        self._json_model = json_model or self.DEFAULT_JSON_MODEL

    # ------------------------------------------------------------------
    # BaseProvider Interface
    # ------------------------------------------------------------------

    @property
    def default_model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            vision=True,
            json_mode=True,
            streaming=True,
            max_context=1_000_000,
            local=False,
        )

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        """
        Sohbet geçmişiyle metin tamamlama.
        SIRIUS'un mevcut Planner.gemini_key kullanımıyla aynı SDK.
        """
        return await asyncio.to_thread(self._complete_sync, request)

    async def complete_json(self, request: ProviderRequest) -> ProviderResponse:
        """
        JSON modunda tamamlama — orchestrator.py'nin _call_gemini_json()
        fonksiyonuyla aynı mantık, ama BaseProvider arayüzüyle.
        """
        request.json_mode = True
        return await asyncio.to_thread(self._complete_sync, request)

    async def complete_with_image(
        self,
        request: ProviderRequest,
        image: ImageAttachment,
    ) -> ProviderResponse:
        """Vision çağrısı — base64 PNG + metin prompt."""
        request.images.append(image)
        return await asyncio.to_thread(self._complete_sync, request)

    async def health_check(self) -> bool:
        if not self.api_key:
            return False
        try:
            from google import genai  # type: ignore
            client = genai.Client(api_key=self.api_key)
            # En hafif çağrı: model listesi
            await asyncio.to_thread(
                lambda: list(client.models.list())[:1]
            )
            return True
        except Exception as e:
            logger.warning(f"[Gemini] Health check başarısız: {e}")
            return False

    # ------------------------------------------------------------------
    # Senkron İç Uygulama (asyncio.to_thread ile çağrılır)
    # ------------------------------------------------------------------

    def _complete_sync(self, request: ProviderRequest) -> ProviderResponse:
        """
        Blocking Gemini çağrısı.
        asyncio.to_thread() içinde çalışır — event loop'u bloke etmez.

        SIRIUS'un mevcut Planner.py'sindeki pattern:
            client = genai.Client(api_key=self.gemini_key)
            response = client.models.generate_content(model=..., contents=...)
        """
        if not self.api_key:
            return ProviderResponse(
                success=False,
                error="Gemini API anahtarı eksik.",
                provider=self.provider_type,
            )

        start = self._start_timer()

        try:
            from google import genai         # type: ignore
            from google.genai import types   # type: ignore
        except ImportError:
            return ProviderResponse(
                success=False,
                error="google-genai paketi bulunamadı: pip install google-genai",
                provider=self.provider_type,
            )

        client = genai.Client(api_key=self.api_key)
        model_name = request.model_override or (
            self._json_model if request.json_mode else self._model
        )

        # Mesajları Gemini formatına çevir
        contents = self._build_contents(request)

        # Yapılandırma
        gen_config_kwargs: dict = {
            "temperature": request.temperature,
            "max_output_tokens": request.max_tokens,
        }
        if request.json_mode:
            gen_config_kwargs["response_mime_type"] = "application/json"

        # System instruction
        system_parts = None
        if request.system:
            system_parts = request.system

        try:
            response = client.models.generate_content(
                model=model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    **gen_config_kwargs,
                    system_instruction=system_parts,
                ),
            )
        except Exception as e:
            logger.error(f"[Gemini] API çağrısı başarısız: {e}")
            return ProviderResponse(
                success=False,
                error=str(e),
                provider=self.provider_type,
                model=model_name,
                latency_ms=self._elapsed_ms(start),
            )

        raw_text = ""
        try:
            raw_text = response.text or ""
        except Exception:
            # Bazı durumlarda .text raise edebilir (safety block vb.)
            try:
                raw_text = response.candidates[0].content.parts[0].text
            except Exception:
                raw_text = ""

        if request.json_mode:
            raw_text = self._clean_json_fence(raw_text)

        # Token kullanımı
        input_tok = output_tok = 0
        try:
            usage = response.usage_metadata
            input_tok  = usage.prompt_token_count or 0
            output_tok = usage.candidates_token_count or 0
        except Exception:
            pass

        return ProviderResponse(
            text=raw_text,
            provider=self.provider_type,
            model=model_name,
            input_tokens=input_tok,
            output_tokens=output_tok,
            latency_ms=self._elapsed_ms(start),
            success=True,
        )

    def _build_contents(self, request: ProviderRequest) -> list:
        """
        ChatMessage listesini Gemini API'sinin beklediği `contents` formatına çevirir.

        Gemini format:
            [{"role": "user", "parts": [{"text": "..."}]}, ...]

        Görseller varsa son user mesajına ek part olarak eklenir.
        """
        contents = []
        for msg in request.messages:
            # Gemini "system" rolünü desteklemiyor — user'a çevir
            role = "user" if msg.role.value == "system" else msg.role.value
            contents.append({
                "role": role,
                "parts": [{"text": msg.content}],
            })

        # Görseller varsa son user mesajına ekle
        if request.images and contents:
            last = contents[-1]
            for img in request.images:
                last["parts"].append({
                    "inline_data": {
                        "mime_type": img.mime_type,
                        "data": img.base64_data,
                    }
                })

        return contents
