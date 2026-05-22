"""
vision/coord_engine.py
======================
OpenGuider'ın src/screenshot.js + koordinat sistemi mantığının saf Python portu.

Bu modül SIRIUS'un vision/ katmanına entegre olur ve şu yetenekleri sağlar:
    1. Ekran görüntüsü alma   (mss — Windows'ta en stabil, Electron'dan ~3× hızlı)
    2. Bölge tespiti          (cv2 template matching + kenar tespiti)
    3. OCR metin tespiti      (pytesseract — ekrandaki yazıları bul)
    4. AI destekli koordinat  (Gemini Vision — "Kaydet düğmesi nerede?" → (x, y))
    5. Fare hareketi/tıklama  (pyautogui — OpenGuider'ın cursor overlay'inin karşılığı)
    6. Bölge vurgulama        (win32gui ile şeffaf overlay penceresi)

OpenGuider JS → Python dönüşüm notları:
    desktopCapturer.getSources()  → mss.mss().grab()
    canvas.getContext('2d')       → PIL.Image / cv2 ndarray
    coord hint JSON {x,y,label}   → CoordHint(Pydantic BaseModel)
    cursor overlay (Electron BW)  → win32gui şeffaf overlay
    Promise/async chain           → asyncio coroutine

Windows bağımlılıkları (tümü setup.py'de zaten olmalı):
    pip install mss pillow opencv-python pytesseract pyautogui pywin32
    Tesseract-OCR kurulumu: https://github.com/UB-Mannheim/tesseract/wiki
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import cv2
import mss
import mss.tools
import numpy as np
import pyautogui
import pytesseract
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field as PydanticField

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
logger = logging.getLogger("sirius.vision.coord_engine")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(asctime)s] [%(name)s] %(levelname)s — %(message)s", "%H:%M:%S"))
    logger.addHandler(_h)
logger.setLevel(logging.DEBUG)

# ---------------------------------------------------------------------------
# Tesseract yolu — Windows varsayılanı; farklıysa config.json'dan okunur
# ---------------------------------------------------------------------------
_TESSERACT_DEFAULT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
if os.path.exists(_TESSERACT_DEFAULT):
    pytesseract.pytesseract.tesseract_cmd = _TESSERACT_DEFAULT

# pyautogui güvenlik duraklaması — her hareket/tıklama arası (saniye)
pyautogui.PAUSE = 0.05
pyautogui.FAILSAFE = True   # sol üst köşeye git → durdur (güvenlik)


# ---------------------------------------------------------------------------
# Pydantic Modeller (OpenGuider'ın zod coord şemalarının Python karşılığı)
# ---------------------------------------------------------------------------

class CoordHint(BaseModel):
    """
    Ekranda tespit edilen tek bir hedef noktası.
    OpenGuider'ın {x, y, label, confidence} JSON objesinin Pydantic karşılığı.
    """
    x:          int   = 0
    y:          int   = 0
    label:      str   = ""
    confidence: float = 0.0      # 0.0–1.0
    method:     str   = "ai"     # "ai" | "template" | "ocr" | "manual"
    region:     Optional["ScreenRegion"] = None


class ScreenRegion(BaseModel):
    """Ekranda dikdörtgen bir bölge."""
    left:   int = 0
    top:    int = 0
    width:  int = 0
    height: int = 0

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def center(self) -> tuple[int, int]:
        return (self.left + self.width // 2, self.top + self.height // 2)

    def as_mss_dict(self) -> dict:
        """mss kütüphanesinin beklediği format."""
        return {"left": self.left, "top": self.top, "width": self.width, "height": self.height}


class OCRResult(BaseModel):
    """pytesseract'tan dönen tek kelime/satır sonucu."""
    text:       str   = ""
    x:          int   = 0
    y:          int   = 0
    width:      int   = 0
    height:     int   = 0
    confidence: float = 0.0


class ScreenSnapshot(BaseModel):
    """
    Tam bir ekran görüntüsü + meta bilgiler.
    OpenGuider'ın captureScreen() dönüş değerinin Python karşılığı.
    """
    timestamp:   float = PydanticField(default_factory=time.time)
    width:       int   = 0
    height:      int   = 0
    base64_png:  str   = ""    # AI'ya gönderilecek format
    monitor_idx: int   = 1     # 1 = birincil monitör

    class Config:
        arbitrary_types_allowed = True


# ---------------------------------------------------------------------------
# ScreenCapture  (OpenGuider desktopCapturer → Python mss)
# ---------------------------------------------------------------------------

