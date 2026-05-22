"""
ai_core/providers/openai_provider.py
======================================
OpenAI ve Groq sağlayıcıları.

Groq, OpenAI'nın istemci kütüphanesiyle %100 uyumlu endpoint sunar —
sadece base_url değişir. Bu yüzden tek sınıfta ikisi de desteklenir.

SIRIUS'ta Groq anahtarı config_data["groq_key"] içinde zaten var.

OpenAI:  gpt-4o, gpt-4o-mini, gpt-4-turbo
Groq:    llama-3.3-70b-versatile, mixtral-8x7b-32768, gemma2-9b-it

Vision: OpenAI ✅  Groq ❌  JSON Mode: ✅  Streaming: ✅

Kurulum:
    pip install openai
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from ai_core.providers.base_provider import (
    BaseProvider, ProviderCapabilities, ProviderRequest,
    ProviderResponse, ProviderType, ImageAttachment,
)

logger = logging.getLogger("sirius.providers.openai")


class OpenAIProvider(BaseProvider):
    """
    OpenAI GPT sağlayıcısı.

    OpenGuider'ın @langchain/openai paketinin Python karşılığı.
    SIRIUS'ta Gemini müsait değilse veya kullanıcı tercih ederse devreye girer.

    Entegrasyon:
        registry = ProviderRegistry(...)
        registry.get(ProviderType.OPENAI).api_key = "sk-..."
    """

    provider_type = ProviderType.OPENAI
    DEFAULT_MODEL  = "gpt-4o-mini"
    BASE_URL       = None  # None = OpenAI varsayılan endpoint

    def __init__(
        self,
        api_key: str = "",
        model: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self.api_key  = api_key
        self._model   = model or self.DEFAULT_MODEL
        self._base_url = base_url or self.BASE_URL

    @property
    def default_model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            vision=True,
            json_mode=True,
            streaming=True,
            max_context=128_000,
            local=False,
        )

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        return await asyncio.to_thread(self._complete_sync, request)

    async def complete_json(self, request: ProviderRequest) -> ProviderResponse:
        request.json_mode = True
        return await asyncio.to_thread(self._complete_sync, request)

    async def health_check(self) -> bool:
        if not self.api_key:
            return False
        try:
            from openai import OpenAI  # type: ignore
            kwargs: dict = {"api_key": self.api_key}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            client = OpenAI(**kwargs)
            await asyncio.to_thread(lambda: client.models.list())
            return True
        except Exception as e:
            logger.warning(f"[OpenAI] Health check başarısız: {e}")
            return False

    # ------------------------------------------------------------------
    def _complete_sync(self, request: ProviderRequest) -> ProviderResponse:
        """Blocking OpenAI çağrısı — asyncio.to_thread ile sarılır."""
        if not self.api_key:
            return ProviderResponse(
                success=False,
                error="OpenAI API anahtarı eksik.",
                provider=self.provider_type,
            )

        start = self._start_timer()

        try:
            from openai import OpenAI  # type: ignore
        except ImportError:
            return ProviderResponse(
                success=False,
                error="openai paketi bulunamadı: pip install openai",
                provider=self.provider_type,
            )

        client_kwargs: dict = {"api_key": self.api_key}
        if self._base_url:
            client_kwargs["base_url"] = self._base_url
        client = OpenAI(**client_kwargs)

        model_name = request.model_override or self._model
        messages   = self._build_messages(request)

        call_kwargs: dict = {
            "model":       model_name,
            "messages":    messages,
            "temperature": request.temperature,
            "max_tokens":  request.max_tokens,
        }
        if request.json_mode:
            call_kwargs["response_format"] = {"type": "json_object"}

        try:
            response = client.chat.completions.create(**call_kwargs)
        except Exception as e:
            logger.error(f"[OpenAI] API çağrısı başarısız: {e}")
            return ProviderResponse(
                success=False,
                error=str(e),
                provider=self.provider_type,
                model=model_name,
                latency_ms=self._elapsed_ms(start),
            )

        raw_text = response.choices[0].message.content or ""
        if request.json_mode:
            raw_text = self._clean_json_fence(raw_text)

        usage = response.usage
        return ProviderResponse(
            text=raw_text,
            provider=self.provider_type,
            model=model_name,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            latency_ms=self._elapsed_ms(start),
            success=True,
        )

    def _build_messages(self, request: ProviderRequest) -> list[dict]:
        """
        ChatMessage listesini OpenAI chat/completions formatına çevirir.
        Görseller varsa content array'i kullanılır (vision format).
        """
        messages: list[dict] = []

        # System mesajı ayrı ekle
        if request.system:
            messages.append({"role": "system", "content": request.system})

        for i, msg in enumerate(request.messages):
            # Son user mesajına görselleri ekle
            is_last_user = (
                msg.role.value == "user"
                and i == len(request.messages) - 1
                and request.images
            )

            if is_last_user:
                content: list = [{"type": "text", "text": msg.content}]
                for img in request.images:
                    content.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{img.mime_type};base64,{img.base64_data}"
                        }
                    })
                messages.append({"role": "user", "content": content})
            else:
                messages.append({"role": msg.role.value, "content": msg.content})

        return messages


# ---------------------------------------------------------------------------
# Groq Sağlayıcısı (OpenAIProvider'ın özelleşmiş alt sınıfı)
# ---------------------------------------------------------------------------

class GroqProvider(OpenAIProvider):
    """
    Groq sağlayıcısı — OpenAI istemcisiyle uyumlu endpoint.

    SIRIUS'ta zaten groq_key config'de mevcut:
        self.config_data.get("groq_key", "")

    Groq'un avantajı: Gemini/OpenAI'dan çok daha hızlı inference
    (özellikle Llama modelleri ile saniyede 500+ token).

    Kısıtlama: Vision desteklemiyor, JSON mode sınırlı.

    Modeller:
        llama-3.3-70b-versatile  → genel amaç (varsayılan)
        llama-3.1-8b-instant     → çok hızlı, düşük gecikme
        mixtral-8x7b-32768       → uzun context (32K)
        gemma2-9b-it             → küçük ama yetenekli
    """

    provider_type = ProviderType.GROQ
    DEFAULT_MODEL = "llama-3.3-70b-versatile"
    BASE_URL      = "https://api.groq.com/openai/v1"

    def __init__(self, api_key: str = "", model: Optional[str] = None):
        super().__init__(
            api_key=api_key,
            model=model or self.DEFAULT_MODEL,
            base_url=self.BASE_URL,
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            vision=False,       # Groq vision desteklemiyor
            json_mode=True,
            streaming=True,
            max_context=32_768,
            local=False,
        )

    @property
    def default_model(self) -> str:
        return self._model
