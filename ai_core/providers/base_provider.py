"""
ai_core/providers/base_provider.py
===================================
Tüm AI sağlayıcılarının uyması gereken soyut temel sınıf.

OpenGuider'da LangChain'in BaseChatModel ABC'si bu rolü üstleniyordu:
    @langchain/anthropic, @langchain/openai, @langchain/google-genai
    hepsi aynı .invoke() arayüzünü uygular.

Python'da ABC (Abstract Base Class) + Protocol ile aynı sözleşme sağlanır.
SIRIUS'taki mevcut `Planner` sınıfı doğrudan `google.genai` kullanıyor —
bu modül onu değiştirmez, sadece orchestrator.py'e çok sağlayıcı desteği ekler.

Sağlayıcı seçimi öncelik sırası:
    1. config["active_provider"] varsa onu kullan
    2. Yoksa mevcut Gemini anahtarı varsa Gemini
    3. Yoksa OpenAI anahtarı → OpenAI
    4. Yoksa Ollama (yerel, ücretsiz)
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger("sirius.providers.base")


# ---------------------------------------------------------------------------
# Enum & Veri Modelleri
# ---------------------------------------------------------------------------

class ProviderType(str, Enum):
    GEMINI  = "gemini"
    OPENAI  = "openai"
    OLLAMA  = "ollama"
    GROQ    = "groq"       # OpenAI uyumlu endpoint


class MessageRole(str, Enum):
    SYSTEM    = "system"
    USER      = "user"
    ASSISTANT = "assistant"


@dataclass
class ChatMessage:
    """Tek bir sohbet mesajı — tüm sağlayıcılara ortak format."""
    role:    MessageRole
    content: str

    @classmethod
    def system(cls, text: str) -> "ChatMessage":
        return cls(role=MessageRole.SYSTEM, content=text)

    @classmethod
    def user(cls, text: str) -> "ChatMessage":
        return cls(role=MessageRole.USER, content=text)

    @classmethod
    def assistant(cls, text: str) -> "ChatMessage":
        return cls(role=MessageRole.ASSISTANT, content=text)


@dataclass
class ImageAttachment:
    """Base64 kodlu görsel ek — vision çağrıları için."""
    base64_data: str
    mime_type:   str = "image/png"


@dataclass
class ProviderRequest:
    """
    Bir AI çağrısının tüm parametreleri.
    Sağlayıcıdan bağımsız ortak istek formatı.
    """
    messages:      list[ChatMessage]
    system:        str                       = ""
    json_mode:     bool                      = False    # JSON formatında yanıt iste
    temperature:   float                     = 0.2
    max_tokens:    int                       = 2048
    images:        list[ImageAttachment]     = field(default_factory=list)
    model_override: Optional[str]            = None     # varsayılan modeli geçersiz kıl


@dataclass
class ProviderResponse:
    """
    Bir AI çağrısının sonucu.
    Sağlayıcıdan bağımsız ortak yanıt formatı.
    """
    text:           str   = ""
    provider:       str   = ""
    model:          str   = ""
    input_tokens:   int   = 0
    output_tokens:  int   = 0
    latency_ms:     float = 0.0
    success:        bool  = True
    error:          str   = ""

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class ProviderCapabilities:
    """Bir sağlayıcının desteklediği özellikler."""
    vision:       bool = False   # görsel girdi
    json_mode:    bool = False   # garantili JSON çıktı
    streaming:    bool = False   # stream yanıt
    max_context:  int  = 8192    # maksimum context penceresi (token)
    local:        bool = False   # yerel çalışır (internet gerekmez)


# ---------------------------------------------------------------------------
# Soyut Temel Sınıf
# ---------------------------------------------------------------------------

class BaseProvider(ABC):
    """
    Tüm AI sağlayıcılarının uygulaması gereken soyut sınıf.

    OpenGuider'ın @langchain/anthropic vb. paketlerinin ortak BaseChatModel
    arayüzünün Python ABC karşılığı.

    Alt sınıflar şunları implement etmek ZORUNDA:
        - complete()       : temel metin tamamlama
        - complete_json()  : JSON modunda tamamlama

    Şunları isteğe bağlı override edebilir:
        - complete_with_image(): vision çağrısı
        - capabilities: sağlayıcı yetenekleri
        - health_check(): sağlayıcı erişilebilirliği
    """

    provider_type: ProviderType = ProviderType.GEMINI

    # ------------------------------------------------------------------
    # Abstract metodlar — alt sınıflar implement ETMEK ZORUNDA
    # ------------------------------------------------------------------

    @abstractmethod
    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        """
        Verilen mesaj geçmişine göre metin tamamlama yapar.
        Her sağlayıcı kendi API'sini kullanarak bu metodu implement eder.
        """

    @abstractmethod
    async def complete_json(self, request: ProviderRequest) -> ProviderResponse:
        """
        JSON modunda tamamlama. Yanıt parse edilebilir geçerli JSON olmalı.
        request.json_mode otomatik True set edilir.
        """

    @property
    @abstractmethod
    def capabilities(self) -> ProviderCapabilities:
        """Bu sağlayıcının desteklediği özellikler."""

    @property
    @abstractmethod
    def default_model(self) -> str:
        """Varsayılan model adı."""

    # ------------------------------------------------------------------
    # Opsiyonel override metodlar — varsayılan implementasyon var
    # ------------------------------------------------------------------

    async def complete_with_image(
        self,
        request: ProviderRequest,
        image: ImageAttachment,
    ) -> ProviderResponse:
        """
        Görsel + metin çağrısı.
        Vision desteklemeyen sağlayıcılar için varsayılan: hata mesajı döner.
        """
        if not self.capabilities.vision:
            return ProviderResponse(
                success=False,
                error=f"{self.provider_type} vision desteklemiyor.",
                provider=self.provider_type,
            )
        # Vision destekliyorsa request.images'e ekleyip complete() çağır
        request.images.append(image)
        return await self.complete(request)

    async def health_check(self) -> bool:
        """
        Sağlayıcının erişilebilir olup olmadığını kontrol eder.
        Varsayılan: basit bir test mesajı gönderir.
        Alt sınıflar daha hafif bir kontrol için override edebilir.
        """
        try:
            req = ProviderRequest(
                messages=[ChatMessage.user("test")],
                max_tokens=5,
                temperature=0.0,
            )
            resp = await asyncio.wait_for(self.complete(req), timeout=10.0)
            return resp.success
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Yardımcı metodlar — alt sınıflar kullanabilir
    # ------------------------------------------------------------------

    def _start_timer(self) -> float:
        return time.time()

    def _elapsed_ms(self, start: float) -> float:
        return round((time.time() - start) * 1000, 1)

    def _clean_json_fence(self, text: str) -> str:
        """
        AI yanıtında ```json ... ``` fence varsa temizler.
        Tüm sağlayıcılarda aynı sorun — tek yerde çözüyoruz.
        """
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            inner = [
                line for line in lines
                if not line.strip().startswith("```")
            ]
            text = "\n".join(inner).strip()
        return text

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} model={self.default_model}>"
