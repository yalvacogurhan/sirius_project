"""
ai_core/orchestrator.py  —  NİHAİ VERSİYON
============================================
SIRIUS Görev Orkestratörü — tüm fazların birleştiği merkez.

Bu dosya proje boyunca yazılan tüm modülleri kullanır:

    Faz 1 (bu dosyanın ilk versiyonu):
        TaskPlanner  → AI'ya hedef → yapılandırılmış plan

    Faz 4 (şimdi TAM entegre):
        TaskStateTracker  → Pydantic durum makinesi, adım/replan geçişleri
        StepEvaluator     → iki katmanlı (kural + AI) adım değerlendirme
        TaskReplanner     → akıllı yeniden planlama, tamamlanmış adımları korur

    Faz 5 (PluginDispatcher):
        PluginDispatcher.as_executor() → tüm SIRIUS + browser tool'ları

    Faz 3 (ProviderRegistry):
        ProviderRegistry → Gemini/Groq/OpenAI/Ollama otomatik fallback

OpenGuider JS → Python dönüşüm özeti:
    orchestrator.js (Promise + EventEmitter)  →  asyncio coroutine + asyncio.Event
    planner.js (zod schema + JSON)            →  Pydantic TaskPlan + StepRecord
    evaluator.js (JSON verdict)               →  EvalResult (Pydantic)
    replanner.js (plan mutation)              →  tracker.apply_replan()
    session state (mutable JS object)         →  TaskStateTracker (immutable snapshots)
    IPC channel (Electron)                    →  asyncio.Queue + on_status_change callback

SIRIUS main.py entegrasyonu:
    from ai_core.orchestrator import TaskOrchestrator

    self.orchestrator = TaskOrchestrator(
        provider_registry = self.provider_registry,   # Faz 3
        tool_executor     = self.dispatcher.as_executor(),  # Faz 5
        on_status_change  = self._on_orchestrator_status,
    )

    task_id = await self.orchestrator.submit(goal="Hava durumunu öğren")
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Callable, Coroutine, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Faz 4 modülleri
# ---------------------------------------------------------------------------
from ai_core.task_state import (
    TaskStatus,
    StepStatus,
    StepRecord,
    TaskSnapshot,
    TaskStateTracker,
    TransitionError,
)
from ai_core.evaluator import StepEvaluator, EvalResult, EvalContext
from ai_core.replanner import TaskReplanner, ReplanLimitError, ReplanParseError

# ---------------------------------------------------------------------------
logger = logging.getLogger("sirius.orchestrator")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(
        logging.Formatter(
            "[%(asctime)s] [%(name)s] %(levelname)s — %(message)s", "%H:%M:%S"
        )
    )
    logger.addHandler(_h)
logger.setLevel(logging.DEBUG)

# Tip alias — Faz 5 PluginDispatcher.as_executor() ile uyumlu
ToolExecutor = Callable[[str, dict], Coroutine[Any, Any, str]]


# ---------------------------------------------------------------------------
# Planlayıcı Pydantic Modelleri (geriye uyumluluk + ProviderRegistry desteği)
# ---------------------------------------------------------------------------

class TaskStep(BaseModel):
    """
    Planlayıcıdan gelen tek adım.
    TaskStateTracker.set_steps() duck-typing ile StepRecord'a çevirir.
    """
    step_id:     str   = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    index:       int   = 0
    title:       str   = ""
    description: str   = ""
    tool:        str   = ""
    tool_args:   dict  = Field(default_factory=dict)


class TaskPlan(BaseModel):
    """Planlayıcıdan dönen yapılandırılmış plan."""
    task_id:      str            = Field(default_factory=lambda: str(uuid.uuid4()))
    goal:         str            = ""
    context:      str            = ""
    steps:        list[TaskStep] = Field(default_factory=list)
    summary_plan: str            = ""


# ---------------------------------------------------------------------------
# Prompt Builder'lar
# ---------------------------------------------------------------------------

def _build_planning_prompt(goal: str, context: str) -> str:
    ctx_block = f"\n\nMevcut Bağlam:\n{context}" if context else ""
    return f"""Sen bir yapay zeka görev planlayıcısısın. Kullanıcının hedefini analiz et ve sıralı, somut adımlara böl.

HEDEF: {goal}{ctx_block}

