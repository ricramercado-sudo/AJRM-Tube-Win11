import os
import sys
import shutil
import subprocess
import threading
from io import BytesIO
from pathlib import Path
from tkinter import filedialog, messagebox
import requests
from PIL import Image
import customtkinter as ctk
import yt_dlp

# ==============================================================================
# PALETA VISUAL EXACTA DE NUNAY AGENCIA (Dark Mode Profundo + Índigo Vibrante)
# ==============================================================================
COLORS = {
    "bg_main": "#050507",
    "bg_card": "#0D0D12",
    "bg_subtle": "#15151E",
    "bg_hover": "#1E1E2B",
    "indigo": "#4F46E5",
    "indigo_hover": "#4338CA",
    "indigo_text": "#818CF8",
    "success": "#10B981",
    "warning": "#F59E0B",
    "danger": "#EF4444",
    "text_main": "#FFFFFF",
    "text_muted": "#94A3B8",
    "border": "#222230",
}

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

YTDLP_RELEASE_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"


def get_bundled_bin_dir() -> str:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def get_app_data_bin_dir() -> str:
    """Carpeta persistente con permisos de escritura para auto-actualizar yt-dlp.exe."""
    local_app = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    bin_dir = os.path.join(local_app, "AJRM-Tube", "bin")
    os.makedirs(bin_dir, exist_ok=True)
    return bin_dir


def setup_environment():
    bundled_dir = get_bundled_bin_dir()
    data_bin_dir = get_app_data_bin_dir()
    os.environ["PATH"] = data_bin_dir + os.pathsep + bundled_dir + os.pathsep + os.environ.get("PATH", "")


def get_ytdlp_executable() -> str:
    return os.path.join(get_app_data_bin_dir(), "yt-dlp.exe")


def format_duration(seconds) -> str:
    if not seconds:
        return "En vivo / Desconocida"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


