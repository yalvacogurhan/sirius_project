"""
ai_core/evaluator.py
=====================
Görev adımlarını değerlendiren bağımsız modül.

OpenGuider'ın src/agent/evaluator.js'inin tam Python portu.
orchestrator.py içinde gömülüydü — bu dosyayla bağımsız, test edilebilir
ve ProviderRegistry uyumlu hale getirildi.

Değerlendirici ne yapar?
    Bir adım çalıştırıldıktan sonra:
        - Sonuç başarılı mı?             (success)
        - Geçici hata, tekrar dene mi?   (should_retry)
        - Durum değişti, replan mı?      (replan_needed)
        - Neden? (human-readable reason)

JS → Python:
    async evaluateStep(step, result) → async def evaluate(step, result)
    JSON.parse(verdict)              → Pydantic EvalResult
    this.ai.complete(prompt)         → provider_registry.complete_json(prompt)

Kullanım:
    evaluator = StepEvaluator(provider_registry=registry)
    verdict = await evaluator.evaluate(step_record, "araç sonucu buraya")
    if verdict.success:
        tracker.step_done(verdict.reason)
    elif verdict.should_retry:
        tracker.step_retry()
    elif verdict.replan_needed:
        tracker.replan_started()
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from pydantic import BaseModel, Field

logger = logging.getLogger("sirius.evaluator")


# ---------------------------------------------------------------------------
# Veri Modelleri
# ---------------------------------------------------------------------------

class EvalResult(BaseModel):
    """
    Bir adım değerlendirmesinin sonucu.
    orchestrator.py'deki EvalResult ile aynı arayüz — geriye uyumlu.
    """
    success:       bool  = False
    reason:        str   = ""
    should_retry:  bool  = False
    replan_needed: bool  = False
    confidence:    float = 0.0    # AI'nın bu karara güveni (0.0–1.0)
    raw_output:    str   = ""     # AI'nın ham yanıtı (debug için)

    @property
    def verdict_label(self) -> str:
        """Kısa etiket: UI ve loglama için."""
        if self.success:
            return "BAŞARILI"
        if self.should_retry:
            return "YENİDEN_DENE"
        if self.replan_needed:
            return "REPLAN"
        return "BAŞARISIZ"


class EvalContext(BaseModel):
    """
    Değerlendirmeye ek bağlam bilgisi.
    Daha iyi değerlendirme için opsiyonel olarak verilir.
    """
    goal:          str = ""    # üst görevin hedefi
    step_index:    int = 0
    total_steps:   int = 0
    attempt_no:    int = 1
    previous_results: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Prompt Builder
# ---------------------------------------------------------------------------

def _build_eval_prompt(
    step_title:       str,
    step_description: str,
    tool_used:        str,
    result:           str,
    context:          Optional[EvalContext] = None,
) -> str:
    """
    Değerlendirici AI'ına gönderilecek prompt.
    OpenGuider'ın evaluator.js içindeki buildEvaluationPrompt() fonksiyonunun
    geliştirilmiş Python portu.

    Orijinalinden farklar:
        - EvalContext ile üst görev bağlamı eklendi
        - confidence skoru istendi
        - Türkçe açıklama zorunlu
        - Geçici hata tespiti için örüntüler eklendi
    """
    ctx_block = ""
    if context:
        ctx_block = f"""
Üst Görev: {context.goal}
Adım: {context.step_index + 1}/{context.total_steps}
Deneme: {context.attempt_no}
"""
        if context.previous_results:
            prev = context.previous_results[-2:]  # son 2 sonuç
            ctx_block += f"Önceki Sonuçlar: {' | '.join(prev)}\n"

    tool_block = f"\nKullanılan Araç: {tool_used}" if tool_used else ""

    return f"""Sen bir görev değerlendirici yapay zekasısın. Bir adımın başarıyla tamamlanıp tamamlanmadığına karar ver.
{ctx_block}
ADIM BAŞLIĞI: {step_title}
ADIM AÇIKLAMASI: {step_description}{tool_block}
ELDE EDİLEN SONUÇ: {result}

