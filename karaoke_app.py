import os
import sys
import time
import datetime
import threading
import re
import requests
import sounddevice as sd
import numpy as np
import pygame
import soundfile as sf
from mutagen import File as MutagenFile
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# --- Custom Multiline Lyrics Dialog ---
class CustomLyricsDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Provide Custom Lyrics")
        self.geometry("550x450")
        self.transient(parent)
        self.grab_set()
        
        self.lyrics = None
        
        ttk.Label(self, text="Enter or paste your plaintext lyrics below:", font=("Helvetica", 11, "bold")).pack(anchor=tk.W, padx=15, pady=10)
        
        text_frame = ttk.Frame(self)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=5)
        
        self.text_widget = tk.Text(text_frame, wrap=tk.WORD, font=("Helvetica", 12), bg="#1e1e1e", fg="#ffffff", insertbackground="white")
        self.text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=self.text_widget.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.text_widget.config(yscrollcommand=scrollbar.set)
        
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=tk.X, padx=15, pady=15)
        
        btn_submit = ttk.Button(btn_frame, text="Submit Lyrics", command=self.on_submit)
        btn_submit.pack(side=tk.RIGHT, padx=5)
        
        btn_cancel = ttk.Button(btn_frame, text="Cancel", command=self.on_cancel)
        btn_cancel.pack(side=tk.RIGHT, padx=5)
        
        self.protocol("WM_DELETE_WINDOW", self.on_cancel)
        self.wait_window(self)
        
    def on_submit(self):
        self.lyrics = self.text_widget.get("1.0", tk.END).strip()
        self.destroy()
        
    def on_cancel(self):
        self.lyrics = None
        self.destroy()

# --- Audacity Pipe Client for Windows ---
class AudacityPipeClient:
    def __init__(self):
        if sys.platform == 'win32':
            self.toname = r'\\.\pipe\ToSrvPipe'
            self.fromname = r'\\.\pipe\FromSrvPipe'
            self.eol = '\r\n\0'
        else:
            uid = os.getuid()
            self.toname = f'/tmp/audacity_script_pipe.to.{uid}'
            self.fromname = f'/tmp/audacity_script_pipe.from.{uid}'
            self.eol = '\n'

    def send_command(self, command, retries=5, delay=1.0):
        to_file = None
        from_file = None
        
        for attempt in range(retries):
            try:
                to_file = open(self.toname, 'w', encoding='ascii')
                from_file = open(self.fromname, 'r', encoding='ascii')
                break
            except OSError:
                if attempt < retries - 1:
                    time.sleep(delay)
                else:
                    raise ConnectionError("Could not connect to Audacity pipes. Ensure Audacity is running normally with mod-script-pipe enabled.")

        try:
            to_file.write(command + self.eol)
            to_file.flush()
            
            response = ""
            while True:
                line = from_file.readline()
                if not line or line.startswith("BatchCommand finished: OK"):
                    break
                if line.startswith("BatchCommand finished: Failed"):
                    raise RuntimeError(f"Audacity command failed: {line.strip()}")
                response += line
            return response
        finally:
            if to_file:
                to_file.close()
            if from_file:
                from_file.close()

# --- LRCLIB API Handler ---
def search_lrclib(artist, title):
    url = "https://lrclib.net/api/search"
    query = f"{artist} - {title}"
    headers = {"User-Agent": "KaraokeStudioApp/1.0 (Python Desktop App)"}
    
    for attempt in range(10):
        try:
            resp = requests.get(url, params={"q": query}, headers=headers, timeout=10)
            if resp.status_code == 503:
                time.sleep(1)
                continue
            resp.raise_for_status()
            results = resp.json()
            if results:
                for item in results:
                    if item.get("syncedLyrics"):
                        return item
                for item in results:
                    if item.get("plainLyrics"):
                        return item
                return results[0]
            return None
        except requests.RequestException:
            if attempt == 9:
                raise
            time.sleep(1)
    return None

# --- LRC Parser ---
def parse_lrc(lrc_text):
    parsed = []
    pattern = re.compile(r'\[(\d{2}):(\d{2})\.(\d{2,3})\](.*)')
    for line in lrc_text.splitlines():
        match = pattern.match(line.strip())
        if match:
            minutes, seconds, millis, text = match.groups()
            total_seconds = int(minutes) * 60 + int(seconds) + int(millis) / (1000 if len(millis) == 3 else 100)
            parsed.append((total_seconds, text.strip()))
    return sorted(parsed, key=lambda x: x[0])

