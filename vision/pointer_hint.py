"""
vision/pointer_hint.py
======================
CoordHint önbellekleme, öğrenme ve SIRIUS hafıza sistemi entegrasyonu.

Bu modül iki şey yapar:
    1. Daha önce bulunan koordinatları önbelleğe alır →
       Aynı hedef tekrar istendiğinde Gemini çağrısı yapmadan hızlıca döner.

    2. Başarılı koordinatları SIRIUS'un long_term.json hafızasına yazar →
       Uygulama yeniden başlasa bile "Kaydet butonu Chrome'da (245, 312)'deydi"
       bilgisi korunur.

OpenGuider'da bu yoktu. SIRIUS'un "Sonsuz Hafıza" özelliğiyle birleştirilen
özgün ekleme. OpenGuider sadece session boyunca koordinat tutar — SIRIUS kalıcı
öğrenir.

Hafıza yapısı (long_term.json içinde):
    {
      "coord_hints": {
        "chrome.exe::kaydet butonu": {
          "x": 245, "y": 312,
          "label": "Kaydet",
          "confidence": 0.94,
          "method": "ai",
          "hit_count": 7,
          "last_used": 1716000000.0,
          "app_title": "Belge1 - Google Chrome",
          "screen_hash": "1920x1080"
        }
      }
    }
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from typing import Optional

from pydantic import BaseModel, Field

from vision.coord_engine import CoordHint, ScreenRegion

# ---------------------------------------------------------------------------
logger = logging.getLogger("sirius.vision.pointer_hint")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(asctime)s] [%(name)s] %(levelname)s — %(message)s", "%H:%M:%S"))
    logger.addHandler(_h)
logger.setLevel(logging.DEBUG)

# Hafıza dosyası yolu — SIRIUS kök dizinindeki long_term.json
_MEMORY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "long_term.json"
)


# ---------------------------------------------------------------------------
# Veri Modelleri
# ---------------------------------------------------------------------------

class CachedHint(BaseModel):
    """
    Önbellekte veya hafızada saklanan genişletilmiş CoordHint.
    CoordHint'e ek olarak kullanım istatistikleri ve bağlam bilgisi içerir.
    """
    x:           int   = 0
    y:           int   = 0
    label:       str   = ""
    confidence:  float = 0.0
    method:      str   = "ai"

    # Önbellek meta verileri
    hit_count:   int   = 0           # kaç kez başarıyla kullanıldı
    miss_count:  int   = 0           # kaç kez geçersiz çıktı (koordinat kaydı)
    last_used:   float = Field(default_factory=time.time)
    created_at:  float = Field(default_factory=time.time)

    # Bağlam bilgisi — aynı "kaydet" butonu farklı uygulamalarda farklı yerde
    app_process: str   = ""          # chrome.exe, notepad.exe ...
    app_title:   str   = ""          # pencere başlığının ilk 40 karakteri
    screen_hash: str   = ""          # "1920x1080" — çözünürlük değişirse geçersiz say

    def to_coord_hint(self) -> CoordHint:
        """CachedHint'i CoordHint'e dönüştürür."""
        return CoordHint(
            x=self.x,
            y=self.y,
            label=self.label,
            confidence=self.confidence,
            method=self.method,
        )

    def is_stale(
        self,
        current_app: str = "",
        current_screen: str = "",
        max_age_hours: float = 48.0,
        max_miss_ratio: float = 0.4,
    ) -> bool:
        """
        Önbellek kaydının geçersiz (stale) olup olmadığını kontrol eder.

        Stale sayılır eğer:
            - Çok eski (max_age_hours geçmişse)
            - Çözünürlük değişmişse (farklı ekran)
            - Uygulama değişmişse
            - Miss oranı çok yüksekse (koordinat artık yanlış)
        """
        # Yaş kontrolü
        age_hours = (time.time() - self.last_used) / 3600.0
        if age_hours > max_age_hours:
            return True

        # Çözünürlük kontrolü
        if current_screen and self.screen_hash and current_screen != self.screen_hash:
            return True

        # Uygulama kontrolü
        if current_app and self.app_process and current_app.lower() != self.app_process.lower():
            return True

        # Miss oranı kontrolü
        total = self.hit_count + self.miss_count
        if total >= 3 and (self.miss_count / total) > max_miss_ratio:
            return True

        return False


class HintCacheEntry(BaseModel):
    """Önbellekte bir anahtar altındaki tam kayıt."""
    key:   str        = ""
    hints: list[CachedHint] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Önbellek Anahtarı Üretici
# ---------------------------------------------------------------------------

def _make_cache_key(
    target: str,
    app_process: str = "",
    app_title: str = "",
    loose: bool = False,
) -> str:
    """
    Önbellek anahtarı oluşturur.

    loose=False: Uygulama spesifik anahtar
        "chrome.exe::kaydet butonu"
    loose=True: Sadece hedef metni (uygulama bağımsız)
        "kaydet butonu"

    Anahtar normalleştirilir:
        - Küçük harf
        - Fazla boşluklar temizlenir
        - app_title'ın ilk 20 karakteri
    """
    target_norm = " ".join(target.lower().split())[:50]

    if loose or not app_process:
        return target_norm

    app_norm = app_process.lower().replace(".exe", "")
    title_norm = " ".join(app_title.lower().split())[:20]

    if title_norm:
        return f"{app_norm}::{title_norm}::{target_norm}"
    return f"{app_norm}::{target_norm}"


def _screen_hash(width: int, height: int) -> str:
    """Ekran çözünürlüğünü hash string'e çevirir."""
    return f"{width}x{height}"


