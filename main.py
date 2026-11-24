import os
import re
import sys
import threading
import subprocess
from io import BytesIO
from pathlib import Path
from tkinter import filedialog, messagebox
import requests
from PIL import Image
import customtkinter as ctk
import yt_dlp

# ==============================================================================
# PALETA VISUAL INSPIRADA EN NUNAY AGENCIA (Limpia, moderna, alto contraste)
# ==============================================================================
COLORS = {
    "bg_main": "#F5F7FA",         # Fondo general limpio y luminoso
    "bg_card": "#FFFFFF",         # Tarjetas blancas con jerarquía
    "bg_subtle": "#EDF1F7",       # Contenedores secundarios / inputs
    "primary": "#0F172A",         # Azul marino profundo (identidad corporativa)
    "primary_hover": "#1E293B",
    "accent": "#0EA5E9",          # Cian vibrante para acciones y progreso
    "accent_hover": "#0284C7",
    "success": "#10B981",         # Verde esmeralda (completado)
    "warning": "#F59E0B",         # Ámbar (pausado)
    "danger": "#EF4444",          # Rojo coral (cancelar / error)
    "text_main": "#0F172A",
    "text_muted": "#64748B",
    "border": "#E2E8F0",
}

ctk.set_appearance_mode("Light")
ctk.set_default_color_theme("blue")


