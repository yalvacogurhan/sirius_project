# plugins/audio_manager.py

import sounddevice as sd
from scipy.io.wavfile import write
from faster_whisper import WhisperModel
import subprocess
import pygame
import time
import os

class AudioManager:
    def __init__(self):
        print("🎙️ Ses modülü (Kulak ve Ağız) başlatılıyor...")
        # Ses çalmak için pygame'i başlatıyoruz
        pygame.mixer.init()
        
        # Fısıltı (Whisper) modelini yüklüyoruz (küçük ve hızlı olan 'tiny' modeli)
        print("⏳ Yapay zekâ işitme modeli yükleniyor (İlk seferde biraz sürebilir)...")
        self.model = WhisperModel("base", device="cpu", compute_type="int8")
        print("✅ Ses modülü hazır!")
        
    def listen(self, duration=6):
        """Mikrofondan ses kaydeder ve metne çevirir."""
        print(f"\n🎧 Sirius dinliyor... (Lütfen {duration} saniye içinde konuşun)")
        fs = 16000  # Standart ses kalitesi
        
        # Sesi mikrofondan kaydet
        myrecording = sd.rec(int(duration * fs), samplerate=fs, channels=1)
        sd.wait()  # Kaydın bitmesini bekle
        
        print("⏳ Sesiniz algılanıyor...")
        write('gecici_ses.wav', fs, myrecording)
        
        # Sesi metne çevir
        segments, info = self.model.transcribe("gecici_ses.wav", beam_size=5, language="tr")
        
        metin = ""
        for segment in segments:
            metin += segment.text + " "
            
        sonuc = metin.strip()
        print(f"👤 SİZ: {sonuc}")
        return sonuc

    def speak(self, text):
        """Metni sese çevirir ve hoparlörden çalar."""
        print(f"🗣️ SİRİUS: {text}")
        audio_file = "yanit.mp3"
        
        # Edge-TTS ile metni sese dönüştür (Emel isimli Türkçe modeli kullanıyoruz)
        subprocess.run(['edge-tts', '--voice', 'tr-TR-EmelNeural', '--text', text, '--write-media', audio_file])
        
        # Sesi çal
        pygame.mixer.music.load(audio_file)
        pygame.mixer.music.play()
        
        # Sesin bitmesini bekle
        while pygame.mixer.music.get_busy():
            time.sleep(0.1)
            
        pygame.mixer.music.unload()
        
        # Geçici ses dosyasını temizle
        try:
            os.remove(audio_file)
        except:
            pass