class ScreenCapture:
    """
    Windows'ta en stabil ekran yakalama sınıfı.

    OpenGuider'da:
        const sources = await desktopCapturer.getSources({types:['screen']})
        const img = sources[0].thumbnail.toPNG()

    Python'da:
        mss.mss().grab(monitor) → PIL.Image

    Neden mss?
    - Native Win32 BitBlt kullanır → en hızlı Python seçenek
    - DPI-aware (Windows 10/11 yüksek DPI ekranlar)
    - Thread-safe değil → asyncio.to_thread ile çağrılır
    """

    def __init__(self, monitor_index: int = 1):
        """
        monitor_index: 1 = birincil ekran, 2 = ikincil ekran, 0 = tüm ekranlar birleşik
        """
        self.monitor_index = monitor_index
        self._sct: Optional[mss.base.MSSBase] = None

    def _get_sct(self) -> mss.base.MSSBase:
        """mss nesnesi thread-local olmalı — her çağrıda yeni oluştur."""
        return mss.mss()

    def capture_full(self) -> tuple[np.ndarray, ScreenSnapshot]:
        """
        Tam ekran yakalar.
        Döner: (BGR ndarray cv2 için, ScreenSnapshot Pydantic modeli)

        Bu metod BLOCKING — asyncio.to_thread ile çağır.
        """
        with self._get_sct() as sct:
            monitor = sct.monitors[self.monitor_index]
            raw = sct.grab(monitor)

        # BGRA → BGR (cv2 standardı)
        bgr = cv2.cvtColor(np.array(raw), cv2.COLOR_BGRA2BGR)

        # PIL üzerinden base64 PNG (Gemini Vision için)
        pil_img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        buffer = io.BytesIO()
        pil_img.save(buffer, format="PNG", optimize=True)
        b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

        snapshot = ScreenSnapshot(
            width=raw.width,
            height=raw.height,
            base64_png=b64,
            monitor_idx=self.monitor_index,
        )
        logger.debug(f"[Capture] Ekran yakalandı: {raw.width}×{raw.height}")
        return bgr, snapshot

    def capture_region(self, region: ScreenRegion) -> tuple[np.ndarray, ScreenSnapshot]:
        """
        Ekranın belirli bir bölgesini yakalar.
        OpenGuider'ın captureRegion(bounds) fonksiyonunun Python karşılığı.
        """
        with self._get_sct() as sct:
            raw = sct.grab(region.as_mss_dict())

        bgr = cv2.cvtColor(np.array(raw), cv2.COLOR_BGRA2BGR)

        pil_img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        buffer = io.BytesIO()
        pil_img.save(buffer, format="PNG", optimize=True)
        b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

        snapshot = ScreenSnapshot(
            width=raw.width,
            height=raw.height,
            base64_png=b64,
            monitor_idx=self.monitor_index,
        )
        return bgr, snapshot

    async def capture_full_async(self) -> tuple[np.ndarray, ScreenSnapshot]:
        """asyncio uyumlu tam ekran yakalama."""
        return await asyncio.to_thread(self.capture_full)

    async def capture_region_async(self, region: ScreenRegion) -> tuple[np.ndarray, ScreenSnapshot]:
        """asyncio uyumlu bölge yakalama."""
        return await asyncio.to_thread(self.capture_region, region)


# ---------------------------------------------------------------------------
# TemplateMatcher  (OpenGuider image matching → Python OpenCV)
# ---------------------------------------------------------------------------

class TemplateMatcher:
    """
    Ekranda referans görüntü arar (template matching).

    OpenGuider'da bu işlem Electron'un sharp kütüphanesiyle yapılır.
    Python'da cv2.matchTemplate() çok daha hızlı ve doğru.

    Kullanım:
        matcher = TemplateMatcher()
        hint = matcher.find(screen_bgr, template_path="save_button.png", threshold=0.85)
    """

    def find(
        self,
        screen_bgr: np.ndarray,
        template_path: str,
        threshold: float = 0.80,
        method: int = cv2.TM_CCOEFF_NORMED,
    ) -> Optional[CoordHint]:
        """
        Ekranda şablonu arar, bulursa CoordHint döner.

        threshold: 0.0–1.0, yüksek = daha kesin eşleşme gerekir
        """
        if not os.path.exists(template_path):
            logger.warning(f"[Template] Şablon dosyası bulunamadı: {template_path}")
            return None

        template_bgr = cv2.imread(template_path)
        if template_bgr is None:
            logger.warning(f"[Template] Şablon okunamadı: {template_path}")
            return None

        # Gri tonlamaya çevir — daha hızlı ve renk bağımsız
        screen_gray   = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)
        template_gray = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY)

        th, tw = template_gray.shape[:2]
        result = cv2.matchTemplate(screen_gray, template_gray, method)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        if max_val < threshold:
            logger.debug(f"[Template] Eşleşme bulunamadı (skor: {max_val:.3f} < {threshold})")
            return None

        # Merkez koordinatı hesapla
        cx = max_loc[0] + tw // 2
        cy = max_loc[1] + th // 2

        hint = CoordHint(
            x=cx,
            y=cy,
            label=os.path.basename(template_path),
            confidence=float(max_val),
            method="template",
            region=ScreenRegion(left=max_loc[0], top=max_loc[1], width=tw, height=th),
        )
        logger.info(f"[Template] '{hint.label}' bulundu: ({cx}, {cy}) — skor: {max_val:.3f}")
        return hint

    def find_all(
        self,
        screen_bgr: np.ndarray,
        template_path: str,
        threshold: float = 0.80,
        max_results: int = 10,
    ) -> list[CoordHint]:
        """
        Ekranda şablonun tüm oluşumlarını bulur.
        Örnek: ekrandaki tüm 'Kapat' (×) düğmelerini bul.
        """
        if not os.path.exists(template_path):
            return []

        template_bgr = cv2.imread(template_path)
        if template_bgr is None:
            return []

        screen_gray   = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)
        template_gray = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY)
        th, tw = template_gray.shape[:2]

        result = cv2.matchTemplate(screen_gray, template_gray, cv2.TM_CCOEFF_NORMED)
        locations = np.where(result >= threshold)

        hints: list[CoordHint] = []
        used_positions: list[tuple[int, int]] = []

        for pt in zip(*locations[::-1]):  # (x, y) formatına çevir
            # Çakışan sonuçları filtrele (non-maximum suppression basit hali)
            too_close = any(
                abs(pt[0] - up[0]) < tw // 2 and abs(pt[1] - up[1]) < th // 2
                for up in used_positions
            )
            if too_close:
                continue

            used_positions.append(pt)
            cx = pt[0] + tw // 2
            cy = pt[1] + th // 2
            confidence = float(result[pt[1], pt[0]])

            hints.append(CoordHint(
                x=cx, y=cy,
                label=f"{os.path.basename(template_path)}_{len(hints)}",
                confidence=confidence,
                method="template",
                region=ScreenRegion(left=pt[0], top=pt[1], width=tw, height=th),
            ))

            if len(hints) >= max_results:
                break

        # Güven skoruna göre sırala
        hints.sort(key=lambda h: h.confidence, reverse=True)
        logger.info(f"[Template] {len(hints)} eşleşme bulundu: {template_path}")
        return hints