# --- Main Application GUI ---
class KaraokeApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Python Karaoke & Recording Studio")
        self.root.geometry("1065x875")  # 25% larger width and height
        
        self.audio_path = None
        self.playback_audio_path = os.path.abspath(os.path.join(".", "karaoke_playback_temp.wav"))
        self.artist = ""
        self.title = ""
        self.lyrics_data = None
        self.is_synced = False
        self.parsed_lyrics = []
        self.player_name = ""
        
        self.is_playing = False
        self.is_recording = False
        self.audio_stream = None
        self.recorded_frames = []
        self.song_start_time = 0
        
        self.create_welcome_screen()

    def clear_window(self):
        for widget in self.root.winfo_children():
            widget.destroy()

    def create_welcome_screen(self):
        self.clear_window()
        
        frame = ttk.Frame(self.root, padding=30)
        frame.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(frame, text="🎤 Python Karaoke Studio", font=("Helvetica", 22, "bold")).pack(pady=20)
        
        # Player Name Input
        name_frame = ttk.LabelFrame(frame, text=" Singer Session Configuration ", padding=15)
        name_frame.pack(fill=tk.X, pady=10)
        
        ttk.Label(name_frame, text="Enter Your Name:", font=("Helvetica", 11)).pack(anchor=tk.W, pady=5)
        self.name_entry = ttk.Entry(name_frame, font=("Helvetica", 12))
        self.name_entry.pack(fill=tk.X, pady=5)
        if self.player_name:
            self.name_entry.insert(0, self.player_name)
            
        # Audio File Selection
        file_frame = ttk.LabelFrame(frame, text=" Audio Track Selection ", padding=15)
        file_frame.pack(fill=tk.X, pady=15)
        
        self.file_label = ttk.Label(file_frame, text="No file selected.", font=("Helvetica", 10, "italic"))
        self.file_label.pack(anchor=tk.W, pady=5)
        
        btn_browse = ttk.Button(file_frame, text="Browse Audio File (MP3, FLAC, OGG, M4A, WAV, etc.)", command=self.load_audio_file)
        btn_browse.pack(anchor=tk.W, pady=5)
        
        # Proceed Button
        self.btn_next = ttk.Button(frame, text="Fetch Lyrics & Process Backing Track", command=self.process_track, state=tk.DISABLED)
        self.btn_next.pack(pady=25)

    def load_audio_file(self):
        audio_filetypes = [
            ("All Supported Audio", "*.mp3 *.wav *.ogg *.flac *.m4a *.aac *.opus *.wma *.aiff"),
            ("MP3 Audio", "*.mp3"),
            ("FLAC Audio", "*.flac"),
            ("OGG Vorbis", "*.ogg"),
            ("WAV Audio", "*.wav"),
            ("AAC / M4A", "*.m4a *.aac"),
            ("Opus Audio", "*.opus"),
            ("WMA Audio", "*.wma"),
            ("AIFF Audio", "*.aiff"),
            ("All Files", "*.*")
        ]
        path = filedialog.askopenfilename(filetypes=audio_filetypes)
        if path:
            self.audio_path = path
            filename = os.path.basename(path)
            fallback_title = os.path.splitext(filename)[0]
            
            try:
                audio_file = MutagenFile(path, easy=True)
                if audio_file is not None:
                    self.artist = audio_file.get("artist", ["Unknown Artist"])[0]
                    self.title = audio_file.get("title", [fallback_title])[0]
                else:
                    audio_file = MutagenFile(path)
                    if audio_file is not None and hasattr(audio_file, "tags") and audio_file.tags:
                        tags = audio_file.tags
                        self.artist = str(tags.get("TPE1", tags.get("artist", ["Unknown Artist"])[0]))
                        self.title = str(tags.get("TIT2", tags.get("title", [fallback_title])[0]))
                    else:
                        self.artist = "Unknown Artist"
                        self.title = fallback_title
            except Exception:
                self.artist = "Unknown Artist"
                self.title = fallback_title
                
            self.file_label.config(text=f"Selected: {filename}\nArtist: {self.artist} | Title: {self.title}")
            self.btn_next.config(state=tk.NORMAL)

    def process_track(self):
        name = self.name_entry.get().strip()
        if not name:
            messagebox.showerror("Error", "Please enter your singer name for the session.")
            return
        self.player_name = name
        
        prog_win = tk.Toplevel(self.root)
        prog_win.title("Processing Audio & Lyrics...")
        prog_win.geometry("420x160")
        prog_win.transient(self.root)
        prog_win.grab_set()
        
        prog_label = ttk.Label(prog_win, text="Searching lyrics via LRCLIB...", font=("Helvetica", 11))
        prog_label.pack(pady=15)
        
        pb = ttk.Progressbar(prog_win, mode="indeterminate", length=350)
        pb.pack(pady=10)
        pb.start(10)
        
        def background_tasks():
            try:
                lyric_res = search_lrclib(self.artist, self.title)
                lyrics_found = False
                
                if lyric_res:
                    if lyric_res.get("syncedLyrics"):
                        self.lyrics_data = lyric_res["syncedLyrics"]
                        self.is_synced = True
                        self.parsed_lyrics = parse_lrc(self.lyrics_data)
                        lyrics_found = True
                    elif lyric_res.get("plainLyrics"):
                        self.lyrics_data = lyric_res["plainLyrics"]
                        self.is_synced = False
                        lyrics_found = True
                
                prog_win.destroy()
                
                if not lyrics_found:
                    def handle_no_lyrics():
                        answer = messagebox.askyesno(
                            "No Lyrics Found", 
                            "No lyrics were found on LRCLIB.\nWould you like to provide your own lyrics?"
                        )
                        if answer:
                            dialog = CustomLyricsDialog(self.root)
                            if dialog.lyrics is not None:
                                self.lyrics_data = dialog.lyrics if dialog.lyrics else "Manual lyrics provided (blank). Scroll manually."
                                self.is_synced = False
                                self.run_audio_processing()
                            else:
                                self.create_welcome_screen()
                        else:
                            self.create_welcome_screen()
                    
                    self.root.after(0, handle_no_lyrics)
                else:
                    self.run_audio_processing()
                    
            except Exception as e:
                err_msg = str(e)
                prog_win.destroy()
                self.root.after(0, lambda msg=err_msg: messagebox.showerror("Error", f"Failed during processing: {msg}"))

        threading.Thread(target=background_tasks, daemon=True).start()

    def run_audio_processing(self):
        prog_win = tk.Toplevel(self.root)
        prog_win.title("Processing Audio...")
        prog_win.geometry("420x160")
        prog_win.transient(self.root)
        prog_win.grab_set()
        
        prog_label = ttk.Label(prog_win, text="Waiting for audio conversion & Audacity separation...", font=("Helvetica", 11))
        prog_label.pack(pady=15)
        
        pb = ttk.Progressbar(prog_win, mode="indeterminate", length=350)
        pb.pack(pady=10)
        pb.start(10)
        
        def audio_task():
            try:
                audacity_success = False
                temp_audacity_output = os.path.abspath(os.path.join(".", "karaoke_backing_export.wav"))
                
                try:
                    client = AudacityPipeClient()
                    clean_path = self.audio_path.replace("\\", "/")
                    client.send_command(f'Import2: Filename="{clean_path}"')
                    client.send_command('SelectAll:')
                    client.send_command('Macro_MusicSeparation:')
                    client.send_command('SelectAll:')
                    out_mix = temp_audacity_output.replace("\\", "/")
                    client.send_command(f'Export2: Filename="{out_mix}"')
                    client.send_command('SelectAll:')
                    client.send_command('RemoveTracks:')
                    audacity_success = True
                except Exception as ex:
                    print(f"Audacity automation note: {ex}. Using direct audio fallback.")
                
                source_for_playback = temp_audacity_output if (audacity_success and os.path.exists(temp_audacity_output)) else self.audio_path
                
                try:
                    data, samplerate = sf.read(source_for_playback)
                    sf.write(self.playback_audio_path, data, samplerate, subtype='PCM_16')
                except Exception as sf_ex:
                    raise RuntimeError(f"Could not decode audio format for playback: {sf_ex}")
                
                prog_win.destroy()
                self.root.after(0, self.create_karaoke_screen)
            except Exception as e:
                err_msg = str(e)
                prog_win.destroy()
                self.root.after(0, lambda msg=err_msg: messagebox.showerror("Error", f"Failed during processing: {msg}"))

        threading.Thread(target=audio_task, daemon=True).start()

    def create_karaoke_screen(self):
        self.clear_window()
        
        main_frame = ttk.Frame(self.root, padding=20)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        header_text = f"🎵 Now Singing: {self.artist} - {self.title} | Singer: {self.player_name}"
        ttk.Label(main_frame, text=header_text, font=("Helvetica", 14, "bold")).pack(pady=10)
        
        lyrics_frame = ttk.LabelFrame(main_frame, text=" Lyrics ", padding=10)
        lyrics_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        
        self.lyrics_text = tk.Text(lyrics_frame, wrap=tk.WORD, font=("Helvetica", 14), bg="#1e1e1e", fg="#ffffff")
        self.lyrics_text.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
        
        scrollbar = ttk.Scrollbar(lyrics_frame, orient=tk.VERTICAL, command=self.lyrics_text.yview)
        scrollbar.pack(fill=tk.Y, side=tk.RIGHT)
        self.lyrics_text.config(yscrollcommand=scrollbar.set)
        
        if self.is_synced:
            for _, line in self.parsed_lyrics:
                self.lyrics_text.insert(tk.END, line + "\n")
        else:
            self.lyrics_text.insert(tk.END, self.lyrics_data if self.lyrics_data else "No lyrics available.")
        self.lyrics_text.config(state=tk.DISABLED)
        
        ctrl_frame = ttk.Frame(main_frame)
        ctrl_frame.pack(fill=tk.X, pady=15)
        
        self.btn_play_start = ttk.Button(ctrl_frame, text="▶ Start Performance", command=self.toggle_playback)
        self.btn_play_start.pack(side=tk.LEFT, padx=5, expand=True, fill=tk.X)
        
        btn_stop = ttk.Button(ctrl_frame, text="⏹ Stop & Save", command=self.stop_and_save)
        btn_stop.pack(side=tk.LEFT, padx=5, expand=True, fill=tk.X)
        
        pygame.mixer.init()

    def toggle_playback(self):
        if not self.is_playing:
            pygame.mixer.music.load(self.playback_audio_path)
            pygame.mixer.music.play()
            
            self.song_start_time = time.time()
            self.is_playing = True
            self.btn_play_start.config(text="▶ Performance in Progress", state=tk.DISABLED)
            
            self.recorded_frames = []
            sample_rate = 44100
            
            def callback(indata, frames, time_info, status):
                if self.is_recording:
                    self.recorded_frames.append(indata.copy())
            
            self.is_recording = True
            self.audio_stream = sd.InputStream(samplerate=sample_rate, channels=1, callback=callback)
            self.audio_stream.start()
            
            if self.is_synced:
                threading.Thread(target=self.sync_lyrics_loop, daemon=True).start()

    def sync_lyrics_loop(self):
        while self.is_playing and pygame.mixer.music.get_busy():
            elapsed = time.time() - self.song_start_time
            current_idx = 0
            for idx, (timestamp, _) in enumerate(self.parsed_lyrics):
                if elapsed >= timestamp:
                    current_idx = idx
            
            self.root.after(0, lambda idx=current_idx: self.highlight_line(idx))
            time.sleep(0.1)

    def highlight_line(self, index):
        self.lyrics_text.config(state=tk.NORMAL)
        self.lyrics_text.tag_remove("highlight", "1.0", tk.END)
        line_start = f"{index + 1}.0"
        line_end = f"{index + 1}.end"
        self.lyrics_text.tag_add("highlight", line_start, line_end)
        self.lyrics_text.tag_config("highlight", background="#4a90e2", foreground="#ffffff")
        self.lyrics_text.see(line_start)
        self.lyrics_text.config(state=tk.DISABLED)

    def stop_and_save(self):
        if not self.is_playing:
            return
        
        self.is_playing = False
        self.is_recording = False
        pygame.mixer.music.stop()
        
        try:
            pygame.mixer.music.unload()
        except Exception:
            pass
        
        if self.audio_stream:
            self.audio_stream.stop()
            self.audio_stream.close()
            
        if self.recorded_frames:
            audio_data = np.concatenate(self.recorded_frames, axis=0)
            os.makedirs("./recordings", exist_ok=True)
            
            timestamp_str = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            safe_song_name = re.sub(r'[^\w\-]', '_', self.title)
            safe_player_name = re.sub(r'[^\w\-]', '_', self.player_name)
            filename = f"{timestamp_str}-{safe_player_name}-{safe_song_name}.mp3"
            filepath = os.path.join("./recordings", filename)
            
            try:
                sf.write(filepath, audio_data, 44100)
            except Exception:
                wav_path = filepath.replace(".mp3", ".wav")
                sf.write(wav_path, audio_data, 44100)
                filepath = wav_path
                
            messagebox.showinfo("Saved", f"Karaoke recording successfully saved to:\n{filepath}")
        
        self.create_welcome_screen()

if __name__ == "__main__":
    root = tk.Tk()
    app = KaraokeApp(root)
    root.mainloop()