# ---------------------------------------------------------------------------
# PointerHintCache — In-Memory LRU Önbellek
# ---------------------------------------------------------------------------

class PointerHintCache:
    """
    Session boyunca koordinatları bellekte tutar.
    OpenGuider'ın session state'inin Python karşılığı + LRU mantığı.

    JS'deki:
        this._hintCache = new Map()
        hintCache.set(key, {x, y, label, ...})

    Python:
        dict + max_size ile LRU (en az kullanılanı atar)
    """

    def __init__(self, max_size: int = 200):
        self.max_size = max_size
        self._cache: dict[str, CachedHint] = {}
        self._access_order: list[str] = []  # LRU sırası

    def get(self, key: str) -> Optional[CachedHint]:
        """Önbellekten hint alır. Bulunursa LRU'da öne taşır."""
        hint = self._cache.get(key)
        if hint:
            # LRU güncelle
            if key in self._access_order:
                self._access_order.remove(key)
            self._access_order.append(key)
            logger.debug(f"[Cache] HIT: '{key}' → ({hint.x}, {hint.y})")
        return hint

    def set(self, key: str, hint: CachedHint) -> None:
        """Önbelleğe hint ekler. Doluysa en az kullanılanı atar."""
        if key in self._cache:
            self._access_order.remove(key)
        elif len(self._cache) >= self.max_size:
            # LRU: en eski erişileni çıkar
            oldest_key = self._access_order.pop(0)
            del self._cache[oldest_key]
            logger.debug(f"[Cache] LRU evict: '{oldest_key}'")

        self._cache[key] = hint
        self._access_order.append(key)
        logger.debug(f"[Cache] SET: '{key}' → ({hint.x}, {hint.y})")

    def invalidate(self, key: str) -> None:
        """Belirli bir anahtarı geçersiz kılar."""
        if key in self._cache:
            del self._cache[key]
            if key in self._access_order:
                self._access_order.remove(key)
            logger.debug(f"[Cache] INVALIDATE: '{key}'")

    def invalidate_by_app(self, app_process: str) -> int:
        """Belirli bir uygulamaya ait tüm kayıtları temizler."""
        needle = app_process.lower().replace(".exe", "")
        to_remove = [k for k in self._cache if needle in k.lower()]
        for key in to_remove:
            self.invalidate(key)
        logger.info(f"[Cache] '{app_process}' için {len(to_remove)} kayıt temizlendi.")
        return len(to_remove)

    def record_hit(self, key: str) -> None:
        """Başarılı kullanımı kaydet."""
        if key in self._cache:
            self._cache[key].hit_count += 1
            self._cache[key].last_used = time.time()

    def record_miss(self, key: str) -> None:
        """Koordinat geçersiz çıktı — miss kaydet."""
        if key in self._cache:
            self._cache[key].miss_count += 1

    def stats(self) -> dict:
        """Önbellek istatistikleri."""
        total_hits  = sum(h.hit_count  for h in self._cache.values())
        total_miss  = sum(h.miss_count for h in self._cache.values())
        return {
            "entry_count": len(self._cache),
            "total_hits":  total_hits,
            "total_misses": total_miss,
            "hit_rate": (
                round(total_hits / (total_hits + total_miss), 3)
                if (total_hits + total_miss) > 0 else 0.0
            ),
        }

    def clear(self) -> None:
        """Önbelleği tamamen temizler."""
        self._cache.clear()
        self._access_order.clear()