# ---------------------------------------------------------------------------
# OCREngine  (ekranda metin ara — OpenGuider'ın textDetection mantığı)
# ---------------------------------------------------------------------------

class OCREngine:
    """
    Ekranda OCR ile metin tespiti.

    OpenGuider'da Electron'un nativeImage üzerinden metin tespiti yapılır.
    Python'da pytesseract + cv2 ön işleme ile çok daha iyi sonuç alınır.

    Tesseract gereksinimi:
        https://github.com/UB-Mannheim/tesseract/wiki adresinden kur.
        Türkçe dil paketi: tesseract kurulumunda "Turkish" seçeneğini işaretle.
    """

    def __init__(self, language: str = "tur+eng"):
        """
        language: Tesseract dil kodu. "tur+eng" = Türkçe+İngilizce.
        Sadece İngilizce için: "eng"
        """
        self.language = language
        self._tesseract_ok = self._check_tesseract()

    def _check_tesseract(self) -> bool:
        try:
            pytesseract.get_tesseract_version()
            return True
        except Exception:
            logger.warning(
                "[OCR] Tesseract bulunamadı. OCR devre dışı. "
                "Kurulum: https://github.com/UB-Mannheim/tesseract/wiki"
            )
            return False

    def _preprocess(self, bgr: np.ndarray) -> np.ndarray:
        """
        OCR doğruluğunu artırmak için ön işleme.
        OpenGuider'da bu adım yoktu — ekstra doğruluk için eklendi.

        Adımlar:
            1. Gri tona çevir
            2. Boyutu 2× büyüt (küçük yazılar için)
            3. Gaussian blur → gürültü azalt
            4. Otsu eşikleme → siyah/beyaza çevir
        """
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

        # 2× büyütme — küçük fontlar için kritik
        h, w = gray.shape
        gray = cv2.resize(gray, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC)

        # Gaussian blur — gürültüyü azalt
        gray = cv2.GaussianBlur(gray, (3, 3), 0)

        # Otsu eşikleme — adaptif ikili görüntü
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return binary

    def find_text(
        self,
        screen_bgr: np.ndarray,
        target_text: str,
        case_sensitive: bool = False,
        min_confidence: float = 60.0,
    ) -> Optional[CoordHint]:
        """
        Ekranda belirli bir metni arar ve koordinatını döner.
        OpenGuider'ın findTextOnScreen() fonksiyonunun Python portu.

        Örnek:
            hint = engine.find_text(screen, "Kaydet")
            # → CoordHint(x=245, y=312, label="Kaydet", confidence=0.87)
        """
        if not self._tesseract_ok:
            return None

        processed = self._preprocess(screen_bgr)
        search = target_text if case_sensitive else target_text.lower()

        try:
            data = pytesseract.image_to_data(
                processed,
                lang=self.language,
                output_type=pytesseract.Output.DICT,
                config="--psm 11",  # sparse text — tüm ekranda kelime ara
            )
        except Exception as e:
            logger.error(f"[OCR] Tesseract hatası: {e}")
            return None

        n_boxes = len(data["text"])
        for i in range(n_boxes):
            raw_text = data["text"][i]
            conf = float(data["conf"][i])

            if conf < min_confidence:
                continue

            compared = raw_text if case_sensitive else raw_text.lower()
            if search not in compared:
                continue

            # Koordinatları 2× ölçek geriye çevir
            x = data["left"][i] // 2
            y = data["top"][i] // 2
            w = data["width"][i] // 2
            h = data["height"][i] // 2

            hint = CoordHint(
                x=x + w // 2,
                y=y + h // 2,
                label=raw_text.strip(),
                confidence=conf / 100.0,
                method="ocr",
                region=ScreenRegion(left=x, top=y, width=w, height=h),
            )
            logger.info(f"[OCR] '{raw_text.strip()}' bulundu: ({hint.x}, {hint.y}) — güven: {conf:.0f}%")
            return hint

        logger.debug(f"[OCR] '{target_text}' metni ekranda bulunamadı.")
        return None

    def extract_all_text(
        self,
        screen_bgr: np.ndarray,
        min_confidence: float = 60.0,
    ) -> list[OCRResult]:
        """
        Ekrandaki tüm metni çıkarır.
        AI'ya bağlam vermek için kullanılır (coord_engine._build_context_prompt).
        """
        if not self._tesseract_ok:
            return []

        processed = self._preprocess(screen_bgr)

        try:
            data = pytesseract.image_to_data(
                processed,
                lang=self.language,
                output_type=pytesseract.Output.DICT,
                config="--psm 11",
            )
        except Exception as e:
            logger.error(f"[OCR] Tesseract hatası: {e}")
            return []

        results: list[OCRResult] = []
        n = len(data["text"])
        for i in range(n):
            text = data["text"][i].strip()
            conf = float(data["conf"][i])
            if not text or conf < min_confidence:
                continue
            results.append(OCRResult(
                text=text,
                x=data["left"][i] // 2,
                y=data["top"][i] // 2,
                width=data["width"][i] // 2,
                height=data["height"][i] // 2,
                confidence=conf / 100.0,
            ))

        logger.debug(f"[OCR] {len(results)} metin öğesi çıkarıldı.")
        return results


