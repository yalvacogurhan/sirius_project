"""
ai_core/providers/ollama_provider.py
======================================
Ollama sağlayıcısı — tamamen yerel, internet gerektirmez.

OpenGuider'ın src/ai/ollama.js karşılığı. Ollama, Llama 3, Mistral, Phi-3
gibi modelleri yerel olarak çalıştırır. SIRIUS'u bulut bağımlılığından kurtarır.

Ollama API'si OpenAI uyumlu endpoint sunar:
    POST http://localhost:11434/v1/chat/completions

Kurulum:
    1. https://ollama.com adresinden Ollama'yı kur
    2. Bir model indir: ollama pull llama3.2
    3. OllamaProvider'ı kullan

Önerilen modeller:
    llama3.2          → 3B, hızlı, genel amaç (varsayılan)
    llama3.1:8b       → 8B, daha yetenekli
    mistral           → 7B, iyi Türkçe
    phi3.5            → 3.8B, Microsoft'un küçük ama güçlü modeli
    qwen2.5:7b        → 7B, mükemmel kod + Türkçe
    deepseek-r1:7b    → 7B, akıl yürütme için

Vision:
    llava, llava-phi3, moondream

Kurulum gereksinimleri:
    pip install openai   (Ollama OpenAI uyumlu endpoint kullanır)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from ai_core.providers.base_provider import (
    BaseProvider, ProviderCapabilities, ProviderRequest,
    ProviderResponse, ProviderType, ImageAttachment,
)
from ai_core.providers.openai_provider import OpenAIProvider

logger = logging.getLogger("sirius.providers.ollama")


class OllamaProvider(OpenAIProvider):
    """
    Ollama yerel LLM sağlayıcısı.

    OpenAIProvider'ı miras alır çünkü Ollama, OpenAI uyumlu endpoint sunar.
    Sadece base_url ve api_key (gereksiz ama zorunlu "ollama") değişir.

    SIRIUS entegrasyonu:
        # Gemini anahtarı yoksa otomatik fallback:
        registry = ProviderRegistry(config_data)
        provider = registry.get_best_available()
        # → Gemini yok, OpenAI yok → OllamaProvider

    Ollama'nın çalıştığını kontrol et:
        import httpx; httpx.get("http://localhost:11434")
    """

    provider_type = ProviderType.OLLAMA
    DEFAULT_MODEL = "llama3.2"
    OLLAMA_URL    = "http://localhost:11434/v1"

    def __init__(
        self,
        model: Optional[str] = None,
        host: str = "localhost",
        port: int = 11434,
    ):
        """
        model: Çalıştırılacak Ollama modeli (varsayılan: llama3.2)
        host:  Ollama sunucu adresi (varsayılan: localhost)
        port:  Ollama port (varsayılan: 11434)
        """
        base_url = f"http://{host}:{port}/v1"
        super().__init__(
            api_key="ollama",    # Ollama anahtar istemez ama openai client zorunlu tutar
            model=model or self.DEFAULT_MODEL,
            base_url=base_url,
        )
        self.host = host
        self.port = port

    @property
    def capabilities(self) -> ProviderCapabilities:
        # Vision modeli yüklüyse True olabilir — dinamik tespit yapmıyoruz
        # Kullanıcı llava gibi bir model seçmişse manuel True set etmeli
        return ProviderCapabilities(
            vision=False,
            json_mode=True,
            streaming=True,
            max_context=8_192,
            local=True,     # İnternet gerektirmez
        )

    @property
    def default_model(self) -> str:
        return self._model

    async def health_check(self) -> bool:
        """Ollama'nın çalışıp çalışmadığını kontrol eder."""
        try:
            import httpx  # type: ignore
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"http://{self.host}:{self.port}/api/tags")
                return resp.status_code == 200
        except Exception:
            try:
                # httpx yoksa urllib ile dene
                import urllib.request
                urllib.request.urlopen(
                    f"http://{self.host}:{self.port}/api/tags", timeout=3
                )
                return True
            except Exception as e:
                logger.warning(f"[Ollama] Çalışmıyor veya erişilemiyor: {e}")
                return False

    async def list_models(self) -> list[str]:
        """
        Ollama'ya yüklenmiş modellerin listesini döner.
        Hangi modellerin kullanılabilir olduğunu görmek için kullanılır.
        """
        try:
            import httpx  # type: ignore
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"http://{self.host}:{self.port}/api/tags")
                if resp.status_code == 200:
                    data = resp.json()
                    return [m["name"] for m in data.get("models", [])]
        except Exception as e:
            logger.warning(f"[Ollama] Model listesi alınamadı: {e}")
        return []

    async def pull_model(self, model_name: str) -> bool:
        """
        Ollama'ya model indirir (ollama pull <model> komutu gibi).
        Büyük modeller dakikalarca sürebilir — arka planda çalıştır.
        """
        try:
            import httpx  # type: ignore
            logger.info(f"[Ollama] '{model_name}' indiriliyor...")
            async with httpx.AsyncClient(timeout=300.0) as client:
                resp = await client.post(
                    f"http://{self.host}:{self.port}/api/pull",
                    json={"name": model_name},
                )
                return resp.status_code == 200
        except Exception as e:
            logger.error(f"[Ollama] Model indirme hatası: {e}")
            return False
