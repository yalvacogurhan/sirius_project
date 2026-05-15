# build.py

import os
import sys
import subprocess
import shutil

def main():
    print("🚀 Sirius Mimarisi Derleniyor (.exe Oluşturuluyor)...\n")

    # 1. PyInstaller kontrolü
    try:
        import PyInstaller
    except ImportError:
        print("⚠️ PyInstaller bulunamadı. Sisteme entegre ediliyor...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])
        print("✅ PyInstaller kuruldu.\n")

    sep = os.pathsep

    # 2. Temel Derleme Komutları
    pyinstaller_command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",              
        "--onedir",                 
        "--windowed",               
        "--name", "Sirius",         
    ]

    # --- AKILLI DOSYA EKLEME (Sadece var olanları ekler, çökmeyi engeller) ---
    
    # Uygulama ikonu (icon.ico) varsa ekle
    if os.path.exists("icon.ico"):
        pyinstaller_command.extend(["--icon", "icon.ico"])
        pyinstaller_command.extend(["--add-data", f"icon.ico{sep}."])
        
    # Göz animasyonu (mini_eye.mp4) varsa ekle
    if os.path.exists("mini_eye.mp4"):
        pyinstaller_command.extend(["--add-data", f"mini_eye.mp4{sep}."])

    # Yetenekler klasörü varsa ekle
    if os.path.exists("plugins"):
        pyinstaller_command.extend(["--add-data", f"plugins{sep}plugins"])

    # Görüntü işleme klasörü varsa ekle
    if os.path.exists("vision"):
        pyinstaller_command.extend(["--add-data", f"vision{sep}vision"])

    # Eğer sonradan core klasörü açarsan otomatik tanır, yoksa pas geçer
    if os.path.exists("core"):
        pyinstaller_command.extend(["--add-data", f"core{sep}core"])

    # --- GİZLİ ORGANLAR (Kütüphaneler) ---
    hidden_imports = [
        "sounddevice", "google.genai", "playwright", "pyautogui",
        "mss", "cv2", "numpy", "pygetwindow", "pyperclip"
    ]
    for imp in hidden_imports:
        pyinstaller_command.extend(["--hidden-import", imp])

    # Ana dosya
    pyinstaller_command.append("main.py")

    print("[1/2] Parçalar birleştiriliyor, bu işlem biraz sürebilir...")
    
    try:
        subprocess.run(pyinstaller_command, check=True)
        
        # Build klasörünü temizle (Sadece 'dist' kalsın)
        if os.path.exists("build"):
            shutil.rmtree("build")
            
        print("\n" + "="*50)
        print("✅ DERLEME KUSURSUZ TAMAMLANDI PATRON!")
        print("📁 Sirius'u çalıştırmak için şu yola gidin: dist/Sirius/Sirius.exe")
        print("="*50)
        
    except Exception as e:
        print(f"\n❌ Derleme sırasında bir pürüz çıktı: {e}")

if __name__ == "__main__":
    main()