Sadece aşağıdaki JSON formatında yanıt ver, başka hiçbir şey yazma:
{{
  "success": true,
  "reason": "Neden başarılı veya başarısız olduğunun Türkçe açıklaması",
  "should_retry": false,
  "replan_needed": false,
  "confidence": 0.95
}}

DEĞERLENDİRME KURALLARI:
- success: Adım hedefine ulaştıysa true. Kısmi sonuç veya hata varsa false.
- should_retry: "HATA:", "timeout", "bağlantı" gibi GEÇİCİ hata belirtileri varsa true.
  Mantıksal hata veya "bulunamadı" gibi kalıcı durumlar için false.
- replan_needed: Sonuç planın geri kalanını TAMAMEN GEÇERSİZ kılıyorsa true.
  Örnek: "Dosya mevcut değil" → sonraki adımlar dosyaya erişmeye çalışacaksa replan gerekir.
- confidence: Bu karardan ne kadar emin olduğun (0.0–1.0).
- Türkçe yaz."""


# ---------------------------------------------------------------------------
# Hata Örüntüsü Tespiti (kural tabanlı — AI çağrısı gerektirmez)
# ---------------------------------------------------------------------------

# Geçici hata belirteçleri → should_retry = True
_TRANSIENT_ERROR_PATTERNS = [
    "timeout", "zaman aşımı", "connection", "bağlantı",
    "network", "ağ", "rate limit", "429", "503", "502",
    "temporarily", "geçici", "retry", "tekrar",
    "hata oluştu", "exception", "traceback",
]

# Kalıcı hata belirteçleri → success = False, should_retry = False
_PERMANENT_ERROR_PATTERNS = [
    "bulunamadı", "not found", "404", "permission",
    "yetki", "access denied", "bilinmeyen araç",
    "geçersiz", "invalid", "unsupported",
]

# Replan gerektiren durumlar
_REPLAN_PATTERNS = [
    "mevcut değil", "silinmiş", "değiştirilmiş",
    "farklı bir yol", "alternatif", "plan geçersiz",
    "does not exist", "no longer",
]


def _rule_based_eval(result: str, step_title: str) -> Optional[EvalResult]:
    """
    Sonuç metnine bakarak kurallara dayalı hızlı değerlendirme.
    AI çağrısından önce dener — hem hızlı hem de token tasarruflu.

    Belirgin hata/başarı durumlarında AI'ya gitmeden karar verir.
    Belirsiz durumlarda None döner → AI'ya devret.
    """
    result_lower = result.lower()

    # Boş sonuç → belirsiz, AI'ya bırak
    if not result.strip():
        return EvalResult(
            success=False,
            reason="Araç boş sonuç döndürdü.",
            should_retry=True,
            confidence=0.7,
        )

    # "HATA:" ile başlıyorsa (orchestrator convention)
    if result_lower.startswith("hata:") or result_lower.startswith("error:"):
        is_transient = any(p in result_lower for p in _TRANSIENT_ERROR_PATTERNS)
        is_replan    = any(p in result_lower for p in _REPLAN_PATTERNS)
        return EvalResult(
            success=False,
            reason=result[:120],
            should_retry=is_transient,
            replan_needed=is_replan,
            confidence=0.85,
            raw_output=result,
        )

    # Açıkça başarılı sinyaller
    success_signals = ["tamamlandı", "başarılı", "completed", "done", "ok", "✅"]
    if any(s in result_lower for s in success_signals) and len(result) < 500:
        return EvalResult(
            success=True,
            reason=f"Araç başarı sinyali verdi: {result[:80]}",
            confidence=0.80,
            raw_output=result,
        )

    # Belirsiz — AI değerlendirmesine bırak
    return None


# ---------------------------------------------------------------------------
# StepEvaluator
# ---------------------------------------------------------------------------

class StepEvaluator:
    """
    Adım değerlendirici.

    İki katmanlı değerlendirme:
        1. Kural tabanlı hızlı kontrol  (AI çağrısı yok)
        2. AI tabanlı derin değerlendirme (belirsiz durumlarda)

    ProviderRegistry veya doğrudan gemini_key ile çalışır.

    Kullanım A — ProviderRegistry ile (önerilen):
        evaluator = StepEvaluator(provider_registry=registry)

    Kullanım B — Geriye uyumlu (orchestrator.py'deki gibi):
        evaluator = StepEvaluator(gemini_key="AIza...")
    """

    def __init__(
        self,
        provider_registry=None,   # ai_core.providers.ProviderRegistry
        gemini_key: str = "",     # geriye uyumluluk için
        skip_rule_check: bool = False,
        min_result_length_for_ai: int = 10,
    ):
        """
        skip_rule_check:          True ise her zaman AI'ya sor (daha yavaş ama daha güvenli).
        min_result_length_for_ai: Bu uzunluktan kısa sonuçları kural tabanlı değerlendir.
        """
        self._registry       = provider_registry
        self._gemini_key     = gemini_key
        self._skip_rules     = skip_rule_check
        self._min_ai_len     = min_result_length_for_ai

    async def evaluate(
        self,
        step,              # StepRecord veya orchestrator.TaskStep (duck-typing)
        result: str,
        context: Optional[EvalContext] = None,
    ) -> EvalResult:
        """
        Adımı değerlendirir.

        step: StepRecord (task_state.py) veya TaskStep (orchestrator.py) —
              ikisi de .title, .description, .tool alanlarına sahip.
        result: Adımdan elde edilen çıktı string'i.
        context: Opsiyonel üst bağlam.
        """
        title       = getattr(step, "title", "")
        description = getattr(step, "description", "")
        tool        = getattr(step, "tool", "")

        logger.debug(f"[Evaluator] Değerlendiriliyor: '{title}' — sonuç uzunluğu: {len(result)}")

        # --- Kural tabanlı hızlı kontrol ---
        if not self._skip_rules:
            quick = _rule_based_eval(result, title)
            if quick is not None:
                logger.debug(
                    f"[Evaluator] Kural tabanlı karar: {quick.verdict_label} "
                    f"(güven: {quick.confidence:.0%})"
                )
                return quick

        # --- AI tabanlı değerlendirme ---
        return await self._ai_evaluate(title, description, tool, result, context)

    async def evaluate_batch(
        self,
        steps_and_results: list[tuple],
        context: Optional[EvalContext] = None,
    ) -> list[EvalResult]:
        """
        Birden fazla adımı paralel değerlendirir.
        JS'deki Promise.all() karşılığı.

        steps_and_results: [(step, result), ...] listesi
        """
        tasks = [
            self.evaluate(step, result, context)
            for step, result in steps_and_results
        ]
        return list(await asyncio.gather(*tasks))

    # ------------------------------------------------------------------
    # AI Değerlendirme (İç)
    # ------------------------------------------------------------------

    async def _ai_evaluate(
        self,
        title: str,
        description: str,
        tool: str,
        result: str,
        context: Optional[EvalContext],
    ) -> EvalResult:
        """AI'ya değerlendirme yaptırır."""
        prompt = _build_eval_prompt(title, description, tool, result, context)

        try:
            raw_text = await self._call_ai(prompt)
        except Exception as e:
            logger.warning(f"[Evaluator] AI çağrısı başarısız, muhafazakâr karar: {e}")
            # AI yoksa güvenli varsayılan: başarısız + retry
            return EvalResult(
                success=False,
                reason=f"Değerlendirme AI hatası: {e}",
                should_retry=True,
                confidence=0.0,
            )

        return self._parse_response(raw_text, result)

    async def _call_ai(self, prompt: str) -> str:
        """ProviderRegistry veya doğrudan Gemini ile AI çağrısı yapar."""
        if self._registry is not None:
            resp = await self._registry.complete_json(prompt, max_tokens=300)
            if resp.success:
                return resp.text
            raise RuntimeError(resp.error)

        # Geriye uyumlu: doğrudan google.genai
        if self._gemini_key:
            return await self._call_gemini_direct(prompt)

        raise RuntimeError("Ne ProviderRegistry ne de gemini_key verildi.")

    async def _call_gemini_direct(self, prompt: str) -> str:
        """
        Geriye uyumluluk için doğrudan Gemini çağrısı.
        orchestrator.py'deki _call_gemini_json() ile aynı mantık.
        """
        try:
            from google import genai       # type: ignore
            from google.genai import types # type: ignore
        except ImportError:
            raise RuntimeError("google-genai paketi bulunamadı.")

        client = genai.Client(api_key=self._gemini_key)

        def _sync_call() -> str:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                    max_output_tokens=300,
                ),
            )
            return response.text or ""

        raw = await asyncio.to_thread(_sync_call)
        return raw

    def _parse_response(self, raw_text: str, original_result: str) -> EvalResult:
        """AI yanıtını parse eder, hata durumunda güvenli varsayılan döner."""
        # Markdown fence temizle
        text = raw_text.strip()
        if text.startswith("```"):
            text = "\n".join(
                line for line in text.splitlines()
                if not line.strip().startswith("```")
            ).strip()

        try:
            data = json.loads(text)
            return EvalResult(
                success=bool(data.get("success", False)),
                reason=str(data.get("reason", "")),
                should_retry=bool(data.get("should_retry", False)),
                replan_needed=bool(data.get("replan_needed", False)),
                confidence=float(data.get("confidence", 0.5)),
                raw_output=raw_text,
            )
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"[Evaluator] JSON parse hatası: {e} — ham: {raw_text[:80]}")
            return EvalResult(
                success=False,
                reason=f"Değerlendirme yanıtı parse edilemedi: {e}",
                should_retry=True,
                confidence=0.0,
                raw_output=raw_text,
            )


