# ui.py

import os
import tkinter as tk
import customtkinter as ctk
from PIL import Image, ImageTk
import cv2

ctk.set_appearance_mode("dark")

# --- AYARLAR PENCERESİ ---
class SettingsWindow(ctk.CTkToplevel):
    def __init__(self, master, current_keys, on_save_callback):
        super().__init__(master)
        self.title("Sirius Ayarlar")
        self.geometry("400x240")
        self.configure(fg_color="#000000")
        self.attributes("-topmost", True)
        self.resizable(False, False)
        self.on_save_callback = on_save_callback

        ctk.CTkLabel(self, text="⚙️ API KEY AYARLARI", font=("Consolas", 14, "bold"), text_color="#ffffff").pack(pady=(15, 5))
        
        self.key_entry = ctk.CTkEntry(self, width=300, placeholder_text="Groq API Key (gsk_...)", fg_color="#0a0a0a", border_color="#ffffff", text_color="white")
        self.key_entry.pack(pady=5)
        self.key_entry.insert(0, current_keys.get("api_key", "")) 

        self.serp_entry = ctk.CTkEntry(self, width=300, placeholder_text="SerpApi Key...", fg_color="#0a0a0a", border_color="#ffffff", text_color="white")
        self.serp_entry.pack(pady=5)
        self.serp_entry.insert(0, current_keys.get("serpapi_key", "")) 

        self.gemini_entry = ctk.CTkEntry(self, width=300, placeholder_text="Gemini API Key...", fg_color="#0a0a0a", border_color="#ffffff", text_color="white")
        self.gemini_entry.pack(pady=5)
        self.gemini_entry.insert(0, current_keys.get("gemini_key", ""))

        ctk.CTkButton(self, text="KAYDET", width=120, fg_color="#ffffff", hover_color="#cccccc", text_color="#000000", font=("Consolas", 12, "bold"), command=self.save_and_close).pack(pady=10)

    def save_and_close(self):
        self.on_save_callback(self.key_entry.get().strip(), self.serp_entry.get().strip(), self.gemini_entry.get().strip())
        self.destroy()

# --- ANA ARAYÜZ ---
class SiriusUI(ctk.CTk):
    def __init__(self, on_text_cmd, on_voice_cmd, on_open_settings):
        super().__init__()
        
        self.geometry("460x65+200+100") 
        self.overrideredirect(True) 
        self.attributes("-topmost", True)
        
        transparent_key = '#000001'
        self.config(bg=transparent_key)
        self.attributes("-transparentcolor", transparent_key)

        # Butonlara tıklandığında main.py'deki fonksiyonları tetikleyecek bağlantılar
        self.on_text_cmd = on_text_cmd
        self.on_voice_cmd = on_voice_cmd
        self.on_open_settings = on_open_settings

        self.main_frame = ctk.CTkFrame(self, bg_color=transparent_key, fg_color="#000000", border_color="#ffffff", border_width=1, corner_radius=30)
        self.main_frame.pack(fill="both", expand=True, padx=2, pady=2)
        
        self.main_frame.bind("<Button-1>", self.start_move)
        self.main_frame.bind("<B1-Motion>", self.do_move)

        self.video_label = tk.Label(self.main_frame, bg="#000000", bd=0)
        self.video_label.pack(side="left", padx=(15, 5))
        try:
            self.cap = cv2.VideoCapture("mini_eye.mp4")
            self.animate_video()
        except: pass

        text_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        text_frame.pack(side="left", padx=(0, 10))
        self.status_label = ctk.CTkLabel(text_frame, text="IDLE", font=("Consolas", 12, "bold"), text_color="#ffffff")
        self.status_label.pack(anchor="w", pady=(2,0))
        ctk.CTkLabel(text_frame, text="Core Mode", font=("Consolas", 10), text_color="#777777").pack(anchor="w")

        self.entry = ctk.CTkEntry(self.main_frame, width=190, height=32, placeholder_text="Emredin...", font=("Consolas", 11), fg_color="#0a0a0a", border_color="#ffffff", border_width=1, text_color="white")
        self.entry.pack(side="left", padx=(2, 5))
        self.entry.bind("<Return>", lambda e: self._handle_text_input())

        self.voice_btn = ctk.CTkButton(self.main_frame, text="🎙️", width=35, height=32, font=("Consolas", 14), fg_color="#0a0a0a", hover_color="#333333", border_width=1, border_color="#ffffff", text_color="#ffffff", command=self.on_voice_cmd)
        self.voice_btn.pack(side="left", padx=(0, 5))

        self.close_btn = ctk.CTkButton(self.main_frame, text="✖", width=30, height=30, font=("Consolas", 12), fg_color="transparent", hover_color="#ff4444", text_color="#ffffff", command=self.quit)
        self.close_btn.pack(side="right", padx=(0, 15))
        
        self.settings_btn = ctk.CTkButton(self.main_frame, text="⚙️", width=30, height=30, font=("Consolas", 14), fg_color="transparent", hover_color="#333333", text_color="#ffffff", command=self.on_open_settings)
        self.settings_btn.pack(side="right", padx=(2, 0))

    def _handle_text_input(self):
        query = self.entry.get().strip()
        if query: 
            self.entry.delete(0, tk.END)
            self.on_text_cmd(query)

    def update_status(self, text, color):
        self.after(0, lambda: self.status_label.configure(text=text, text_color=color))

    def animate_video(self):
        if hasattr(self, 'cap') and self.cap.isOpened():
            ret, frame = self.cap.read()
            if not ret: 
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ret, frame = self.cap.read()
            if ret:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(frame).resize((46, 32), Image.Resampling.LANCZOS)
                self.photo_image = ImageTk.PhotoImage(img)
                self.video_label.config(image=self.photo_image)
            self.after(30, self.animate_video)

    def start_move(self, e): self.x, self.y = e.x, e.y
    def do_move(self, e): self.geometry(f"+{self.winfo_x()+(e.x-self.x)}+{self.winfo_y()+(e.y-self.y)}")