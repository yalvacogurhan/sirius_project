# security/login_ui.py
import tkinter as tk
from tkinter import messagebox
import cv2
from PIL import Image, ImageTk
import sys
import webbrowser

def show_login_window(lic_manager, admin_key, server_url):
    root = tk.Tk()
    root.title("SIRIUS - Güvenlik Kalkanı")
    root.geometry("800x600")
    root.resizable(False, False)
    
    # Giriş başarılı mı diye takip edeceğimiz değişken
    success = [False] 

    # --- VİDEO ARKA PLAN AYARLARI ---
    video_path = "giris.mp4"
    cap = cv2.VideoCapture(video_path)
    
    if not cap.isOpened():
        messagebox.showerror("Hata", f"Video bulunamadı: {video_path}\nLütfen dosyayı ana klasöre ekleyin.")
        sys.exit(0)

    canvas = tk.Canvas(root, width=800, height=600, highlightthickness=0)
    canvas.pack(fill="both", expand=True)

    # --- ARAYÜZ (GİRİŞ KUTUSU VE BUTON) ---
    # Siyah fontlu, havalı giriş kutusu
    entry_key = tk.Entry(root, font=("Helvetica", 16, "bold"), fg="black", bg="white", justify="center")
    entry_window = canvas.create_window(400, 440, window=entry_key, width=350, height=45)
    def check_key():
        key = entry_key.get().strip()
        if key == admin_key:
            webbrowser.open(f"{server_url}/admin/login")
            sys.exit(0)

        # Doğrulama işlemi
        entry_key.config(state="disabled") # Kontrol ederken spam tıklanmasın
        if lic_manager.verify_key_online(key):
            success[0] = True
            root.destroy() # Geçerliyse pencereyi kapat, Sirius'a geç
        else:
            messagebox.showerror("Erişim Reddedildi", "Geçersiz Lisans Anahtarı!")
            entry_key.config(state="normal")
            entry_key.delete(0, tk.END)

    # Siyah renkli Giriş Butonu
    btn_login = tk.Button(root, text="SİSTEMİ BAŞLAT", font=("Helvetica", 12, "bold"), 
                          fg="white", bg="black", activebackground="#333333", 
                          activeforeground="white", cursor="hand2", command=check_key)
    btn_window = canvas.create_window(400, 530, window=btn_login, width=200, height=40)

    # Pencere çarpıdan kapatılırsa sistemi komple durdur
    def on_closing():
        cap.release()
        sys.exit(0)
    root.protocol("WM_DELETE_WINDOW", on_closing)

    image_item = None

    # --- VİDEO OYNATICI DÖNGÜSÜ ---
    def update_frame():
        nonlocal image_item
        ret, frame = cap.read()
        if not ret: # Video biterse başa sar (Loop)
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ret, frame = cap.read()

        if ret:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frame = cv2.resize(frame, (800, 600))
            
            # Bellek sızıntısı olmaması için eski resmi güncelle
            photo = ImageTk.PhotoImage(image=Image.fromarray(frame))
            if image_item is None:
                image_item = canvas.create_image(0, 0, image=photo, anchor="nw")
            else:
                canvas.itemconfig(image_item, image=photo)
            
            # Fotoğrafın silinmemesi için referansı kaydet
            canvas.image = photo 
            
            # Kutucukların videonun üstünde kalmasını sağla
            canvas.tag_raise(entry_window)
            canvas.tag_raise(btn_window)

        # Saniyede ~30 kare (33 milisaniye)
        root.after(33, update_frame)

    update_frame()
    root.mainloop()
    
    cap.release()
    return success[0]