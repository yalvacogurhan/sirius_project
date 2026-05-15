# main.py

import os
import sys
import asyncio
import threading
import traceback
from datetime import datetime

import sounddevice as sd
from google import genai
from google.genai import types

# --- UI (Arayüz) Modülü ---
from ui import SiriusUI, SettingsWindow

# --- AYAR VE HAFIZA YÖNETİCİLERİ ---
from memory.config_manager import get_config, save_api_keys
from memory.memory_manager import load_memory, format_memory_for_prompt, update_memory

# --- AI Core (Yapay Zeka Beyni, Kuyruk ve Hata Yöneticisi) ---
from ai_core.planner import Planner
from ai_core.error_handler import ErrorHandler
from ai_core.executor import SiriusExecutor
from ai_core.task_queue import SiriusTaskQueue, TaskPriority

# --- Plugins (Yetenek Cephaneliği) ---
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
from plugins.council_manager import CouncilManager  # <--- KONSEY EKLENDİ

LIVE_MODEL = "models/gemini-2.5-flash-native-audio-preview-12-2025"
CHANNELS = 1
SEND_SAMPLE_RATE = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE = 1024

# --- PROMPT, ZAMAN VE HAFIZA BİRLEŞTİRİCİ ---
def build_system_instruction():
    try:
        with open("core/prompt.txt", "r", encoding="utf-8") as f:
            base_prompt = f.read()
    except Exception as e:
        print(f"⚠️ core/prompt.txt okunamadı. Hata: {e}")
        base_prompt = "Sen Sirius'sun. Gerçek zamanlı sesli asistansın. Türkçe konuş."

    now = datetime.now()
    time_str = now.strftime("%A, %d %B %Y — %H:%M")
    time_ctx = f"[ŞU ANKİ TARİH VE SAAT]\nŞu anki zaman: {time_str}\nSaat ve gün hesaplamaları için bunu kullan.\n\n"

    mem_data = load_memory()
    mem_str = format_memory_for_prompt(mem_data)

    parts = [time_ctx]
    if mem_str: 
        parts.append(mem_str)
    parts.append(base_prompt)

    return "\n".join(parts)

# --- ARAÇ BİLDİRİMLERİ (LIVE API İÇİN) ---
TOOL_DECLARATIONS = [
    {"name": "open_app", "description": "Uygulama açar.", "parameters": {"type": "OBJECT", "properties": {"app_name": {"type": "STRING"}}, "required": ["app_name"]}},
    {"name": "web_search", "description": "İnternette arama yapar.", "parameters": {"type": "OBJECT", "properties": {"query": {"type": "STRING"}}, "required": ["query"]}},
    {"name": "weather_report", "description": "Hava durumunu söyler.", "parameters": {"type": "OBJECT", "properties": {"city": {"type": "STRING"}}, "required": ["city"]}},
    {"name": "send_message", "description": "Mesaj gönderir.", "parameters": {"type": "OBJECT", "properties": {"receiver": {"type": "STRING"}, "message_text": {"type": "STRING"}, "platform": {"type": "STRING"}}, "required": ["receiver", "message_text", "platform"]}},
    {"name": "youtube_video", "description": "YouTube'u kontrol eder.", "parameters": {"type": "OBJECT", "properties": {"action": {"type": "STRING"}, "query": {"type": "STRING"}, "url": {"type": "STRING"}}}},
    {"name": "computer_settings", "description": "Bilgisayar ayarlarını değiştirir.", "parameters": {"type": "OBJECT", "properties": {"action": {"type": "STRING"}, "value": {"type": "STRING"}}}},
    {"name": "screen_process", "description": "Ekrana veya kameraya bakarak ne olduğunu analiz eder.", "parameters": {"type": "OBJECT", "properties": {"angle": {"type": "STRING"}, "text": {"type": "STRING"}}, "required": ["text"]}},
    {"name": "flight_finder", "description": "Uçak veya otobüs bileti bulur.", "parameters": {"type": "OBJECT", "properties": {"origin": {"type": "STRING"}, "destination": {"type": "STRING"}, "date": {"type": "STRING"}}, "required": ["origin", "destination", "date"]}},
    {"name": "agent_task", "description": "Kuyruğa karmaşık, çok adımlı bir otonom görev ekler.", "parameters": {"type": "OBJECT", "properties": {"goal": {"type": "STRING"}}, "required": ["goal"]}},
    {"name": "save_memory", "description": "Kullanıcı hakkında önemli bir bilgiyi sessizce hafızaya kaydeder.", "parameters": {"type": "OBJECT", "properties": {"category": {"type": "STRING"}, "key": {"type": "STRING"}, "value": {"type": "STRING"}}, "required": ["category", "key", "value"]}},
    {"name": "call_council", "description": "Kullanıcı karmaşık bir karar, strateji veya fikir danıştığında Yüksek Zeka Konseyini (çoklu yapay zeka tartışması) çağırır. Tavsiye verir.", "parameters": {"type": "OBJECT", "properties": {"topic": {"type": "STRING"}, "profile": {"type": "STRING", "description": "'execution' (hızlı karar), 'exploration' (derin analiz) veya 'classic' (tüm üyeler) olabilir."}}, "required": ["topic"]}},
    {"name": "shutdown_jarvis", "description": "Sistemi kapatır.", "parameters": {"type": "OBJECT", "properties": {}}}
]

