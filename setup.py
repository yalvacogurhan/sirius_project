# setup.py

import subprocess
import sys

def main():
    print("🚀 Sirius Otomatik Kurulum Sihirbazı Başlatılıyor...\n")
    
    try:
        print("[1/2] 📦 Gereksinimler (requirements.txt) indiriliyor ve kuruluyor...")
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], check=True)
        
        print("\n[2/2] 🌐 Playwright web tarayıcı motorları sisteme entegre ediliyor...")
        subprocess.run([sys.executable, "-m", "playwright", "install"], check=True)
        
        print("\n✅ Kurulum Kusursuz Tamamlandı patron!")
        print("▶️ Sirius'u uyandırmak için terminale şunu yazın: python main.py")
        
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Kurulum sırasında bir hata oluştu: {e}")
        print("Lütfen requirements.txt dosyasının klasörde olduğundan ve internet bağlantınızdan emin olun.")

if __name__ == "__main__":
    main()