# ---------------------------------------------------------------------------
# AICoordDetector  (OpenGuider'ın Vision AI prompt mantığı → Gemini Vision)
# ---------------------------------------------------------------------------

class AICoordDetector:
    """
    Gemini Vision API kullanarak ekranda nesne/koordinat tespiti.

    OpenGuider'da Electron'un webContents üzerinden AI'ya ekran gönderilir.
    Python'da doğrudan Gemini 2.5 Flash Vision'a base64 PNG gönderilir.

    Bu sınıf, şablonla bulunamayan ve OCR'ın yetersiz kaldığı durumlar için
    "son çare" olarak kullanılır — AI her şeyi bulabilir ama yavaştır.

    OpenGuider JS:
        const response = await model.generateContent([imagePart, prompt])
        const coords = JSON.parse(response.text())

    Python:
        response = await asyncio.to_thread(client.generate_content, [img_part, prompt])
        coords = json.loads(response.text)
    """

    def __init__(self, gemini_key: str, model: str = "gemini-2.5-flash"):
        self.gemini_key = gemini_key
        self.model = model

    def _build_find_prompt(self, target_description: str, ocr_context: str) -> str:
        ctx = f"\n\nEkranda tespit edilen metinler:\n{ocr_context}" if ocr_context else ""
        return f"""Sen bir ekran analiz yapay zekasısın. Verilen ekran görüntüsünde belirtilen UI öğesini bul.

HEDEF: {target_description}{ctx}

Sadece aşağıdaki JSON formatında yanıt ver, başka hiçbir şey yazma:
{{
  "found": true,
  "x": 245,
  "y": 312,
  "label": "Bulunan öğenin kısa açıklaması",
  "confidence": 0.92,
  "reasoning": "Neden bu koordinatı seçtiğinin kısa açıklaması"
}}

KURALLAR:
- x ve y, piksel cinsinden ekran koordinatı (hedefin merkezi).
- found false ise x ve y 0 olsun.
- confidence 0.0 ile 1.0 arasında.
- Türkçe yaz."""

    async def find(
        self,
        snapshot: ScreenSnapshot,
        target_description: str,
        ocr_context: str = "",
    ) -> Optional[CoordHint]:
        """
        Gemini Vision'a ekran görüntüsü + hedef açıklaması gönderir,
        koordinat döner.

        Dönen koordinatlar Gemini'nin gördüğü görüntü boyutuna göredir —
        eğer snapshot farklı çözünürlükteyse ölçekleme gerekebilir.
        """
        try:
            import google.generativeai as genai  # type: ignore
        except ImportError:
            raise RuntimeError("google-generativeai paketi bulunamadı: pip install google-generativeai")

        genai.configure(api_key=self.gemini_key)
        client = genai.GenerativeModel(
            model_name=self.model,
            generation_config=genai.types.GenerationConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )

        prompt = self._build_find_prompt(target_description, ocr_context)
        image_part = {
            "inline_data": {
                "mime_type": "image/png",
                "data": snapshot.base64_png,
            }
        }

        logger.info(f"[AI Coord] Gemini Vision çağrılıyor: '{target_description[:50]}'")

        try:
            response = await asyncio.to_thread(
                client.generate_content, [image_part, prompt]
            )
            raw = response.text.strip()

            # Markdown fence temizle
            if raw.startswith("```"):
                raw = "\n".join(
                    line for line in raw.splitlines() if not line.strip().startswith("```")
                )

            data = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error(f"[AI Coord] JSON parse hatası: {e}")
            return None
        except Exception as e:
            logger.error(f"[AI Coord] Gemini Vision hatası: {e}")
            return None

        if not data.get("found", False):
            logger.info(f"[AI Coord] '{target_description}' ekranda bulunamadı.")
            return None

        hint = CoordHint(
            x=int(data.get("x", 0)),
            y=int(data.get("y", 0)),
            label=data.get("label", target_description),
            confidence=float(data.get("confidence", 0.0)),
            method="ai",
        )
        logger.info(
            f"[AI Coord] '{hint.label}' bulundu: ({hint.x}, {hint.y}) "
            f"— güven: {hint.confidence:.0%} — {data.get('reasoning', '')[:60]}"
        )
        return hint


