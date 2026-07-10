"""
Elora HUD (Heads-Up Display) v2.
A centralized PySide6 visual panel for hands-free local voice interaction.
Integrates Spacebar Hold-to-Talk recording, tmux task tracking, and RSS telemetry.
"""

import sys
import os
import json
import math
import wave
import logging
import subprocess
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal, Slot, QTimer, QPoint, QRectF
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLineEdit, QLabel, QGraphicsDropShadowEffect, QTextBrowser,
    QListWidget, QListWidgetItem, QSizePolicy
)
from PySide6.QtGui import QPainter, QColor, QRadialGradient, QPen, QFont, QPainterPath

from elora.brain import query_elora
from elora.actions import execute_agent_task
from elora.news import get_news_summary
from elora.stt import _get_stt_model
from elora.voice import speak_text
from elora.config import load_config

logger = logging.getLogger("elora.hud")

VOICE_INPUT_PATH = "/tmp/elora_voice_input.wav"


class STTWorkerThread(QThread):
    """
    Worker thread that runs offline Vosk speech recognition on the recorded WAV.
    
    Why: Prevents transcription execution from freezing the GUI animations.
    """
    transcription_finished = Signal(str)

    def __init__(self, wav_path: str):
        super().__init__()
        self.wav_path = wav_path

    def run(self):
        try:
            model = _get_stt_model()
            if model is None or not os.path.exists(self.wav_path):
                self.transcription_finished.emit("")
                return

            wf = wave.open(self.wav_path, "rb")
            if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getcomptype() != "NONE":
                logger.error("Audio must be mono PCM 16-bit WAV.")
                self.transcription_finished.emit("")
                return

            from vosk import KaldiRecognizer
            rec = KaldiRecognizer(model, wf.getframerate())
            
            while True:
                data = wf.readframes(4000)
                if len(data) == 0:
                    break
                rec.AcceptWaveform(data)

            res = json.loads(rec.FinalResult())
            text = res.get("text", "").strip()
            self.transcription_finished.emit(text)
        except Exception as e:
            logger.error("STT Worker Error: %s", e)
            self.transcription_finished.emit("")


class BrainWorkerThread(QThread):
    """
    Worker thread to execute Ollama client queries and ReAct agent loops asynchronously.
    """
    query_finished = Signal(dict)
    status_changed = Signal(str)

    def __init__(self, prompt: str, history: list):
        super().__init__()
        self.prompt = prompt
        self.history = history

    def run(self):
        try:
            from elora.agent import run_agent_loop
            
            def emit_status(status_text: str):
                self.status_changed.emit(status_text)
                
            res = run_agent_loop(self.prompt, self.history, emit_status)
            self.query_finished.emit(res)
        except Exception as e:
            logger.error("Brain worker failed: %s", e)
            self.query_finished.emit({"action": "reply", "arguments": {"message": "Sorry, I encountered an internal brain error."}})