# --- LIVE API BAĞLANTISI (Gerçek Zamanlı Ses) ---
class SiriusLive:
    def __init__(self, controller):
        self.controller = controller
        self.session = None
        self.audio_in_queue = None
        self.out_queue = None
        self._loop = None
        self._is_speaking = False
        self._speaking_lock = threading.Lock()
        self._turn_done_event = None

    def send_text(self, text: str):
        if not self._loop or not self.session: return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(turns={"parts": [{"text": text}]}, turn_complete=True),
            self._loop
        )

    def set_speaking(self, value: bool):
        with self._speaking_lock: self._is_speaking = value
        if value: self.controller.ui.update_status("SPEAKING", "#2ecc71")
        elif not self.controller.is_muted: self.controller.ui.update_status("LISTENING", "#ff4444")

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})
        print(f"[SiriusLive] 🔧 Araç Tetiklendi: {name} {args}")
        self.controller.ui.update_status("THINKING", "#f1c40f")

        if name == "save_memory":
            cat = args.get("category", "notes")
            k = args.get("key", "")
            v = args.get("value", "")
            if k and v: update_memory({cat: {k: {"value": v}}})
            if not self.controller.is_muted: self.controller.ui.update_status("LISTENING", "#ff4444")
            return types.FunctionResponse(id=fc.id, name=name, response={"result": "ok", "silent": True})

        result = "Anlaşılamadı."
        try:
            if name == "open_app": result = self.controller.launcher.execute(args)
            elif name == "weather_report": result = self.controller.weather.execute(args)
            elif name == "web_search": result = self.controller.web.execute(args)
            elif name == "send_message": result = self.controller.messenger.execute(args)
            elif name == "youtube_video": result = self.controller.yt.execute(args)
            elif name == "computer_settings": result = self.controller.sys_set.execute(args.get("action"), args.get("value"))
            elif name == "screen_process": result = self.controller.vision.execute(args)
            elif name == "flight_finder": result = self.controller.travel.execute(args)
            elif name == "agent_task":
                task_id = self.controller.queue.submit(goal=args.get("goal"), priority=TaskPriority.NORMAL)
                result = f"Görev arka plana eklendi (ID: {task_id})."
            elif name == "call_council":  # <--- KONSEY TETİKLEYİCİSİ
                result = self.controller.council.execute(args.get("topic"), args.get("profile", "execution"))
            elif name == "shutdown_jarvis":
                self.controller.ui.update_status("OFFLINE", "#7f8c8d")
                os._exit(0)
            else: result = f"Bilinmeyen araç: {name}"

        except Exception as e:
            result = f"Hata oluştu: {str(e)}"
            print(f"[SiriusLive] ❌ {result}")

        if not self.controller.is_muted: self.controller.ui.update_status("LISTENING", "#ff4444")
        return types.FunctionResponse(id=fc.id, name=name, response={"result": result})

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            await self.session.send_realtime_input(media=msg)

    async def _listen_audio(self):
        loop = asyncio.get_event_loop()
        def callback(indata, frames, time_info, status):
            with self._speaking_lock: is_speaking = self._is_speaking
            if not is_speaking and not self.controller.is_muted:
                loop.call_soon_threadsafe(self.out_queue.put_nowait, {"data": indata.tobytes(), "mime_type": "audio/pcm"})

        try:
            with sd.InputStream(samplerate=SEND_SAMPLE_RATE, channels=CHANNELS, dtype="int16", blocksize=CHUNK_SIZE, callback=callback):
                while True: await asyncio.sleep(0.1)
        except Exception as e: print(f"[SiriusLive] ❌ Mic Hatası: {e}")

    async def _receive_audio(self):
        try:
            while True:
                async for response in self.session.receive():
                    if response.data:
                        if self._turn_done_event and self._turn_done_event.is_set(): self._turn_done_event.clear()
                        self.audio_in_queue.put_nowait(response.data)
                    
                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                        await self.session.send_tool_response(function_responses=fn_responses)
        except Exception as e: print(f"[SiriusLive] ❌ Alım Hatası: {e}")

    async def _play_audio(self):
        stream = sd.RawOutputStream(samplerate=RECEIVE_SAMPLE_RATE, channels=CHANNELS, dtype="int16", blocksize=CHUNK_SIZE)
        stream.start()
        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(self.audio_in_queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    if self._turn_done_event and self._turn_done_event.is_set() and self.audio_in_queue.empty():
                        self.set_speaking(False)
                        self._turn_done_event.clear()
                    continue
                self.set_speaking(True)
                await asyncio.to_thread(stream.write, chunk)
        finally:
            self.set_speaking(False)
            stream.stop(); stream.close()

    async def run(self):
        gemini_key = self.controller.config_data.get("gemini_key")
        if not gemini_key:
            print("[SiriusLive] ⚠️ Gemini API Anahtarı eksik! Lütfen ayarlardan girin.")
            await asyncio.sleep(5)
            return

        client = genai.Client(api_key=gemini_key, http_options={"api_version": "v1beta"})
        system_prompt = build_system_instruction()
        
        while True:
            try:
                self.controller.ui.update_status("CONNECTING", "#f39c12")
                config = types.LiveConnectConfig(
                    response_modalities=["AUDIO"],
                    system_instruction=system_prompt,
                    tools=[{"function_declarations": TOOL_DECLARATIONS}]
                )
                async with client.aio.live.connect(model=LIVE_MODEL, config=config) as session, asyncio.TaskGroup() as tg:
                    self.session = session
                    self._loop = asyncio.get_event_loop()
                    self.audio_in_queue = asyncio.Queue()
                    self.out_queue = asyncio.Queue()
                    self._turn_done_event = asyncio.Event()

                    self.controller.ui.update_status("LISTENING", "#ff4444")
                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())
            except Exception as e:
                print(f"[SiriusLive] ⚠️ Bağlantı koptu, yeniden deneniyor... {e}")
                await asyncio.sleep(3)