# ---------------------------------------------------------------------------
# CoordActionExecutor  (OpenGuider'ın cursor overlay → Python pyautogui)
# ---------------------------------------------------------------------------

class CoordActionExecutor:
    """
    Bulunan koordinata fare hareketi ve tıklama yapar.

    OpenGuider'da bu Electron'un robot.js veya Playwright üzerinden yapılır.
    Python'da pyautogui doğrudan Win32 SendInput API'sini kullanır.

    OpenGuider JS:
        await robot.moveMouse(x, y)
        await robot.mouseClick('left')

    Python:
        pyautogui.moveTo(x, y, duration=0.3)
        pyautogui.click()
    """

    def __init__(self, move_duration: float = 0.25):
        """
        move_duration: Fare hareketinin süresi (saniye). 0 = anlık.
        Çok hızlı hareket (0.0) bazı uygulamaları kaçırabilir.
        """
        self.move_duration = move_duration

    async def click(self, hint: CoordHint, button: str = "left", clicks: int = 1) -> bool:
        """
        Koordinata tıklar.

        button: "left" | "right" | "middle"
        clicks: 1 = tek tık, 2 = çift tık
        """
        logger.info(f"[Action] {button.upper()} tık: ({hint.x}, {hint.y}) — '{hint.label}'")
        try:
            await asyncio.to_thread(
                pyautogui.click,
                x=hint.x,
                y=hint.y,
                clicks=clicks,
                button=button,
                duration=self.move_duration,
            )
            return True
        except pyautogui.FailSafeException:
            logger.error("[Action] pyautogui güvenlik durumu tetiklendi (sol üst köşe)!")
            return False
        except Exception as e:
            logger.error(f"[Action] Tıklama hatası: {e}")
            return False

    async def move_to(self, hint: CoordHint) -> bool:
        """Fareyi koordinata taşır, tıklamaz."""
        logger.debug(f"[Action] Fare taşınıyor: ({hint.x}, {hint.y})")
        try:
            await asyncio.to_thread(
                pyautogui.moveTo, hint.x, hint.y, duration=self.move_duration
            )
            return True
        except Exception as e:
            logger.error(f"[Action] Fare taşıma hatası: {e}")
            return False

    async def type_text(self, text: str, interval: float = 0.03) -> bool:
        """
        Metni klavyeden yazar.
        interval: her karakter arası bekleme (saniye)
        """
        logger.info(f"[Action] Metin yazılıyor: '{text[:30]}{'...' if len(text) > 30 else ''}'")
        try:
            await asyncio.to_thread(pyautogui.typewrite, text, interval=interval)
            return True
        except Exception as e:
            logger.error(f"[Action] Yazma hatası: {e}")
            return False

    async def hotkey(self, *keys: str) -> bool:
        """
        Klavye kısayolu çalıştırır.
        Örnek: await executor.hotkey("ctrl", "s")  # Kaydet
        """
        logger.info(f"[Action] Kısayol: {'+'.join(keys)}")
        try:
            await asyncio.to_thread(pyautogui.hotkey, *keys)
            return True
        except Exception as e:
            logger.error(f"[Action] Kısayol hatası: {e}")
            return False

    async def scroll(self, hint: CoordHint, clicks: int = 3, direction: str = "down") -> bool:
        """
        Koordinatta kaydırma yapar.
        clicks: kaydırma miktarı (pozitif = yukarı, negatif = aşağı)
        direction: "up" | "down"
        """
        amount = abs(clicks) * (-1 if direction == "down" else 1)
        logger.debug(f"[Action] Scroll: ({hint.x}, {hint.y}) — {direction} {abs(clicks)} tık")
        try:
            await asyncio.to_thread(pyautogui.scroll, amount, x=hint.x, y=hint.y)
            return True
        except Exception as e:
            logger.error(f"[Action] Scroll hatası: {e}")
            return False


# ---------------------------------------------------------------------------
# OverlayRenderer  (OpenGuider cursor overlay → Python win32gui)
# ---------------------------------------------------------------------------