# ---------------------------------------------------------------------------
# PersistentHintStore — long_term.json entegrasyonu
# ---------------------------------------------------------------------------

class PersistentHintStore:
    """
    Önbellek verilerini SIRIUS'un long_term.json dosyasına okur/yazar.
    Uygulama kapanıp açılsa bile koordinatlar korunur.

    JS karşılığı: electron-store (sadece session'da)
    Python artısı: kalıcı hafıza, SIRIUS memory katmanıyla birleşik.
    """

    MEMORY_KEY = "coord_hints"

    def __init__(self, memory_path: str = _MEMORY_PATH):
        self.memory_path = memory_path
        os.makedirs(os.path.dirname(memory_path), exist_ok=True)

    def _load_memory(self) -> dict:
        """long_term.json'ı yükler, yoksa boş dict döner."""
        if not os.path.exists(self.memory_path):
            return {}
        try:
            with open(self.memory_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"[PersistentStore] Hafıza okunamadı: {e}")
            return {}

    def _save_memory(self, data: dict) -> bool:
        """Hafızayı long_term.json'a yazar."""
        try:
            with open(self.memory_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except OSError as e:
            logger.error(f"[PersistentStore] Hafıza yazılamadı: {e}")
            return False

    def load_all(self) -> dict[str, CachedHint]:
        """
        Hafızadaki tüm coord_hints'i yükler.
        Döner: {anahtar: CachedHint}
        """
        data = self._load_memory()
        raw_hints: dict = data.get(self.MEMORY_KEY, {})
        result: dict[str, CachedHint] = {}

        for key, val in raw_hints.items():
            try:
                result[key] = CachedHint(**val)
            except Exception as e:
                logger.warning(f"[PersistentStore] '{key}' parse hatası: {e}")

        logger.debug(f"[PersistentStore] {len(result)} koordinat kaydı yüklendi.")
        return result

    def save_hint(self, key: str, hint: CachedHint) -> bool:
        """Tek bir hint'i hafızaya kaydeder."""
        data = self._load_memory()
        if self.MEMORY_KEY not in data:
            data[self.MEMORY_KEY] = {}

        data[self.MEMORY_KEY][key] = hint.model_dump()
        success = self._save_memory(data)
        if success:
            logger.debug(f"[PersistentStore] '{key}' kaydedildi → ({hint.x}, {hint.y})")
        return success

    def save_all(self, hints: dict[str, CachedHint]) -> bool:
        """Birden fazla hint'i tek seferde kaydeder."""
        data = self._load_memory()
        data[self.MEMORY_KEY] = {k: v.model_dump() for k, v in hints.items()}
        success = self._save_memory(data)
        if success:
            logger.info(f"[PersistentStore] {len(hints)} koordinat kaydı yazıldı.")
        return success

    def delete_hint(self, key: str) -> bool:
        """Hafızadan belirli bir kaydı siler."""
        data = self._load_memory()
        hints = data.get(self.MEMORY_KEY, {})
        if key in hints:
            del hints[key]
            data[self.MEMORY_KEY] = hints
            self._save_memory(data)
            logger.info(f"[PersistentStore] '{key}' silindi.")
            return True
        return False

    def prune_stale(
        self,
        max_age_hours: float = 72.0,
        max_miss_ratio: float = 0.5,
    ) -> int:
        """
        Eski veya güvenilmez kayıtları hafızadan temizler.
        Uygulama başlangıcında çağrılması önerilir.
        """
        hints = self.load_all()
        before = len(hints)
        current_time = time.time()

        cleaned = {
            key: hint for key, hint in hints.items()
            if not hint.is_stale(max_age_hours=max_age_hours, max_miss_ratio=max_miss_ratio)
        }

        removed = before - len(cleaned)
        if removed > 0:
            self.save_all(cleaned)
            logger.info(f"[PersistentStore] {removed} eski koordinat kaydı temizlendi.")
        return removed


# ---------------------------------------------------------------------------
# PointerHintManager — Ana Sınıf
# ---------------------------------------------------------------------------

class PointerHintManager:
    """
    In-memory önbellek + kalıcı hafıza entegrasyonunu tek noktada birleştirir.

    CoordEngine bu sınıfı kullanarak:
        1. Önce önbellekte ara (hızlı)
        2. Önbellekte yoksa hafızada ara (disk, ama hızlı)
        3. İkisinde de yoksa AI/OCR/template ile bul
        4. Bulunan koordinatı hem önbelleğe hem hafızaya yaz

    SIRIUS entegrasyon noktası:
        # coord_engine.py içinde CoordEngine.__init__():
        from vision.pointer_hint import PointerHintManager
        self.hint_manager = PointerHintManager()
        self.hint_manager.load_from_memory()  # başlangıçta yükle

        # find() metodunda:
        cached = self.hint_manager.lookup(target, app_process, app_title, screen_hash)
        if cached and not cached.is_stale(...):
            return cached.to_coord_hint()
        ...
        # AI bulduktan sonra:
        self.hint_manager.store(target, hint, app_process, app_title, screen_hash)
    """

    def __init__(
        self,
        memory_path: str = _MEMORY_PATH,
        cache_max_size: int = 200,
        auto_persist: bool = True,
        persist_min_hits: int = 2,
    ):
        """
        memory_path:       long_term.json yolu.
        cache_max_size:    In-memory önbellek kapasitesi.
        auto_persist:      Başarılı hit_count >= persist_min_hits olunca otomatik kaydet.
        persist_min_hits:  Kaç başarılı kullanımdan sonra kalıcıya yaz.
        """
        self.auto_persist    = auto_persist
        self.persist_min_hits = persist_min_hits

        self._cache = PointerHintCache(max_size=cache_max_size)
        self._store = PersistentHintStore(memory_path=memory_path)

    def load_from_memory(self) -> int:
        """
        Kalıcı hafızadan koordinatları in-memory önbelleğe yükler.
        Uygulama başlangıcında SiriusController.__init__() içinde çağrılır.
        Döner: yüklenen kayıt sayısı.
        """
        # Önce eski/güvensiz kayıtları temizle
        self._store.prune_stale()

        hints = self._store.load_all()
        for key, hint in hints.items():
            self._cache.set(key, hint)

        logger.info(f"[HintManager] {len(hints)} koordinat kalıcı hafızadan yüklendi.")
        return len(hints)

    def lookup(
        self,
        target: str,
        app_process: str = "",
        app_title: str = "",
        screen_wh: tuple[int, int] = (0, 0),
        loose_fallback: bool = True,
    ) -> Optional[CachedHint]:
        """
        Önce spesifik (uygulama+hedef), sonra genel (sadece hedef) anahtar ile arar.

        loose_fallback=True: Spesifik anahtarda bulunamazsa uygulama bağımsız dene.
        """
        sh = _screen_hash(*screen_wh) if screen_wh[0] > 0 else ""

        # Spesifik arama
        key = _make_cache_key(target, app_process, app_title, loose=False)
        hint = self._cache.get(key)

        if hint:
            if hint.is_stale(
                current_app=app_process,
                current_screen=sh,
            ):
                logger.info(f"[HintManager] '{key}' stale — geçersiz kılındı.")
                self._cache.invalidate(key)
                self._store.delete_hint(key)
                return None
            return hint

        # Loose arama (uygulama bağımsız)
        if loose_fallback:
            loose_key = _make_cache_key(target, loose=True)
            hint = self._cache.get(loose_key)
            if hint and not hint.is_stale(current_screen=sh):
                logger.debug(f"[HintManager] Loose hit: '{loose_key}'")
                return hint

        return None

    def store(
        self,
        target: str,
        coord_hint: CoordHint,
        app_process: str = "",
        app_title: str = "",
        screen_wh: tuple[int, int] = (0, 0),
    ) -> str:
        """
        Bulunan koordinatı önbelleğe ve (koşullu) kalıcı hafızaya yazar.
        Döner: kullanılan önbellek anahtarı.
        """
        key = _make_cache_key(target, app_process, app_title, loose=False)
        sh  = _screen_hash(*screen_wh) if screen_wh[0] > 0 else ""

        cached = CachedHint(
            x=coord_hint.x,
            y=coord_hint.y,
            label=coord_hint.label or target,
            confidence=coord_hint.confidence,
            method=coord_hint.method,
            hit_count=1,
            app_process=app_process,
            app_title=app_title[:40],
            screen_hash=sh,
        )

        self._cache.set(key, cached)

        # Kalıcı hafızaya yaz (eşik kontrolü)
        if self.auto_persist and cached.hit_count >= self.persist_min_hits:
            self._store.save_hint(key, cached)

        return key

    def record_success(self, key: str) -> None:
        """
        Koordinat başarıyla kullanıldı — hit sayısını artır.
        Eşiğe ulaşıldıysa kalıcı hafızaya da kaydet.
        """
        self._cache.record_hit(key)
        hint = self._cache.get(key)

        if hint and self.auto_persist and hint.hit_count >= self.persist_min_hits:
            self._store.save_hint(key, hint)
            logger.debug(f"[HintManager] '{key}' kalıcı hafızaya yazıldı (hit={hint.hit_count}).")

    def record_failure(self, key: str) -> None:
        """
        Koordinat geçersiz çıktı — miss kaydet, yüksek miss oranında temizle.
        """
        self._cache.record_miss(key)
        hint = self._cache.get(key)

        if hint and hint.is_stale(max_miss_ratio=0.5):
            logger.info(f"[HintManager] '{key}' yüksek miss oranı — temizlendi.")
            self._cache.invalidate(key)
            self._store.delete_hint(key)

    def invalidate_app(self, app_process: str) -> int:
        """
        Bir uygulama kapandığında veya güncellendiğinde
        o uygulamaya ait tüm koordinatları geçersiz kıl.
        """
        count = self._cache.invalidate_by_app(app_process)
        # Kalıcı hafızadan da temizle
        hints = self._store.load_all()
        needle = app_process.lower().replace(".exe", "")
        stale_keys = [k for k in hints if needle in k.lower()]
        for k in stale_keys:
            self._store.delete_hint(k)
        total = count + len(stale_keys)
        logger.info(f"[HintManager] '{app_process}' için {total} toplam kayıt geçersiz kılındı.")
        return total

    def flush_to_memory(self) -> bool:
        """
        In-memory önbellekteki tüm kayıtları kalıcı hafızaya yazar.
        Uygulama kapanmadan önce SiriusController.shutdown() içinde çağrılır.
        """
        all_hints = dict(self._cache._cache)
        if not all_hints:
            return True
        success = self._store.save_all(all_hints)
        if success:
            logger.info(f"[HintManager] {len(all_hints)} koordinat flush edildi.")
        return success

    def get_stats(self) -> dict:
        """Önbellek ve hafıza istatistikleri."""
        cache_stats = self._cache.stats()
        persistent_count = len(self._store.load_all())
        return {
            **cache_stats,
            "persistent_entries": persistent_count,
            "memory_path": self.memory_path if hasattr(self, "memory_path") else _MEMORY_PATH,
        }

    @property
    def memory_path(self) -> str:
        return self._store.memory_path


# ---------------------------------------------------------------------------
# Bağımsız Test
# ---------------------------------------------------------------------------

async def _demo() -> None:
    import tempfile

    print("\n🧠  SIRIUS PointerHintManager Demo Başlatılıyor...")
    print("─" * 50)

    # Geçici hafıza dosyası (test için gerçek long_term.json'a dokunma)
    tmp_dir  = tempfile.mkdtemp()
    tmp_mem  = os.path.join(tmp_dir, "long_term.json")
    os.makedirs(os.path.join(tmp_dir), exist_ok=True)

    manager = PointerHintManager(
        memory_path=tmp_mem,
        persist_min_hits=2,
    )

    # 1. Başlangıçta boş
    count = manager.load_from_memory()
    print(f"\n1. Başlangıç yüklemesi: {count} kayıt")

    # 2. Koordinat kaydet
    hint = CoordHint(x=245, y=312, label="Kaydet Butonu", confidence=0.94, method="ai")
    key = manager.store(
        target="kaydet butonu",
        coord_hint=hint,
        app_process="chrome.exe",
        app_title="Belge1 - Google Chrome",
        screen_wh=(1920, 1080),
    )
    print(f"\n2. Koordinat kaydedildi: anahtar='{key}'")

    # 3. Lookup
    found = manager.lookup("kaydet butonu", "chrome.exe", "Belge1 - Google Chrome", (1920, 1080))
    if found:
        print(f"\n3. Lookup başarılı: ({found.x}, {found.y}) — '{found.label}'")
    else:
        print("\n3. Lookup başarısız (beklenmiyor!)")

    # 4. Hit kaydet → persist_min_hits=2 eşiğini geç
    manager.record_success(key)
    manager.record_success(key)
    print(f"\n4. 2 başarılı hit kaydedildi — kalıcıya yazılmalı")

    # 5. Kalıcı hafızadan yeniden yükle
    manager2 = PointerHintManager(memory_path=tmp_mem)
    count2 = manager2.load_from_memory()
    print(f"\n5. Yeni instance yükleme: {count2} kayıt (kalıcıdan geldi)")

    found2 = manager2.lookup("kaydet butonu", "chrome.exe")
    print(f"   Lookup sonucu: {'✅ ' + str((found2.x, found2.y)) if found2 else '❌ bulunamadı'}")

    # 6. Miss kaydı
    manager.record_failure(key)
    print(f"\n6. Miss kaydedildi")

    # 7. Uygulama geçersiz kılma
    removed = manager.invalidate_app("chrome.exe")
    print(f"\n7. chrome.exe geçersiz kılındı: {removed} kayıt temizlendi")

    # 8. İstatistikler
    stats = manager.get_stats()
    print(f"\n8. Önbellek istatistikleri: {stats}")

    # Temizlik
    import shutil
    shutil.rmtree(tmp_dir, ignore_errors=True)

    print("\n✅ PointerHintManager demo tamamlandı.")


if __name__ == "__main__":
    asyncio.run(_demo())
