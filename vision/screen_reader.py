# vision/screen_reader.py

import cv2
import numpy as np
import pyautogui
import threading
import time
import easyocr

class ScreenReader:
    def __init__(self):
        print("👁️ Canlı Akış ve OCR (Metin Okuma) başlatılıyor... Lütfen bekleyin.")
        self.current_frame = None
        self.is_running = True
        
        # OCR Motorunu yükle (İlk çalışmada model indirebilir, biraz bekletebilir)
        # Türkçe ve İngilizce desteği eklendi. gpu=False yaptık ki her bilgisayarda sorunsuz çalışsın.
        self.ocr = easyocr.Reader(['tr', 'en'], gpu=False) 
        
        self.stream_thread = threading.Thread(target=self._capture_stream, daemon=True)
        self.stream_thread.start()
        print("✅ Gözler tamamen açıldı! Ekranda ne var ne yok okuyabiliyorum.")

    def _capture_stream(self):
        """Sürekli olarak ekranı kaydeder ve hafızada güncel tutar."""
        while self.is_running:
            img = pyautogui.screenshot()
            frame = np.array(img)
            self.current_frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            time.sleep(0.05)

    def get_latest_frame(self, save_path="current_live_frame.png"):
        """O anki canlı yayından bir kareyi beyne gönderilmek üzere diske kaydeder."""
        if self.current_frame is not None:
            cv2.imwrite(save_path, self.current_frame)
            return save_path
        return None

    def find_text_coordinates(self, target_text):
        """Ekranda belirli bir yazıyı arar ve bulursa tam merkezinin (X, Y) koordinatını döndürür."""
        if self.current_frame is None or not target_text:
            return None

        print(f"🔍 Ekranda taranıyor: '{target_text}' ...")
        # Sadece o anki güncel kareyi OCR ile oku
        results = self.ocr.readtext(self.current_frame)

        target_text_lower = target_text.lower()

        # Ekrandaki tüm yazıları kontrol et
        for (bbox, text, prob) in results:
            if target_text_lower in text.lower():
                # bbox (Bounding Box) kutusunun köşe koordinatlarından tam merkezi hesaplıyoruz
                x_center = int((bbox[0][0] + bbox[1][0]) / 2)
                y_center = int((bbox[0][1] + bbox[2][1]) / 2)
                
                print(f"🎯 Bulundu: '{text}' -> Koordinat: X:{x_center}, Y:{y_center}")
                return x_center, y_center

        print(f"❌ '{target_text}' ekranda bulunamadı.")
        return None

    def stop(self):
        self.is_running = False