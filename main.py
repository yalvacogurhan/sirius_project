# main.py
# SIRIUS — Groq STT + LLM + edge-tts Versiyonu
#
# Gemini Live API tamamen kaldırıldı.
# Yeni ses mimarisi:
#   STT : faster-whisper (mikrofon → metin, yerel, ücretsiz)
#   LLM : Groq llama-3.3-70b (metin → cevap, çok hızlı)
#   TTS : edge-tts tr-TR-EmelNeural (karanlık, derin kadın tonu)
#
# Kurulum:
#   pip install groq edge-tts faster-whisper sounddevice pygame

import os
import sys
import asyncio
import threading
import tempfile
import logging
from datetime import datetime
from dotenv import load_dotenv

import sounddevice as sd
import numpy as np

# --- Loglama ---
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("sirius.main")

# --- Güvenlik ---
from security.license_manager import LicenseManager

load_dotenv()

# --- UI ---
from ui import SiriusUI, SettingsWindow

# --- Hafıza & Config ---
from memory.config_manager import get_config, save_api_keys
from memory.memory_manager import load_memory, format_memory_for_prompt, update_memory

# --- AI Core ---
from ai_core.planner import Planner
from ai_core.error_handler import ErrorHandler
from ai_core.executor import SiriusExecutor
from ai_core.task_queue import SiriusTaskQueue, TaskPriority
from ai_core.orchestrator import TaskOrchestrator
from ai_core.providers import ProviderRegistry, ProviderType

# --- Vision (Faz 2) ---
from vision.coord_engine import CoordEngine
from vision.ui_region import ScreenRegionManager
from vision.pointer_hint import PointerHintManager

# --- Plugin Dispatcher (Faz 5) ---
from plugins.plugin_dispatcher import PluginDispatcher, BROWSER_TOOL_DECLARATIONS

# --- Pluginler ---
from plugins.window_manager import WindowManager
from plugins.messenger import Messenger
from plugins.browser_control import BrowserController
from plugins.computer_control import ComputerController
from plugins.code_helper import CodeHelper
from plugins.dev_agent import DevAgent
from plugins.desktop import DesktopManager
from plugins.file_controller import FileController
from plugins.file_processor import FileProcessor
from plugins.computer_settings import ComputerSettings
from plugins.app_launcher import AppLauncher
from plugins.game_updater import GameUpdater
from plugins.reminder import ReminderManager
from plugins.screen_processor import VisionProcessor
from plugins.travel_manager import TravelManager
from plugins.weather import WeatherManager
from plugins.web_search import WebSearchManager
from plugins.youtube_manager import YouTubeManager
from plugins.council_manager import CouncilManager

# ---------------------------------------------------------------------------
# SABİTLER
# ---------------------------------------------------------------------------
# Türkçe edge-tts sesleri (karanlık / derin kadın tonu önerileri):
#   tr-TR-EmelNeural   → varsayılan Türkçe kadın, sakin ve net
#   tr-TR-AhmetNeural  → erkek (yedek)
# Daha karanlık/dramatik ton için pitch ve rate ayarları kullanıyoruz.

TTS_VOICE        = "tr-TR-EmelNeural"   # Türkçe kadın sesi
TTS_RATE         = "-5%"                # biraz yavaş → daha ağır hissi
TTS_PITCH        = "-10Hz"              # daha düşük/karanlık ton
TTS_VOLUME       = "+0%"

STT_MODEL        = "whisper-large-v3"   # Groq Whisper STT modeli
LLM_MODEL        = "llama-3.3-70b-versatile"  # Groq LLM

SAMPLE_RATE      = 16000
CHANNELS         = 1
CHUNK_DURATION   = 0.1    # saniye
SILENCE_THRESHOLD = 500   # RMS eşiği — bu altı sessizlik sayılır
SILENCE_DURATION  = 1.5   # saniye sessizlik → kayıt bitti
MAX_RECORD_SEC    = 30    # maksimum kayıt süresi