def get_bundled_bin_dir() -> str:
    """Detecta la carpeta de binarios empaquetados (ffmpeg.exe, deno.exe) si corre como .exe."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def setup_environment():
    bin_dir = get_bundled_bin_dir()
    os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")


def format_duration(seconds) -> str:
    if not seconds:
        return "En vivo / Desconocida"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()


# ==============================================================================
# CONTROLADOR DE CADA DESCARGA EN LA COLA (Soporta Pausa, Reanudar y Cancelar)
# ==============================================================================
class DownloadTask:
    def __init__(self, url: str, title: str, mode: str, quality: str, out_dir: str, ui_callback):
        self.url = url
        self.title = title
        self.mode = mode          # "Audio" o "Video"
        self.quality = quality    # Ej: "MP3 320 kbps", "1080p Full HD"
        self.out_dir = out_dir
        self.ui_callback = ui_callback

        self.pause_event = threading.Event()
        self.pause_event.set()    # True = activo, False = pausado
        self.cancel_flag = False
        self.status = "En cola"
        self.progress = 0.0
        self.speed_text = "Esperando..."
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def toggle_pause(self):
        if self.status in ("Completado", "Cancelado", "Error"):
            return
        if self.pause_event.is_set():
            self.pause_event.clear()
            self.status = "Pausado"
            self.speed_text = "Descarga en pausa"
        else:
            self.pause_event.set()
            self.status = "Descargando"
            self.speed_text = "Reanudando..."
        self.ui_callback(self)

    def cancel(self):
        self.cancel_flag = True
        self.pause_event.set()
        self.status = "Cancelado"
        self.speed_text = "Cancelado por el usuario"
        self.ui_callback(self)

    def _progress_hook(self, d):
        if self.cancel_flag:
            raise Exception("CANCELLED_BY_USER")

        # Si está pausado, bloquea el hilo limpiamente hasta que el usuario reanude
        while not self.pause_event.is_set():
            if self.cancel_flag:
                raise Exception("CANCELLED_BY_USER")
            threading.Event().wait(0.25)

        if d["status"] == "downloading":
            self.status = "Descargando"
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)
            if total > 0:
                self.progress = min(downloaded / total, 0.99)
            speed = d.get("_speed_str", "").strip()
            eta = d.get("_eta_str", "").strip()
            pct = int(self.progress * 100)
            self.speed_text = f"{pct}%  •  {speed}  •  Restante: {eta}" if speed else f"{pct}%"
            self.ui_callback(self)

        elif d["status"] == "finished":
            self.status = "Procesando"
            self.progress = 0.99
            self.speed_text = "Uniendo audio/video con FFmpeg..."
            self.ui_callback(self)

    def _run(self):
        try:
            self.status = "Iniciando"
            self.speed_text = "Conectando..."
            self.ui_callback(self)

            bin_dir = get_bundled_bin_dir()
            outtmpl = os.path.join(self.out_dir, "%(title).150B.%(ext)s")

            ydl_opts = {
                "outtmpl": outtmpl,
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "progress_hooks": [self._progress_hook],
                "ffmpeg_location": bin_dir,
                "concurrent_fragment_downloads": 4,
                "retries": 5,
                "fragment_retries": 5,
            }

            if self.mode == "Audio":
                if "M4A" in self.quality:
                    ydl_opts["format"] = "bestaudio[ext=m4a]/bestaudio/best"
                    ydl_opts["postprocessors"] = [{"key": "FFmpegMetadata"}]
                else:
                    bitrate = "320" if "320" in self.quality else "192"
                    ydl_opts["format"] = "bestaudio/best"
                    ydl_opts["writethumbnail"] = True
                    ydl_opts["postprocessors"] = [
                        {
                            "key": "FFmpegExtractAudio",
                            "preferredcodec": "mp3",
                            "preferredquality": bitrate,
                        },
                        {"key": "EmbedThumbnail"},
                        {"key": "FFmpegMetadata"},
                    ]
            else:
                # Video MP4 según resolución elegida
                height_map = {
                    "480p Estándar": 480,
                    "720p HD": 720,
                    "1080p Full HD": 1080,
                    "4K Ultra HD (2160p)": 2160,
                }
                max_h = height_map.get(self.quality, 1080)
                ydl_opts["format"] = (
                    f"bestvideo[height<={max_h}][ext=mp4]+bestaudio[ext=m4a]/"
                    f"bestvideo[height<={max_h}]+bestaudio/"
                    f"best[height<={max_h}]/best"
                )
                ydl_opts["merge_output_format"] = "mp4"

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([self.url])

            if not self.cancel_flag:
                self.status = "Completado"
                self.progress = 1.0
                self.speed_text = f"Guardado en: {os.path.basename(self.out_dir)}"
                self.ui_callback(self)

        except Exception as e:
            if "CANCELLED_BY_USER" in str(e) or self.cancel_flag:
                self.status = "Cancelado"
                self.speed_text = "Cancelado"
            else:
                self.status = "Error"
                err_msg = str(e).splitlines()[-1] if str(e) else "Error desconocido"
                self.speed_text = f"Error: {err_msg[:65]}"
            self.ui_callback(self)


# ==============================================================================
# VENTANA DE SELECCIÓN DE PLAYLIST
# ==============================================================================
class PlaylistSelectorDialog(ctk.CTkToplevel):
    def __init__(self, parent, playlist_title: str, entries: list, on_confirm):
        super().__init__(parent)
        self.title("Seleccionar elementos de la Playlist")
        self.geometry("680x520")
        self.configure(fg_color=COLORS["bg_main"])
        self.transient(parent)
        self.grab_set()

        self.entries = entries
        self.on_confirm = on_confirm
        self.check_vars = []

        # Encabezado
        header = ctk.CTkFrame(self, fg_color=COLORS["bg_card"], corner_radius=12)
        header.pack(fill="x", padx=16, pady=(16, 10))

        ctk.CTkLabel(
            header,
            text=f"Playlist detectada ({len(entries)} elementos)",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color=COLORS["accent"],
        ).pack(anchor="w", padx=16, pady=(12, 0))

        ctk.CTkLabel(
            header,
            text=playlist_title[:75],
            font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"),
            text_color=COLORS["text_main"],
        ).pack(anchor="w", padx=16, pady=(2, 10))

        # Botones rápidos Marcar / Desmarcar todos
        bar = ctk.CTkFrame(header, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=(0, 12))

        ctk.CTkButton(
            bar,
            text="Seleccionar todos",
            width=130,
            height=30,
            fg_color=COLORS["bg_subtle"],
            text_color=COLORS["text_main"],
            hover_color=COLORS["border"],
            command=lambda: self._set_all(True),
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            bar,
            text="Desmarcar todos",
            width=130,
            height=30,
            fg_color=COLORS["bg_subtle"],
            text_color=COLORS["text_main"],
            hover_color=COLORS["border"],
            command=lambda: self._set_all(False),
        ).pack(side="left")

        # Lista desplazable
        scroll = ctk.CTkScrollableFrame(self, fg_color=COLORS["bg_card"], corner_radius=12)
        scroll.pack(fill="both", expand=True, padx=16, pady=6)

        for idx, item in enumerate(entries, start=1):
            var = ctk.BooleanVar(value=True)
            self.check_vars.append((var, item))
            title = item.get("title") or f"Elemento #{idx}"
            dur = format_duration(item.get("duration"))
            row = ctk.CTkCheckBox(
                scroll,
                text=f"{idx:02d}. {title[:68]}  [{dur}]",
                variable=var,
                font=ctk.CTkFont(family="Segoe UI", size=13),
                text_color=COLORS["text_main"],
                fg_color=COLORS["accent"],
                hover_color=COLORS["accent_hover"],
            )
            row.pack(anchor="w", fill="x", padx=10, pady=5)

        # Pie con botón confirmar
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=16, pady=(8, 16))

        ctk.CTkButton(
            footer,
            text="Cancelar",
            width=110,
            height=38,
            fg_color=COLORS["bg_subtle"],
            text_color=COLORS["text_main"],
            hover_color=COLORS["border"],
            command=self.destroy,
        ).pack(side="right", padx=(8, 0))

        ctk.CTkButton(
            footer,
            text="Añadir seleccionados a la cola",
            height=38,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            fg_color=COLORS["primary"],
            hover_color=COLORS["primary_hover"],
            command=self._confirm,
        ).pack(side="right")

    def _set_all(self, value: bool):
        for var, _ in self.check_vars:
            var.set(value)

    def _confirm(self):
        selected = [item for var, item in self.check_vars if var.get()]
        if not selected:
            messagebox.showwarning("Sin selección", "Marca al menos un elemento para descargar.")
            return
        self.destroy()
        self.on_confirm(selected)


# ==============================================================================
# APLICACIÓN PRINCIPAL: AJRM-TUBE PARA WINDOWS 11
# ==============================================================================
class AJRMTubeApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        setup_environment()

        self.title("AJRM-Tube  |  Descargador Multimedia")
        self.geometry("940x720")
        self.minsize(840, 640)
        self.configure(fg_color=COLORS["bg_main"])

        self.download_dir = str(Path.home() / "Downloads")
        self.current_info = None
        self.tasks = []
        self.task_widgets = {}

        self._build_ui()

    def _build_ui(self):
        # ----------------------------------------------------------------------
        # 1. BARRA SUPERIOR (Identidad limpia estilo Nunay Agencia)
        # ----------------------------------------------------------------------
        topbar = ctk.CTkFrame(self, fg_color=COLORS["bg_card"], corner_radius=0, height=64)
        topbar.pack(fill="x")
        topbar.pack_propagate(False)

        brand_frame = ctk.CTkFrame(topbar, fg_color="transparent")
        brand_frame.pack(side="left", padx=24)

        ctk.CTkLabel(
            brand_frame,
            text="AJRM",
            font=ctk.CTkFont(family="Segoe UI", size=22, weight="bold"),
            text_color=COLORS["primary"],
        ).pack(side="left")

        ctk.CTkLabel(
            brand_frame,
            text="•TUBE",
            font=ctk.CTkFont(family="Segoe UI", size=22, weight="bold"),
            text_color=COLORS["accent"],
        ).pack(side="left", padx=(2, 12))

        ctk.CTkLabel(
            brand_frame,
            text="YouTube  •  Facebook  •  TikTok  •  Instagram",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=COLORS["text_muted"],
        ).pack(side="left", pady=(3, 0))

        # Selector rápido de carpeta destino en barra superior
        folder_box = ctk.CTkFrame(topbar, fg_color="transparent")
        folder_box.pack(side="right", padx=24)

        self.folder_btn = ctk.CTkButton(
            folder_box,
            text=f"📁  {os.path.basename(self.download_dir)}",
            height=34,
            corner_radius=8,
            fg_color=COLORS["bg_subtle"],
            text_color=COLORS["text_main"],
            hover_color=COLORS["border"],
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=self._choose_folder,
        )
        self.folder_btn.pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            folder_box,
            text="Abrir carpeta",
            height=34,
            width=105,
            corner_radius=8,
            fg_color="transparent",
            border_width=1,
            border_color=COLORS["border"],
            text_color=COLORS["text_muted"],
            hover_color=COLORS["bg_subtle"],
            font=ctk.CTkFont(family="Segoe UI", size=12),
            command=self._open_folder,
        ).pack(side="left")

        # ----------------------------------------------------------------------
        # 2. TARJETA PRINCIPAL DE BÚSQUEDA Y VISTA PREVIA
        # ----------------------------------------------------------------------
        main_card = ctk.CTkFrame(
            self,
            fg_color=COLORS["bg_card"],
            corner_radius=16,
            border_width=1,
            border_color=COLORS["border"],
        )
        main_card.pack(fill="x", padx=24, pady=(20, 14))

        ctk.CTkLabel(
            main_card,
            text="Pega el enlace del video, reel o playlist",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color=COLORS["text_main"],
        ).pack(anchor="w", padx=20, pady=(16, 8))

        # Fila URL + Botones Pegar y Analizar
        url_row = ctk.CTkFrame(main_card, fg_color="transparent")
        url_row.pack(fill="x", padx=20, pady=(0, 14))

        self.url_entry = ctk.CTkEntry(
            url_row,
            placeholder_text="https://www.youtube.com/watch?v=...  o enlace de TikTok / Facebook / Instagram",
            height=42,
            corner_radius=10,
            fg_color=COLORS["bg_subtle"],
            border_color=COLORS["border"],
            text_color=COLORS["text_main"],
            font=ctk.CTkFont(family="Segoe UI", size=13),
        )
        self.url_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.url_entry.bind("<Return>", lambda e: self._analyze_url())

        ctk.CTkButton(
            url_row,
            text="Pegar",
            width=80,
            height=42,
            corner_radius=10,
            fg_color=COLORS["bg_subtle"],
            text_color=COLORS["text_main"],
            hover_color=COLORS["border"],
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            command=self._paste_from_clipboard,
        ).pack(side="left", padx=(0, 8))

        self.analyze_btn = ctk.CTkButton(
            url_row,
            text="Analizar enlace",
            width=140,
            height=42,
            corner_radius=10,
            fg_color=COLORS["primary"],
            hover_color=COLORS["primary_hover"],
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            command=self._analyze_url,
        )
        self.analyze_btn.pack(side="left")

        # Panel de Vista Previa (Miniatura + Título + Selector + Botón DESCARGAR)
        self.preview_frame = ctk.CTkFrame(
            main_card,
            fg_color=COLORS["bg_subtle"],
            corner_radius=12,
        )
        self.preview_frame.pack(fill="x", padx=20, pady=(0, 18))

        # Miniatura
        self.thumb_label = ctk.CTkLabel(
            self.preview_frame,
            text="Vista previa\ndel contenido",
            width=210,
            height=118,
            fg_color=COLORS["border"],
            text_color=COLORS["text_muted"],
            corner_radius=10,
            font=ctk.CTkFont(family="Segoe UI", size=12),
        )
        self.thumb_label.pack(side="left", padx=14, pady=14)

        # Detalles y controles de descarga
        details_box = ctk.CTkFrame(self.preview_frame, fg_color="transparent")
        details_box.pack(side="left", fill="both", expand=True, padx=(0, 14), pady=14)

        self.platform_badge = ctk.CTkLabel(
            details_box,
            text="LISTO PARA ANALIZAR",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color=COLORS["accent"],
        )
        self.platform_badge.pack(anchor="w")

        self.title_label = ctk.CTkLabel(
            details_box,
            text="Pega un enlace arriba para cargar su miniatura, título y opciones",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color=COLORS["text_main"],
            wraplength=540,
            justify="left",
        )
        self.title_label.pack(anchor="w", pady=(2, 2))

        self.meta_label = ctk.CTkLabel(
            details_box,
            text="Duración: --:--  •  Canal: --",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=COLORS["text_muted"],
        )
        self.meta_label.pack(anchor="w", pady=(0, 10))

        # Fila de formato + calidad + botón DESCARGAR
        controls_row = ctk.CTkFrame(details_box, fg_color="transparent")
        controls_row.pack(fill="x")

        self.mode_segment = ctk.CTkSegmentedButton(
            controls_row,
            values=["Audio", "Video"],
            width=150,
            height=36,
            selected_color=COLORS["primary"],
            selected_hover_color=COLORS["primary_hover"],
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            command=self._on_mode_change,
        )
        self.mode_segment.set("Audio")
        self.mode_segment.pack(side="left", padx=(0, 10))

        self.quality_menu = ctk.CTkOptionMenu(
            controls_row,
            values=["MP3 320 kbps (Recomendado)", "MP3 192 kbps", "M4A Original (Rápido)"],
            width=225,
            height=36,
            fg_color=COLORS["bg_card"],
            button_color=COLORS["primary"],
            button_hover_color=COLORS["primary_hover"],
            text_color=COLORS["text_main"],
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
        )
        self.quality_menu.pack(side="left", padx=(0, 10))

        self.download_btn = ctk.CTkButton(
            controls_row,
            text="DESCARGAR",
            height=38,
            corner_radius=10,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            state="disabled",
            command=self._trigger_download,
        )
        self.download_btn.pack(side="right", fill="x", expand=True)

        # ----------------------------------------------------------------------
        # 3. COLA DE DESCARGAS ACTIVA
        # ----------------------------------------------------------------------
        queue_header = ctk.CTkFrame(self, fg_color="transparent")
        queue_header.pack(fill="x", padx=24, pady=(4, 6))

        ctk.CTkLabel(
            queue_header,
            text="Cola de descargas",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color=COLORS["text_main"],
        ).pack(side="left")

        ctk.CTkButton(
            queue_header,
            text="Limpiar completados",
            height=28,
            width=145,
            fg_color="transparent",
            text_color=COLORS["text_muted"],
            hover_color=COLORS["border"],
            font=ctk.CTkFont(family="Segoe UI", size=12),
            command=self._clear_finished,
        ).pack(side="right")

        self.queue_scroll = ctk.CTkScrollableFrame(
            self,
            fg_color=COLORS["bg_card"],
            corner_radius=16,
            border_width=1,
            border_color=COLORS["border"],
        )
        self.queue_scroll.pack(fill="both", expand=True, padx=24, pady=(0, 20))

        self.empty_queue_label = ctk.CTkLabel(
            self.queue_scroll,
            text="Aún no hay descargas en la cola.\nPega un enlace arriba y presiona DESCARGAR.",
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color=COLORS["text_muted"],
        )
        self.empty_queue_label.pack(pady=45)

    # --------------------------------------------------------------------------
    # ACCIONES DE INTERFAZ Y ANÁLISIS RÁPIDO
    # --------------------------------------------------------------------------
    def _choose_folder(self):
        selected = filedialog.askdirectory(initialdir=self.download_dir)
        if selected:
            self.download_dir = selected
            self.folder_btn.configure(text=f"📁  {os.path.basename(selected)}")

    def _open_folder(self):
        if os.path.exists(self.download_dir):
            os.startfile(self.download_dir)

    def _paste_from_clipboard(self):
        try:
            text = self.clipboard_get().strip()
            self.url_entry.delete(0, "end")
            self.url_entry.insert(0, text)
            if text.startswith("http"):
                self._analyze_url()
        except Exception:
            pass

    def _on_mode_change(self, value: str):
        if value == "Audio":
            self.quality_menu.configure(
                values=["MP3 320 kbps (Recomendado)", "MP3 192 kbps", "M4A Original (Rápido)"]
            )
            self.quality_menu.set("MP3 320 kbps (Recomendado)")
        else:
            self.quality_menu.configure(
                values=["1080p Full HD", "720p HD", "480p Estándar", "4K Ultra HD (2160p)"]
            )
            self.quality_menu.set("1080p Full HD")

    def _analyze_url(self):
        url = self.url_entry.get().strip()
        if not url:
            return

        self.analyze_btn.configure(state="disabled", text="Cargando...")
        self.download_btn.configure(state="disabled")
        self.platform_badge.configure(text="ANALIZANDO ENLACE...")
        self.title_label.configure(text="Obteniendo información rápida del servidor...")
        self.meta_label.configure(text="Por favor espera un momento...")

        threading.Thread(target=self._fetch_metadata, args=(url,), daemon=True).start()

    def _fetch_metadata(self, url: str):
        try:
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": "in_playlist",  # Carga instantánea incluso en playlists largas
                "skip_download": True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

            self.current_info = info
            self.after(0, lambda: self._update_preview(info))

        except Exception as e:
            err = str(e).splitlines()[-1] if str(e) else "Enlace no compatible"
            self.after(0, lambda: self._show_analyze_error(err))

    def _show_analyze_error(self, err: str):
        self.analyze_btn.configure(state="normal", text="Analizar enlace")
        self.platform_badge.configure(text="ERROR AL ANALIZAR", text_color=COLORS["danger"])
        self.title_label.configure(text="No se pudo leer este enlace")
        self.meta_label.configure(text=err[:90])

    def _update_preview(self, info: dict):
        self.analyze_btn.configure(state="normal", text="Analizar enlace")
        self.download_btn.configure(state="normal")

        extractor = (info.get("extractor_key") or "WEB").upper()
        is_playlist = info.get("_type") == "playlist" or "entries" in info

        if is_playlist:
            entries = [e for e in (info.get("entries") or []) if e]
            title = info.get("title") or "Playlist sin título"
            self.platform_badge.configure(
                text=f"{extractor}  •  PLAYLIST ({len(entries)} ELEMENTOS)",
                text_color=COLORS["accent"],
            )
            self.title_label.configure(text=title)
            self.meta_label.configure(text="Presiona DESCARGAR para elegir qué elementos bajar")
            thumb_url = info.get("thumbnail") or (entries[0].get("thumbnail") if entries else None)
        else:
            title = info.get("title") or "Video sin título"
            dur = format_duration(info.get("duration"))
            uploader = info.get("uploader") or info.get("channel") or "Desconocido"
            self.platform_badge.configure(text=f"{extractor}  •  LISTO", text_color=COLORS["accent"])
            self.title_label.configure(text=title)
            self.meta_label.configure(text=f"Duración: {dur}  •  Autor: {uploader}")
            thumb_url = info.get("thumbnail")

        if thumb_url:
            threading.Thread(target=self._load_thumbnail, args=(thumb_url,), daemon=True).start()

    def _load_thumbnail(self, thumb_url: str):
        try:
            resp = requests.get(thumb_url, timeout=6)
            img = Image.open(BytesIO(resp.content)).convert("RGB")
            img = img.resize((210, 118), Image.Resampling.LANCZOS)
            ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(210, 118))
            self.after(0, lambda: self.thumb_label.configure(image=ctk_img, text=""))
        except Exception:
            pass

    # --------------------------------------------------------------------------
    # GESTIÓN DE COLA DE DESCARGAS
    # --------------------------------------------------------------------------
    def _trigger_download(self):
        if not self.current_info:
            return

        mode = self.mode_segment.get()
        quality = self.quality_menu.get()
        info = self.current_info
        is_playlist = info.get("_type") == "playlist" or "entries" in info

        if is_playlist:
            entries = [e for e in (info.get("entries") or []) if e]
            PlaylistSelectorDialog(
                self,
                info.get("title") or "Playlist",
                entries,
                lambda selected: self._enqueue_playlist_items(selected, mode, quality),
            )
        else:
            url = info.get("webpage_url") or self.url_entry.get().strip()
            title = info.get("title") or "Archivo multimedia"
            self._add_task_to_queue(url, title, mode, quality)

    def _enqueue_playlist_items(self, items: list, mode: str, quality: str):
        for item in items:
            item_url = item.get("url") or item.get("webpage_url")
            if item_url and not item_url.startswith("http"):
                item_url = f"https://www.youtube.com/watch?v={item_url}"
            title = item.get("title") or "Elemento de playlist"
            if item_url:
                self._add_task_to_queue(item_url, title, mode, quality)

    def _add_task_to_queue(self, url: str, title: str, mode: str, quality: str):
        if self.empty_queue_label.winfo_exists():
            self.empty_queue_label.destroy()

        task = DownloadTask(
            url=url,
            title=title,
            mode=mode,
            quality=quality,
            out_dir=self.download_dir,
            ui_callback=lambda t: self.after(0, lambda: self._refresh_task_row(t)),
        )
        self.tasks.append(task)
        self._create_task_row(task)
        task.start()

    def _create_task_row(self, task: DownloadTask):
        row = ctk.CTkFrame(
            self.queue_scroll,
            fg_color=COLORS["bg_subtle"],
            corner_radius=12,
        )
        row.pack(fill="x", padx=10, pady=6)

        left = ctk.CTkFrame(row, fg_color="transparent")
        left.pack(side="left", fill="both", expand=True, padx=14, pady=10)

        title_lbl = ctk.CTkLabel(
            left,
            text=f"[{task.mode} • {task.quality}]  {task.title[:65]}",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color=COLORS["text_main"],
        )
        title_lbl.pack(anchor="w")

        progress_bar = ctk.CTkProgressBar(
            left,
            height=8,
            corner_radius=4,
            fg_color=COLORS["border"],
            progress_color=COLORS["accent"],
        )
        progress_bar.set(0)
        progress_bar.pack(fill="x", pady=(6, 4))

        status_lbl = ctk.CTkLabel(
            left,
            text=task.speed_text,
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=COLORS["text_muted"],
        )
        status_lbl.pack(anchor="w")

        # Botones de control: Pausar/Reanudar y Cancelar
        btns = ctk.CTkFrame(row, fg_color="transparent")
        btns.pack(side="right", padx=14)

        pause_btn = ctk.CTkButton(
            btns,
            text="Pausar",
            width=84,
            height=32,
            corner_radius=8,
            fg_color=COLORS["bg_card"],
            text_color=COLORS["text_main"],
            hover_color=COLORS["border"],
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=task.toggle_pause,
        )
        pause_btn.pack(side="left", padx=(0, 6))

        cancel_btn = ctk.CTkButton(
            btns,
            text="Cancelar",
            width=84,
            height=32,
            corner_radius=8,
            fg_color="#FEE2E2",
            text_color=COLORS["danger"],
            hover_color="#FECACA",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=task.cancel,
        )
        cancel_btn.pack(side="left")

        self.task_widgets[task] = {
            "row": row,
            "progress": progress_bar,
            "status": status_lbl,
            "pause_btn": pause_btn,
            "cancel_btn": cancel_btn,
        }

    def _refresh_task_row(self, task: DownloadTask):
        widgets = self.task_widgets.get(task)
        if not widgets or not widgets["row"].winfo_exists():
            return

        widgets["progress"].set(task.progress)
        widgets["status"].configure(text=task.speed_text)

        if task.status == "Pausado":
            widgets["progress"].configure(progress_color=COLORS["warning"])
            widgets["pause_btn"].configure(text="Reanudar")
        elif task.status == "Completado":
            widgets["progress"].configure(progress_color=COLORS["success"])
            widgets["pause_btn"].configure(state="disabled", text="Listo")
            widgets["cancel_btn"].configure(state="disabled")
        elif task.status in ("Cancelado", "Error"):
            widgets["progress"].configure(progress_color=COLORS["danger"])
            widgets["pause_btn"].configure(state="disabled")
            widgets["cancel_btn"].configure(state="disabled")
        else:
            widgets["progress"].configure(progress_color=COLORS["accent"])
            widgets["pause_btn"].configure(text="Pausar")

    def _clear_finished(self):
        for task in list(self.tasks):
            if task.status in ("Completado", "Cancelado", "Error"):
                widgets = self.task_widgets.pop(task, None)
                if widgets and widgets["row"].winfo_exists():
                    widgets["row"].destroy()
                self.tasks.remove(task)


if __name__ == "__main__":
    app = AJRMTubeApp()
    app.mainloop()