# ---------------------------------------------------------------------------
# Bağımsız Test
# ---------------------------------------------------------------------------

async def _demo() -> None:
    from ai_core.task_state import StepRecord, StepStatus

    print("\n🔍  StepEvaluator Demo Başlatılıyor...")
    print("─" * 50)

    # Mock AI — API anahtarı gerektirmez
    class MockRegistry:
        async def complete_json(self, prompt: str, **kwargs):
            class Resp:
                success = True
                text = json.dumps({
                    "success": True,
                    "reason": "Araç başarılı sonuç döndürdü.",
                    "should_retry": False,
                    "replan_needed": False,
                    "confidence": 0.93,
                })
            return Resp()

    evaluator = StepEvaluator(provider_registry=MockRegistry())

    test_cases = [
        ("Web araması yap", "Hava durumunu ara", "web_search",
         "İstanbul: 18°C, parçalı bulutlu"),

        ("Dosyayı kaydet", "Dosyayı diske kaydet", "file_controller",
         "HATA: timeout — bağlantı zaman aşımına uğradı"),

        ("Menüyü aç", "Dosya menüsünü aç", "screen_process",
         "HATA: 'Dosya' metni ekranda bulunamadı."),

        ("Sonucu bildir", "Kullanıcıya sesli bildir", "",
         "Tamamlandı."),

        ("Boş sonuç testi", "Bir şey yap", "open_app", ""),
    ]

    ctx = EvalContext(goal="Hava durumunu öğren", total_steps=5)

    for i, (title, desc, tool, result) in enumerate(test_cases):
        step = StepRecord(index=i, title=title, description=desc, tool=tool)
        ctx.step_index = i
        verdict = await evaluator.evaluate(step, result, ctx)
        print(
            f"\n  Test {i+1}: '{title}'\n"
            f"    Sonuç    : {result[:50] or '(boş)'}\n"
            f"    Karar    : {verdict.verdict_label} (güven: {verdict.confidence:.0%})\n"
            f"    Açıklama : {verdict.reason[:70]}"
        )

    print("\n✅ StepEvaluator demo tamamlandı.")


if __name__ == "__main__":
    asyncio.run(_demo())