# ==============================================================================
# CONTROLADOR DE CADA DESCARGA (Usa el motor yt-dlp.exe auto-actualizable)
# ==============================================================================
class DownloadTask:
    def __init__(self, url: str, title: str, mode: str, quality: str, out_dir: str, ui_callback):
        self.url = url
        self.title = title
        self.mode = mode
        self.quality = quality
        self.out_dir = out_dir
        self.ui_callback = ui_callback

        self.pause_event = threading.Event()
        self.pause_event.set()
        self.cancel_flag = False
        self.status = "En cola"
        self.progress = 0.0
        self.speed_text = "Esperando..."
        self.output_filepath = None
        self.process = None
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
            self._terminate_process()
        else:
            self.pause_event.set()
            self.status = "Descargando"
            self.speed_text = "Reanudando descarga..."
            threading.Thread(target=self._run, daemon=True).start()
        self.ui_callback(self)

    def cancel(self):
        self.cancel_flag = True
        self.pause_event.set()
        self._terminate_process()
        self.status = "Cancelado"
        self.speed_text = "Cancelado por el usuario"
        self.ui_callback(self)

    def _terminate_process(self):
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
            except Exception:
                pass

    def open_file(self):
        if self.output_filepath and os.path.exists(self.output_filepath):
            os.startfile(self.output_filepath)
        elif os.path.exists(self.out_dir):
            os.startfile(self.out_dir)

    def _run(self):
        try:
            self.status = "Iniciando"
            self.speed_text = "Conectando..."
            self.ui_callback(self)

            ytdlp_exe = get_ytdlp_executable()
            bundled_dir = get_bundled_bin_dir()
            outtmpl = os.path.join(self.out_dir, "%(title).150B.%(ext)s")

            if os.path.exists(ytdlp_exe):
                cmd = [
                    ytdlp_exe,
                    "--no-playlist",
                    "--newline",
                    "--ffmpeg-location", bundled_dir,
                    "--concurrent-fragments", "4",
                    "--retries", "5",
                    "-o", outtmpl,
                    "--print", "after_move:filepath",
                ]

                if self.mode == "Audio":
                    if "M4A" in self.quality:
                        cmd += ["-f", "bestaudio[ext=m4a]/bestaudio/best", "--embed-metadata"]
                    else:
                        bitrate = "320K" if "320" in self.quality else "192K"
                        cmd += [
                            "-f", "bestaudio/best",
                            "-x", "--audio-format", "mp3",
                            "--audio-quality", bitrate,
                            "--embed-thumbnail", "--embed-metadata",
                        ]
                else:
                    height_map = {
                        "480p Estándar": "480",
                        "720p HD": "720",
                        "1080p Full HD": "1080",
                        "4K Ultra HD (2160p)": "2160",
                    }
                    max_h = height_map.get(self.quality, "1080")
                    cmd += [
                        "-f",
                        f"bestvideo[height<={max_h}][ext=mp4]+bestaudio[ext=m4a]/"
                        f"bestvideo[height<={max_h}]+bestaudio/best[height<={max_h}]/best",
                        "--merge-output-format", "mp4",
                    ]

                cmd.append(self.url)

                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

                self.process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    startupinfo=startupinfo,
                )

                last_line = ""
                for raw_line in self.process.stdout:
                    if self.cancel_flag or not self.pause_event.is_set():
                        break
                    line = raw_line.strip()
                    if not line:
                        continue
                    last_line = line

                    if line.startswith("[download]") and "%" in line:
                        self.status = "Descargando"
                        parts = line.split()
                        for p in parts:
                            if p.endswith("%"):
                                try:
                                    pct_val = float(p.replace("%", ""))
                                    self.progress = min(pct_val / 100.0, 0.99)
                                except ValueError:
                                    pass
                        self.speed_text = line.replace("[download]", "").strip()
                        self.ui_callback(self)
                    elif line.startswith("[ExtractAudio]") or line.startswith("[Merger]"):
                        self.status = "Procesando"
                        self.progress = 0.99
                        self.speed_text = "Procesando formato final con FFmpeg..."
                        self.ui_callback(self)
                    elif os.path.exists(line):
                        self.output_filepath = line

                self.process.wait()

                if self.cancel_flag or not self.pause_event.is_set():
                    return

                if self.process.returncode == 0:
                    self.status = "Completado"
                    self.progress = 1.0
                    fname = os.path.basename(self.output_filepath) if self.output_filepath else self.out_dir
                    self.speed_text = f"Listo: {fname[:60]}"
                    self.ui_callback(self)
                else:
                    self.status = "Error"
                    self.speed_text = f"Error: {last_line[:65]}"
                    self.ui_callback(self)
            else:
                self._run_fallback_lib(bundled_dir, outtmpl)

        except Exception as e:
            if not self.cancel_flag and self.pause_event.is_set():
                self.status = "Error"
                err_msg = str(e).splitlines()[-1] if str(e) else "Error desconocido"
                self.speed_text = f"Error: {err_msg[:65]}"
                self.ui_callback(self)

    def _run_fallback_lib(self, bin_dir: str, outtmpl: str):
        def hook(d):
            if self.cancel_flag:
                raise Exception("CANCELLED")
            if d["status"] == "downloading":
                self.status = "Descargando"
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                dl = d.get("downloaded_bytes", 0)
                if total > 0:
                    self.progress = min(dl / total, 0.99)
                self.speed_text = f"{int(self.progress * 100)}% • {d.get('_speed_str', '').strip()}"
                self.ui_callback(self)

        ydl_opts = {
            "outtmpl": outtmpl,
            "noplaylist": True,
            "quiet": True,
            "progress_hooks": [hook],
            "ffmpeg_location": bin_dir,
        }
        if self.mode == "Audio":
            ydl_opts["format"] = "bestaudio/best"
            ydl_opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "320"}]
        else:
            ydl_opts["format"] = "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]/best"
            ydl_opts["merge_output_format"] = "mp4"

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(self.url, download=True)
            prep = ydl.prepare_filename(info)
            base, _ = os.path.splitext(prep)
            ext = ".mp3" if self.mode == "Audio" else ".mp4"
            self.output_filepath = base + ext if os.path.exists(base + ext) else prep

        self.status = "Completado"
        self.progress = 1.0
        self.speed_text = f"Listo: {os.path.basename(self.output_filepath)[:60]}"
        self.ui_callback(self)


