# vision/pointer.py

from pynput.mouse import Controller, Button
import pyautogui
import time

class Pointer:
    def __init__(self):
        self.mouse = Controller()

    def move_to(self, x, y):
        x, y = int(x), int(y)
        print(f"🖱️ Fare hareket ediyor: ({x}, {y})")
        self.mouse.position = (x, y)
        time.sleep(0.3)

    def click(self, x=None, y=None):
        if x is not None and y is not None:
            self.move_to(x, y)
        print("👆 Tıklama yapıldı!")
        self.mouse.click(Button.left, 1)

    def double_click(self, x, y):
        if x and y:
            pyautogui.moveTo(x, y, duration=0.5)
            pyautogui.click(clicks=2, interval=0.1) # Sadece çift tıkla

    def find_and_click(self, image_name):
        """Ekranda verilen resmi arar ve bulursa tam ortasına çift tıklar."""
        print(f"🔍 Ekranda '{image_name}' aranıyor...")
        try:
            # confidence=0.8 demek "Resmin %80 benzemesi yeterli" demektir
            location = pyautogui.locateCenterOnScreen(image_name, confidence=0.8)
            
            if location is not None:
                print(f"🎯 Hedef bulundu! Koordinatlar: X={location.x}, Y={location.y}")
                self.double_click(location.x, location.y)
                return True
            else:
                print("❌ Hedef ekranda bulunamadı!")
                return False
        except Exception as e:
            print(f"Arama sırasında hata: {e}")
            return False