"""
ai_core/task_state.py
======================
SIRIUS görev sistemi için merkezi durum makinesi.

OpenGuider'ın JS'de dağınık hâlde tutuğu state yönetimini
(orchestrator.js içindeki this.currentStep, this.status, this.plan
değişkenleri) Pydantic modelleriyle tek dosyada toplar.

Bu modül:
    - Tüm durum enum'larını tanımlar        (TaskStatus, StepStatus, TransitionError)
    - Geçerli durum geçişlerini zorlar       (StateMachine)
    - Görev + adım durumunu takip eder       (TaskStateTracker)
    - Geçmiş kayıt tutar                     (TaskHistory)
    - Serialization/deserialization sağlar   (JSON ↔ Pydantic)

orchestrator.py bu modülü şöyle kullanır:
    tracker = TaskStateTracker(plan)
    tracker.transition(TaskStatus.RUNNING)
    tracker.step_done(result="Tamamlandı")
    tracker.step_failed(reason="Zaman aşımı")
    tracker.replan_started()

JS → Python dönüşüm:
    this.status = "running"    →  tracker.transition(TaskStatus.RUNNING)
    this.currentStep++         →  tracker.advance_step()
    if (verdict.success) {}    →  tracker.step_done(result)
    EventEmitter("replan")     →  tracker.replan_started() + asyncio.Event
"""

from __future__ import annotations

import logging
import time
import uuid
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

logger = logging.getLogger("sirius.task_state")


# ---------------------------------------------------------------------------
# Enum'lar
# ---------------------------------------------------------------------------

class TaskStatus(str, Enum):
    """
    Bir görevin olası durumları ve izin verilen geçişler.

    Geçiş diyagramı:
        QUEUED
          ↓
        PLANNING ──(hata)──→ FAILED
          ↓
        RUNNING ←──────────── REPLANNING
          ↓           ↑            ↑
        AWAITING_CONFIRM    (değerlendirici karar verir)
          ↓
        RUNNING
          ↓
        DONE / FAILED / CANCELLED
    """
    QUEUED            = "queued"
    PLANNING          = "planning"
    RUNNING           = "running"
    AWAITING_CONFIRM  = "awaiting_confirm"
    REPLANNING        = "replanning"
    DONE              = "done"
    FAILED            = "failed"
    CANCELLED         = "cancelled"

    @property
    def is_terminal(self) -> bool:
        """Bu durumdan başka duruma geçilemez."""
        return self in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED)

    @property
    def is_active(self) -> bool:
        """Görev hâlâ çalışıyor mu?"""
        return self in (
            TaskStatus.PLANNING,
            TaskStatus.RUNNING,
            TaskStatus.AWAITING_CONFIRM,
            TaskStatus.REPLANNING,
        )


class StepStatus(str, Enum):
    """Tek bir adımın durumu."""
    PENDING  = "pending"
    RUNNING  = "running"
    DONE     = "done"
    FAILED   = "failed"
    SKIPPED  = "skipped"
    RETRYING = "retrying"

    @property
    def is_terminal(self) -> bool:
        return self in (StepStatus.DONE, StepStatus.FAILED, StepStatus.SKIPPED)


class TransitionError(Exception):
    """Geçersiz durum geçişi denendiğinde fırlatılır."""
    pass


# ---------------------------------------------------------------------------
# Geçerli Durum Geçişleri
# ---------------------------------------------------------------------------