# ==============================================================================
# VENTANA DE SELECCIÓN DE PLAYLIST
# ==============================================================================
class PlaylistSelectorDialog(ctk.CTkToplevel):
    def __init__(self, parent, playlist_title: str, entries: list, on_confirm):
        super().__init__(parent)
        self.title("Seleccionar elementos de la Playlist")
        self.geometry("700x540")
        self.configure(fg_color=COLORS["bg_main"])
        self.transient(parent)
        self.grab_set()

        self.entries = entries
        self.on_confirm = on_confirm
        self.check_vars = []

        header = ctk.CTkFrame(self, fg_color=COLORS["bg_card"], corner_radius=16, border_width=1, border_color=COLORS["border"])
        header.pack(fill="x", padx=18, pady=(18, 10))

        ctk.CTkLabel(header, text=f"PLAYLIST DETECTADA  •  {len(entries)} ELEMENTOS", font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"), text_color=COLORS["indigo_text"]).pack(anchor="w", padx=18, pady=(14, 0))
        ctk.CTkLabel(header, text=playlist_title[:75], font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"), text_color=COLORS["text_main"]).pack(anchor="w", padx=18, pady=(2, 10))

        bar = ctk.CTkFrame(header, fg_color="transparent")
        bar.pack(fill="x", padx=18, pady=(0, 14))

        ctk.CTkButton(bar, text="Seleccionar todos", width=140, height=32, corner_radius=16, fg_color=COLORS["bg_subtle"], border_width=1, border_color=COLORS["border"], text_color=COLORS["text_main"], hover_color=COLORS["bg_hover"], command=lambda: self._set_all(True)).pack(side="left", padx=(0, 8))
        ctk.CTkButton(bar, text="Desmarcar todos", width=140, height=32, corner_radius=16, fg_color=COLORS["bg_subtle"], border_width=1, border_color=COLORS["border"], text_color=COLORS["text_main"], hover_color=COLORS["bg_hover"], command=lambda: self._set_all(False)).pack(side="left")

        scroll = ctk.CTkScrollableFrame(self, fg_color=COLORS["bg_card"], corner_radius=16, border_width=1, border_color=COLORS["border"])
        scroll.pack(fill="both", expand=True, padx=18, pady=6)

        for idx, item in enumerate(entries, start=1):
            var = ctk.BooleanVar(value=True)
            self.check_vars.append((var, item))
            title = item.get("title") or f"Elemento #{idx}"
            dur = format_duration(item.get("duration"))
            row = ctk.CTkCheckBox(scroll, text=f"{idx:02d}. {title[:68]}  [{dur}]", variable=var, font=ctk.CTkFont(family="Segoe UI", size=13), text_color=COLORS["text_main"], fg_color=COLORS["indigo"], hover_color=COLORS["indigo_hover"], border_color=COLORS["border"])
            row.pack(anchor="w", fill="x", padx=12, pady=6)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=18, pady=(10, 18))

        ctk.CTkButton(footer, text="Cancelar", width=110, height=40, corner_radius=20, fg_color="transparent", border_width=1, border_color=COLORS["border"], text_color=COLORS["text_main"], hover_color=COLORS["bg_subtle"], command=self.destroy).pack(side="right", padx=(10, 0))
        ctk.CTkButton(footer, text="Añadir seleccionados  →", height=40, width=210, corner_radius=20, font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"), fg_color=COLORS["indigo"], hover_color=COLORS["indigo_hover"], text_color=COLORS["text_main"], command=self._confirm).pack(side="right")

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
# APLICACIÓN PRINCIPAL: AJRM-TUBE (ESTÉTICA LIMPIA NUNAY)
# ==============================================================================
class AJRMTubeApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        setup_environment()

        self.title("AJRM-Tube  |  Descargador Multimedia")
        self.geometry("980x750")
        self.minsize(880, 660)
        self.configure(fg_color=COLORS["bg_main"])

        self.download_dir = str(Path.home() / "Downloads")
        self.current_info = None
        self.tasks = []
        self.task_widgets = {}
        self.row_order = []
        self._last_analyzed_url = ""
        self._is_analyzing = False

        self._build_ui()
        # Actualización 100% invisible y silenciosa en segundo plano al iniciar
        threading.Thread(target=self._silent_update_engine, daemon=True).start()

    def _build_ui(self):
        topbar = ctk.CTkFrame(self, fg_color=COLORS["bg_main"], corner_radius=0, height=72)
        topbar.pack(fill="x", padx=28, pady=(8, 0))
        topbar.pack_propagate(False)

        brand_frame = ctk.CTkFrame(topbar, fg_color="transparent")
        brand_frame.pack(side="left")

        ctk.CTkLabel(brand_frame, text="ajrm", font=ctk.CTkFont(family="Segoe UI", size=26, weight="bold"), text_color=COLORS["text_main"]).pack(side="left")
        ctk.CTkLabel(brand_frame, text="tube™", font=ctk.CTkFont(family="Segoe UI", size=26, weight="bold"), text_color=COLORS["indigo_text"]).pack(side="left", padx=(3, 16))
        ctk.CTkLabel(brand_frame, text="YouTube  •  Facebook  •  TikTok  •  Instagram", font=ctk.CTkFont(family="Segoe UI", size=12), text_color=COLORS["text_muted"]).pack(side="left", pady=(4, 0))

        folder_box = ctk.CTkFrame(topbar, fg_color="transparent")
        folder_box.pack(side="right")

        self.folder_btn = ctk.CTkButton(
            folder_box,
            text=f"📁  {os.path.basename(self.download_dir)}",
            height=38,
            corner_radius=19,
            fg_color=COLORS["bg_card"],
            border_width=1,
            border_color=COLORS["border"],
            text_color=COLORS["text_main"],
            hover_color=COLORS["bg_hover"],
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=self._choose_folder,
        )
        self.folder_btn.pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            folder_box,
            text="ABRIR CARPETA",
            height=38,
            width=135,
            corner_radius=19,
            fg_color=COLORS["indigo"],
            hover_color=COLORS["indigo_hover"],
            text_color=COLORS["text_main"],
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            command=self._open_folder,
        ).pack(side="left")

        # Tarjeta principal
        main_card = ctk.CTkFrame(self, fg_color=COLORS["bg_card"], corner_radius=20, border_width=1, border_color=COLORS["border"])
        main_card.pack(fill="x", padx=28, pady=(8, 16))

        header_tag = ctk.CTkFrame(main_card, fg_color="transparent")
        header_tag.pack(fill="x", padx=24, pady=(18, 8))

        ctk.CTkLabel(header_tag, text="Descarga multimedia en", font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"), text_color=COLORS["text_main"]).pack(side="left")
        ctk.CTkLabel(header_tag, text=" máxima calidad", font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"), text_color=COLORS["indigo_text"]).pack(side="left")

        url_row = ctk.CTkFrame(main_card, fg_color="transparent")
        url_row.pack(fill="x", padx=24, pady=(0, 16))

        self.url_var = ctk.StringVar()
        self.url_var.trace_add("write", self._on_url_changed)

        self.url_entry = ctk.CTkEntry(
            url_row,
            textvariable=self.url_var,
            placeholder_text="Pega aquí el enlace de YouTube, TikTok, Facebook o Instagram...",
            height=44,
            corner_radius=22,
            fg_color=COLORS["bg_subtle"],
            border_width=1,
            border_color=COLORS["border"],
            text_color=COLORS["text_main"],
            placeholder_text_color=COLORS["text_muted"],
            font=ctk.CTkFont(family="Segoe UI", size=13),
        )
        self.url_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.url_entry.bind("<Return>", lambda e: self._analyze_url(force=True))

        ctk.CTkButton(url_row, text="Pegar", width=85, height=44, corner_radius=22, fg_color="transparent", border_width=1, border_color=COLORS["border"], text_color=COLORS["text_main"], hover_color=COLORS["bg_hover"], font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"), command=self._paste_from_clipboard).pack(side="left", padx=(0, 8))

        self.analyze_btn = ctk.CTkButton(url_row, text="Analizar  →", width=135, height=44, corner_radius=22, fg_color=COLORS["indigo"], hover_color=COLORS["indigo_hover"], text_color=COLORS["text_main"], font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"), command=lambda: self._analyze_url(force=True))
        self.analyze_btn.pack(side="left")

        # Vista Previa
        self.preview_frame = ctk.CTkFrame(main_card, fg_color=COLORS["bg_subtle"], corner_radius=16, border_width=1, border_color=COLORS["border"])
        self.preview_frame.pack(fill="x", padx=24, pady=(0, 20))

        self.thumb_label = ctk.CTkLabel(self.preview_frame, text="Vista previa", width=216, height=122, fg_color=COLORS["bg_main"], text_color=COLORS["text_muted"], corner_radius=12, font=ctk.CTkFont(family="Segoe UI", size=12))
        self.thumb_label.pack(side="left", padx=16, pady=16)

        details_box = ctk.CTkFrame(self.preview_frame, fg_color="transparent")
        details_box.pack(side="left", fill="both", expand=True, padx=(0, 16), pady=16)

        self.platform_badge = ctk.CTkLabel(details_box, text="LISTO PARA ANALIZAR", font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"), text_color=COLORS["indigo_text"])
        self.platform_badge.pack(anchor="w")

        self.title_label = ctk.CTkLabel(details_box, text="Pega un enlace arriba para ver su miniatura y elegir formato", font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"), text_color=COLORS["text_main"], wraplength=560, justify="left")
        self.title_label.pack(anchor="w", pady=(3, 3))

        self.meta_label = ctk.CTkLabel(details_box, text="Duración: --:--  •  Canal: --", font=ctk.CTkFont(family="Segoe UI", size=12), text_color=COLORS["text_muted"])
        self.meta_label.pack(anchor="w", pady=(0, 12))

        controls_row = ctk.CTkFrame(details_box, fg_color="transparent")
        controls_row.pack(fill="x")

        self.mode_segment = ctk.CTkSegmentedButton(controls_row, values=["Audio", "Video"], width=155, height=38, corner_radius=19, fg_color=COLORS["bg_main"], selected_color=COLORS["indigo"], selected_hover_color=COLORS["indigo_hover"], unselected_color=COLORS["bg_main"], unselected_hover_color=COLORS["bg_hover"], text_color=COLORS["text_main"], font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"), command=self._on_mode_change)
        self.mode_segment.set("Audio")
        self.mode_segment.pack(side="left", padx=(0, 10))

        self.quality_menu = ctk.CTkComboBox(
            controls_row,
            values=["MP3 320 kbps (Recomendado)", "MP3 192 kbps", "M4A Original (Rápido)"],
            width=245,
            height=38,
            corner_radius=19,
            state="readonly",
            fg_color=COLORS["bg_main"],
            border_width=1,
            border_color=COLORS["border"],
            button_color=COLORS["indigo"],
            button_hover_color=COLORS["indigo_hover"],
            dropdown_fg_color=COLORS["bg_card"],
            dropdown_hover_color=COLORS["indigo"],
            dropdown_text_color=COLORS["text_main"],
            text_color=COLORS["text_main"],
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
        )
        self.quality_menu.set("MP3 320 kbps (Recomendado)")
        self.quality_menu.pack(side="left", padx=(0, 12))

        self.download_btn = ctk.CTkButton(controls_row, text="DESCARGAR  ↓", height=40, corner_radius=20, fg_color=COLORS["indigo"], hover_color=COLORS["indigo_hover"], text_color=COLORS["text_main"], font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"), state="disabled", command=self._trigger_download)
        self.download_btn.pack(side="right", fill="x", expand=True)

        # Cola de descargas
        queue_header = ctk.CTkFrame(self, fg_color="transparent")
        queue_header.pack(fill="x", padx=28, pady=(2, 8))

        ctk.CTkLabel(queue_header, text="Cola de descargas", font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"), text_color=COLORS["text_main"]).pack(side="left")
        ctk.CTkButton(queue_header, text="Limpiar historial", height=28, width=125, corner_radius=14, fg_color="transparent", border_width=1, border_color=COLORS["border"], text_color=COLORS["text_muted"], hover_color=COLORS["bg_subtle"], font=ctk.CTkFont(family="Segoe UI", size=11), command=self._clear_finished).pack(side="right")

        self.queue_scroll = ctk.CTkScrollableFrame(self, fg_color=COLORS["bg_card"], corner_radius=20, border_width=1, border_color=COLORS["border"])
        self.queue_scroll.pack(fill="both", expand=True, padx=28, pady=(0, 22))

        self.empty_queue_label = ctk.CTkLabel(self.queue_scroll, text="Aún no hay descargas en la cola.\nPega un enlace arriba y presiona DESCARGAR.", font=ctk.CTkFont(family="Segoe UI", size=13), text_color=COLORS["text_muted"])
        self.empty_queue_label.pack(pady=48)

    # --------------------------------------------------------------------------
    # AUTO-ACTUALIZACIÓN INVISIBLE DEL MOTOR EN SEGUNDO PLANO
    # --------------------------------------------------------------------------
    def _silent_update_engine(self):
        ytdlp_exe = get_ytdlp_executable()
        try:
            if not os.path.exists(ytdlp_exe):
                resp = requests.get(YTDLP_RELEASE_URL, stream=True, timeout=25)
                resp.raise_for_status()
                tmp_path = ytdlp_exe + ".tmp"
                with open(tmp_path, "wb") as f:
                    shutil.copyfileobj(resp.raw, f)
                os.replace(tmp_path, ytdlp_exe)

            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            subprocess.run(
                [ytdlp_exe, "-U"],
                capture_output=True,
                text=True,
                timeout=30,
                startupinfo=startupinfo,
            )
        except Exception:
            pass

    # --------------------------------------------------------------------------
    # ACCIONES DE INTERFAZ Y ANÁLISIS RÁPIDO AUTOMÁTICO
    # --------------------------------------------------------------------------
    def _choose_folder(self):
        selected = filedialog.askdirectory(initialdir=self.download_dir)
        if selected:
            self.download_dir = selected
            self.folder_btn.configure(text=f"📁  {os.path.basename(selected)}")

    def _open_folder(self):
        if os.path.exists(self.download_dir):
            os.startfile(self.download_dir)

    def _on_url_changed(self, *args):
        text = self.url_var.get().strip()
        if (
            (text.startswith("http://") or text.startswith("https://"))
            and "." in text
            and text != self._last_analyzed_url
            and not self._is_analyzing
        ):
            self.after(150, self._analyze_url)

    def _paste_from_clipboard(self):
        try:
            text = self.clipboard_get().strip()
            self._last_analyzed_url = ""
            self.url_var.set(text)
        except Exception:
            pass

    def _on_mode_change(self, value: str):
        if value == "Audio":
            self.quality_menu.configure(values=["MP3 320 kbps (Recomendado)", "MP3 192 kbps", "M4A Original (Rápido)"])
            self.quality_menu.set("MP3 320 kbps (Recomendado)")
        else:
            self.quality_menu.configure(values=["1080p Full HD", "720p HD", "480p Estándar", "4K Ultra HD (2160p)"])
            self.quality_menu.set("1080p Full HD")

    def _analyze_url(self, force: bool = False):
        url = self.url_var.get().strip()
        if not url or self._is_analyzing:
            return
        if not force and url == self._last_analyzed_url:
            return

        self._is_analyzing = True
        self._last_analyzed_url = url
        self.analyze_btn.configure(state="disabled", text="Cargando...")
        self.download_btn.configure(state="disabled")
        self.platform_badge.configure(text="ANALIZANDO ENLACE...", text_color=COLORS["indigo_text"])
        self.title_label.configure(text="Obteniendo información rápida del servidor...")
        self.meta_label.configure(text="Por favor espera un momento...")

        threading.Thread(target=self._fetch_metadata, args=(url,), daemon=True).start()

    def _fetch_metadata(self, url: str):
        try:
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": "in_playlist",
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
        self._is_analyzing = False
        self.analyze_btn.configure(state="normal", text="Analizar  →")
        self.platform_badge.configure(text="ERROR AL ANALIZAR", text_color=COLORS["danger"])
        self.title_label.configure(text="No se pudo leer este enlace")
        self.meta_label.configure(text=err[:90])

    def _update_preview(self, info: dict):
        self._is_analyzing = False
        self.analyze_btn.configure(state="normal", text="Analizar  →")
        self.download_btn.configure(state="normal")

        extractor = (info.get("extractor_key") or "WEB").upper()
        is_playlist = info.get("_type") == "playlist" or "entries" in info

        if is_playlist:
            entries = [e for e in (info.get("entries") or []) if e]
            title = info.get("title") or "Playlist sin título"
            self.platform_badge.configure(text=f"{extractor}  •  PLAYLIST ({len(entries)} ELEMENTOS)", text_color=COLORS["indigo_text"])
            self.title_label.configure(text=title)
            self.meta_label.configure(text="Presiona DESCARGAR para elegir qué elementos bajar")
            thumb_url = info.get("thumbnail") or (entries[0].get("thumbnail") if entries else None)
        else:
            title = info.get("title") or "Video sin título"
            dur = format_duration(info.get("duration"))
            uploader = info.get("uploader") or info.get("channel") or "Desconocido"
            self.platform_badge.configure(text=f"{extractor}  •  LISTO", text_color=COLORS["indigo_text"])
            self.title_label.configure(text=title)
            self.meta_label.configure(text=f"Duración: {dur}  •  Autor: {uploader}")
            thumb_url = info.get("thumbnail")

        if thumb_url:
            threading.Thread(target=self._load_thumbnail, args=(thumb_url,), daemon=True).start()

    def _load_thumbnail(self, thumb_url: str):
        try:
            resp = requests.get(thumb_url, timeout=6)
            img = Image.open(BytesIO(resp.content)).convert("RGB")
            img = img.resize((216, 122), Image.Resampling.LANCZOS)
            ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(216, 122))
            self.after(0, lambda: self.thumb_label.configure(image=ctk_img, text=""))
        except Exception:
            pass

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
            url = info.get("webpage_url") or self.url_var.get().strip()
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
        self.tasks.insert(0, task)
        self._create_task_row(task)
        task.start()

    def _create_task_row(self, task: DownloadTask):
        row = ctk.CTkFrame(self.queue_scroll, fg_color=COLORS["bg_subtle"], corner_radius=14, border_width=1, border_color=COLORS["border"])

        existing_rows = [r for r in self.row_order if r.winfo_exists()]
        if existing_rows:
            row.pack(fill="x", padx=12, pady=6, before=existing_rows[0])
        else:
            row.pack(fill="x", padx=12, pady=6)
        self.row_order.insert(0, row)

        left = ctk.CTkFrame(row, fg_color="transparent")
        left.pack(side="left", fill="both", expand=True, padx=16, pady=12)

        title_lbl = ctk.CTkLabel(left, text=f"[{task.mode} • {task.quality}]  {task.title[:65]}", font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"), text_color=COLORS["text_main"])
        title_lbl.pack(anchor="w")

        progress_bar = ctk.CTkProgressBar(left, height=7, corner_radius=4, fg_color=COLORS["bg_main"], progress_color=COLORS["indigo"])
        progress_bar.set(0)
        progress_bar.pack(fill="x", pady=(7, 5))

        status_lbl = ctk.CTkLabel(left, text=task.speed_text, font=ctk.CTkFont(family="Segoe UI", size=11), text_color=COLORS["text_muted"])
        status_lbl.pack(anchor="w")

        btns = ctk.CTkFrame(row, fg_color="transparent")
        btns.pack(side="right", padx=16)

        pause_btn = ctk.CTkButton(btns, text="Pausar", width=86, height=34, corner_radius=17, fg_color=COLORS["bg_card"], border_width=1, border_color=COLORS["border"], text_color=COLORS["text_main"], hover_color=COLORS["bg_hover"], font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"), command=task.toggle_pause)
        pause_btn.pack(side="left", padx=(0, 8))

        cancel_btn = ctk.CTkButton(btns, text="Cancelar", width=86, height=34, corner_radius=17, fg_color="#2A1215", text_color=COLORS["danger"], hover_color="#3F171C", font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"), command=task.cancel)
        cancel_btn.pack(side="left")

        open_btn = ctk.CTkButton(btns, text="▶  Abrir", width=105, height=34, corner_radius=17, fg_color=COLORS["indigo"], hover_color=COLORS["indigo_hover"], text_color=COLORS["text_main"], font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"), command=task.open_file)

        self.task_widgets[task] = {
            "row": row,
            "progress": progress_bar,
            "status": status_lbl,
            "pause_btn": pause_btn,
            "cancel_btn": cancel_btn,
            "open_btn": open_btn,
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
            widgets["pause_btn"].pack_forget()
            widgets["cancel_btn"].pack_forget()
            widgets["open_btn"].pack(side="left")
        elif task.status in ("Cancelado", "Error"):
            widgets["progress"].configure(progress_color=COLORS["danger"])
            widgets["pause_btn"].configure(state="disabled")
            widgets["cancel_btn"].configure(state="disabled")
        else:
            widgets["progress"].configure(progress_color=COLORS["indigo"])
            widgets["pause_btn"].configure(text="Pausar")

    def _clear_finished(self):
        for task in list(self.tasks):
            if task.status in ("Completado", "Cancelado", "Error"):
                widgets = self.task_widgets.pop(task, None)
                if widgets and widgets["row"].winfo_exists():
                    if widgets["row"] in self.row_order:
                        self.row_order.remove(widgets["row"])
                    widgets["row"].destroy()
                self.tasks.remove(task)


if __name__ == "__main__":
    app = AJRMTubeApp()
    app.mainloop()