Aşağıdaki JSON formatında yanıt ver. Başka hiçbir şey yazma, sadece JSON döndür:
{{
  "steps": [
    {{
      "index": 0,
      "title": "Kısa adım başlığı",
      "description": "Bu adımda tam olarak ne yapılacağının açıklaması",
      "tool": "tool_ismi_veya_bos_string",
      "tool_args": {{}}
    }}
  ],
  "summary_plan": "Tüm planın tek cümlelik özeti"
}}

KURALLAR:
- Adımları atomik ve doğrulanabilir tut (her adım tek bir işlem).
- Maksimum 8 adım.
- tool alanına SIRIUS tool isimlerini yaz:
    open_app, web_search, weather_report, send_message, youtube_video,
    computer_settings, screen_process, flight_finder, save_memory,
    find_on_screen, switch_ai_provider,
    browser_action, browser_search, browser_navigate, browser_extract,
    browser_click, browser_screenshot, browser_scroll, browser_fill_form.
- Eğer adım bir tool gerektirmiyorsa tool alanını boş string bırak.
- Türkçe yaz."""


def _build_guidance_prompt(goal: str, step_title: str, step_description: str) -> str:
    """Tool olmayan adımlar için kullanıcıya rehberlik metni üretir."""
    return f"""Kullanıcıya şu adımı nasıl yapacağını kısaca ve net olarak anlat (Türkçe, 1-2 cümle):

Hedef: {goal}
Adım: {step_title}
Açıklama: {step_description}

