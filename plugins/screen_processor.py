# plugins/screen_processor.py

import asyncio
import base64
import io
import re
import threading
import time
from typing import Optional
import numpy as np

try:
    import sounddevice as sd
    import cv2
    import mss
    import mss.tools
    import PIL.Image
except ImportError as e:
    print(f"[CanlıGörüş] ⚠️ Eksik kütüphane: {e}. Lütfen kurun.")

from google import genai
from google.genai import types as gtypes

class VisionProcessor:
    def __init__(self):
        print("👁️🎙️ Sirius Canlı Görüş ve Doğal Ses Motoru Aktif!")
        self.gemini_key = "" # main.py'den gelecek
        self.live_model = "models/gemini-2.5-flash-native-audio-preview-12-2025"
        self.system_prompt = (
            "Sen Sirius'sun, son derece zeki ve elit bir yapay zeka asistanısın. "
            "Sana sağlanan görüntüyü (kamera veya ekran) keskin bir zekayla analiz et. "
            "Kullanıcı detay istemedikçe en fazla iki cümleyle, kısa, net ve karizmatik cevap ver. "
            "Kullanıcıya daima 'Patron' diye hitap et."
        )
        
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._session = None
        self._out_queue: Optional[asyncio.Queue] = None
        self._audio_in: Optional[asyncio.Queue] = None
        self._ready_evt = threading.Event()
        self._lock = threading.Lock()
        self._session_up = False

    def _compress(self, img_bytes: bytes) -> tuple[bytes, str]:
        try:
            img = PIL.Image.open(io.BytesIO(img_bytes)).convert("RGB")
            img.thumbnail((640, 360), PIL.Image.BILINEAR)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=60, optimize=False)
            return buf.getvalue(), "image/jpeg"
        except Exception as e:
            print(f"[CanlıGörüş] ⚠️ Sıkıştırma hatası: {e}")
            return img_bytes, "image/png"

    def _capture_screen(self) -> tuple[bytes, str]:
        with mss.mss() as sct:
            target = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            shot = sct.grab(target)
            png = mss.tools.to_png(shot.rgb, shot.size)
        return self._compress(png)

    def _capture_camera(self) -> tuple[bytes, str]:
        cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        if not cap.isOpened(): raise RuntimeError("Kamera açılamadı.")
        for _ in range(5): cap.read() # Kamera ışığı ısınma
        ret, frame = cap.read()
        cap.release()
        if not ret or frame is None: raise RuntimeError("Kameradan görüntü alınamadı.")
        
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = PIL.Image.fromarray(rgb)
        img.thumbnail((640, 360), PIL.Image.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=60)
        return buf.getvalue(), "image/jpeg"

    def start_session(self):
        if not self.gemini_key:
            print("[CanlıGörüş] ❌ Gemini API Key eksik!")
            return
        with self._lock:
            if self._thread and self._thread.is_alive(): return
            self._thread = threading.Thread(target=self._run_event_loop, daemon=True)
            self._thread.start()
        if not self._ready_evt.wait(timeout=25.0):
            print("[CanlıGörüş] ❌ Oturum zaman aşımına uğradı.")
        else:
            print("[CanlıGörüş] ✅ Canlı bağlantı hazır.")
            self._session_up = True

    def _run_event_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._session_loop())

    async def _session_loop(self):
        self._out_queue = asyncio.Queue(maxsize=30)
        self._audio_in = asyncio.Queue()

        client = genai.Client(api_key=self.gemini_key, http_options={"api_version": "v1beta"})
        config = gtypes.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            system_instruction=self.system_prompt,
            speech_config=gtypes.SpeechConfig(
                voice_config=gtypes.VoiceConfig(
                    prebuilt_voice_config=gtypes.PrebuiltVoiceConfig(voice_name="Charon")
                )
            ),
        )

        while True:
            try:
                print("[CanlıGörüş] 🔌 Sunucuya bağlanılıyor...")
                async with client.aio.live.connect(model=self.live_model, config=config) as session:
                    self._session = session
                    self._ready_evt.set()
                    print("[CanlıGörüş] ✅ Bağlantı kuruldu.")
                    
                    async with asyncio.TaskGroup() as tg:
                        tg.create_task(self._send_loop())
                        tg.create_task(self._recv_loop())
                        tg.create_task(self._play_loop())

            except Exception as e:
                print(f"[CanlıGörüş] ⚠️ Bağlantı koptu: {e}")
            finally:
                self._session = None
                self._ready_evt.clear()
            
            await asyncio.sleep(3.0)
            self._ready_evt.set()

    async def _send_loop(self):
        while True:
            image_bytes, mime_type, user_text = await self._out_queue.get()
            if not self._session: continue
            try:
                b64 = base64.b64encode(image_bytes).decode("ascii")
                await self._session.send_client_content(
                    turns={"parts": [{"inline_data": {"mime_type": mime_type, "data": b64}}, {"text": user_text}]},
                    turn_complete=True,
                )
                print(f"[CanlıGörüş] 📤 Görüntü iletildi. Soru: '{user_text[:60]}'")
            except Exception as e:
                print(f"[CanlıGörüş] ⚠️ Gönderme hatası: {e}")

    async def _recv_loop(self):
        transcript = []
        try:
            async for response in self._session.receive():
                if response.data: await self._audio_in.put(response.data)
                
                sc = response.server_content
                if not sc: continue
                
                if sc.output_transcription and sc.output_transcription.text:
                    chunk = sc.output_transcription.text.strip()
                    if chunk: transcript.append(chunk)

                if sc.turn_complete and transcript:
                    full = re.sub(r"\s+", " ", " ".join(transcript)).strip()
                    if full: print(f"\n[Sirius] 💬 {full}\n")
                    transcript = []
        except Exception as e:
            print(f"[CanlıGörüş] ⚠️ Alma hatası: {e}")

    async def _play_loop(self):
        stream = sd.RawOutputStream(samplerate=24000, channels=1, dtype="int16", blocksize=1024)
        stream.start()
        try:
            while True:
                chunk = await self._audio_in.get()
                await asyncio.to_thread(stream.write, chunk)
        except Exception as e:
            print(f"[CanlıGörüş] ❌ Ses çalma hatası: {e}")
        finally:
            stream.stop()
            stream.close()

    def execute(self, params: dict) -> str:
        angle = params.get("angle", "screen").lower()
        text = params.get("text", "Ne görüyorsun?").strip()

        if not self._session_up:
            self.start_session()
            if not self._session_up: return "Canlı bağlantı kurulamadı."

        try:
            if angle == "camera":
                image_bytes, mime_type = self._capture_camera()
            else:
                image_bytes, mime_type = self._capture_screen()
        except Exception as e:
            return f"Görüntü yakalama hatası: {e}"

        if self._loop and self._out_queue:
            asyncio.run_coroutine_threadsafe(self._out_queue.put((image_bytes, mime_type, text)), self._loop)
            return "Sirius görüntüyü inceliyor ve size sesli yanıt veriyor..."
        return "Bağlantı henüz hazır değil."