# ---------------------------------------------------------------------------
# ARAÇ BİLDİRİMLERİ (Groq function calling formatı)
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": "Uygulama açar.",
            "parameters": {
                "type": "object",
                "properties": {"app_name": {"type": "string"}},
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "İnternette arama yapar.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "weather_report",
            "description": "Hava durumunu söyler.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_message",
            "description": "Mesaj gönderir.",
            "parameters": {
                "type": "object",
                "properties": {
                    "receiver":     {"type": "string"},
                    "message_text": {"type": "string"},
                    "platform":     {"type": "string"},
                },
                "required": ["receiver", "message_text", "platform"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "youtube_video",
            "description": "YouTube'u kontrol eder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "query":  {"type": "string"},
                    "url":    {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "computer_settings",
            "description": "Bilgisayar ayarlarını değiştirir.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "value":  {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "screen_process",
            "description": "Ekrani analiz eder, ekranda ne oldugunu soyler veya belirli bir nesneyi bulur ve tiklar. Ekrani analiz et, ekranda ne var, masaustunde ne goruyorsun gibi sorgular icin kullan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Ne yapmak istediginin aciklamasi",
                    },
                    "target": {
                        "type": "string",
                        "description": "Bulunacak veya tiklanacak oge, opsiyonel",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["analyze", "click", "find"],
                        "description": "analyze: genel ekran analizi, click: nesneyi tikla, find: nesneyi bul",
                    },
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "flight_finder",
            "description": "Uçak veya otobüs bileti bulur.",
            "parameters": {
                "type": "object",
                "properties": {
                    "origin":      {"type": "string"},
                    "destination": {"type": "string"},
                    "date":        {"type": "string"},
                },
                "required": ["origin", "destination", "date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "agent_task",
            "description": "Karmaşık çok adımlı otonom görev başlatır.",
            "parameters": {
                "type": "object",
                "properties": {"goal": {"type": "string"}},
                "required": ["goal"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "Kullanıcı hakkında bilgiyi hafızaya kaydeder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "key":      {"type": "string"},
                    "value":    {"type": "string"},
                },
                "required": ["category", "key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "call_council",
            "description": "Çoklu yapay zeka tartışması başlatır.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic":   {"type": "string"},
                    "profile": {"type": "string"},
                },
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_on_screen",
            "description": "Ekranda belirli bir UI ogesini arar ve tiklar. Ornek: Kaydet butonu, Dosya menusu. Belirli bir nesneyi bulmak veya tiklamak icin kullan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Aranacak ogemin aciklamasi, ornek: Kaydet butonu, Dosya menusu",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["click", "find"],
                        "description": "click: bul ve tikla, find: sadece koordinat bul",
                    },
                },
                "required": ["target", "action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "switch_ai_provider",
            "description": "AI sağlayıcısını değiştirir.",
            "parameters": {
                "type": "object",
                "properties": {
                    "provider": {"type": "string"},
                },
                "required": ["provider"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shutdown_jarvis",
            "description": "Sistemi kapatır.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

# Browser tool'larini ekle (Groq formatina cevir)
def _ascii_desc(text: str) -> str:
    tr_map = {"\u0131":"i","\u0130":"I","\u011f":"g","\u011e":"G",
               "\xfc":"u","\xdc":"U","\u015f":"s","\u015e":"S",
               "\xf6":"o","\xd6":"O","\xe7":"c","\xc7":"C"}
    for t, e in tr_map.items():
        text = text.replace(t, e)
    return text

for bt in BROWSER_TOOL_DECLARATIONS:
    props = {}
    for k, v in bt.get("parameters", {}).get("properties", {}).items():
        if k == "type":
            continue
        prop_type = str(v.get("type", "string")).lower()
        if prop_type not in ("string", "number", "boolean", "integer"):
            prop_type = "string"
        props[k] = {
            "type": prop_type,
            "description": _ascii_desc(v.get("description", "")),
        }
    TOOLS.append({
        "type": "function",
        "function": {
            "name": bt["name"],
            "description": _ascii_desc(bt.get("description", bt["name"])),
            "parameters": {
                "type": "object",
                "properties": props,
                "required": [
                    r for r in bt.get("parameters", {}).get("required", [])
                    if r != "type"
                ],
            },
        },
    })


# ---------------------------------------------------------------------------
# PROMPT BİRLEŞTİRİCİ
# ---------------------------------------------------------------------------
def build_system_prompt() -> str:
    try:
        with open("ai_core/promt.txt", "r", encoding="utf-8") as f:
            base_prompt = f.read()
    except Exception as e:
        logger.warning(f"promt.txt okunamadı: {e}")
        base_prompt = "Sen Sirius'sun. Türkçe konuşan, zeki ve kararlı bir AI asistansın."

    now      = datetime.now()
    time_str = now.strftime("%A, %d %B %Y — %H:%M")

    mem_data = load_memory()
    mem_str  = format_memory_for_prompt(mem_data)

    parts = [
        f"[ŞU ANKİ TARİH VE SAAT]\n{time_str}\n",
    ]
    if mem_str:
        parts.append(mem_str)
    parts.append(base_prompt)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# TTS — edge-tts ile karanlık kadın sesi
# ---------------------------------------------------------------------------
class SiriusTTS:
    """
    edge-tts ile metin → ses dönüştürme.
    tr-TR-EmelNeural + düşük pitch = karanlık kadın tonu.
    """

    def __init__(self):
        self._lock = asyncio.Lock()

    async def speak(self, text: str) -> None:
        """Metni seslendirir ve çalar."""
        if not text or not text.strip():
            return

        async with self._lock:
            try:
                import edge_tts          # type: ignore
                import pygame            # type: ignore

                communicate = edge_tts.Communicate(
                    text=text,
                    voice=TTS_VOICE,
                    rate=TTS_RATE,
                    pitch=TTS_PITCH,
                    volume=TTS_VOLUME,
                )

                # Geçici dosyaya yaz
                with tempfile.NamedTemporaryFile(
                    delete=False, suffix=".mp3"
                ) as tmp:
                    tmp_path = tmp.name

                await communicate.save(tmp_path)

                # pygame ile çal
                await asyncio.to_thread(self._play_mp3, tmp_path)

                # Temizle
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

            except ImportError as e:
                logger.error(f"[TTS] Kütüphane eksik: {e}")
                logger.error("Kurulum: pip install edge-tts pygame")
            except Exception as e:
                logger.error(f"[TTS] Hata: {e}")

    def _play_mp3(self, path: str) -> None:
        """pygame ile MP3 çalar (blocking, to_thread içinde çağrılır)."""
        try:
            import pygame  # type: ignore

            if not pygame.mixer.get_init():
                pygame.mixer.init()

            pygame.mixer.music.load(path)
            pygame.mixer.music.play()

            while pygame.mixer.music.get_busy():
                import time
                time.sleep(0.05)

            pygame.mixer.music.stop()
        except Exception as e:
            logger.error(f"[TTS] Oynatma hatası: {e}")

    def speak_sync(self, text: str) -> None:
        """Thread'den çağrılabilir senkron wrapper."""
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(self.speak(text))
            loop.close()
        except Exception as e:
            logger.error(f"[TTS] Sync konuşma hatası: {e}")


# ---------------------------------------------------------------------------
# STT — faster-whisper ile mikrofon → metin
# ---------------------------------------------------------------------------
class SiriusSTT:
    """
    faster-whisper ile yerel ses tanıma.
    Groq Whisper API'si de destekleniyor (groq_key varsa).
    """

    def __init__(self, groq_key: str = ""):
        self.groq_key   = groq_key
        self._model     = None  # lazy load

    def _get_model(self):
        """faster-whisper modelini lazy load eder."""
        if self._model is None:
            try:
                from faster_whisper import WhisperModel  # type: ignore
                logger.info("[STT] faster-whisper modeli yükleniyor (ilk seferinde uzun sürer)...")
                self._model = WhisperModel("base", device="cpu", compute_type="int8")
                logger.info("[STT] Model hazır.")
            except ImportError:
                logger.error("[STT] faster-whisper bulunamadı: pip install faster-whisper")
                return None
        return self._model

    def record_until_silence(self) -> np.ndarray:
        """
        Kullanıcı konuşmaya başlayana kadar bekler,
        sessizlikte durur ve ses verisini döner.
        """
        logger.info("[STT] 🎤 Dinleniyor...")
        frames      = []
        silent_time = 0.0
        speaking    = False
        chunk_size  = int(SAMPLE_RATE * CHUNK_DURATION)

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=chunk_size,
        ) as stream:
            total_time = 0.0
            while total_time < MAX_RECORD_SEC:
                chunk, _ = stream.read(chunk_size)
                rms = np.sqrt(np.mean(chunk.astype(np.float32) ** 2))

                if rms > SILENCE_THRESHOLD:
                    speaking    = True
                    silent_time = 0.0
                    frames.append(chunk.copy())
                elif speaking:
                    frames.append(chunk.copy())
                    silent_time += CHUNK_DURATION
                    if silent_time >= SILENCE_DURATION:
                        break

                total_time += CHUNK_DURATION

        if not frames:
            return np.array([], dtype=np.int16)

        return np.concatenate(frames, axis=0)

    async def transcribe(self, audio: np.ndarray) -> str:
        """Ses verisini metne çevirir."""
        if len(audio) == 0:
            return ""

        # Groq Whisper API varsa kullan (daha hızlı)
        if self.groq_key:
            try:
                return await asyncio.to_thread(
                    self._transcribe_groq, audio
                )
            except Exception as e:
                logger.warning(f"[STT] Groq Whisper hatası, yerel modele geçiliyor: {e}")

        # Yerel faster-whisper
        return await asyncio.to_thread(self._transcribe_local, audio)

    def _transcribe_groq(self, audio: np.ndarray) -> str:
        """Groq Whisper API ile transkripsiyon."""
        from groq import Groq  # type: ignore

        client = Groq(api_key=self.groq_key)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            import soundfile as sf  # type: ignore
            sf.write(tmp_path, audio, SAMPLE_RATE)

            with open(tmp_path, "rb") as f:
                transcription = client.audio.transcriptions.create(
                    file=("audio.wav", f, "audio/wav"),
                    model=STT_MODEL,
                    language="tr",
                    response_format="text",
                )
            return str(transcription).strip()
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def _transcribe_local(self, audio: np.ndarray) -> str:
        """faster-whisper ile yerel transkripsiyon."""
        model = self._get_model()
        if model is None:
            return ""

        try:
            audio_float = audio.astype(np.float32) / 32768.0
            segments, _ = model.transcribe(
                audio_float,
                language="tr",
                beam_size=5,
            )
            return " ".join(seg.text for seg in segments).strip()
        except Exception as e:
            logger.error(f"[STT] Transkripsiyon hatası: {e}")
            return ""


# ---------------------------------------------------------------------------
# GROQ LLM İSTEMCİSİ
# ---------------------------------------------------------------------------
class GroqBrain:
    """
    Groq LLM ile düşünme ve yanıt üretme.
    Function calling desteği var.
    """

    def __init__(self, groq_key: str):
        self.groq_key   = groq_key
        self._history: list[dict] = []
        self._system    = build_system_prompt()

    def reset_history(self) -> None:
        self._history.clear()

    async def think(self, user_text: str) -> tuple[str, list[dict]]:
        """
        Kullanıcı metnini işler.
        Döner: (yanıt_metni, tool_call_listesi)
        tool_call_listesi doluysa → tool yürütülmeli
        """
        if not self.groq_key:
            return "Groq API anahtarı eksik.", []

        try:
            from groq import Groq  # type: ignore
        except ImportError:
            return "Groq kütüphanesi bulunamadı: pip install groq", []

        self._history.append({"role": "user", "content": user_text})

        messages = [{"role": "system", "content": self._system}] + self._history

        try:
            client   = Groq(api_key=self.groq_key)
            response = await asyncio.to_thread(
                lambda: client.chat.completions.create(
                    model=LLM_MODEL,
                    messages=messages,
                    tools=TOOLS,
                    tool_choice="auto",
                    max_tokens=1024,
                    temperature=0.7,
                )
            )
        except Exception as e:
            logger.error(f"[Groq] API hatası: {e}")
            return f"Groq hatası: {e}", []

        msg = response.choices[0].message

        # Tool call var mı?
        if msg.tool_calls:
            self._history.append({
                "role":       "assistant",
                "content":    msg.content or "",
                "tool_calls": [
                    {
                        "id":       tc.id,
                        "type":     "function",
                        "function": {
                            "name":      tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            })
            tool_calls = [
                {
                    "id":   tc.id,
                    "name": tc.function.name,
                    "args": __import__("json").loads(tc.function.arguments or "{}"),
                }
                for tc in msg.tool_calls
            ]
            return "", tool_calls

        # Normal metin yanıtı
        reply = msg.content or ""
        self._history.append({"role": "assistant", "content": reply})

        # Geçmişi maksimum 20 mesajda tut
        if len(self._history) > 20:
            self._history = self._history[-20:]

        return reply, []

    async def send_tool_result(self, tool_id: str, tool_name: str, result: str) -> str:
        """
        Tool sonucunu modele gönderir ve son yanıtı alır.
        """
        if not self.groq_key:
            return result

        self._history.append({
            "role":         "tool",
            "tool_call_id": tool_id,
            "content":      result,
            "name":         tool_name,
        })

        messages = [{"role": "system", "content": self._system}] + self._history

        try:
            from groq import Groq  # type: ignore
            client   = Groq(api_key=self.groq_key)
            response = await asyncio.to_thread(
                lambda: client.chat.completions.create(
                    model=LLM_MODEL,
                    messages=messages,
                    max_tokens=512,
                    temperature=0.7,
                )
            )
            reply = response.choices[0].message.content or result
            self._history.append({"role": "assistant", "content": reply})
            return reply
        except Exception as e:
            logger.error(f"[Groq] Tool yanıt hatası: {e}")
            return result


# ---------------------------------------------------------------------------
# SİRİUS CANLI — Ana Ses + Düşünme Döngüsü
# ---------------------------------------------------------------------------
class SiriusLive:
    """
    Groq STT + LLM + edge-tts ile sesli asistan döngüsü.
    Gemini Live API tamamen kaldırıldı.
    """

    def __init__(self, controller):
        self.controller  = controller
        self.tts         = SiriusTTS()
        self.stt: SiriusSTT | None = None  # apply_keys sonrası init
        self.brain: GroqBrain | None = None
        self._running    = False
        self._muted      = False

    def _init_components(self) -> bool:
        """Groq anahtarı ile STT ve LLM başlatır."""
        groq_key = self._get_groq_key()
        if not groq_key:
            logger.warning("[SiriusLive] Groq API anahtarı eksik!")
            return False

        self.stt   = SiriusSTT(groq_key=groq_key)
        self.brain = GroqBrain(groq_key=groq_key)
        return True

    def _get_groq_key(self) -> str:
        cfg = self.controller.config_data
        return (
            cfg.get("groq_key")
            or cfg.get("groq_api_key")
            or ""
        )

    def send_text(self, text: str) -> None:
        """
        UI metin kutusundan gelen komutu işler.
        Ses kaydı olmadan direkt Groq'a gönderir.
        """
        if not text.strip():
            return
        logger.info(f"[SiriusLive] ✉️  Metin komutu: '{text[:60]}'")
        loop = asyncio.new_event_loop()
        threading.Thread(
            target=lambda: loop.run_until_complete(self._process(text)),
            daemon=True,
        ).start()

    async def _execute_tool(self, tool_id: str, name: str, args: dict) -> str:
        """Tool'u çalıştırır ve sonucu döner."""
        logger.info(f"[SiriusLive] 🔧 Tool: {name} | {args}")
        self.controller.ui.update_status("THINKING", "#f1c40f")

        result = "Anlaşılamadı."
        try:
            if name == "save_memory":
                cat = args.get("category", "notes")
                k   = args.get("key", "")
                v   = args.get("value", "")
                if k and v:
                    update_memory({cat: {k: {"value": v}}})
                return "Hafızaya kaydedildi."

            elif name == "open_app":
                result = self.controller.launcher.execute(args)
            elif name == "weather_report":
                result = self.controller.weather.execute(args)
            elif name == "web_search":
                result = self.controller.web.execute(args)
            elif name == "send_message":
                result = self.controller.messenger.execute(args)
            elif name == "youtube_video":
                result = self.controller.yt.execute(args)
            elif name == "computer_settings":
                result = self.controller.sys_set.execute(
                    args.get("action"), args.get("value")
                )
            elif name == "screen_process":
                target = args.get("target", "")
                action = args.get("action", "analyze")

                if action == "analyze" or (not target and not action):
                    # Ekranın tam bağlamını al — OCR + AI analizi
                    try:
                        ctx = await self.controller.coord_engine.get_screen_context()
                        ocr_texts = ctx.get("text_summary", "")
                        # AI'ya ekran görüntüsü gönder ve ne olduğunu sor
                        b64 = ctx.get("base64_png", "")
                        if b64 and self.controller.coord_engine._ai:
                            from vision.coord_engine import ImageAttachment  # type: ignore
                            snap_class = type("S", (), {"base64_png": b64, "width": ctx.get("width", 0), "height": ctx.get("height", 0)})()
                            ai_result = await self.controller.coord_engine._ai.find(
                                snap_class,
                                "Ekranda ne görüyorsun? Açık uygulamaları, pencereleri ve içerikleri kısaca özetle.",
                                ocr_texts,
                            )
                            if ai_result:
                                result = ai_result.label
                            else:
                                result = f"Ekranda şunlar görünüyor: {ocr_texts[:300]}" if ocr_texts else "Ekran analiz edildi."
                        else:
                            result = f"Ekranda şunlar görünüyor: {ocr_texts[:300]}" if ocr_texts else self.controller.vision.execute(args)
                    except Exception as e:
                        result = self.controller.vision.execute(args)

                elif target and action == "click":
                    hint = await self.controller.coord_engine.find_and_click(
                        target=target, show_overlay=True
                    )
                    result = (
                        f"'{hint.label}' tıklandı: ({hint.x}, {hint.y})"
                        if hint else f"'{target}' ekranda bulunamadı."
                    )
                elif target and action == "find":
                    region = self.controller.region_manager.get_safe_search_region()
                    hint   = await self.controller.coord_engine.find(target, region=region)
                    result = (
                        f"'{hint.label}' bulundu: ({hint.x}, {hint.y})"
                        if hint else f"'{target}' bulunamadı."
                    )
                else:
                    result = self.controller.vision.execute(args)

            elif name == "flight_finder":
                result = self.controller.travel.execute(args)
            elif name == "agent_task":
                result = await self.controller.run_orchestrated_task(args.get("goal", ""))
            elif name == "call_council":
                result = self.controller.council.execute(
                    args.get("topic"), args.get("profile", "execution")
                )
            elif name == "find_on_screen":
                target = args.get("target", "")
                action = args.get("action", "click")
                region = self.controller.region_manager.get_safe_search_region()
                if action == "click":
                    hint = await self.controller.coord_engine.find_and_click(
                        target=target, region=region, show_overlay=True
                    )
                    result = (
                        f"'{hint.label}' tıklandı: ({hint.x}, {hint.y})"
                        if hint else f"'{target}' ekranda bulunamadı."
                    )
                else:
                    hint = await self.controller.coord_engine.find(target, region=region)
                    result = (
                        f"'{hint.label}': ({hint.x}, {hint.y})"
                        if hint else f"'{target}' bulunamadı."
                    )
            elif name == "switch_ai_provider":
                provider_name = args.get("provider", "").lower()
                provider_map  = {
                    "gemini": ProviderType.GEMINI,
                    "groq":   ProviderType.GROQ,
                    "openai": ProviderType.OPENAI,
                    "ollama": ProviderType.OLLAMA,
                }
                pt = provider_map.get(provider_name)
                if pt:
                    self.controller.provider_registry.set_active(pt)
                    result = f"Sağlayıcı değiştirildi: {provider_name.upper()}"
                else:
                    result = f"Bilinmeyen sağlayıcı: {provider_name}"

            elif name.startswith("browser_"):
                result = await self.controller.dispatcher.dispatch(name, args)

            elif name == "shutdown_jarvis":
                self.controller.ui.update_status("OFFLINE", "#7f8c8d")
                self.controller.shutdown()
                os._exit(0)

            else:
                result = f"Bilinmeyen araç: {name}"

        except Exception as e:
            result = f"Hata: {e}"
            logger.error(f"[SiriusLive] ❌ Tool hatası [{name}]: {e}", exc_info=True)

        return str(result)

    async def _process(self, user_text: str) -> None:
        """
        Kullanıcı metnini işler:
        1. Groq'a gönder
        2. Tool call varsa çalıştır
        3. Yanıtı seslendir
        """
        if not self.brain:
            if not self._init_components():
                await self.tts.speak("Groq API anahtarı eksik. Lütfen ayarlardan girin.")
                return

        self.controller.ui.update_status("THINKING", "#f1c40f")

        # LLM'e gönder
        reply, tool_calls = await self.brain.think(user_text)

        # Tool call'ları işle
        for tc in tool_calls:
            tool_result = await self._execute_tool(tc["id"], tc["name"], tc["args"])
            # Tool sonucunu modele geri gönder ve son yanıtı al
            reply = await self.brain.send_tool_result(tc["id"], tc["name"], tool_result)

        # Cevabı seslendir
        if reply and reply.strip():
            self.controller.ui.update_status("SPEAKING", "#2ecc71")
            await self.tts.speak(reply)

        if not self.controller.is_muted:
            self.controller.ui.update_status("LISTENING", "#ff4444")

    async def run(self) -> None:
        """
        Ana ses döngüsü:
        Mikrofon → STT → Groq → TTS → tekrar
        """
        logger.info("[SiriusLive] 🎙️  Ses döngüsü başlatılıyor...")

        if not self._init_components():
            self.controller.ui.update_status("NO KEY", "#e74c3c")
            logger.error("[SiriusLive] Groq anahtarı bulunamadı — ses döngüsü duruyor.")
            # Metin moduyla devam et (ses yok ama UI çalışır)
            while True:
                await asyncio.sleep(5)
                # Anahtar gelirse yeniden dene
                if self._get_groq_key():
                    if self._init_components():
                        logger.info("[SiriusLive] Groq anahtarı bulundu, ses döngüsü başlıyor.")
                        break

        self._running = True
        self.controller.ui.update_status("LISTENING", "#ff4444")

        # Hoşgeldin mesajı
        await self.tts.speak("Sirius aktif. Sizi dinliyorum.")

        while self._running:
            try:
                if self.controller.is_muted:
                    await asyncio.sleep(0.5)
                    continue

                # Ses kaydet
                audio = await asyncio.to_thread(self.stt.record_until_silence)

                if len(audio) == 0:
                    continue

                # STT
                self.controller.ui.update_status("PROCESSING", "#9b59b6")
                user_text = await self.stt.transcribe(audio)

                if not user_text or len(user_text.strip()) < 2:
                    self.controller.ui.update_status("LISTENING", "#ff4444")
                    continue

                logger.info(f"[STT] 📝 '{user_text}'")
                self.controller.ui.update_status("THINKING", "#f1c40f")

                # İşle ve yanıtla
                await self._process(user_text)

            except Exception as e:
                logger.error(f"[SiriusLive] ❌ Döngü hatası: {e}", exc_info=True)
                await asyncio.sleep(1)


# ---------------------------------------------------------------------------
# KOMUTA MERKEZİ
# ---------------------------------------------------------------------------
class SiriusController:
    def __init__(self):
        self.config_data = get_config()
        self.is_muted    = False

        self.ui = SiriusUI(
            on_text_cmd=self.start_text_agent,
            on_voice_cmd=self.toggle_mute,
            on_open_settings=self.open_settings,
        )

        # AI Core
        self.brain         = Planner()
        self.error_handler = ErrorHandler()

        # Pluginler
        self.windows   = WindowManager()
        self.messenger = Messenger()
        self.browser   = BrowserController()
        self.computer  = ComputerController()
        self.coder     = CodeHelper()
        self.dev_agent = DevAgent()
        self.desktop   = DesktopManager()
        self.file_ctrl = FileController()
        self.file_proc = FileProcessor()
        self.sys_set   = ComputerSettings()
        self.launcher  = AppLauncher()
        self.games     = GameUpdater()
        self.remind    = ReminderManager()
        self.vision    = VisionProcessor()
        self.travel    = TravelManager()
        self.weather   = WeatherManager()
        self.web       = WebSearchManager()
        self.yt        = YouTubeManager()
        self.council   = CouncilManager()

        # Executor & Queue
        plugins_dict = {
            "messenger":  self.messenger,
            "windows":    self.windows,
            "web_search": self.web,
        }
        self.executor = SiriusExecutor(
            brain=self.brain,
            error_handler=self.error_handler,
            plugins_dict=plugins_dict,
        )
        self.queue = SiriusTaskQueue(executor=self.executor)

        # Faz 5: Plugin Dispatcher
        self.dispatcher = PluginDispatcher(controller=self)

        # Ses sistemi (Groq STT + LLM + edge-tts)
        self.live_api = SiriusLive(self)

        # Faz 3: Provider Registry
        self.provider_registry = ProviderRegistry(self.config_data)
        logger.info(f"[Sirius] AI Sağlayıcılar:\n{self.provider_registry.summary()}")

        # Faz 2: Ekran Farkındalığı
        self.region_manager = ScreenRegionManager()
        self.hint_manager   = PointerHintManager()
        loaded = self.hint_manager.load_from_memory()
        logger.info(f"[Sirius] {loaded} koordinat önbellekten yüklendi.")

        self.coord_engine = CoordEngine(
            gemini_key=self._get_gemini_key(),
            enable_overlay=True,
        )

        # Faz 1+4+5: Orchestrator
        self.orchestrator = TaskOrchestrator(
            provider_registry=self.provider_registry,
            tool_executor=self.dispatcher.as_executor(),
            on_status_change=self._on_orchestrator_status,
            max_step_attempts=2,
            step_timeout_seconds=60.0,
            require_confirm=False,
        )
        logger.info("[Sirius] ✅ TaskOrchestrator hazır.")

        self.apply_keys()

    # ------------------------------------------------------------------
    # Anahtar yardımcıları
    # ------------------------------------------------------------------

    def _get_gemini_key(self) -> str:
        return (
            self.config_data.get("gemini_key")
            or self.config_data.get("gemini_api_key")
            or ""
        )

    def _get_groq_key(self) -> str:
        return (
            self.config_data.get("groq_key")
            or self.config_data.get("groq_api_key")
            or ""
        )

    # ------------------------------------------------------------------
    # Anahtar Dağıtımı
    # ------------------------------------------------------------------

    def apply_keys(self):
        gk  = self._get_gemini_key()
        grk = self._get_groq_key()

        self.brain.gemini_key         = gk
        self.error_handler.gemini_key = gk
        self.executor.gemini_key      = gk
        self.vision.gemini_key        = gk
        self.web.gemini_key           = gk
        self.yt.gemini_key            = gk
        self.travel.gemini_key        = gk
        self.coder.gemini_key         = gk
        self.dev_agent.gemini_key     = gk
        self.council.gemini_key       = gk

        if hasattr(self, "provider_registry"):
            self.provider_registry.update_keys(self.config_data)

        if hasattr(self, "coord_engine") and getattr(self.coord_engine, "_ai", None):
            self.coord_engine._ai.gemini_key = gk

        if hasattr(self, "orchestrator"):
            self.orchestrator.update_keys(
                provider_registry=self.provider_registry,
                gemini_key=gk,
            )

        # SiriusLive'ı yeniden başlat (anahtar değişince)
        if hasattr(self, "live_api") and self.live_api.brain:
            self.live_api.brain.groq_key = grk
        if hasattr(self, "live_api") and self.live_api.stt:
            self.live_api.stt.groq_key = grk

        logger.info(
            f"[Sirius] Anahtarlar dağıtıldı — "
            f"Gemini: {'✅' if gk else '❌'} | "
            f"Groq: {'✅' if grk else '❌'}"
        )

    # ------------------------------------------------------------------
    # Orchestrator Callback
    # ------------------------------------------------------------------

    def _on_orchestrator_status(self, plan) -> None:
        status_colors = {
            "planning":         ("#f39c12", "PLANNING"),
            "running":          ("#3498db", "RUNNING"),
            "awaiting_confirm": ("#9b59b6", "CONFIRM?"),
            "replanning":       ("#e67e22", "REPLANNING"),
            "done":             ("#2ecc71", "DONE"),
            "failed":           ("#e74c3c", "FAILED"),
            "cancelled":        ("#7f8c8d", "CANCELLED"),
        }
        color, label = status_colors.get(
            plan.status, ("#ffffff", plan.status.upper())
        )
        self.ui.update_status(label, color)

        if plan.status in ("done", "failed") and plan.summary:
            self.live_api.send_text(
                f"Görev tamamlandı: {plan.summary}"
                if plan.status == "done"
                else f"Görev başarısız: {plan.summary}"
            )

    # ------------------------------------------------------------------
    # Orkestrasyon
    # ------------------------------------------------------------------

    async def run_orchestrated_task(self, goal: str) -> str:
        try:
            ctx         = self.region_manager.context_as_string()
            screen_ctx  = await self.coord_engine.get_screen_context()
            ocr_summary = screen_ctx.get("text_summary", "")
            context     = (
                f"{ctx}\nEkran: {ocr_summary}" if ocr_summary else ctx
            )
        except Exception:
            context = ""
        task_id = await self.orchestrator.submit(goal=goal, context=context)
        return f"Görev başlatıldı (ID: {task_id[:8]})."

    # ------------------------------------------------------------------
    # Kapatma
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        try:
            self.live_api._running = False
            if hasattr(self, "hint_manager"):
                self.hint_manager.flush_to_memory()
            logger.info("[Sirius] Kapatıldı.")
        except Exception as e:
            logger.warning(f"[Sirius] Kapatma hatası: {e}")

    # ------------------------------------------------------------------
    # UI Callback'leri
    # ------------------------------------------------------------------

    def open_settings(self):
        SettingsWindow(self.ui, self.config_data, self.update_keys)

    def update_keys(self, groq_key, serp_key, gemini_key, openai_key):
        save_api_keys(
            gemini_api_key=gemini_key,
            serpapi_key=serp_key,
            groq_api_key=groq_key,
            openai_api_key=openai_key,
        )
        self.config_data = get_config()
        self.apply_keys()
        self.ui.update_status("SAVED!", "#2ecc71")

    def start_text_agent(self, query: str):
        """UI metin kutusundan gelen komutu işler."""
        self.live_api.send_text(query)

    def toggle_mute(self):
        self.is_muted = not self.is_muted
        if self.is_muted:
            self.ui.update_status("MUTED", "#7f8c8d")
        else:
            self.ui.update_status("LISTENING", "#ff4444")

    # ------------------------------------------------------------------
    # Çalıştır
    # ------------------------------------------------------------------

    def run(self):
        self.queue.start()

        def live_runner():
            asyncio.run(self.live_api.run())

        threading.Thread(target=live_runner, daemon=True).start()
        self.ui.mainloop()


# ---------------------------------------------------------------------------
# GÜVENLİK DUVARI
# ---------------------------------------------------------------------------
def security_checkpoint():
    lic_manager = LicenseManager()
    if lic_manager.check_local_license():
        logger.info("✅ Lisans doğrulandı.")
        return

    admin_key  = "UMeHLZiuxHxyFJLG2mS4f79CxonKsIi4PEdDSDjpW3PCLUjoeMcKfGtSTkbXms1"
    server_url = "https://yalvac.pythonanywhere.com"

    try:
        from security.login_ui import show_login_window
        is_authenticated = show_login_window(lic_manager, admin_key, server_url)
        if not is_authenticated:
            logger.error("❌ Giriş iptal edildi.")
            sys.exit(0)
    except ImportError as e:
        logger.error(f"⚠️  Görsel arayüz yüklenemedi: {e}")
        sys.exit(1)

    logger.info("✅ Lisans geçerli. Sirius başlatılıyor...")


# ---------------------------------------------------------------------------
# BAŞLANGIÇ
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    security_checkpoint()
    app = SiriusController()
    app.run()