Sadece yönlendirme metnini yaz, JSON veya başka format yok."""


# ---------------------------------------------------------------------------
# TaskPlanner
# ---------------------------------------------------------------------------

class TaskPlanner:
    """
    Hedefi yapılandırılmış adımlara bölen planlayıcı.

    ProviderRegistry ile çalışır (önerilen) veya geriye uyumluluk için
    doğrudan gemini_key kabul eder.
    """

    MAX_STEPS = 8

    def __init__(
        self,
        provider_registry=None,  # ai_core.providers.ProviderRegistry
        gemini_key: str = "",
    ):
        self._registry   = provider_registry
        self._gemini_key = gemini_key

    async def plan(self, goal: str, context: str = "") -> TaskPlan:
        """
        Hedefi AI'ya gönderir, TaskPlan döner.
        """
        logger.info(f"[Planner] 🗺  Plan oluşturuluyor: '{goal[:60]}'")
        prompt = _build_planning_prompt(goal, context)

        try:
            raw_text = await self._call_ai_json(prompt)
        except Exception as e:
            logger.error(f"[Planner] AI çağrısı başarısız: {e}")
            raise RuntimeError(f"Plan oluşturulamadı: {e}") from e

        # JSON parse
        clean = raw_text.strip()
        if clean.startswith("```"):
            clean = "\n".join(
                l for l in clean.splitlines() if not l.strip().startswith("```")
            ).strip()

        try:
            data = json.loads(clean)
        except json.JSONDecodeError as e:
            raise ValueError(f"Plan JSON'ı geçersiz: {e}\nHam yanıt: {raw_text[:200]}") from e

        raw_steps: list[dict] = data.get("steps", [])
        if not raw_steps:
            raise ValueError("AI sıfır adım döndürdü — hedef çok belirsiz olabilir.")

        raw_steps = raw_steps[: self.MAX_STEPS]

        steps = [
            TaskStep(
                index=i,
                title=s.get("title", f"Adım {i + 1}"),
                description=s.get("description", ""),
                tool=s.get("tool", ""),
                tool_args=s.get("tool_args", {}),
            )
            for i, s in enumerate(raw_steps)
        ]

        plan = TaskPlan(
            goal=goal,
            context=context,
            steps=steps,
            summary_plan=data.get("summary_plan", ""),
        )
        logger.info(f"[Planner] ✅ {len(steps)} adımlık plan: {plan.summary_plan}")
        return plan

    async def _call_ai_json(self, prompt: str) -> str:
        """ProviderRegistry veya doğrudan Gemini ile JSON yanıt alır."""
        if self._registry is not None:
            resp = await self._registry.complete_json(prompt, max_tokens=1500)
            if resp.success:
                return resp.text
            raise RuntimeError(resp.error)

        if self._gemini_key:
            return await self._call_gemini_direct(prompt)

        raise RuntimeError("Provider registry veya gemini_key gerekli.")

    async def _call_gemini_direct(self, prompt: str) -> str:
        """Geriye uyumluluk: doğrudan google.genai çağrısı."""
        try:
            from google import genai          # type: ignore
            from google.genai import types    # type: ignore
        except ImportError:
            raise RuntimeError("google-genai paketi bulunamadı: pip install google-genai")

        client = genai.Client(api_key=self._gemini_key)

        def _sync() -> str:
            r = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2,
                    max_output_tokens=1500,
                ),
            )
            return r.text or ""

        return await asyncio.to_thread(_sync)


# ---------------------------------------------------------------------------
# TaskOrchestrator — NİHAİ VERSİYON
# ---------------------------------------------------------------------------

class TaskOrchestrator:
    """
    SIRIUS'un görev orkestratörü — projenin beyni.

    Tüm fazları birleştirir:
        ┌─────────────────────────────────────────────────────────┐
        │  submit(goal)                                           │
        │       ↓                                                 │
        │  TaskPlanner.plan()          [Faz 1 + ProviderRegistry] │
        │       ↓                                                 │
        │  TaskStateTracker.set_steps() [Faz 4]                  │
        │       ↓                                                 │
        │  ┌── step döngüsü ──────────────────────────────────┐  │
        │  │  PluginDispatcher.dispatch() [Faz 5]              │  │
        │  │       ↓                                           │  │
        │  │  StepEvaluator.evaluate()   [Faz 4]               │  │
        │  │       ↓                                           │  │
        │  │  success → tracker.step_done()                    │  │
        │  │  retry   → tracker.step_retry() + bekle           │  │
        │  │  replan  → TaskReplanner.replan_and_apply() [Faz4]│  │
        │  │  fail    → tracker.step_skip()                    │  │
        │  └───────────────────────────────────────────────────┘  │
        │       ↓                                                 │
        │  tracker.finalize()                                     │
        │       ↓                                                 │
        │  on_status_change(snapshot)   [UI güncelleme]          │
        └─────────────────────────────────────────────────────────┘
    """

    def __init__(
        self,
        provider_registry=None,            # Faz 3: ProviderRegistry
        gemini_key: str = "",              # geriye uyumluluk
        tool_executor: Optional[ToolExecutor] = None,  # Faz 5: PluginDispatcher.as_executor()
        max_step_attempts: int = 2,
        step_timeout_seconds: float = 60.0,
        require_confirm: bool = False,
        on_status_change: Optional[Callable[[TaskSnapshot], None]] = None,
    ):
        """
        provider_registry:    ProviderRegistry (Faz 3). Gemini/Groq/OpenAI/Ollama.
        gemini_key:           Geriye uyumluluk — registry yoksa kullanılır.
        tool_executor:        PluginDispatcher.as_executor() (Faz 5).
        max_step_attempts:    Her adım için maksimum deneme sayısı.
        step_timeout_seconds: Bir adımın maksimum süresi (saniye).
        require_confirm:      True ise her adımdan önce onay bekler.
        on_status_change:     Durum değişince çağrılan callback (UI için).
        """
        self._registry      = provider_registry
        self.gemini_key     = gemini_key     # apply_keys() günceller
        self.tool_executor  = tool_executor
        self.max_attempts   = max_step_attempts
        self.step_timeout   = step_timeout_seconds
        self.require_confirm = require_confirm
        self.on_status_change = on_status_change

        # Alt modüller — Faz 4
        self._planner   = TaskPlanner(
            provider_registry=provider_registry,
            gemini_key=gemini_key,
        )
        self._evaluator = StepEvaluator(
            provider_registry=provider_registry,
            gemini_key=gemini_key,
        )
        self._replanner = TaskReplanner(
            provider_registry=provider_registry,
            gemini_key=gemini_key,
        )

        # Aktif görev takibi
        # task_id → TaskStateTracker
        self._trackers:  dict[str, TaskStateTracker]  = {}
        # task_id → asyncio.Task
        self._tasks:     dict[str, asyncio.Task]       = {}
        # task_id → onay asyncio.Event (require_confirm için)
        self._confirms:  dict[str, asyncio.Event]      = {}
        # Eş zamanlılık kilidi
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Anahtarları Güncelle (apply_keys çağrısı)
    # ------------------------------------------------------------------

    def update_keys(self, provider_registry=None, gemini_key: str = "") -> None:
        """
        SiriusController.apply_keys() içinden çağrılır.
        Tüm alt modüllerin anahtarlarını günceller.

        main.py apply_keys() içine ekle:
            self.orchestrator.update_keys(
                provider_registry=self.provider_registry,
                gemini_key=self.config_data.get("gemini_key", ""),
            )
        """
        if provider_registry is not None:
            self._registry = provider_registry
            self._planner._registry   = provider_registry
            self._evaluator._registry = provider_registry
            self._replanner._registry = provider_registry

        if gemini_key:
            self.gemini_key                 = gemini_key
            self._planner._gemini_key       = gemini_key
            self._evaluator._gemini_key     = gemini_key
            self._replanner._gemini_key     = gemini_key

        logger.info("[Orchestrator] Anahtarlar güncellendi.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def submit(self, goal: str, context: str = "") -> str:
        """
        Yeni görev kuyruğa alır, task_id döner.

        SIRIUS main.py kullanımı:
            task_id = await self.orchestrator.submit(
                goal=args.get("goal", ""),
                context=self.region_manager.context_as_string(),
            )
            return f"Görev başlatıldı (ID: {task_id[:8]})"
        """
        tracker = TaskStateTracker(goal=goal, context=context)
        task_id = tracker.task_id

        async with self._lock:
            self._trackers[task_id] = tracker
            self._confirms[task_id] = asyncio.Event()

        loop = asyncio.get_event_loop()
        task = loop.create_task(
            self._run(task_id),
            name=f"sirius_task_{task_id[:8]}"
        )
        self._tasks[task_id] = task
        task.add_done_callback(lambda t: self._on_task_done(task_id, t))

        logger.info(f"[Orchestrator] 📬 Görev alındı: {task_id[:8]} — '{goal[:50]}'")
        return task_id

    def confirm_step(self, task_id: str) -> None:
        """
        require_confirm=True modunda bir sonraki adıma geçişi onaylar.

        SiriusLive içinde kullanım:
            # Kullanıcı "devam et" dediğinde:
            self.controller.orchestrator.confirm_step(task_id)
        """
        ev = self._confirms.get(task_id)
        if ev:
            ev.set()
            logger.info(f"[Orchestrator] ✅ Adım onaylandı: {task_id[:8]}")
        else:
            logger.warning(f"[Orchestrator] Bilinmeyen task_id: {task_id[:8]}")

    async def cancel(self, task_id: str) -> None:
        """
        Çalışan görevi iptal eder.

        SiriusLive içinde kullanım:
            await self.controller.orchestrator.cancel(task_id)
        """
        task = self._tasks.get(task_id)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        tracker = self._trackers.get(task_id)
        if tracker:
            tracker.cancel("Kullanıcı tarafından iptal edildi.")
            self._notify(tracker.snapshot())

        logger.info(f"[Orchestrator] 🛑 Görev iptal edildi: {task_id[:8]}")

    def get_snapshot(self, task_id: str) -> Optional[TaskSnapshot]:
        """Görevin anlık snapshot'ını döner (UI sorgulama için)."""
        tracker = self._trackers.get(task_id)
        return tracker.snapshot() if tracker else None

    def get_status(self, task_id: str) -> Optional[TaskStatus]:
        """Görevin anlık durumunu döner."""
        tracker = self._trackers.get(task_id)
        return tracker.status if tracker else None

    def list_active(self) -> list[TaskSnapshot]:
        """Tüm aktif (terminal olmayan) görevlerin snapshot listesi."""
        return [
            t.snapshot()
            for t in self._trackers.values()
            if t.status.is_active
        ]

    def list_all(self) -> list[TaskSnapshot]:
        """Tüm görevlerin (aktif + tamamlanmış) snapshot listesi."""
        return [t.snapshot() for t in self._trackers.values()]

    # ------------------------------------------------------------------
    # Ana Çalışma Döngüsü (Private)
    # ------------------------------------------------------------------

    async def _run(self, task_id: str) -> None:
        """
        Görevin tam yaşam döngüsü.

        OpenGuider orchestrator.js'deki ana while döngüsünün
        Python + TaskStateTracker + StepEvaluator + TaskReplanner portu.
        """
        tracker = self._trackers[task_id]

        # ── 1. PLANLAMA ─────────────────────────────────────────────
        tracker.transition(TaskStatus.PLANNING)
        self._notify(tracker.snapshot())

        try:
            plan = await self._planner.plan(tracker._snap.goal, tracker._snap.context)
        except Exception as e:
            logger.error(f"[Orchestrator] Planlama hatası: {e}")
            tracker.transition_safe(TaskStatus.FAILED, f"Planlama başarısız: {e}")
            snap = tracker.snapshot()
            snap = snap.model_copy(update={"summary": f"Plan oluşturulamadı: {e}"})
            self._notify(snap)
            return

        # Adımları tracker'a yükle (duck-typing ile StepRecord'a çevirir)
        tracker.set_steps(plan.steps)
        tracker.transition(TaskStatus.RUNNING)
        self._notify(tracker.snapshot())

        # EvalContext — değerlendirici için üst bağlam
        eval_ctx = EvalContext(
            goal=plan.goal,
            total_steps=len(plan.steps),
        )

        # ── 2. ADIM DÖNGÜSÜ ─────────────────────────────────────────
        while not tracker.is_finished():
            snap     = tracker.snapshot()
            step_rec = snap.current_step_record

            if step_rec is None:
                logger.error(f"[Orchestrator] Geçersiz step indeksi: {snap.current_step}")
                break

            # ── 2a. Onay Modu ──────────────────────────────────────
            if self.require_confirm:
                tracker.transition_safe(TaskStatus.AWAITING_CONFIRM)
                self._notify(tracker.snapshot())
                logger.info(
                    f"[Orchestrator] ⏸  Onay bekleniyor: "
                    f"Adım {step_rec.index} — '{step_rec.title}'"
                )
                confirm_ev = self._confirms[task_id]
                confirm_ev.clear()
                try:
                    await asyncio.wait_for(confirm_ev.wait(), timeout=300.0)
                except asyncio.TimeoutError:
                    logger.warning("[Orchestrator] Onay zaman aşımı — görev iptal.")
                    tracker.cancel("Onay zaman aşımı")
                    self._notify(tracker.snapshot())
                    return
                tracker.transition_safe(TaskStatus.RUNNING)
                self._notify(tracker.snapshot())

            # ── 2b. Adımı Çalıştır ─────────────────────────────────
            eval_ctx.step_index  = step_rec.index
            eval_ctx.attempt_no  = step_rec.attempts + 1
            eval_ctx.previous_results = [
                s.last_result for s in snap.steps
                if s.status == StepStatus.DONE and s.last_result
            ]

            tracker.step_start()
            self._notify(tracker.snapshot())

            raw_result = await self._execute_step(step_rec, plan.goal)

            # ── 2c. Adımı Değerlendir (Faz 4: StepEvaluator) ───────
            # StepRecord duck-typing ile StepEvaluator tarafından kabul edilir
            verdict: EvalResult = await self._evaluator.evaluate(
                step_rec, raw_result, eval_ctx
            )

            logger.debug(
                f"[Orchestrator] Adım {step_rec.index} değerlendirme: "
                f"{verdict.verdict_label} (güven: {verdict.confidence:.0%})"
            )

            # ── 2d. Verdict'e Göre Karar ────────────────────────────

            if verdict.success:
                # ✅ Başarılı
                tracker.step_done(raw_result)
                self._notify(tracker.snapshot())

            elif verdict.should_retry and tracker.step_attempt_count() < self.max_attempts:
                # 🔁 Geçici hata — kısa bekleyip yeniden dene
                wait_s = 2.0 * tracker.step_attempt_count()
                logger.warning(
                    f"[Orchestrator] ⚠️  Adım {step_rec.index} retry "
                    f"({tracker.step_attempt_count()}/{self.max_attempts}) "
                    f"— {wait_s}s bekleniyor. Sebep: {verdict.reason[:60]}"
                )
                tracker.step_retry()
                self._notify(tracker.snapshot())
                await asyncio.sleep(wait_s)
                # Döngü başına dön — aynı adım tekrar çalıştırılacak

            elif verdict.replan_needed and tracker.can_replan:
                # 🔄 Durum değişti — yeniden planla (Faz 4: TaskReplanner)
                logger.info(
                    f"[Orchestrator] 🔄 Replan tetiklendi — "
                    f"Adım {step_rec.index}: {verdict.reason[:60]}"
                )
                tracker.step_failed(verdict.reason)
                tracker.replan_started()
                self._notify(tracker.snapshot())

                try:
                    replan_result = await self._replanner.replan_and_apply(
                        tracker=tracker,
                        failed_step=step_rec,
                        failure_reason=verdict.reason,
                        goal=plan.goal,
                    )
                    tracker.transition(TaskStatus.RUNNING)
                    self._notify(tracker.snapshot())
                    logger.info(
                        f"[Orchestrator] Replan uygulandı: "
                        f"{len(replan_result.new_steps)} yeni adım — "
                        f"{replan_result.strategy}"
                    )

                except ReplanLimitError as e:
                    logger.error(f"[Orchestrator] Replan limiti aşıldı: {e}")
                    tracker.transition_safe(TaskStatus.FAILED, str(e))
                    snap_final = tracker.snapshot()
                    self._notify(snap_final)
                    return

                except ReplanParseError as e:
                    logger.error(f"[Orchestrator] Replan parse hatası: {e}")
                    # Replan başarısız → adımı atla, devam et
                    tracker.step_skip(f"Replan başarısız: {e}")
                    tracker.transition_safe(TaskStatus.RUNNING)
                    self._notify(tracker.snapshot())

            else:
                # ❌ Başarısız + retry yok + replan yok → adımı atla
                reason_short = verdict.reason[:80]
                logger.warning(
                    f"[Orchestrator] ❌ Adım {step_rec.index} atlanıyor: {reason_short}"
                )
                tracker.step_failed(verdict.reason)
                tracker.advance_step()
                self._notify(tracker.snapshot())

        # ── 3. FİNALİZE ─────────────────────────────────────────────
        tracker.finalize()
        final_snap = tracker.snapshot()
        self._notify(final_snap)

        logger.info(
            f"[Orchestrator] 🏁 Görev bitti: {task_id[:8]} — "
            f"{final_snap.status.upper()} — {final_snap.summary}"
        )

    # ------------------------------------------------------------------
    # Adım Çalıştırma (Private)
    # ------------------------------------------------------------------

    async def _execute_step(self, step: StepRecord, goal: str) -> str:
        """
        Tek adımı çalıştırır.

        Faz 5 PluginDispatcher → tool varsa çağır
        Tool yoksa → AI'dan rehberlik metni al

        JS'deki:
            const result = await this.plugins[step.tool](step.toolArgs)
              .catch(e => `ERROR: ${e.message}`)

        Python:
            asyncio.wait_for(tool_executor(tool, args), timeout=...)
        """
        logger.info(
            f"[Orchestrator] ▶  Adım {step.index}: '{step.title}'"
            + (f" [tool: {step.tool}]" if step.tool else " [rehberlik]")
        )

        try:
            if step.tool and self.tool_executor:
                # ── Faz 5: PluginDispatcher üzerinden çalıştır ──────
                result = await asyncio.wait_for(
                    self.tool_executor(step.tool, step.tool_args),
                    timeout=self.step_timeout,
                )
                return str(result) if result is not None else "Tamamlandı."

            else:
                # ── Tool yok: AI rehberlik metni üret ───────────────
                return await self._generate_guidance(goal, step)

        except asyncio.TimeoutError:
            msg = f"Zaman aşımı ({self.step_timeout}s): '{step.title}'"
            logger.warning(f"[Orchestrator] ⏱  {msg}")
            return f"HATA: {msg}"

        except asyncio.CancelledError:
            raise  # Üst seviyeye ilet

        except Exception as e:
            msg = f"Adım çalıştırma hatası: {e}"
            logger.error(f"[Orchestrator] ❌ {msg}", exc_info=True)
            return f"HATA: {msg}"

    async def _generate_guidance(self, goal: str, step: StepRecord) -> str:
        """
        Tool olmayan adımlar için AI'dan rehberlik metni üretir.
        OpenGuider'ın "guide me" modunun Python karşılığı.
        """
        prompt = _build_guidance_prompt(goal, step.title, step.description)
        try:
            if self._registry is not None:
                resp = await self._registry.complete(prompt, max_tokens=200, temperature=0.3)
                if resp.success:
                    return resp.text.strip()
                return step.description  # fallback: orijinal açıklamayı dön

            if self.gemini_key:
                try:
                    from google import genai          # type: ignore
                    from google.genai import types    # type: ignore
                except ImportError:
                    return step.description

                client = genai.Client(api_key=self.gemini_key)

                def _sync() -> str:
                    r = client.models.generate_content(
                        model="gemini-2.5-flash",
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            temperature=0.3,
                            max_output_tokens=200,
                        ),
                    )
                    return r.text or step.description

                return await asyncio.to_thread(_sync)

            return step.description

        except Exception as e:
            logger.warning(f"[Orchestrator] Rehberlik üretme hatası: {e}")
            return step.description

    # ------------------------------------------------------------------
    # Bildirim & Temizlik (Private)
    # ------------------------------------------------------------------

    def _notify(self, snapshot: TaskSnapshot) -> None:
        """
        on_status_change callback'ini çağırır.
        JS'deki EventEmitter.emit('status', plan) → Python callback.
        UI güncellemesi (SiriusUI.update_status) buradan tetiklenir.
        """
        if self.on_status_change:
            try:
                self.on_status_change(snapshot)
            except Exception as e:
                logger.warning(f"[Orchestrator] on_status_change hatası: {e}")

    def _on_task_done(self, task_id: str, task: asyncio.Task) -> None:
        """
        asyncio.Task tamamlandığında çalışır.
        JS'deki Promise.finally() karşılığı.
        """
        self._tasks.pop(task_id, None)
        self._confirms.pop(task_id, None)

        if task.cancelled():
            logger.debug(f"[Orchestrator] Task {task_id[:8]} iptal edildi.")
        elif task.exception():
            exc = task.exception()
            logger.error(
                f"[Orchestrator] Task {task_id[:8]} beklenmedik hatayla bitti: {exc}",
                exc_info=exc,
            )
            # Tracker'ı FAILED yap
            tracker = self._trackers.get(task_id)
            if tracker and not tracker.status.is_terminal:
                tracker.transition_safe(TaskStatus.FAILED, str(exc))
                self._notify(tracker.snapshot())