# --- KOMUTA MERKEZİ ---
class SiriusController:
    def __init__(self):
        self.config_data = get_config()
        self.is_muted = False

        self.ui = SiriusUI(on_text_cmd=self.start_text_agent, on_voice_cmd=self.toggle_mute, on_open_settings=self.open_settings)

        self.brain = Planner()
        self.error_handler = ErrorHandler()
        self.windows = WindowManager()
        self.messenger = Messenger()
        self.browser = BrowserController()
        self.computer = ComputerController()
        self.coder = CodeHelper()
        self.dev_agent = DevAgent()
        self.desktop = DesktopManager()
        self.file_ctrl = FileController()
        self.file_proc = FileProcessor()
        self.sys_set = ComputerSettings()
        self.launcher = AppLauncher()
        self.games = GameUpdater()
        self.remind = ReminderManager()
        self.vision = VisionProcessor()
        self.travel = TravelManager()
        self.weather = WeatherManager()
        self.web = WebSearchManager()
        self.yt = YouTubeManager()
        self.council = CouncilManager() # <--- KONSEY BAĞLANDI
        
        plugins_dict = {"messenger": self.messenger, "windows": self.windows, "web_search": self.web}
        self.executor = SiriusExecutor(brain=self.brain, error_handler=self.error_handler, plugins_dict=plugins_dict)
        self.queue = SiriusTaskQueue(executor=self.executor)
        
        self.live_api = SiriusLive(self)
        self.apply_keys()

    def apply_keys(self):
        gk = self.config_data.get("gemini_key", "")
        self.brain.gemini_key = gk
        self.error_handler.gemini_key = gk
        self.executor.gemini_key = gk
        self.vision.gemini_key = gk
        self.web.gemini_key = gk
        self.yt.gemini_key = gk
        self.travel.gemini_key = gk
        self.coder.gemini_key = gk
        self.dev_agent.gemini_key = gk
        self.council.gemini_key = gk # <--- KONSEYE ANAHTAR VERİLDİ

    def open_settings(self):
        SettingsWindow(self.ui, self.config_data, self.update_keys)

    def update_keys(self, groq_key, serp_key, gemini_key):
        save_api_keys(gemini_api_key=gemini_key, serpapi_key=serp_key, groq_api_key=groq_key)
        self.config_data = get_config()
        self.apply_keys()
        self.ui.update_status("SAVED!", "#2ecc71")

    def start_text_agent(self, query):
        self.live_api.send_text(query)

    def toggle_mute(self):
        self.is_muted = not self.is_muted
        if self.is_muted: self.ui.update_status("MUTED", "#7f8c8d")
        else: self.ui.update_status("LISTENING", "#ff4444")

    def run(self):
        self.queue.start()
        def live_runner(): asyncio.run(self.live_api.run())
        threading.Thread(target=live_runner, daemon=True).start()
        self.ui.mainloop()

if __name__ == "__main__":
    app = SiriusController()
    app.run()