class OverlayRenderer:
    """
    Ekranda şeffaf bir overlay penceresi ile koordinatları görselleştirir.
    OpenGuider'ın Electron BrowserWindow overlay'inin Python karşılığı.

    Yöntem: PIL ile PNG oluştur → win32gui ile ekranda göster.
    Kalıcı overlay yerine geçici "flash" gösterimi tercih edildi
    (daha az kaynak, Windows 11 uyumlu).

    Kullanım:
        renderer = OverlayRenderer()
        renderer.flash_point(hint, duration_ms=1500)
    """

    def flash_point(
        self,
        hint: CoordHint,
        screen_width: int = 1920,
        screen_height: int = 1080,
        duration_ms: int = 1500,
        color: tuple[int, int, int] = (255, 80, 80),
    ) -> None:
        """
        Koordinatı kırmızı daire + etiketle işaretler ve kısa süre gösterir.
        Bu metod BLOCKING — asyncio.to_thread ile çağır.

        OpenGuider JS:
            overlayWindow.webContents.send('show-pointer', {x, y, label})
            setTimeout(() => overlayWindow.hide(), duration)

        Python: PIL ile PNG oluştur, win32gui ile göster.
        """
        try:
            import win32api   # type: ignore
            import win32con   # type: ignore
            import win32gui   # type: ignore
            import win32ui    # type: ignore
        except ImportError:
            logger.warning("[Overlay] pywin32 bulunamadı — görsel işaretleme devre dışı.")
            self._fallback_flash(hint, duration_ms)
            return

        # PIL ile işaret görüntüsü oluştur
        radius = 20
        padding = 10
        img_size = (radius * 2 + padding * 2, radius * 2 + padding * 2)
        img = Image.new("RGBA", img_size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Dış halka
        draw.ellipse(
            (padding, padding, padding + radius * 2, padding + radius * 2),
            outline=(*color, 255),
            width=3,
        )
        # İç nokta
        inner = 6
        cx, cy = padding + radius, padding + radius
        draw.ellipse(
            (cx - inner, cy - inner, cx + inner, cy + inner),
            fill=(*color, 200),
        )

        # Etiket
        try:
            font = ImageFont.truetype("arial.ttf", 12)
        except Exception:
            font = ImageFont.load_default()

        label_text = hint.label[:20] if hint.label else f"({hint.x},{hint.y})"
        draw.text((padding, padding + radius * 2 + 2), label_text, font=font, fill=(*color, 220))

        # Geçici PNG'ye kaydet
        tmp_path = os.path.join(os.environ.get("TEMP", "."), "sirius_overlay.png")
        img.save(tmp_path, "PNG")

        logger.debug(f"[Overlay] İşaret: ({hint.x}, {hint.y}) — '{hint.label}' — {duration_ms}ms")

        # win32gui ile şeffaf pencere
        try:
            self._show_win32_overlay(
                hint.x - radius - padding,
                hint.y - radius - padding,
                img_size[0], img_size[1],
                tmp_path, duration_ms,
            )
        except Exception as e:
            logger.warning(f"[Overlay] win32gui hatası, fallback: {e}")
            self._fallback_flash(hint, duration_ms)

    def _show_win32_overlay(
        self, x: int, y: int, w: int, h: int, img_path: str, duration_ms: int
    ) -> None:
        """win32gui ile şeffaf overlay penceresi oluşturur ve gösterir."""
        import win32api, win32con, win32gui, win32ui  # type: ignore

        wc = win32gui.WNDCLASS()
        wc.hInstance = win32api.GetModuleHandle(None)
        wc.lpszClassName = "SiriusOverlay"
        wc.style = win32con.CS_HREDRAW | win32con.CS_VREDRAW
        wc.hbrBackground = win32con.COLOR_WINDOW

        try:
            win32gui.RegisterClass(wc)
        except Exception:
            pass  # Zaten kayıtlıysa devam et

        hwnd = win32gui.CreateWindowEx(
            win32con.WS_EX_TOPMOST | win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT,
            "SiriusOverlay",
            None,
            win32con.WS_POPUP,
            x, y, w, h,
            None, None, wc.hInstance, None,
        )

        # Şeffaflık: siyah renk → transparan (colorkey)
        win32gui.SetLayeredWindowAttributes(hwnd, 0x00000000, 0, win32con.LWA_COLORKEY)
        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        win32gui.UpdateWindow(hwnd)

        # Belirtilen süre sonra kapat
        time.sleep(duration_ms / 1000.0)
        win32gui.DestroyWindow(hwnd)

    def _fallback_flash(self, hint: CoordHint, duration_ms: int) -> None:
        """
        win32gui yoksa pyautogui ile uyarı: fareyi koordinata taşı.
        Görsel olmasa da kullanıcıya konum hissi verir.
        """
        try:
            original = pyautogui.position()
            pyautogui.moveTo(hint.x, hint.y, duration=0.2)
            time.sleep(duration_ms / 1000.0)
            pyautogui.moveTo(original.x, original.y, duration=0.2)
        except Exception as e:
            logger.warning(f"[Overlay] Fallback hareket hatası: {e}")

    async def flash_async(self, hint: CoordHint, duration_ms: int = 1500) -> None:
        """asyncio uyumlu flash gösterimi."""
        await asyncio.to_thread(self.flash_point, hint, duration_ms=duration_ms)


# ---------------------------------------------------------------------------
# CoordEngine — Hepsini Birleştiren Ana Sınıf
# ---------------------------------------------------------------------------

class CoordEngine:
    """
    SIRIUS'un merkezi koordinat ve ekran farkındalık motoru.

    OpenGuider'ın src/screenshot.js + renderer/cursor-overlay.js +
    src/agent/ içindeki vision çağrılarını tek Python sınıfında birleştirir.

    Arama stratejisi (kademeli, OpenGuider'ın fallback mantığıyla aynı):
        1. Template matching (en hızlı, referans görüntü varsa)
        2. OCR metin arama (yazı içeren butonlar için)
        3. Gemini Vision AI (her şeyi bulur, en yavaş)

    SIRIUS entegrasyon noktası:
        # SiriusController.__init__() içinde:
        from vision.coord_engine import CoordEngine
        self.coord_engine = CoordEngine(gemini_key=gk)

        # plugins/screen_processor.py'de:
        async def execute(self, args: dict) -> str:
            target = args.get("target", "")
            hint = await self.controller.coord_engine.find_and_click(target)
            return f"'{target}' tıklandı: ({hint.x}, {hint.y})" if hint else "Bulunamadı."
    """

    def __init__(
        self,
        gemini_key: str = "",
        monitor_index: int = 1,
        ocr_language: str = "tur+eng",
        enable_overlay: bool = True,
        move_duration: float = 0.25,
    ):
        self.gemini_key = gemini_key
        self.enable_overlay = enable_overlay

        self.capture   = ScreenCapture(monitor_index)
        self.template  = TemplateMatcher()
        self.ocr       = OCREngine(ocr_language)
        self.executor  = CoordActionExecutor(move_duration)
        self.overlay   = OverlayRenderer()
        self._ai: Optional[AICoordDetector] = (
            AICoordDetector(gemini_key) if gemini_key else None
        )

    # ------------------------------------------------------------------
    # Temel Yüksek Seviye API
    # ------------------------------------------------------------------

    async def find(
        self,
        target: str,
        template_path: Optional[str] = None,
        use_ai: bool = True,
        region: Optional[ScreenRegion] = None,
    ) -> Optional[CoordHint]:
        """
        Ekranda hedefi arar. Kademeli strateji kullanır.

        target:        Aranacak metin veya UI öğesinin açıklaması.
        template_path: Varsa önce template matching dene.
        use_ai:        Diğer yöntemler başarısız olursa Gemini Vision kullan.
        region:        Sadece belirli bir bölgede ara (performans için).
        """
        # Ekran görüntüsü al
        if region:
            bgr, snapshot = await self.capture.capture_region_async(region)
        else:
            bgr, snapshot = await self.capture.capture_full_async()

        # --- Strateji 1: Template Matching ---
        if template_path:
            hint = await asyncio.to_thread(
                self.template.find, bgr, template_path
            )
            if hint:
                # Bölge varsa koordinatları global ekrana çevir
                if region:
                    hint.x += region.left
                    hint.y += region.top
                return hint

        # --- Strateji 2: OCR Metin Arama ---
        hint = await asyncio.to_thread(self.ocr.find_text, bgr, target)
        if hint:
            if region:
                hint.x += region.left
                hint.y += region.top
            return hint

        # --- Strateji 3: Gemini Vision AI ---
        if use_ai and self._ai:
            # OCR bağlamını AI'ya ver — daha iyi sonuç için
            ocr_items = await asyncio.to_thread(self.ocr.extract_all_text, bgr)
            ocr_context = ", ".join(r.text for r in ocr_items[:30])

            hint = await self._ai.find(snapshot, target, ocr_context)
            if hint and region:
                hint.x += region.left
                hint.y += region.top
            return hint

        return None

    async def find_and_click(
        self,
        target: str,
        template_path: Optional[str] = None,
        button: str = "left",
        clicks: int = 1,
        show_overlay: bool = True,
        region: Optional[ScreenRegion] = None,
    ) -> Optional[CoordHint]:
        """
        Hedefi bulur, overlay gösterir ve tıklar.
        OpenGuider'ın findAndClick() fonksiyonunun Python portu.

        Döner: CoordHint (bulunduysa) veya None
        """
        hint = await self.find(target, template_path, use_ai=True, region=region)

        if hint is None:
            logger.warning(f"[CoordEngine] '{target}' bulunamadı — tıklama yapılmadı.")
            return None

        # Overlay göster (OpenGuider'ın cursor highlight'ı)
        if show_overlay and self.enable_overlay:
            asyncio.create_task(self.overlay.flash_async(hint, duration_ms=1200))

        # Tıkla
        success = await self.executor.click(hint, button=button, clicks=clicks)
        if not success:
            return None

        return hint

    async def get_screen_context(self, region: Optional[ScreenRegion] = None) -> dict:
        """
        Ekranın tam bağlamını çıkarır: boyutlar, OCR metinleri, base64.
        orchestrator.py'deki planner'a bağlam vermek için kullanılır.

        OpenGuider'ın getScreenContext() → plan.context olarak geçilir.
        """
        if region:
            bgr, snapshot = await self.capture.capture_region_async(region)
        else:
            bgr, snapshot = await self.capture.capture_full_async()

        ocr_items = await asyncio.to_thread(self.ocr.extract_all_text, bgr)
        texts = [r.text for r in ocr_items[:50]]  # ilk 50 kelime

        return {
            "width":      snapshot.width,
            "height":     snapshot.height,
            "ocr_texts":  texts,
            "text_summary": " | ".join(texts[:20]),
            "base64_png": snapshot.base64_png,
            "timestamp":  snapshot.timestamp,
        }

    async def watch_for(
        self,
        target: str,
        timeout_seconds: float = 30.0,
        poll_interval: float = 1.0,
        region: Optional[ScreenRegion] = None,
    ) -> Optional[CoordHint]:
        """
        Ekranda bir öğe görünene kadar bekler.
        OpenGuider'ın waitForElement() fonksiyonunun Python portu.

        JS'deki:
            await waitUntil(() => findElement(target), {timeout: 30000})

        Python:
            while time.time() - start < timeout: await asyncio.sleep(poll)
        """
        start = time.time()
        logger.info(f"[CoordEngine] '{target}' bekleniyor (max {timeout_seconds}s)...")

        while time.time() - start < timeout_seconds:
            hint = await self.find(target, use_ai=False, region=region)  # AI olmadan hızlı tara
            if hint:
                logger.info(f"[CoordEngine] '{target}' göründü: ({hint.x}, {hint.y})")
                return hint
            await asyncio.sleep(poll_interval)

        # Timeout — son bir kez AI ile dene
        logger.warning(f"[CoordEngine] '{target}' {timeout_seconds}s'de bulunamadı. AI ile son deneme...")
        return await self.find(target, use_ai=True, region=region)

    async def screenshot_for_ai(self, region: Optional[ScreenRegion] = None) -> str:
        """
        Gemini Vision'a göndermek için base64 PNG döner.
        orchestrator.py'deki adım executoru tarafından çağrılır.

        Kullanım:
            b64 = await coord_engine.screenshot_for_ai()
            # → AI'ya image_part olarak gönder
        """
        if region:
            _, snapshot = await self.capture.capture_region_async(region)
        else:
            _, snapshot = await self.capture.capture_full_async()
        return snapshot.base64_png


# ---------------------------------------------------------------------------
# Bağımsız Test (python -m vision.coord_engine)
# ---------------------------------------------------------------------------

async def _demo() -> None:
    """
    CoordEngine'i gerçek bir Windows ekranında test eder.
    Gemini API anahtarı olmadan sadece OCR ve screenshot test edilir.
    """
    import os

    print("\n🖥️  SIRIUS CoordEngine Demo Başlatılıyor...")
    print("─" * 50)

    engine = CoordEngine(
        gemini_key=os.environ.get("GEMINI_KEY", ""),
        enable_overlay=True,
    )

    # 1. Ekran görüntüsü al
    print("\n1. Ekran görüntüsü alınıyor...")
    bgr, snapshot = await engine.capture.capture_full_async()
    print(f"   ✅ {snapshot.width}×{snapshot.height} piksel — base64 uzunluğu: {len(snapshot.base64_png)}")

    # 2. OCR test
    print("\n2. Ekranda OCR metin tespiti...")
    ocr_items = await asyncio.to_thread(engine.ocr.extract_all_text, bgr)
    top_texts = [r.text for r in sorted(ocr_items, key=lambda x: x.confidence, reverse=True)[:10]]
    print(f"   ✅ {len(ocr_items)} metin öğesi. En yüksek güvenilirlikle: {top_texts}")

    # 3. Ekran bağlamı
    print("\n3. Ekran bağlamı çıkarılıyor...")
    ctx = await engine.get_screen_context()
    print(f"   ✅ OCR metinleri: {ctx['text_summary'][:80]}...")

    # 4. Metin arama (sadece OCR, API anahtarı gerekmez)
    search_term = "Görev" if top_texts else "OK"
    print(f"\n4. '{search_term}' metni aranıyor (OCR)...")
    hint = await asyncio.to_thread(engine.ocr.find_text, bgr, search_term)
    if hint:
        print(f"   ✅ Bulundu: ({hint.x}, {hint.y}) — '{hint.label}' — güven: {hint.confidence:.0%}")
    else:
        print(f"   ℹ️  '{search_term}' metni bu ekranda bulunamadı.")

    print("\n✅ CoordEngine demo tamamlandı.")
    print("   Tam test için GEMINI_KEY ortam değişkeni ile çalıştırın.")


if __name__ == "__main__":
    asyncio.run(_demo())