# ---------------------------------------------------------------------------
# SIRIUS Entegrasyon Yardımcısı (geriye uyumluluk şim)
# ---------------------------------------------------------------------------

def make_sirius_tool_executor(controller: Any) -> ToolExecutor:
    """
    Geriye uyumluluk: SiriusController'ı doğrudan ToolExecutor'a sarar.

    Faz 5 PluginDispatcher kullanılıyorsa bu fonksiyona gerek yok.
    Sadece dispatcher kurulmadan önce geçiş için kullanılır.

    Önerilen kullanım (Faz 5 ile):
        self.orchestrator = TaskOrchestrator(
            tool_executor=self.dispatcher.as_executor(),
            ...
        )

    Eski kullanım (Faz 5 öncesi):
        self.orchestrator = TaskOrchestrator(
            tool_executor=make_sirius_tool_executor(self),
            ...
        )
    """
    async def _executor(tool_name: str, tool_args: dict) -> str:
        name = tool_name.lower().strip()

        dispatch: dict[str, Any] = {
            "open_app":          lambda a: controller.launcher.execute(a),
            "weather_report":    lambda a: controller.weather.execute(a),
            "web_search":        lambda a: controller.web.execute(a),
            "send_message":      lambda a: controller.messenger.execute(a),
            "youtube_video":     lambda a: controller.yt.execute(a),
            "computer_settings": lambda a: controller.sys_set.execute(
                a.get("action"), a.get("value")
            ),
            "screen_process":    lambda a: controller.vision.execute(a),
            "flight_finder":     lambda a: controller.travel.execute(a),
            "save_memory":       lambda a: (
                __import__("memory.memory_manager", fromlist=["update_memory"])
                .update_memory({
                    a.get("category", "notes"): {
                        a.get("key", ""): {"value": a.get("value", "")}
                    }
                }) or "Hafızaya kaydedildi."
            ),
        }

        handler = dispatch.get(name)
        if handler is None:
            return f"Bilinmeyen tool: '{name}'"

        try:
            result = await asyncio.to_thread(handler, tool_args)
            return str(result) if result is not None else "Tamamlandı."
        except Exception as e:
            return f"Tool hatası [{name}]: {e}"

    return _executor