class OrbWidget(QWidget):
    """
    Double-buffered vector widget drawing the pulsing, glowing AI Core.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(160, 160)
        self.state = "idle"  # idle, listening, thinking, speaking
        self.phase = 0.0

        # Animation timer driving the breathing cycles at 60 FPS
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick_animation)
        self.timer.start(16)

    def set_state(self, state: str):
        self.state = state
        self.update()

    def tick_animation(self):
        # Update trigonometric phase shift
        if self.state == "listening":
            self.phase += 0.2
        elif self.state == "thinking":
            self.phase += 0.3
        elif self.state == "speaking":
            self.phase += 0.15
        else:
            self.phase += 0.045  # Slow breathing phase
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        width = self.width()
        height = self.height()
        center_x = width / 2.0
        center_y = height / 2.0

        # Calculate dynamics based on phase
        sine_val = math.sin(self.phase)
        
        # Determine base colors and dynamics from state
        if self.state == "listening":
            # Pulsing pink/purple glowing waves
            radius = 32 + sine_val * 4.0
            glow_color = QColor(236, 72, 153, 160)  # Pink-500
            center_color = QColor(244, 63, 94, 255)   # Rose-500
        elif self.state == "thinking":
            # Spinning orbits simulation
            radius = 30 + abs(sine_val) * 2.0
            glow_color = QColor(79, 70, 229, 180)   # Indigo-600
            center_color = QColor(99, 102, 241, 255)  # Indigo-500
        elif self.state == "speaking":
            # Pulse waves matching speech
            radius = 32 + sine_val * 6.0
            glow_color = QColor(16, 185, 129, 160)  # Emerald-500
            center_color = QColor(52, 211, 153, 255) # Emerald-400
        else:
            # Idle breathing silver glow
            radius = 28 + sine_val * 1.5
            glow_color = QColor(156, 163, 175, 70)  # Gray-400
            center_color = QColor(209, 213, 219, 180) # Gray-300

        # Paint outer radial glow
        glow_grad = QRadialGradient(center_x, center_y, radius * 1.6)
        glow_grad.setColorAt(0.0, glow_color)
        glow_grad.setColorAt(0.7, QColor(glow_color.red(), glow_color.green(), glow_color.blue(), 25))
        glow_grad.setColorAt(1.0, QColor(0, 0, 0, 0))

        painter.setBrush(glow_grad)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(center_x - radius * 1.6, center_y - radius * 1.6, radius * 3.2, radius * 3.2)

        # Paint solid inner core
        core_grad = QRadialGradient(center_x, center_y, radius)
        core_grad.setColorAt(0.0, center_color)
        core_grad.setColorAt(1.0, QColor(max(0, center_color.red() - 30), max(0, center_color.green() - 30), max(0, center_color.blue() - 30), 255))

        painter.setBrush(core_grad)
        painter.drawEllipse(center_x - radius, center_y - radius, radius * 2.0, radius * 2.0)


class EloraHUD(QWidget):
    """
    Centralized HUD v2 Window interface for Elora desktop interaction.
    """
    def __init__(self):
        super().__init__()
        self.session_history = []
        self.record_process: Optional[subprocess.Popen] = None
        self.is_recording = False

        # App ID metadata for Niri compositor matching rules
        self.setObjectName("EloraHUD")
        self.setWindowTitle("Elora HUD")
        
        # Configure window flags: Frameless & Stays on top
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        
        # Size specifications (centralized panel)
        self.resize(950, 520)

        # Styling sheet for the HUD container card
        self.setStyleSheet("""
            QWidget#EloraHUD {
                background: transparent;
            }
            QWidget#CentralCard {
                background-color: rgba(15, 16, 26, 0.94); /* Dense OLED anthracite glass */
                border: 1px solid rgba(255, 255, 255, 0.16); /* Sleek highlight frame border */
                border-radius: 24px;
            }
            QLabel {
                color: #FFFFFF;
                font-family: 'Inter', sans-serif;
            }
            QTextBrowser {
                background-color: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 12px;
                color: #E5E7EB;
                font-family: 'Inter', sans-serif;
                font-size: 13px;
                padding: 10px;
            }
            QListWidget {
                background-color: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 12px;
                color: #E5E7EB;
                font-family: 'Inter', sans-serif;
                font-size: 12px;
                padding: 5px;
            }
            QListWidget::item {
                border-bottom: 1px solid rgba(255, 255, 255, 0.04);
                padding: 8px;
            }
            QListWidget::item:hover {
                background-color: rgba(255, 255, 255, 0.06);
                border-radius: 6px;
            }
        """)

        # Main Layout
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(10, 10, 10, 10)

        self.central_card = QWidget(self)
        self.central_card.setObjectName("CentralCard")
        self.main_layout.addWidget(self.central_card)

        # Apply soft drop shadow
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(30)
        shadow.setColor(QColor(0, 0, 0, 150))
        shadow.setOffset(0, 10)
        self.central_card.setGraphicsEffect(shadow)

        self.card_layout = QHBoxLayout(self.central_card)
        self.card_layout.setContentsMargins(25, 25, 25, 25)
        self.card_layout.setSpacing(20)

        # =====================================================================
        # LEFT PANEL: Core AI & Conversation Logs
        # =====================================================================
        self.left_panel = QWidget(self)
        self.left_layout = QVBoxLayout(self.left_panel)
        self.left_layout.setContentsMargins(0, 0, 0, 0)
        
        # State label
        self.state_label = QLabel("SYSTEM STANDBY", self)
        self.state_label.setStyleSheet("color: rgba(255, 255, 255, 0.4); font-family: 'JetBrains Mono'; font-size: 11px; font-weight: bold; letter-spacing: 2px;")
        self.left_layout.addWidget(self.state_label, alignment=Qt.AlignmentFlag.AlignLeft)

        # Core animated AI orb
        self.orb = OrbWidget(self)
        self.left_layout.addWidget(self.orb, alignment=Qt.AlignmentFlag.AlignCenter)

        # Text Console output area
        self.console_output = QTextBrowser(self)
        self.console_output.append("<span style='color: #6366F1;'>Elora:</span> Centralized HUD ready. Hold <b>Spacebar</b> to talk, release when finished.")
        self.left_layout.addWidget(self.console_output)

        # PTT Tip Label
        self.ptt_tip = QLabel("[ Hold SPACE to record ]  •  [ Press ESC to exit ]", self)
        self.ptt_tip.setStyleSheet("color: rgba(255, 255, 255, 0.35); font-family: 'JetBrains Mono'; font-size: 10px; font-weight: bold;")
        self.left_layout.addWidget(self.ptt_tip, alignment=Qt.AlignmentFlag.AlignCenter)

        self.card_layout.addWidget(self.left_panel, stretch=3)

        # =====================================================================
        # RIGHT PANEL: Telemetry and Background Task Tracker
        # =====================================================================
        self.right_panel = QWidget(self)
        self.right_layout = QVBoxLayout(self.right_panel)
        self.right_layout.setContentsMargins(0, 0, 0, 0)
        self.right_layout.setSpacing(15)

        # Task Panel
        self.task_header = QLabel("ACTIVE BACKGROUND TASKS", self)
        self.task_header.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 10px; font-weight: bold; letter-spacing: 1px; color: rgba(255, 255, 255, 0.5);")
        self.right_layout.addWidget(self.task_header)

        self.task_list = QListWidget(self)
        self.right_layout.addWidget(self.task_list)

        # News Skimmer Panel
        self.news_header = QLabel("LATEST NEWS SKIM", self)
        self.news_header.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 10px; font-weight: bold; letter-spacing: 1px; color: rgba(255, 255, 255, 0.5);")
        self.right_layout.addWidget(self.news_header)

        self.news_list = QListWidget(self)
        self.news_list.itemClicked.connect(self.on_news_clicked)
        self.right_layout.addWidget(self.news_list)

        self.card_layout.addWidget(self.right_panel, stretch=2)

        # Center on Active Monitor
        self.center_on_screen()

        # Telemetry Timers
        self.task_timer = QTimer(self)
        self.task_timer.timeout.connect(self.update_tmux_tasks)
        self.task_timer.start(2000)  # Refresh tmux list every 2 seconds

        # Spawn initial RSS feed skimmer load
        QTimer.singleShot(500, self.load_news_skimmer)

    def center_on_screen(self):
        screen = QApplication.primaryScreen().geometry()
        size = self.geometry()
        self.move((screen.width() - size.width()) / 2, (screen.height() - size.height()) / 2)

    # =====================================================================
    # Spacebar Hold-to-Talk Key Event Handling
    # =====================================================================
    def keyPressEvent(self, event):
        if event.isAutoRepeat():
            return
        
        if event.key() == Qt.Key.Key_Space:
            self.start_voice_recording()
        elif event.key() == Qt.Key.Key_Escape:
            # Terminate any recording processes before closing
            if self.record_process:
                self.record_process.terminate()
                self.record_process.wait()
            self.close()
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.isAutoRepeat():
            return
        
        if event.key() == Qt.Key.Key_Space:
            self.stop_voice_recording()
        else:
            super().keyReleaseEvent(event)

    def start_voice_recording(self):
        """Spawns an arecord subprocess to capture microphone input."""
        if self.is_recording:
            return
        self.is_recording = True
        self.state_label.setText("LISTENING...")
        self.orb.set_state("listening")
        self.console_output.append("<br><span style='color: #EC4899;'>System:</span> Recording voice...")

        # Record raw wav mono S16_LE PCM audio at 16kHz
        try:
            self.record_process = subprocess.Popen(
                ["arecord", "-r", "16000", "-f", "S16_LE", "-c", "1", VOICE_INPUT_PATH, "-q"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except Exception as e:
            logger.error("Failed to spawn arecord: %s", e)
            self.console_output.append("<span style='color: #EF4444;'>Error:</span> Failed to start arecord recorder.")
            self.reset_to_idle()

    def stop_voice_recording(self):
        """Stops the arecord process and dispatches transcription."""
        if not self.is_recording:
            return
        self.is_recording = False
        
        if self.record_process:
            self.state_label.setText("TRANSCRIBING...")
            self.orb.set_state("thinking")
            self.record_process.terminate()
            self.record_process.wait()
            self.record_process = None

            # Spawn Vosk STT thread
            self.stt_worker = STTWorkerThread(VOICE_INPUT_PATH)
            self.stt_worker.transcription_finished.connect(self.handle_transcription)
            self.stt_worker.start()
        else:
            self.reset_to_idle()

    @Slot(str)
    def handle_transcription(self, text: str):
        """Processes the transcribed query or resets if silent."""
        if not text:
            self.console_output.append("<span style='color: rgba(255,255,255,0.4);'>System: No speech detected.</span>")
            self.reset_to_idle()
            return

        self.console_output.append(f"<span style='color: #10B981;'>You:</span> {text}")
        self.session_history.append({"role": "user", "content": text})
        if len(self.session_history) > 10:
            self.session_history.pop(0)

        self.state_label.setText("THINKING...")
        self.orb.set_state("thinking")

        # Spawn Ollama worker thread
        self.brain_worker = BrainWorkerThread(text, self.session_history)
        self.brain_worker.status_changed.connect(self.handle_status_change)
        self.brain_worker.query_finished.connect(self.handle_brain_response)
        self.brain_worker.start()

    @Slot(str)
    def handle_status_change(self, status_text: str):
        """Displays intermediate tool execution updates in the logs."""
        self.console_output.append(f"<span style='color: rgba(255, 255, 255, 0.45);'>System: {status_text}</span>")

    @Slot(dict)
    def handle_brain_response(self, result: dict):
        """Parses action paylods returned from Ollama."""
        action = result.get("action")
        args = result.get("arguments", {})

        # Default to standby after query finishes
        self.reset_to_idle()

        if action == "reply":
            msg = args.get("message", "")
            self.session_history.append({"role": "assistant", "content": msg})
            if len(self.session_history) > 10:
                self.session_history.pop(0)
                
            self.console_output.append(f"<span style='color: #6366F1;'>Elora:</span> {msg}")
            
            # Speak reply out loud using kokoro-onnx
            self.orb.set_state("speaking")
            speak_text(msg)
            self.orb.set_state("idle")

        elif action == "news_fetch":
            mode = args.get("mode", "skim")
            if mode == "skim":
                self.console_output.append("<span style='color: #6366F1;'>Elora:</span> Skimming news feeds...")
                summary = get_news_summary()
                self.console_output.append(f"<pre style='color: #D1D5DB;'>{summary}</pre>")
                # Refresh RSS panel list
                self.load_news_skimmer()
            elif mode == "deep_dive":
                idx = args.get("index")
                self.console_output.append(f"<span style='color: #6366F1;'>Elora:</span> Launching article index {idx} in your browser...")
                open_article(idx)

        elif action == "antigravity":
            task_prompt = args.get("prompt", "")
            if task_prompt:
                self.console_output.append(f"<span style='color: #6366F1;'>Elora:</span> Spawning task: \"{task_prompt}\"")
                session = execute_agent_task(task_prompt)
                if session:
                    self.console_output.append(f"<span style='color: #10B981;'>System: Spawned tmux session '{session}'</span>")
                else:
                    self.console_output.append("<span style='color: #EF4444;'>System: Failed to spawn background task.</span>")

    def reset_to_idle(self):
        self.state_label.setText("SYSTEM STANDBY")
        self.orb.set_state("idle")
        if os.path.exists(VOICE_INPUT_PATH):
            try:
                os.remove(VOICE_INPUT_PATH)
            except Exception:
                pass

    # =====================================================================
    # Telemetry Updates: Tmux Sessions and News Skimmer
    # =====================================================================
    def update_tmux_tasks(self):
        """Polls active tmux session list."""
        self.task_list.clear()
        try:
            output = subprocess.check_output(["tmux", "list-sessions"], stderr=subprocess.DEVNULL).decode()
            for line in output.strip().split("\n"):
                if line:
                    item = QListWidgetItem(line.strip())
                    self.task_list.addItem(item)
        except subprocess.CalledProcessError:
            # Tmux server not running or no sessions
            pass

    def load_news_skimmer(self):
        """Parses local feeds and populates RSS list."""
        self.news_list.clear()
        # Fetch news raw summary, parse line items
        try:
            # Import feedparser locally
            import feedparser
            from elora.news import load_config
            config = load_config()
            feeds = config.get("news", {}).get("feeds", [])
            
            count = 1
            for feed_url in feeds:
                parsed = feedparser.parse(feed_url)
                feed_title = parsed.feed.get("title", "News")
                # Add top 2 entries per feed to keep the grid clean
                for entry in parsed.entries[:2]:
                    title = entry.get("title", "No Title")
                    link = entry.get("link", "")
                    
                    item_text = f"[{count}] {title} ({feed_title[:12]})"
                    item = QListWidgetItem(item_text)
                    item.setData(Qt.ItemDataRole.UserRole, link)
                    self.news_list.addItem(item)
                    count += 1
        except Exception as e:
            logger.error("Failed to load news skimmer telemetry: %s", e)

    def on_news_clicked(self, item: QListWidgetItem):
        """Launches article link in browser on item click."""
        link = item.data(Qt.ItemDataRole.UserRole)
        if link:
            self.console_output.append(f"<span style='color: #10B981;'>System:</span> Opening link in web browser...")
            # Run browser launcher
            from elora.actions import open_browser_url
            open_browser_url(link)


def start_hud():
    """Launches the PySide6 event loop for the HUD v2 window."""
    app = QApplication(sys.argv)
    hud = EloraHUD()
    hud.show()
    sys.exit(app.exec())