# {mevcut_durum: {geçilebilecek_durumlar}}
VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.QUEUED: {
        TaskStatus.PLANNING,
        TaskStatus.CANCELLED,
    },
    TaskStatus.PLANNING: {
        TaskStatus.RUNNING,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.RUNNING: {
        TaskStatus.AWAITING_CONFIRM,
        TaskStatus.REPLANNING,
        TaskStatus.DONE,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.AWAITING_CONFIRM: {
        TaskStatus.RUNNING,
        TaskStatus.CANCELLED,
    },
    TaskStatus.REPLANNING: {
        TaskStatus.RUNNING,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    # Terminal durumlardan geçiş yok
    TaskStatus.DONE:      set(),
    TaskStatus.FAILED:    set(),
    TaskStatus.CANCELLED: set(),
}


# ---------------------------------------------------------------------------
# Tarihsel Kayıt Modelleri
# ---------------------------------------------------------------------------

class StatusEvent(BaseModel):
    """Bir durum geçişinin kaydı."""
    from_status: TaskStatus
    to_status:   TaskStatus
    timestamp:   float = Field(default_factory=time.time)
    reason:      str   = ""


class StepRecord(BaseModel):
    """
    Tek bir adımın tam yaşam kaydı.
    orchestrator.py'deki TaskStep'in genişletilmiş hali —
    tüm deneme geçmişini içerir.
    """
    step_id:     str        = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    index:       int        = 0
    title:       str        = ""
    description: str        = ""
    tool:        str        = ""
    tool_args:   dict       = Field(default_factory=dict)
    status:      StepStatus = StepStatus.PENDING

    # Çalışma geçmişi
    attempts:    int        = 0
    results:     list[str]  = Field(default_factory=list)   # her denemeden sonuç
    errors:      list[str]  = Field(default_factory=list)   # her hatanın sebebi
    started_at:  Optional[float] = None
    finished_at: Optional[float] = None

    @property
    def last_result(self) -> str:
        return self.results[-1] if self.results else ""

    @property
    def last_error(self) -> str:
        return self.errors[-1] if self.errors else ""

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.started_at and self.finished_at:
            return round(self.finished_at - self.started_at, 2)
        return None

    def start(self) -> None:
        self.status     = StepStatus.RUNNING
        self.attempts  += 1
        self.started_at = time.time()

    def succeed(self, result: str) -> None:
        self.status      = StepStatus.DONE
        self.finished_at = time.time()
        self.results.append(result)

    def fail(self, reason: str) -> None:
        self.status      = StepStatus.FAILED
        self.finished_at = time.time()
        self.errors.append(reason)

    def retry(self) -> None:
        self.status     = StepStatus.RETRYING
        self.started_at = time.time()

    def skip(self, reason: str = "") -> None:
        self.status      = StepStatus.SKIPPED
        self.finished_at = time.time()
        if reason:
            self.errors.append(f"Atlandı: {reason}")


class TaskSnapshot(BaseModel):
    """
    Bir görevin belirli bir andaki tam durumu.
    Orchestrator'ın JSON olarak serialize ettiği checkpoint formatı.
    long_term.json'a kaydedilebilir veya hata ayıklama için loglanabilir.
    """
    task_id:       str             = Field(default_factory=lambda: str(uuid.uuid4()))
    goal:          str             = ""
    context:       str             = ""
    status:        TaskStatus      = TaskStatus.QUEUED
    steps:         list[StepRecord]= Field(default_factory=list)
    current_step:  int             = 0
    replan_count:  int             = 0
    summary:       str             = ""
    history:       list[StatusEvent] = Field(default_factory=list)
    created_at:    float           = Field(default_factory=time.time)
    updated_at:    float           = Field(default_factory=time.time)

    @property
    def total_steps(self) -> int:
        return len(self.steps)

    @property
    def done_steps(self) -> int:
        return sum(1 for s in self.steps if s.status == StepStatus.DONE)

    @property
    def failed_steps(self) -> int:
        return sum(1 for s in self.steps if s.status == StepStatus.FAILED)

    @property
    def progress_pct(self) -> float:
        if not self.steps:
            return 0.0
        return round(self.done_steps / len(self.steps) * 100, 1)

    @property
    def elapsed_seconds(self) -> float:
        return round(time.time() - self.created_at, 1)

    @property
    def current_step_record(self) -> Optional[StepRecord]:
        if 0 <= self.current_step < len(self.steps):
            return self.steps[self.current_step]
        return None

    def progress_line(self) -> str:
        """UI için kısa ilerleme satırı."""
        return (
            f"[{self.done_steps}/{self.total_steps}] "
            f"{self.progress_pct}% — {self.status.upper()} "
            f"({self.elapsed_seconds}s)"
        )


# ---------------------------------------------------------------------------
# StateMachine — Geçiş Kurallarını Uygular
# ---------------------------------------------------------------------------

class StateMachine:
    """
    Görev durum geçişlerini kurallarla yöneten sınıf.

    JS'deki dağınık if/else zinciri:
        if (this.status === 'running') { this.status = 'done' }

    Python'da kurala dayalı:
        machine.transition(current, TaskStatus.DONE)
        # VALID_TRANSITIONS kontrolü → geçersizse TransitionError
    """

    def validate(
        self,
        current: TaskStatus,
        target: TaskStatus,
        strict: bool = True,
    ) -> bool:
        """
        current → target geçişinin geçerli olup olmadığını kontrol eder.

        strict=True:  Geçersizse TransitionError fırlatır.
        strict=False: Geçersizse False döner (sessiz kontrol).
        """
        allowed = VALID_TRANSITIONS.get(current, set())
        if target in allowed:
            return True

        msg = (
            f"Geçersiz durum geçişi: {current.value} → {target.value}. "
            f"İzin verilenler: {[s.value for s in allowed]}"
        )
        if strict:
            raise TransitionError(msg)
        logger.warning(f"[StateMachine] {msg}")
        return False

    def can_transition(self, current: TaskStatus, target: TaskStatus) -> bool:
        """Sessiz geçiş kontrolü."""
        return self.validate(current, target, strict=False)

    def what_can_follow(self, current: TaskStatus) -> list[TaskStatus]:
        """Bu durumdan hangi durumlara geçilebileceğini döner."""
        return list(VALID_TRANSITIONS.get(current, set()))


# ---------------------------------------------------------------------------
# TaskStateTracker — Ana Sınıf
# ---------------------------------------------------------------------------

class TaskStateTracker:
    """
    Bir görevin tüm durum yönetimini tek noktada yönetir.

    orchestrator.py bu sınıfı şöyle kullanır:

        # Plan oluşturulduktan sonra:
        tracker = TaskStateTracker(task_id, goal)
        tracker.set_steps(plan.steps)
        tracker.transition(TaskStatus.RUNNING)

        # Her adım döngüsünde:
        tracker.step_start()
        result = await execute_step(...)
        if success:
            tracker.step_done(result)
        else:
            tracker.step_failed(reason)

        # Replan gerekirse:
        tracker.replan_started()
        new_steps = await replanner.replan(...)
        tracker.apply_replan(new_steps)
        tracker.transition(TaskStatus.RUNNING)

        # Sonunda:
        snapshot = tracker.snapshot()
        print(snapshot.progress_line())
    """

    MAX_REPLAN = 3

    def __init__(self, task_id: str = "", goal: str = "", context: str = ""):
        self.task_id = task_id or str(uuid.uuid4())
        self._machine = StateMachine()
        self._snap = TaskSnapshot(
            task_id=self.task_id,
            goal=goal,
            context=context,
        )

    # ------------------------------------------------------------------
    # Durum Geçişleri
    # ------------------------------------------------------------------

    def transition(self, target: TaskStatus, reason: str = "") -> None:
        """
        Görevi yeni duruma taşır.
        Geçersiz geçişte TransitionError fırlatır.
        """
        current = self._snap.status
        self._machine.validate(current, target, strict=True)

        event = StatusEvent(
            from_status=current,
            to_status=target,
            reason=reason,
        )
        self._snap.history.append(event)
        self._snap.status     = target
        self._snap.updated_at = time.time()

        logger.debug(
            f"[Tracker:{self.task_id[:8]}] "
            f"{current.value} → {target.value}"
            + (f" ({reason})" if reason else "")
        )

    def transition_safe(self, target: TaskStatus, reason: str = "") -> bool:
        """TransitionError fırlatmadan geçiş dener. False döner başarısızsa."""
        try:
            self.transition(target, reason)
            return True
        except TransitionError:
            return False

    # ------------------------------------------------------------------
    # Adım Yönetimi
    # ------------------------------------------------------------------

    def set_steps(self, steps: list) -> None:
        """
        Planlayıcıdan gelen adım listesini kaydeder.
        steps: orchestrator.TaskStep listesi veya dict listesi
        """
        records: list[StepRecord] = []
        for s in steps:
            if isinstance(s, StepRecord):
                records.append(s)
            elif isinstance(s, dict):
                records.append(StepRecord(**s))
            else:
                # orchestrator.TaskStep uyumluluğu için duck-typing
                records.append(StepRecord(
                    index=getattr(s, "index", len(records)),
                    title=getattr(s, "title", ""),
                    description=getattr(s, "description", ""),
                    tool=getattr(s, "tool", ""),
                    tool_args=getattr(s, "tool_args", {}),
                ))
        self._snap.steps = records
        self._snap.current_step = 0
        self._snap.updated_at = time.time()
        logger.info(f"[Tracker:{self.task_id[:8]}] {len(records)} adım yüklendi.")

    def step_start(self) -> Optional[StepRecord]:
        """Mevcut adımı başlatır, StepRecord döner."""
        step = self._snap.current_step_record
        if step is None:
            logger.warning(f"[Tracker] step_start: geçersiz adım indeksi {self._snap.current_step}")
            return None
        step.start()
        self._snap.updated_at = time.time()
        logger.info(
            f"[Tracker:{self.task_id[:8]}] "
            f"▶ Adım {step.index}/{self._snap.total_steps - 1}: '{step.title}'"
        )
        return step

    def step_done(self, result: str) -> None:
        """Mevcut adımı başarılı olarak işaretler ve sonraki adıma geçer."""
        step = self._snap.current_step_record
        if step is None:
            return
        step.succeed(result)
        self._snap.current_step += 1
        self._snap.updated_at = time.time()
        logger.info(
            f"[Tracker:{self.task_id[:8]}] "
            f"✅ Adım {step.index} tamamlandı. "
            f"({self._snap.done_steps}/{self._snap.total_steps})"
        )

    def step_failed(self, reason: str) -> None:
        """Mevcut adımı başarısız olarak işaretler. Bir sonraki adıma GEÇMEz."""
        step = self._snap.current_step_record
        if step is None:
            return
        step.fail(reason)
        self._snap.updated_at = time.time()
        logger.warning(
            f"[Tracker:{self.task_id[:8]}] "
            f"❌ Adım {step.index} başarısız: {reason[:60]}"
        )

    def step_retry(self) -> None:
        """Mevcut adımı yeniden deneme durumuna geçirir."""
        step = self._snap.current_step_record
        if step:
            step.retry()
            self._snap.updated_at = time.time()
            logger.info(
                f"[Tracker:{self.task_id[:8]}] "
                f"🔁 Adım {step.index} yeniden deneniyor (deneme #{step.attempts})"
            )

    def step_skip(self, reason: str = "") -> None:
        """Mevcut adımı atlar ve sonraki adıma geçer."""
        step = self._snap.current_step_record
        if step:
            step.skip(reason)
        self._snap.current_step += 1
        self._snap.updated_at = time.time()

    def advance_step(self) -> None:
        """Sonraki adıma geçer (step_done/step_skip çağrılmadan)."""
        self._snap.current_step += 1
        self._snap.updated_at = time.time()

    def is_finished(self) -> bool:
        """Tüm adımlar işlendi mi?"""
        return self._snap.current_step >= self._snap.total_steps

    # ------------------------------------------------------------------
    # Replan Yönetimi
    # ------------------------------------------------------------------

    def replan_started(self) -> None:
        """Replan sürecini başlatır."""
        self.transition(TaskStatus.REPLANNING, "Değerlendirici replan istedi")

    def apply_replan(self, new_steps: list, replan_reason: str = "") -> None:
        """
        Replanner'dan gelen yeni adımları uygular.
        Tamamlanmış adımlar korunur, sadece PENDING olanlar değişir.
        """
        if self._snap.replan_count >= self.MAX_REPLAN:
            raise TransitionError(
                f"Maksimum replan sayısına ulaşıldı ({self.MAX_REPLAN})."
            )

        done = [s for s in self._snap.steps if s.status.is_terminal]
        new_records: list[StepRecord] = []
        for i, s in enumerate(new_steps):
            if isinstance(s, StepRecord):
                s.index = len(done) + i
                new_records.append(s)
            elif isinstance(s, dict):
                s["index"] = len(done) + i
                new_records.append(StepRecord(**s))
            else:
                new_records.append(StepRecord(
                    index=len(done) + i,
                    title=getattr(s, "title", f"Yeni Adım {i+1}"),
                    description=getattr(s, "description", ""),
                    tool=getattr(s, "tool", ""),
                    tool_args=getattr(s, "tool_args", {}),
                ))

        self._snap.steps        = done + new_records
        self._snap.current_step = len(done)
        self._snap.replan_count += 1
        self._snap.updated_at   = time.time()

        logger.info(
            f"[Tracker:{self.task_id[:8]}] "
            f"🔄 Replan #{self._snap.replan_count}: "
            f"{len(new_records)} yeni adım"
            + (f" — {replan_reason}" if replan_reason else "")
        )

    # ------------------------------------------------------------------
    # Görev Tamamlama
    # ------------------------------------------------------------------

    def finalize(self) -> None:
        """
        Tüm adımlar işlendiğinde görevi tamamlar.
        Başarısız adım sayısına göre DONE veya FAILED seçer.
        """
        failed = self._snap.failed_steps
        done   = self._snap.done_steps
        total  = self._snap.total_steps

        if failed == 0:
            self.transition(TaskStatus.DONE, "Tüm adımlar başarılı")
            self._snap.summary = (
                f"'{self._snap.goal}' {total} adımda tamamlandı "
                f"({self._snap.elapsed_seconds}s)."
            )
        elif done > 0:
            # Kısmen başarılı
            self.transition(TaskStatus.DONE, f"{failed} adım başarısız ama devam edildi")
            self._snap.summary = (
                f"'{self._snap.goal}' kısmen tamamlandı: "
                f"{done}/{total} adım başarılı, {failed} başarısız."
            )
        else:
            self.transition(TaskStatus.FAILED, "Tüm adımlar başarısız")
            self._snap.summary = f"'{self._snap.goal}' başarısız oldu."

        self._snap.updated_at = time.time()
        logger.info(f"[Tracker:{self.task_id[:8]}] 🏁 {self._snap.summary}")

    def cancel(self, reason: str = "") -> None:
        """Görevi iptal eder."""
        if not self._snap.status.is_terminal:
            self.transition_safe(TaskStatus.CANCELLED, reason or "İptal edildi")
            self._snap.summary = f"Görev iptal edildi: {reason}"

    # ------------------------------------------------------------------
    # Sorgulama
    # ------------------------------------------------------------------

    def snapshot(self) -> TaskSnapshot:
        """Anlık durum snapshot'ı döner (kopyası)."""
        return self._snap.model_copy(deep=True)

    @property
    def status(self) -> TaskStatus:
        return self._snap.status

    @property
    def current_step_index(self) -> int:
        return self._snap.current_step

    @property
    def replan_count(self) -> int:
        return self._snap.replan_count

    @property
    def can_replan(self) -> bool:
        return self._snap.replan_count < self.MAX_REPLAN

    def step_attempt_count(self) -> int:
        """Mevcut adımın kaç kez denendiğini döner."""
        step = self._snap.current_step_record
        return step.attempts if step else 0

    def to_json(self) -> str:
        """Tüm durumu JSON string'e çevirir (checkpoint için)."""
        return self._snap.model_dump_json(indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> "TaskStateTracker":
        """JSON string'den tracker yükler (checkpoint geri yükleme)."""
        snap = TaskSnapshot.model_validate_json(json_str)
        tracker = cls(task_id=snap.task_id, goal=snap.goal, context=snap.context)
        tracker._snap = snap
        return tracker

    def __repr__(self) -> str:
        s = self._snap
        return (
            f"<TaskStateTracker id={self.task_id[:8]} "
            f"status={s.status.value} "
            f"step={s.current_step}/{s.total_steps} "
            f"replan={s.replan_count}>"
        )


# ---------------------------------------------------------------------------
# Bağımsız Test
# ---------------------------------------------------------------------------

def _demo() -> None:
    print("\n⚙️  TaskStateTracker Demo Başlatılıyor...")
    print("─" * 50)

    # Tracker oluştur
    tracker = TaskStateTracker(goal="İstanbul hava durumunu öğren ve bildir")
    print(f"\n1. Başlangıç: {tracker}")

    # Adımlar yükle
    steps_raw = [
        {"index": 0, "title": "Web araması yap",    "description": "Hava durumu ara",   "tool": "web_search"},
        {"index": 1, "title": "Sonucu analiz et",   "description": "Veriyi işle",        "tool": ""},
        {"index": 2, "title": "Kullanıcıya bildir", "description": "Sesli bildir",       "tool": ""},
    ]
    tracker.set_steps(steps_raw)
    print(f"2. Adımlar yüklendi: {tracker}")

    # QUEUED → PLANNING → RUNNING
    tracker.transition(TaskStatus.PLANNING)
    tracker.transition(TaskStatus.RUNNING)
    print(f"\n3. Çalışmaya başladı: {tracker.status.value}")

    # Adım 0: başarılı
    tracker.step_start()
    tracker.step_done("İstanbul: 18°C, parçalı bulutlu")
    print(f"4. Adım 0 tamamlandı. İlerleme: {tracker.snapshot().progress_line()}")

    # Adım 1: retry → başarılı
    tracker.step_start()
    tracker.step_retry()
    tracker.step_start()
    tracker.step_done("Veri işlendi: sıcaklık 18°C")
    print(f"5. Adım 1 (retry sonrası) tamamlandı.")

    # Replan dene
    print(f"\n6. Replan testi...")
    tracker.replan_started()
    new_steps = [
        {"index": 2, "title": "Kısa özet hazırla", "description": "Tek cümle özet", "tool": ""},
        {"index": 3, "title": "Sesli bildir",       "description": "Kullanıcıya söyle", "tool": ""},
    ]
    tracker.apply_replan(new_steps, replan_reason="Kullanıcı daha kısa özet istedi")
    tracker.transition(TaskStatus.RUNNING)
    print(f"   Replan sonrası: {tracker}")

    # Kalan adımları tamamla
    while not tracker.is_finished():
        step = tracker.step_start()
        tracker.step_done(f"'{step.title}' tamamlandı.")

    # Finalize
    tracker.finalize()
    snap = tracker.snapshot()
    print(f"\n7. Final durum:")
    print(f"   Status   : {snap.status.value.upper()}")
    print(f"   Özet     : {snap.summary}")
    print(f"   İlerleme : {snap.progress_line()}")
    print(f"   Geçmiş   : {[e.to_status.value for e in snap.history]}")

    # JSON serialize/deserialize test
    json_str = tracker.to_json()
    restored = TaskStateTracker.from_json(json_str)
    print(f"\n8. JSON roundtrip: {restored}")

    # Geçersiz geçiş testi
    print(f"\n9. Geçersiz geçiş testi (DONE → RUNNING)...")
    try:
        tracker.transition(TaskStatus.RUNNING)
        print("   ❌ Hata: Exception fırlatılmadı!")
    except TransitionError as e:
        print(f"   ✅ TransitionError doğru fırlatıldı: {e}")

    print("\n✅ TaskStateTracker demo tamamlandı.")


if __name__ == "__main__":
    _demo()
