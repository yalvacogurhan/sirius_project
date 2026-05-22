"""
ai_core/replanner.py
=====================
Görev yeniden planlayıcısı — bağımsız modül.

OpenGuider'ın src/agent/replanner.js'inin tam Python portu.
orchestrator.py içinde gömülüydü — bu dosyayla bağımsız, test edilebilir
ve TaskStateTracker entegrasyonuyla güçlendirildi.

Replanner ne zaman devreye girer?
    StepEvaluator → replan_needed = True
        ↓
    TaskStateTracker.replan_started()
        ↓
    TaskReplanner.replan(tracker, failed_step, reason)
        ↓
    AI → yeni adımlar → tracker.apply_replan(new_steps)

JS → Python:
    async replan(plan, failedStep, reason)   →  async def replan(tracker, step, reason)
    plan.steps = done + revised              →  tracker.apply_replan(new_steps)
    this.replanCount++                       →  tracker.replan_count (otomatik)
    EventEmitter("replan-done")              →  asyncio.Event (orchestrator yönetir)

Kullanım:
    replanner = TaskReplanner(provider_registry=registry)

    # Evaluator replan istediğinde:
    tracker.replan_started()
    try:
        new_steps = await replanner.replan(
            tracker=tracker,
            failed_step=current_step,
            failure_reason=verdict.reason,
            goal=original_goal,
        )
        tracker.apply_replan(new_steps, replan_reason="...")
        tracker.transition(TaskStatus.RUNNING)
    except ReplanLimitError:
        tracker.transition(TaskStatus.FAILED)
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from pydantic import BaseModel, Field

from ai_core.task_state import StepRecord, TaskStateTracker

logger = logging.getLogger("sirius.replanner")


# ---------------------------------------------------------------------------
# Özel İstisnalar
# ---------------------------------------------------------------------------

class ReplanLimitError(Exception):
    """Maksimum replan sayısına ulaşıldığında fırlatılır."""
    pass


class ReplanParseError(Exception):
    """AI yanıtı parse edilemediğinde fırlatılır."""
    pass


# ---------------------------------------------------------------------------
# Veri Modelleri
# ---------------------------------------------------------------------------

class ReplanResult(BaseModel):
    """Bir replan işleminin sonucu."""
    new_steps:     list[StepRecord] = Field(default_factory=list)
    replan_reason: str              = ""
    replan_index:  int              = 0     # kaçıncı replan
    strategy:      str              = ""    # AI'nın seçtiği strateji


# ---------------------------------------------------------------------------
# Prompt Builder
# ---------------------------------------------------------------------------

def _build_replan_prompt(
    goal:            str,
    failed_step_title: str,
    failed_step_desc:  str,
    failure_reason:  str,
    done_titles:     list[str],
    remaining_titles: list[str],
    replan_index:    int,
) -> str:
    """
    Yeniden planlayıcı AI'ına gönderilecek prompt.
    OpenGuider'ın replanner.js buildReplanningPrompt() fonksiyonunun
    geliştirilmiş Python portu.

    Orijinalinden farklar:
        - Tamamlanmış adımlar listesi eklendi
        - Replan indeksi ile AI'ya "bu ilk mi yoksa tekrar mı?" bilgisi verildi
        - "strategy" alanı eklendi — AI alternatif yaklaşım açıklasın
        - Maksimum 6 yeni adım (OpenGuider'da da aynı)
    """
    done_block = (
        "\n".join(f"  ✅ {t}" for t in done_titles) if done_titles
        else "  (Henüz tamamlanan adım yok)"
    )
    remaining_block = (
        "\n".join(f"  ⏳ {t}" for t in remaining_titles) if remaining_titles
        else "  (Kalmadı)"
    )
    retry_note = (
        "\nNOT: Bu önceki replan girişimlerinden biri. Farklı bir strateji seç.\n"
        if replan_index > 1 else ""
    )

    return f"""Sen bir görev yeniden planlayıcı yapay zekasısın. Bir adım başarısız oldu ve planı güncellemelisin.
{retry_note}
ANA HEDEF: {goal}

TAMAMLANAN ADIMLAR:
{done_block}

BAŞARISIZ ADIM: {failed_step_title}
AÇIKLAMA: {failed_step_desc}
BAŞARISIZLIK NEDENİ: {failure_reason}

KALAN (GEÇERSİZ OLAN) ADIMLAR:
{remaining_block}

Sadece aşağıdaki JSON formatında SADECE kalan adımları yeniden planla, başka hiçbir şey yazma:
{{
  "revised_steps": [
    {{
      "index": 0,
      "title": "Kısa adım başlığı",
      "description": "Bu adımda tam olarak ne yapılacağı",
      "tool": "tool_ismi_veya_bos_string",
      "tool_args": {{}}
    }}
  ],
  "replan_reason": "Neden bu şekilde yeniden planlandı",
  "strategy": "Hangi alternatif yaklaşım benimsendi"
}}

KURALLAR:
- Tamamlanmış adımları TEKRAR ETME.
- Başarısız adımı farklı bir yolla veya araçla yapmaya çalış.
- Maksimum 6 yeni adım.
- tool alanı için SIRIUS araçları: open_app, web_search, weather_report, send_message,
  youtube_video, computer_settings, screen_process, flight_finder, save_memory, find_on_screen.
- Türkçe yaz."""


# ---------------------------------------------------------------------------
# TaskReplanner
# ---------------------------------------------------------------------------

class TaskReplanner:
    """
    Görev yeniden planlayıcısı.

    ProviderRegistry veya doğrudan gemini_key ile çalışır.
    TaskStateTracker ile tam entegre — tracker.apply_replan() çağırır.

    Kullanım A — ProviderRegistry ile (önerilen):
        replanner = TaskReplanner(provider_registry=registry)

    Kullanım B — Geriye uyumlu:
        replanner = TaskReplanner(gemini_key="AIza...")
    """

    MAX_NEW_STEPS = 6

    def __init__(
        self,
        provider_registry=None,
        gemini_key: str = "",
        max_replan: int = 3,
    ):
        self._registry   = provider_registry
        self._gemini_key = gemini_key
        self._max_replan = max_replan

    async def replan(
        self,
        tracker: TaskStateTracker,
        failed_step,           # StepRecord veya orchestrator.TaskStep
        failure_reason: str,
        goal: str = "",
    ) -> ReplanResult:
        """
        Yeniden planlama yapar ve sonucu döner.

        Bu metod tracker.apply_replan() ÇAĞIRMAZ —
        orchestrator bu kararı verme hakkını saklı tutar.
        Dönen ReplanResult.new_steps'i orchestrator apply eder.

        Fırlatır:
            ReplanLimitError — maksimum replan aşıldı
            ReplanParseError — AI yanıtı parse edilemedi
        """
        snap = tracker.snapshot()

        # Replan limiti kontrolü (tracker zaten sayıyor)
        if not tracker.can_replan:
            raise ReplanLimitError(
                f"Maksimum replan sayısına ulaşıldı ({self._max_replan}). "
                "Görev başarısız kabul ediliyor."
            )

        # Adım listelerini hazırla
        done_titles = [
            s.title for s in snap.steps if s.status.is_terminal and s.status.value == "done"
        ]
        remaining_titles = [
            s.title for s in snap.steps if s.status.value == "pending"
        ]
        failed_title = getattr(failed_step, "title", "")
        failed_desc  = getattr(failed_step, "description", "")
        effective_goal = goal or snap.goal

        logger.info(
            f"[Replanner] Replan #{snap.replan_count + 1}: "
            f"'{failed_title}' — {failure_reason[:60]}"
        )

        # AI çağrısı
        prompt = _build_replan_prompt(
            goal=effective_goal,
            failed_step_title=failed_title,
            failed_step_desc=failed_desc,
            failure_reason=failure_reason,
            done_titles=done_titles,
            remaining_titles=remaining_titles,
            replan_index=snap.replan_count + 1,
        )

        try:
            raw_text = await self._call_ai(prompt)
        except Exception as e:
            logger.error(f"[Replanner] AI çağrısı başarısız: {e}")
            raise ReplanParseError(f"AI replan çağrısı başarısız: {e}") from e

        return self._parse_response(raw_text, replan_index=snap.replan_count + 1)

    async def replan_and_apply(
        self,
        tracker: TaskStateTracker,
        failed_step,
        failure_reason: str,
        goal: str = "",
    ) -> ReplanResult:
        """
        Replan yap ve sonucu tracker'a uygula.
        Kolaylık metodu — orchestrator bu ikisini ayrı çağırabilir,
        ya da bu tek metodu kullanabilir.

        Fırlatır:
            ReplanLimitError, ReplanParseError
        """
        result = await self.replan(tracker, failed_step, failure_reason, goal)
        tracker.apply_replan(result.new_steps, result.replan_reason)
        logger.info(
            f"[Replanner] ✅ Replan uygulandı: {len(result.new_steps)} yeni adım. "
            f"Strateji: {result.strategy}"
        )
        return result

    # ------------------------------------------------------------------
    # AI Çağrıları (İç)
    # ------------------------------------------------------------------

    async def _call_ai(self, prompt: str) -> str:
        """ProviderRegistry veya doğrudan Gemini ile AI çağrısı."""
        if self._registry is not None:
            resp = await self._registry.complete_json(prompt, max_tokens=800)
            if resp.success:
                return resp.text
            raise RuntimeError(resp.error)

        if self._gemini_key:
            return await self._call_gemini_direct(prompt)

        raise RuntimeError("Ne ProviderRegistry ne de gemini_key verildi.")

    async def _call_gemini_direct(self, prompt: str) -> str:
        """Geriye uyumluluk için doğrudan Gemini çağrısı."""
        try:
            from google import genai       # type: ignore
            from google.genai import types # type: ignore
        except ImportError:
            raise RuntimeError("google-genai paketi bulunamadı.")

        client = genai.Client(api_key=self._gemini_key)

        def _sync() -> str:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.3,
                    max_output_tokens=800,
                ),
            )
            return response.text or ""

        return await asyncio.to_thread(_sync)

    # ------------------------------------------------------------------
    # Parse (İç)
    # ------------------------------------------------------------------

    def _parse_response(self, raw_text: str, replan_index: int) -> ReplanResult:
        """AI yanıtını parse eder ve ReplanResult döner."""
        text = raw_text.strip()
        if text.startswith("```"):
            text = "\n".join(
                line for line in text.splitlines()
                if not line.strip().startswith("```")
            ).strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.error(f"[Replanner] JSON parse hatası: {e} — ham: {raw_text[:80]}")
            raise ReplanParseError(f"Replan yanıtı JSON değil: {e}") from e

        raw_steps: list[dict] = data.get("revised_steps", [])
        if not raw_steps:
            raise ReplanParseError("AI replan için sıfır adım döndürdü.")

        # Maksimum adım kısıtı
        raw_steps = raw_steps[: self.MAX_NEW_STEPS]

        new_steps = [
            StepRecord(
                index=i,           # tracker.apply_replan offset ekler
                title=s.get("title", f"Yeni Adım {i+1}"),
                description=s.get("description", ""),
                tool=s.get("tool", ""),
                tool_args=s.get("tool_args", {}),
            )
            for i, s in enumerate(raw_steps)
        ]

        result = ReplanResult(
            new_steps=new_steps,
            replan_reason=data.get("replan_reason", ""),
            strategy=data.get("strategy", ""),
            replan_index=replan_index,
        )

        logger.info(
            f"[Replanner] Parse tamamlandı: {len(new_steps)} yeni adım, "
            f"strateji: '{result.strategy[:60]}'"
        )
        return result


# ---------------------------------------------------------------------------
# Bağımsız Test
# ---------------------------------------------------------------------------

async def _demo() -> None:
    import json as _json

    from ai_core.task_state import TaskStateTracker, TaskStatus

    print("\n🔄  TaskReplanner Demo Başlatılıyor...")
    print("─" * 50)

    # Mock registry — API gerektirmez
    class MockRegistry:
        async def complete_json(self, prompt: str, **kwargs):
            class Resp:
                success = True
                text = _json.dumps({
                    "revised_steps": [
                        {"index": 0, "title": "Alternatif web araması yap",
                         "description": "Farklı bir arama motoru kullan",
                         "tool": "web_search", "tool_args": {"engine": "bing"}},
                        {"index": 1, "title": "Sonucu kullanıcıya bildir",
                         "description": "Sesli bildir", "tool": "", "tool_args": {}},
                    ],
                    "replan_reason": "İlk arama başarısız oldu, alternatif motor deneniyor",
                    "strategy": "Farklı arama motoru ile devam",
                })
            return Resp()

    replanner = TaskReplanner(provider_registry=MockRegistry())

    # Tracker hazırla
    tracker = TaskStateTracker(goal="Hava durumunu öğren")
    tracker.set_steps([
        {"index": 0, "title": "Web araması yap", "description": "Hava ara", "tool": "web_search"},
        {"index": 1, "title": "Sonucu analiz et", "description": "Veriyi işle", "tool": ""},
        {"index": 2, "title": "Kullanıcıya bildir", "description": "Sesli söyle", "tool": ""},
    ])
    tracker.transition(TaskStatus.PLANNING)
    tracker.transition(TaskStatus.RUNNING)
    tracker.step_start()
    tracker.step_failed("Bağlantı zaman aşımı")

    print(f"\n1. Replan öncesi: {tracker}")

    # Replan
    tracker.replan_started()
    failed_step = tracker.snapshot().steps[0]

    try:
        result = await replanner.replan_and_apply(
            tracker=tracker,
            failed_step=failed_step,
            failure_reason="Bağlantı zaman aşımı",
        )
        tracker.transition(TaskStatus.RUNNING)

        print(f"\n2. Replan sonrası: {tracker}")
        print(f"   Strateji  : {result.strategy}")
        print(f"   Sebep     : {result.replan_reason}")
        print(f"   Yeni adımlar:")
        for s in result.new_steps:
            print(f"     [{s.index}] {s.title}")

    except ReplanLimitError as e:
        print(f"   ❌ Limit aşıldı: {e}")
    except ReplanParseError as e:
        print(f"   ❌ Parse hatası: {e}")

    # Replan limit testi
    print(f"\n3. Replan limit testi (maks {tracker.MAX_REPLAN})...")
    for i in range(tracker.MAX_REPLAN):
        if not tracker.can_replan:
            break
        tracker.replan_started()
        try:
            await replanner.replan_and_apply(tracker, failed_step, f"Test hatası {i+1}")
            tracker.transition(TaskStatus.RUNNING)
        except ReplanLimitError:
            pass

    try:
        tracker.replan_started()
        await replanner.replan(tracker, failed_step, "Son deneme")
        print("   ❌ ReplanLimitError fırlatılmadı!")
    except ReplanLimitError as e:
        print(f"   ✅ ReplanLimitError doğru fırlatıldı: {str(e)[:60]}")

    print("\n✅ TaskReplanner demo tamamlandı.")


if __name__ == "__main__":
    asyncio.run(_demo())