# ---------------------------------------------------------------------------
# Bağımsız Test (python -m ai_core.orchestrator)
# ---------------------------------------------------------------------------

async def _demo() -> None:
    """
    Tüm Faz modüllerini mock'larla test eder — API anahtarı gerektirmez.
    """
    import os

    print("\n🚀  SIRIUS Orchestrator NİHAİ VERSİYON Demo")
    print("─" * 55)

    # ── Mock AI çağrısı ─────────────────────────────────────────────
    async def _mock_ai_json(prompt: str, **kw):
        """Planner, evaluator, replanner için sahte AI yanıtı."""
        class MockResp:
            success = True
            text = ""

        p = prompt.lower()

        if "summary_plan" in p or "adım" in p:
            MockResp.text = json.dumps({
                "steps": [
                    {"index": 0, "title": "Web araması yap",
                     "description": "Google'da hava durumunu ara",
                     "tool": "web_search", "tool_args": {"query": "İstanbul hava"}},
                    {"index": 1, "title": "Sonucu analiz et",
                     "description": "Gelen veriyi işle", "tool": "", "tool_args": {}},
                    {"index": 2, "title": "Kullanıcıya bildir",
                     "description": "Sesli olarak bildir", "tool": "", "tool_args": {}},
                ],
                "summary_plan": "Hava durumu araştırma görevi",
            })
        elif "success" in p or "başar" in p:
            MockResp.text = json.dumps({
                "success": True,
                "reason": "Araç başarıyla çalıştı.",
                "should_retry": False,
                "replan_needed": False,
                "confidence": 0.95,
            })
        else:
            MockResp.text = json.dumps({
                "revised_steps": [],
                "replan_reason": "Test",
                "strategy": "Alternatif yol",
            })
        return MockResp()

    async def _mock_complete(prompt: str, **kw):
        class R:
            success = True
            text    = "Bu adımı manuel olarak yapınız."
        return R()

    # ── Mock ProviderRegistry ────────────────────────────────────────
    class MockRegistry:
        async def complete_json(self, prompt, **kw):
            return await _mock_ai_json(prompt)
        async def complete(self, prompt, **kw):
            return await _mock_complete(prompt)

    # ── Mock ToolExecutor (Faz 5 yerine) ────────────────────────────
    async def mock_tool(tool_name: str, args: dict) -> str:
        await asyncio.sleep(0.05)
        return f"'{tool_name}' başarıyla çalıştı. Args: {list(args.keys())}"

    # ── Durum log ───────────────────────────────────────────────────
    status_log: list[str] = []

    def on_status(snap: TaskSnapshot) -> None:
        line = (
            f"  [{snap.status.upper():<18}] "
            f"Adım {snap.current_step}/{snap.total_steps} — "
            f"{snap.progress_pct}%"
        )
        if snap.summary:
            line += f" | {snap.summary[:50]}"
        status_log.append(line)
        print(line)

    # ── Orchestrator oluştur ─────────────────────────────────────────
    orch = TaskOrchestrator(
        provider_registry=MockRegistry(),
        tool_executor=mock_tool,
        require_confirm=False,
        max_step_attempts=2,
        step_timeout_seconds=10.0,
        on_status_change=on_status,
    )

    print("\n1. Normal görev testi:")
    task_id = await orch.submit(
        goal="İstanbul'un hava durumunu öğren ve bildir",
        context="Aktif Pencere: Chrome | Monitor: 1920x1080",
    )
    print(f"   Task ID: {task_id[:8]}")

    # Bitmesini bekle
    start = time.time()
    while task_id in orch._tasks and (time.time() - start) < 30:
        await asyncio.sleep(0.1)

    snap = orch.get_snapshot(task_id)
    if snap:
        print(f"\n   ✅ Final: {snap.status.upper()} — {snap.summary}")
        print(f"   📊 {snap.progress_line()}")
        print(f"   ⏱  Geçmiş: {[e.to_status.value for e in snap.history]}")

    # ── Detaylı adım raporu ──────────────────────────────────────────
    print("\n2. Adım detayları:")
    if snap:
        for s in snap.steps:
            icon = {"done": "✅", "failed": "❌", "skipped": "⏭", "pending": "⏳"}.get(
                s.status.value, "•"
            )
            print(f"   {icon} [{s.status:<8}] Adım {s.index}: {s.title}")
            if s.last_result:
                print(f"       Sonuç: {s.last_result[:60]}")

    # ── İptal testi ─────────────────────────────────────────────────
    print("\n3. İptal testi:")
    task_id2 = await orch.submit(goal="İptal edilecek görev")
    await asyncio.sleep(0.05)
    await orch.cancel(task_id2)
    snap2 = orch.get_snapshot(task_id2)
    if snap2:
        print(f"   Durum: {snap2.status.upper()} — Beklenen: CANCELLED ✅")

    # ── list_active / list_all ───────────────────────────────────────
    print(f"\n4. Toplam görev sayısı: {len(orch.list_all())}")
    print(f"   Aktif görev sayısı : {len(orch.list_active())}")

    print("\n✅ Orchestrator NİHAİ VERSİYON demo tamamlandı.")


if __name__ == "__main__":
    asyncio.run(_demo())