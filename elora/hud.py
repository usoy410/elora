"""
Elora HUD (Heads-Up Display) v2.
An elegant desktop dashboard featuring an animated AI core, system monitors,
and a collapsible sidebar control panel for Chat Logs, Tools, and Voice/Instruction settings.
"""

import sys
import os
import json
import math
import wave
import logging
import subprocess
from typing import Optional, Dict, Any

from PySide6.QtCore import Qt, QThread, Signal, Slot, QTimer, QPoint, QRectF, QEvent
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QGraphicsDropShadowEffect, QTextBrowser, QListWidget, QListWidgetItem,
    QPushButton, QStackedWidget, QComboBox, QSlider, QTextEdit, QCheckBox,
    QFrame, QLineEdit
)
from PySide6.QtGui import QPainter, QColor, QRadialGradient, QFont, QIcon

from elora.brain import query_elora
from elora.actions import execute_agent_task, open_browser_url
from elora.news import get_news_summary, get_spoken_news_summary, open_article
from elora.voice import speak_text
from elora.config import load_config, save_config, set_config_override

logger = logging.getLogger("elora.hud")

VOICE_INPUT_PATH = "/tmp/elora_voice_input.raw"

# Supported local Kokoro voice maps
KOKORO_VOICES = {
    "af_heart": "Heart (US Female - Default)",
    "af_bella": "Bella (US Female)",
    "af_sarah": "Sarah (US Female)",
    "am_adam": "Adam (US Male)",
    "am_michael": "Michael (US Male)",
    "bf_emma": "Emma (UK Female)",
    "bm_george": "George (UK Male)"
}

# Supported STT engines
STT_ENGINES = {
    "gemini": "Gemini Cloud STT (Active)"
}



class DaemonSTTThread(QThread):
    """Background thread delegating live audio recording and Vosk STT to the daemon."""
    status_changed = Signal(str, str)  # status, text payload

    def __init__(self):
        super().__init__()
        self.client = None

    def run(self):
        from elora.daemon_client import EloraDaemonClient
        self.client = EloraDaemonClient()

        def callback(res: dict):
            status = res.get("status")
            if status == "recording":
                self.status_changed.emit("recording", "")
            elif status == "partial_stream":
                self.status_changed.emit("partial_stream", res.get("text", ""))
            elif status == "partial":
                self.status_changed.emit("partial", res.get("text", ""))
            elif status == "final":
                self.status_changed.emit("final", res.get("text", ""))
            elif status == "error":
                self.status_changed.emit("error", res.get("message", ""))

        self.client.start_voice_listening(callback)

    def stop(self):
        if self.client:
            self.client.stop_voice_listening()


class DaemonQueryThread(QThread):
    """Background thread delegating LLM brain querying to the daemon."""
    status_changed = Signal(str)
    query_finished = Signal(dict)

    def __init__(self, prompt: str, save_history: bool = True):
        super().__init__()
        self.prompt = prompt
        self.save_history = save_history

    def run(self):
        from elora.daemon_client import EloraDaemonClient
        client = EloraDaemonClient()
        if not client.connect():
            self.query_finished.emit({
                "action": "reply",
                "arguments": {"message": "Elora background daemon is not running."}
            })
            return

        try:
            # Query the brain via Unix socket line protocol
            payload = json.dumps({"cmd": "query_brain", "text": self.prompt, "save_history": self.save_history}) + "\n"
            client.sock.sendall(payload.encode("utf-8"))

            f = client.sock.makefile("r", encoding="utf-8")
            while True:
                line = f.readline()
                if not line:
                    break
                res = json.loads(line.strip())
                status = res.get("status")
                if status == "brain_status":
                    self.status_changed.emit(res.get("text", ""))
                elif status == "response":
                    self.query_finished.emit(res.get("result", {}))
                    break
                elif status == "error":
                    self.query_finished.emit({
                        "action": "reply",
                        "arguments": {"message": f"Daemon error: {res.get('message')}"}
                    })
                    break
        except Exception as e:
            logger.error("IPC query thread error: %s", e)
            self.query_finished.emit({
                "action": "reply",
                "arguments": {"message": f"Daemon communication failed: {e}"}
            })
        finally:
            client.close()


class OrbWidget(QWidget):
    """Vector canvas rendering the pulsing/glowing AI Core."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(180, 180)
        self.state = "idle"
        self.phase = 0.0

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick_animation)
        self.timer.start(16)

    def set_state(self, state: str):
        self.state = state
        self.update()

    def tick_animation(self):
        if self.state == "listening":
            self.phase += 0.2
        elif self.state == "thinking":
            self.phase += 0.3
        elif self.state == "speaking":
            self.phase += 0.15
        else:
            self.phase += 0.045
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        width = self.width()
        height = self.height()
        center_x = width / 2.0
        center_y = height / 2.0
        sine_val = math.sin(self.phase)
        
        if self.state == "listening":
            radius = 36 + sine_val * 4.0
            glow_color = QColor(236, 72, 153, 160)
            center_color = QColor(244, 63, 94, 255)
        elif self.state == "thinking":
            radius = 34 + abs(sine_val) * 2.0
            glow_color = QColor(79, 70, 229, 180)
            center_color = QColor(99, 102, 241, 255)
        elif self.state == "speaking":
            radius = 36 + sine_val * 6.0
            glow_color = QColor(16, 185, 129, 160)
            center_color = QColor(52, 211, 153, 255)
        else:
            radius = 30 + sine_val * 1.5
            glow_color = QColor(99, 102, 241, 60)
            center_color = QColor(129, 140, 248, 180)

        glow_grad = QRadialGradient(center_x, center_y, radius * 1.6)
        glow_grad.setColorAt(0.0, glow_color)
        glow_grad.setColorAt(0.7, QColor(glow_color.red(), glow_color.green(), glow_color.blue(), 25))
        glow_grad.setColorAt(1.0, QColor(0, 0, 0, 0))

        painter.setBrush(glow_grad)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(center_x - radius * 1.6, center_y - radius * 1.6, radius * 3.2, radius * 3.2)

        core_grad = QRadialGradient(center_x, center_y, radius)
        core_grad.setColorAt(0.0, center_color)
        core_grad.setColorAt(1.0, QColor(max(0, center_color.red() - 40), max(0, center_color.green() - 40), max(0, center_color.blue() - 40), 255))

        painter.setBrush(core_grad)
        painter.drawEllipse(center_x - radius, center_y - radius, radius * 2.0, radius * 2.0)


class EloraModalOverlay(QWidget):
    """A floating modal overlay that displays the collapsible sidebar panels in the center."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ModalOverlay")
        self.setStyleSheet("""
            QWidget#ModalOverlay {
                background-color: rgba(0, 0, 0, 0.45);
            }
        """)
        # Centered layout
        self.overlay_layout = QHBoxLayout(self)
        self.overlay_layout.setContentsMargins(0, 0, 0, 0)
        self.overlay_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Center card container
        self.modal_card = QWidget(self)
        self.modal_card.setObjectName("ModalCard")
        self.modal_card.setFixedWidth(420)
        self.modal_card.setFixedHeight(650)
        self.modal_card.setStyleSheet("""
            QWidget#ModalCard {
                background-color: rgba(15, 17, 26, 0.85);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 16px;
            }
        """)
        self.card_layout = QVBoxLayout(self.modal_card)
        self.card_layout.setContentsMargins(20, 20, 20, 20)
        self.card_layout.setSpacing(15)
        
        self.overlay_layout.addWidget(self.modal_card)
        self.close_callback = None

    def mousePressEvent(self, event):
        # Close modal if clicking outside the modal_card
        pos = event.position() if hasattr(event, 'position') else event.localPos()
        if not self.modal_card.geometry().contains(pos.toPoint()):
            if self.close_callback:
                self.close_callback()
            event.accept()
        else:
            super().mousePressEvent(event)


class EloraHUD(QWidget):
    """Centralized HUD interface styled with modern dark obsidian cards."""
    def __init__(self):
        super().__init__()
        self.session_history = []
        self.record_process: Optional[subprocess.Popen] = None
        self.is_recording = False
        self.active_sidebar_tab = -1  # -1 = closed

        self.setObjectName("EloraHUD")
        self.setWindowTitle("Elora HUD")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        
        # Load user configuration
        self.config = load_config()

        # Stylesheet defining premium colors and round corners
        self.setStyleSheet("""
            QWidget#EloraHUD {
                background: transparent;
            }
            QWidget#CentralCard {
                background-color: transparent;
                border: none;
            }
            QLabel {
                color: #FFFFFF;
                font-family: 'Inter', sans-serif;
            }
            QTextBrowser, QTextEdit {
                background-color: rgba(15, 17, 26, 0.5);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 10px;
                color: #E5E7EB;
                font-family: 'Inter', sans-serif;
                font-size: 13px;
                padding: 12px;
            }
            QListWidget {
                background-color: rgba(15, 17, 26, 0.5);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 10px;
                color: #E5E7EB;
                font-family: 'Inter', sans-serif;
                font-size: 12px;
                padding: 8px;
            }
            QListWidget::item {
                border-bottom: 1px solid rgba(255, 255, 255, 0.03);
                padding: 8px;
            }
            QPushButton {
                background-color: rgba(15, 17, 26, 0.5);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 8px;
                color: #F3F4F6;
                font-family: 'Inter', sans-serif;
                font-size: 12px;
                font-weight: bold;
                padding: 10px 18px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.15);
                border-color: rgba(255, 255, 255, 0.2);
            }
            QPushButton:pressed {
                background-color: rgba(255, 255, 255, 0.25);
            }
            QComboBox {
                background-color: rgba(15, 17, 26, 0.5);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                color: #FFFFFF;
                padding: 8px;
                font-family: 'Inter', sans-serif;
            }
            QComboBox QAbstractItemView {
                background-color: #171822;
                border: 1px solid rgba(255, 255, 255, 0.12);
                color: #FFFFFF;
                selection-background-color: rgba(255, 255, 255, 0.08);
            }
            QLineEdit {
                background-color: rgba(15, 17, 26, 0.5);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 8px;
                color: #FFFFFF;
                font-family: 'Inter', sans-serif;
                font-size: 12px;
                padding: 8px 12px;
            }
            QLineEdit:focus {
                border-color: rgba(129, 140, 248, 0.6);
                background-color: rgba(15, 17, 26, 0.7);
            }
            QCheckBox {
                color: #E5E7EB;
                font-family: 'Inter', sans-serif;
                font-size: 12px;
                spacing: 8px;
            }
        """)

        # Main Layout
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(40, 40, 40, 40)

        self.central_card = QWidget(self)
        self.central_card.setObjectName("CentralCard")
        self.main_layout.addWidget(self.central_card)

        # Drop shadow effect disabled on the main card as it is fully transparent now.
        # We will apply a drop shadow to the modal overlay card instead.

        self.card_layout = QHBoxLayout(self.central_card)
        self.card_layout.setContentsMargins(20, 20, 20, 20)
        self.card_layout.setSpacing(15)

        # =====================================================================
        # LEFT/MAIN DASHBOARD
        # =====================================================================
        self.left_dashboard = QWidget(self)
        self.left_layout = QVBoxLayout(self.left_dashboard)
        self.left_layout.setContentsMargins(0, 0, 0, 0)
        self.left_layout.setSpacing(12)

        # Top Bar: Title & Navigation Action Buttons
        self.top_bar = QWidget(self)
        self.top_bar_layout = QHBoxLayout(self.top_bar)
        self.top_bar_layout.setContentsMargins(0, 0, 0, 0)

        self.title_layout = QVBoxLayout()
        self.title_label = QLabel("ELORA", self)
        self.title_label.setStyleSheet("font-size: 22px; font-weight: 900; letter-spacing: 1px; color: #FFFFFF;")
        self.subtitle_label = QLabel("Linux Desktop OS Orchestrator", self)
        self.subtitle_label.setStyleSheet("font-size: 11px; color: rgba(255, 255, 255, 0.4);")
        self.title_layout.addWidget(self.title_label)
        self.title_layout.addWidget(self.subtitle_label)
        self.top_bar_layout.addLayout(self.title_layout)
        
        self.top_bar_layout.addStretch()

        # Custom Control Tool Buttons (matching the mockup shapes)
        self.btn_tools = QPushButton("Tools", self)
        self.btn_tools.setIcon(QIcon.fromTheme("system-run"))
        self.btn_tools.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        
        self.btn_chat = QPushButton("Chat", self)
        self.btn_chat.setIcon(QIcon.fromTheme("chat"))
        self.btn_chat.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        
        self.btn_settings = QPushButton("Settings", self)
        self.btn_settings.setIcon(QIcon.fromTheme("preferences-system"))
        self.btn_settings.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        
        self.btn_browser = QPushButton("Browser", self)
        self.btn_browser.setIcon(QIcon.fromTheme("web-browser"))
        self.btn_browser.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        
        self.btn_news = QPushButton("News", self)
        self.btn_news.setIcon(QIcon.fromTheme("news"))
        self.btn_news.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        
        self.btn_tools.clicked.connect(lambda: self.toggle_sidebar(1))
        self.btn_chat.clicked.connect(lambda: self.toggle_sidebar(0))
        self.btn_settings.clicked.connect(lambda: self.toggle_sidebar(2))
        self.btn_browser.clicked.connect(lambda: self.toggle_sidebar(3))
        self.btn_news.clicked.connect(lambda: self.toggle_sidebar(4))
 
        self.top_bar_layout.addWidget(self.btn_tools)
        self.top_bar_layout.addWidget(self.btn_chat)
        self.top_bar_layout.addWidget(self.btn_news)
        self.top_bar_layout.addWidget(self.btn_settings)
        self.top_bar_layout.addWidget(self.btn_browser)
 
        self.left_layout.addWidget(self.top_bar)

        # Telemetry Row (System stats: CPU / RAM / TMUX)
        self.telemetry_frame = QFrame(self)
        self.telemetry_frame.setStyleSheet("background-color: rgba(15, 17, 26, 0.5); border: 1px solid rgba(255, 255, 255, 0.12); border-radius: 8px;")
        self.telemetry_layout = QHBoxLayout(self.telemetry_frame)
        self.telemetry_layout.setContentsMargins(16, 12, 16, 12)
        
        self.lbl_cpu = QLabel("CPU Load: --", self)
        self.lbl_cpu.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 11px; color: rgba(255,255,255,0.7);")
        self.lbl_ram = QLabel("RAM: --", self)
        self.lbl_ram.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 11px; color: rgba(255,255,255,0.7);")
        self.lbl_tasks = QLabel("Background Tasks: --", self)
        self.lbl_tasks.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 11px; color: rgba(255,255,255,0.7);")

        self.telemetry_layout.addWidget(self.lbl_cpu)
        self.telemetry_layout.addWidget(self.lbl_ram)
        self.telemetry_layout.addWidget(self.lbl_tasks)
        
        # Center telemetry frame horizontally
        self.left_layout.addWidget(self.telemetry_frame, alignment=Qt.AlignmentFlag.AlignCenter)

        # Vertical stretch above Orb
        self.left_layout.addStretch(1)

        # Center AI Orb section
        self.orb_section = QWidget(self)
        self.orb_layout = QVBoxLayout(self.orb_section)
        self.orb = OrbWidget(self)
        self.orb_layout.addWidget(self.orb, alignment=Qt.AlignmentFlag.AlignCenter)
        
        # State indicator Label
        self.state_label = QLabel("[ HOLD ALT TO TALK ]", self)
        self.state_label.setStyleSheet("color: rgba(255, 255, 255, 0.4); font-family: 'JetBrains Mono'; font-size: 11px; font-weight: bold; letter-spacing: 2px;")
        self.orb_layout.addWidget(self.state_label, alignment=Qt.AlignmentFlag.AlignCenter)
        self.left_layout.addWidget(self.orb_section, alignment=Qt.AlignmentFlag.AlignCenter)

        # Vertical stretch below Orb
        self.left_layout.addStretch(1)

        # Bottom section: Tip Label
        self.ptt_tip = QLabel("[ Hold ALT to Speak ]   •   [ Press ESC to Exit ]", self)
        self.ptt_tip.setStyleSheet("color: rgba(255, 255, 255, 0.3); font-family: 'JetBrains Mono'; font-size: 9px; font-weight: bold;")
        self.left_layout.addWidget(self.ptt_tip, alignment=Qt.AlignmentFlag.AlignCenter)

        self.card_layout.addWidget(self.left_dashboard)

        # =====================================================================
        # COLLAPSIBLE SIDEBAR PANEL (Right)
        # =====================================================================
        self.sidebar_widget = QWidget(self)
        self.sidebar_widget.setObjectName("SidebarWidget")
        self.sidebar_widget.setFixedWidth(380)
        self.sidebar_layout = QVBoxLayout(self.sidebar_widget)
        self.sidebar_layout.setContentsMargins(0, 0, 0, 0)
        self.sidebar_layout.setSpacing(10)

        # Sidebar Title & Close Action
        self.sidebar_header = QWidget(self)
        self.sidebar_header_layout = QHBoxLayout(self.sidebar_header)
        self.sidebar_header_layout.setContentsMargins(0, 0, 0, 0)
        
        self.sidebar_title = QLabel("Conversation", self)
        self.sidebar_title.setStyleSheet("font-size: 14px; font-weight: bold; color: #FFFFFF;")
        self.sidebar_header_layout.addWidget(self.sidebar_title)
        
        self.sidebar_header_layout.addStretch()
        
        self.btn_close_sidebar = QPushButton("✕", self)
        self.btn_close_sidebar.setStyleSheet("background: transparent; border: none; font-size: 14px; color: rgba(255,255,255,0.5);")
        self.btn_close_sidebar.setFixedWidth(30)
        self.btn_close_sidebar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_close_sidebar.clicked.connect(self.close_sidebar)
        self.sidebar_header_layout.addWidget(self.btn_close_sidebar)
        self.sidebar_layout.addWidget(self.sidebar_header)

        # Stacked widgets representing different tabs
        self.stacked_widget = QStackedWidget(self)
        
        # ---------------------------------------------------------------------
        # Tab Page 0: Conversation Chat log
        # ---------------------------------------------------------------------
        self.page_chat = QWidget(self)
        self.chat_layout = QVBoxLayout(self.page_chat)
        self.chat_layout.setContentsMargins(0, 0, 0, 0)
        self.console_output = QTextBrowser(self)
        self.console_output.append("<span style='color: #818CF8;'>Elora:</span> Centralized HUD ready. Hold <b>ALT</b> to talk.")
        self.chat_layout.addWidget(self.console_output)
        
        self.stacked_widget.addWidget(self.page_chat)

        # ---------------------------------------------------------------------
        # Tab Page 1: Tools Skill Toggles
        # ---------------------------------------------------------------------
        self.page_tools = QWidget(self)
        self.tools_layout = QVBoxLayout(self.page_tools)
        self.tools_layout.setContentsMargins(5, 5, 5, 5)
        self.tools_layout.setSpacing(15)

        self.tools_intro = QLabel("Let the assistant act during the conversation. Changes apply live.", self)
        self.tools_intro.setStyleSheet("color: rgba(255,255,255,0.6); font-size: 12px;")
        self.tools_intro.setWordWrap(True)
        self.tools_layout.addWidget(self.tools_intro)

        # Skill active checkboxes
        skills_cfg = self.config.get("skills", {"web_search": True, "web_scrape": True, "command_run": True})
        
        self.chk_web_search = QCheckBox("Web search (DuckDuckGo)", self)
        self.chk_web_search.setChecked(skills_cfg.get("web_search", True))
        self.chk_web_search.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.chk_web_search.clicked.connect(self.save_tools_config)
        self.tools_layout.addWidget(self.chk_web_search)
        
        lbl_search_desc = QLabel("Let Elora browse the web to fetch answers or read news dynamically.", self)
        lbl_search_desc.setStyleSheet("color: rgba(255,255,255,0.4); font-size: 11px; padding-left: 24px;")
        lbl_search_desc.setWordWrap(True)
        self.tools_layout.addWidget(lbl_search_desc)

        self.chk_web_scrape = QCheckBox("Web scrape (BeautifulSoup)", self)
        self.chk_web_scrape.setChecked(skills_cfg.get("web_scrape", True))
        self.chk_web_scrape.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.chk_web_scrape.clicked.connect(self.save_tools_config)
        self.tools_layout.addWidget(self.chk_web_scrape)

        lbl_scrape_desc = QLabel("Let Elora scrape and read plain text documentation from direct URL links.", self)
        lbl_scrape_desc.setStyleSheet("color: rgba(255,255,255,0.4); font-size: 11px; padding-left: 24px;")
        lbl_scrape_desc.setWordWrap(True)
        self.tools_layout.addWidget(lbl_scrape_desc)

        self.chk_command_run = QCheckBox("Shell command executor", self)
        self.chk_command_run.setChecked(skills_cfg.get("command_run", True))
        self.chk_command_run.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.chk_command_run.clicked.connect(self.save_tools_config)
        self.tools_layout.addWidget(self.chk_command_run)

        lbl_cmd_desc = QLabel("Let Elora run terminal status inspection commands locally (free -h, df -h, uname -a).", self)
        lbl_cmd_desc.setStyleSheet("color: rgba(255,255,255,0.4); font-size: 11px; padding-left: 24px;")
        lbl_cmd_desc.setWordWrap(True)
        self.tools_layout.addWidget(lbl_cmd_desc)

        self.tools_layout.addStretch()
        self.stacked_widget.addWidget(self.page_tools)

        # ---------------------------------------------------------------------
        # Tab Page 2: Settings Panel
        # ---------------------------------------------------------------------
        self.page_settings = QWidget(self)
        self.settings_layout = QVBoxLayout(self.page_settings)
        self.settings_layout.setContentsMargins(5, 5, 5, 5)
        self.settings_layout.setSpacing(12)

        # TTS voice selection dropdown
        lbl_voice = QLabel("VOICE", self)
        lbl_voice.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px; font-weight: bold; color: rgba(255,255,255,0.5);")
        self.settings_layout.addWidget(lbl_voice)
        self.cmb_voice = QComboBox(self)
        self.cmb_voice.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for code, label in KOKORO_VOICES.items():
            self.cmb_voice.addItem(label, code)
        # Select active voice from config
        voice_cfg = self.config.get("voice", {})
        active_voice = voice_cfg.get("voice_name", "af_heart")
        if active_voice not in KOKORO_VOICES:
            active_voice = "af_heart"
        idx = self.cmb_voice.findData(active_voice)
        if idx != -1:
            self.cmb_voice.setCurrentIndex(idx)
        self.settings_layout.addWidget(self.cmb_voice)

        # STT model selection dropdown
        lbl_stt_model = QLabel("SPEECH RECOGNITION (STT) ENGINE", self)
        lbl_stt_model.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px; font-weight: bold; color: rgba(255,255,255,0.5);")
        self.settings_layout.addWidget(lbl_stt_model)
        self.cmb_stt_model = QComboBox(self)
        self.cmb_stt_model.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for code, label in STT_ENGINES.items():
            self.cmb_stt_model.addItem(label, code)
        # Select active STT model from config
        stt_cfg = self.config.get("stt", {})
        active_stt_model = stt_cfg.get("model_name", "gemini")
        idx_stt = self.cmb_stt_model.findData(active_stt_model)
        if idx_stt != -1:
            self.cmb_stt_model.setCurrentIndex(idx_stt)
        self.settings_layout.addWidget(self.cmb_stt_model)

        # TTS speech speed slider
        self.lbl_speed_val = QLabel("SPEED: 1.0x", self)
        self.lbl_speed_val.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px; font-weight: bold; color: rgba(255,255,255,0.5);")
        self.settings_layout.addWidget(self.lbl_speed_val)
        
        self.sld_speed = QSlider(Qt.Orientation.Horizontal, self)
        self.sld_speed.setRange(50, 200)  # Map 0.5x to 2.0x
        self.sld_speed.setValue(int(voice_cfg.get("speed", 1.0) * 100))
        self.sld_speed.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.sld_speed.valueChanged.connect(self.on_speed_changed)
        self.settings_layout.addWidget(self.sld_speed)
        
        # Trigger speed update label
        self.on_speed_changed(self.sld_speed.value())

        # AI Personality selection
        lbl_personality = QLabel("AI PERSONALITY", self)
        lbl_personality.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px; font-weight: bold; color: rgba(255,255,255,0.5);")
        self.settings_layout.addWidget(lbl_personality)
        
        self.cmb_personality = QComboBox(self)
        self.cmb_personality.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.cmb_personality.addItem("Jarvis (Default)", "default")
        self.cmb_personality.addItem("Funny", "funny")
        self.cmb_personality.addItem("Direct", "direct")
        self.cmb_personality.addItem("Polite", "polite")
        self.cmb_personality.addItem("Respectful", "respectful")
        self.cmb_personality.addItem("Other...", "other")
        
        # Load personality from config with backward compatibility for legacy custom_instructions
        active_personality = self.config.get("personality")
        if not active_personality:
            legacy_instr = self.config.get("custom_instructions")
            from elora.brain import DEFAULT_CUSTOM_INSTRUCTION
            if legacy_instr and legacy_instr.strip() != DEFAULT_CUSTOM_INSTRUCTION.strip():
                active_personality = "other"
            else:
                active_personality = "default"
                
        idx_p = self.cmb_personality.findData(active_personality)
        if idx_p != -1:
            self.cmb_personality.setCurrentIndex(idx_p)
        self.settings_layout.addWidget(self.cmb_personality)
        
        # Custom/Other personality description field
        self.lbl_custom_personality = QLabel("CUSTOM PERSONALITY TYPE", self)
        self.lbl_custom_personality.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px; font-weight: bold; color: rgba(255,255,255,0.5);")
        self.settings_layout.addWidget(self.lbl_custom_personality)
        
        self.txt_custom_personality = QLineEdit(self)
        self.txt_custom_personality.setPlaceholderText("Enter custom personality (e.g. sarcastic pirate, helpful friend)...")
        
        # Load custom personality description with backward compatibility for legacy custom_instructions
        current_custom = self.config.get("custom_personality")
        if current_custom is None:
            legacy_instr = self.config.get("custom_instructions")
            from elora.brain import DEFAULT_CUSTOM_INSTRUCTION
            if legacy_instr and legacy_instr.strip() != DEFAULT_CUSTOM_INSTRUCTION.strip():
                current_custom = legacy_instr.strip()
            else:
                current_custom = ""
        self.txt_custom_personality.setText(current_custom)
        self.settings_layout.addWidget(self.txt_custom_personality)
        
        # Gemini API Key Field
        lbl_api_key = QLabel("GEMINI API KEY", self)
        lbl_api_key.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px; font-weight: bold; color: rgba(255,255,255,0.5);")
        self.settings_layout.addWidget(lbl_api_key)
        
        self.txt_api_key = QLineEdit(self)
        self.txt_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_api_key.setPlaceholderText("Enter your Gemini API key...")
        self.txt_api_key.setText(self.config.get("gemini_api_key", ""))
        self.settings_layout.addWidget(self.txt_api_key)
        
        # Connect change event to toggle visibility
        self.cmb_personality.currentIndexChanged.connect(self.on_personality_changed)
        
        # Call toggle logic once to initialize visibility of custom inputs
        self.on_personality_changed()

        # Restart conversation button
        self.btn_reset_conv = QPushButton("Restart Conversation", self)
        self.btn_reset_conv.setIcon(QIcon.fromTheme("view-refresh"))
        self.btn_reset_conv.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_reset_conv.setStyleSheet("background-color: rgba(239, 68, 68, 0.15); border-color: rgba(239, 68, 68, 0.3);")
        self.btn_reset_conv.clicked.connect(self.reset_conversation)
        self.settings_layout.addWidget(self.btn_reset_conv)

        # Save settings action
        self.btn_save_settings = QPushButton("Save Settings", self)
        self.btn_save_settings.setIcon(QIcon.fromTheme("document-save"))
        self.btn_save_settings.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_save_settings.setStyleSheet("background-color: rgba(16, 185, 129, 0.2); border-color: rgba(16, 185, 129, 0.4);")
        self.btn_save_settings.clicked.connect(self.save_settings)
        self.settings_layout.addWidget(self.btn_save_settings)

        self.settings_layout.addStretch()
        self.stacked_widget.addWidget(self.page_settings)

        # ---------------------------------------------------------------------
        # Tab Page 3: Browser Live Preview & Automation Abort
        # ---------------------------------------------------------------------
        self.page_browser = QWidget(self)
        self.browser_layout = QVBoxLayout(self.page_browser)
        self.browser_layout.setContentsMargins(5, 5, 5, 5)
        self.browser_layout.setSpacing(10)
        
        lbl_browser_title = QLabel("BRAVE AUTOMATION PREVIEW", self)
        lbl_browser_title.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px; font-weight: bold; color: rgba(255,255,255,0.5);")
        self.browser_layout.addWidget(lbl_browser_title)
        
        self.lbl_screenshot = QLabel(self)
        self.lbl_screenshot.setFixedSize(340, 240)
        self.lbl_screenshot.setStyleSheet("background-color: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.1); border-radius: 8px;")
        self.lbl_screenshot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.browser_layout.addWidget(self.lbl_screenshot)
        
        self.lbl_browser_status = QLabel("CDP Connection: Active", self)
        self.lbl_browser_status.setStyleSheet("font-size: 11px; color: rgba(255,255,255,0.6);")
        self.browser_layout.addWidget(self.lbl_browser_status)
        
        self.btn_refresh_screenshot = QPushButton("Refresh View", self)
        self.btn_refresh_screenshot.setIcon(QIcon.fromTheme("view-refresh"))
        self.btn_refresh_screenshot.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_refresh_screenshot.clicked.connect(self.update_browser_screenshot)
        self.browser_layout.addWidget(self.btn_refresh_screenshot)
        
        self.btn_abort_automation = QPushButton("ABORT AUTOMATION (Ctrl+Alt+C)", self)
        self.btn_abort_automation.setIcon(QIcon.fromTheme("process-stop"))
        self.btn_abort_automation.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_abort_automation.setStyleSheet("background-color: rgba(239, 68, 68, 0.35); border-color: rgba(239, 68, 68, 0.6); color: #EF4444;")
        
        def trigger_abort():
            from elora.os_control import on_abort_activated
            on_abort_activated()
            self.console_output.append("<br><span style='color: #EF4444;'>System: Automation aborted by user request.</span>")
            
        self.btn_abort_automation.clicked.connect(trigger_abort)
        self.browser_layout.addWidget(self.btn_abort_automation)
        
        lbl_abort_desc = QLabel("Pressing ABORT or the 'Ctrl+Alt+C' hotkey immediately terminates active mouse and keyboard automation sequences.", self)
        lbl_abort_desc.setStyleSheet("color: rgba(255, 255, 255, 0.4); font-size: 10px;")
        lbl_abort_desc.setWordWrap(True)
        self.browser_layout.addWidget(lbl_abort_desc)
        
        self.browser_layout.addStretch()
        self.stacked_widget.addWidget(self.page_browser)

        # ---------------------------------------------------------------------
        # Tab Page 4: RSS News / Telemetry
        # ---------------------------------------------------------------------
        self.page_news = QWidget(self)
        self.news_layout = QVBoxLayout(self.page_news)
        self.news_layout.setContentsMargins(5, 5, 5, 5)
        self.news_layout.setSpacing(10)
        
        lbl_news_title = QLabel("TELEMETRY: RECENT ARTICLES", self)
        lbl_news_title.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 11px; font-weight: bold; color: rgba(255,255,255,0.6);")
        self.news_layout.addWidget(lbl_news_title)
        
        self.news_list = QListWidget(self)
        self.news_list.itemClicked.connect(self.on_news_clicked)
        self.news_layout.addWidget(self.news_list)
        
        self.btn_refresh_news = QPushButton("Refresh Feeds", self)
        self.btn_refresh_news.setIcon(QIcon.fromTheme("view-refresh"))
        self.btn_refresh_news.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_refresh_news.clicked.connect(self.load_news_skimmer)
        self.news_layout.addWidget(self.btn_refresh_news)
        
        self.news_layout.addStretch()
        self.stacked_widget.addWidget(self.page_news)
        
        # Start a refresh timer to update screenshot every 2 seconds
        self.browser_refresh_timer = QTimer(self)
        self.browser_refresh_timer.timeout.connect(self.update_browser_screenshot)
        self.browser_refresh_timer.start(2000)

        self.sidebar_layout.addWidget(self.stacked_widget)
        
        # Create the modal overlay as a child of EloraHUD
        self.modal_overlay = EloraModalOverlay(self)
        self.modal_overlay.card_layout.addWidget(self.sidebar_widget)
        self.modal_overlay.close_callback = self.close_sidebar
        self.modal_overlay.hide()

        # Add drop shadow to the modal card inside the overlay
        modal_shadow = QGraphicsDropShadowEffect(self)
        modal_shadow.setBlurRadius(30)
        modal_shadow.setColor(QColor(0, 0, 0, 180))
        modal_shadow.setOffset(0, 10)
        self.modal_overlay.modal_card.setGraphicsEffect(modal_shadow)

        # Initially, keep sidebar closed
        self.sidebar_widget.hide()

        # Start maximized to preserve transparency/compositing on Linux window managers
        self.showMaximized()

        # Telemetry & Monitors Updates
        self.telemetry_timer = QTimer(self)
        self.telemetry_timer.timeout.connect(self.update_system_telemetry)
        self.telemetry_timer.start(1000)  # Refresh metrics every 1 second
        self.update_system_telemetry()



        # Launch dynamic startup greeting welcoming user
        QTimer.singleShot(800, self.trigger_startup_greeting)

        # PTT release timer to prevent speech cut-off
        # Why: Introduces a 450ms tail delay after releasing Space so the last spoken words are fully captured.
        self.ptt_release_timer = QTimer(self)
        self.ptt_release_timer.setSingleShot(True)
        self.ptt_release_timer.timeout.connect(self.stop_voice_recording)

        # Install global application event filter to intercept keyboard presses
        QApplication.instance().installEventFilter(self)

    def update_browser_screenshot(self):
        """Loads /tmp/elora_browser.png and displays it scaled inside the HUD browser tab."""
        from PySide6.QtGui import QPixmap
        import os
        screenshot_path = "/tmp/elora_browser.png"
        if os.path.exists(screenshot_path):
            pixmap = QPixmap(screenshot_path)
            scaled = pixmap.scaled(self.lbl_screenshot.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.lbl_screenshot.setPixmap(scaled)
            self.lbl_browser_status.setText("CDP Connection: Active (Screenshot updated)")
        else:
            self.lbl_screenshot.setText("No screenshot captured yet.\nElora will capture one when navigating.")
            self.lbl_browser_status.setText("CDP Connection: Standing by")

    def center_on_screen(self):
        pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "modal_overlay") and self.modal_overlay:
            self.modal_overlay.setGeometry(self.rect())

    # =====================================================================
    # Sidebar Collapse/Expansion Mechanics
    # =====================================================================
    def toggle_sidebar(self, tab_index: int):
        """Toggles the visibility of the action sidebar."""
        # Update title header text
        titles = ["Conversation", "Tools", "Settings", "Browser Preview", "News Telemetry"]
        self.sidebar_title.setText(titles[tab_index])
        
        if self.active_sidebar_tab == tab_index:
            self.close_sidebar()
        else:
            self.active_sidebar_tab = tab_index
            self.stacked_widget.setCurrentIndex(tab_index)
            self.sidebar_widget.show()
            self.modal_overlay.setGeometry(self.rect())
            self.modal_overlay.show()
            self.modal_overlay.raise_()
            
            # Lazy load news feeds only when the News Telemetry tab is opened
            if tab_index == 4:
                self.news_list.clear()
                self.news_list.addItem("Loading news feeds...")
                QTimer.singleShot(100, self.load_news_skimmer)

    def close_sidebar(self):
        """Collapses the sidebar widget back to minimal HUD layout."""
        self.active_sidebar_tab = -1
        self.sidebar_widget.hide()
        self.modal_overlay.hide()

    # =====================================================================
    # Real-Time Telemetry Gatherers
    # =====================================================================
    def update_system_telemetry(self):
        """Gathers lightweight system telemetry without third party libraries."""
        # 1. RAM Usage
        ram_text = "RAM: Unknown"
        try:
            with open("/proc/meminfo", "r") as f:
                lines = f.readlines()
            mem_total = 0
            mem_avail = 0
            for line in lines:
                if line.startswith("MemTotal:"):
                    mem_total = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    mem_avail = int(line.split()[1])
            if mem_avail == 0:
                # Fallback to MemFree if Available is missing
                for line in lines:
                    if line.startswith("MemFree:"):
                        mem_avail = int(line.split()[1])
            used = mem_total - mem_avail
            ram_text = f"RAM: {used / (1024*1024):.1f}G / {mem_total / (1024*1024):.1f}G ({used*100/mem_total:.0f}%)"
        except Exception:
            pass
        self.lbl_ram.setText(ram_text)

        # 2. CPU Load Average
        cpu_text = "CPU Load: Unknown"
        try:
            with open("/proc/loadavg", "r") as f:
                load = f.read().split()
            cpu_text = f"CPU Load: {load[0]} {load[1]}"
        except Exception:
            pass
        self.lbl_cpu.setText(cpu_text)

        # 3. Active Background Tmux tasks count
        tasks_count = 0
        try:
            output = subprocess.check_output(["tmux", "list-sessions"], stderr=subprocess.DEVNULL).decode()
            tasks_count = len([line for line in output.strip().split("\n") if line])
        except Exception:
            pass
        self.lbl_tasks.setText(f"Active Tasks: {tasks_count}")

    # =====================================================================
    # Event and Button Click Signals
    # =====================================================================
    def on_speed_changed(self, value: int):
        self.lbl_speed_val.setText(f"SPEED: {value/100:.1f}x")

    def on_personality_changed(self):
        """Toggles visibility of the custom personality input fields when 'other' is selected."""
        is_other = self.cmb_personality.currentData() == "other"
        self.lbl_custom_personality.setVisible(is_other)
        self.txt_custom_personality.setVisible(is_other)

    def save_tools_config(self):
        """Saves current state of tool active checkboxes to configuration file."""
        updates = {
            "skills": {
                "web_search": self.chk_web_search.isChecked(),
                "web_scrape": self.chk_web_scrape.isChecked(),
                "command_run": self.chk_command_run.isChecked()
            }
        }
        save_config(updates)

    def save_settings(self):
        """Saves active voice, STT model, and personality configurations to disk."""
        selected_voice = self.cmb_voice.currentData()
        selected_speed = self.sld_speed.value() / 100.0
        selected_stt = self.cmb_stt_model.currentData()
        selected_personality = self.cmb_personality.currentData()
        custom_personality = self.txt_custom_personality.text().strip()
        api_key = self.txt_api_key.text().strip()

        updates = {
            "voice": {
                "voice_name": selected_voice,
                "speed": selected_speed
            },
            "stt": {
                "model_name": selected_stt
            },
            "personality": selected_personality,
            "custom_personality": custom_personality,
            "gemini_api_key": api_key
        }
        
        # Save updates to ~/.config/elora/config.json
        save_config(updates)
        self.console_output.append("<br><span style='color: #10B981;'>System: Settings saved successfully.</span>")

    def reset_conversation(self):
        """Clears dialog context logs and starts conversation anew."""
        self.session_history.clear()
        self.console_output.clear()
        self.console_output.append("<span style='color: #818CF8;'>Elora:</span> Conversation restarted.")
        self.trigger_startup_greeting()

    # =====================================================================
    # Spacebar Hold-to-Talk Logic
    # =====================================================================
    def update_state_ui(self, state: str, text: str):
        """Updates the animated AI orb state and the colored status label."""
        self.state_label.setText(text)
        self.orb.set_state(state)
        
        if state == "listening":
            self.state_label.setStyleSheet("color: #EC4899; font-family: 'JetBrains Mono'; font-size: 13px; font-weight: bold; letter-spacing: 2px;")
        elif state == "thinking":
            self.state_label.setStyleSheet("color: #818CF8; font-family: 'JetBrains Mono'; font-size: 13px; font-weight: bold; letter-spacing: 2px;")
        elif state == "speaking":
            self.state_label.setStyleSheet("color: #10B981; font-family: 'JetBrains Mono'; font-size: 13px; font-weight: bold; letter-spacing: 2px;")
        else:
            self.state_label.setStyleSheet("color: rgba(255, 255, 255, 0.4); font-family: 'JetBrains Mono'; font-size: 11px; font-weight: bold; letter-spacing: 2px;")

    def eventFilter(self, watched, event):
        # Intercept KeyPress and KeyRelease events globally in this application
        if event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Alt, Qt.Key.Key_AltGr):
                if not event.isAutoRepeat():
                    # If user re-presses Alt during the 450ms tail delay, cancel the stop timer.
                    if self.ptt_release_timer.isActive():
                        self.ptt_release_timer.stop()
                    else:
                        self.start_voice_recording()
                return True  # Consume the key event
            elif event.key() == Qt.Key.Key_Escape:
                if hasattr(self, "modal_overlay") and self.modal_overlay.isVisible():
                    self.close_sidebar()
                    return True
                if self.record_process:
                    self.record_process.terminate()
                    self.record_process.wait()
                self.close()
                return True
                
        elif event.type() == QEvent.Type.KeyRelease:
            if event.key() in (Qt.Key.Key_Alt, Qt.Key.Key_AltGr):
                if not event.isAutoRepeat():
                    # Delay calling stop_voice_recording by 450ms to prevent clipping the tail end of speech.
                    self.ptt_release_timer.start(450)
                return True
                
        return super().eventFilter(watched, event)

    def reset_conversation(self):
        """Clears dialog context logs and starts conversation anew."""
        from elora.daemon_client import EloraDaemonClient
        EloraDaemonClient().send_cmd({"cmd": "reset_history"})
        self.session_history.clear()
        self.console_output.clear()
        self.console_output.append("<span style='color: #818CF8;'>Elora:</span> Conversation restarted.")
        self.trigger_startup_greeting()

    def start_voice_recording(self):
        if self.is_recording:
            return
        self.is_recording = True
        self.update_state_ui("listening", "● LISTENING...")
        self.console_output.append("<br><span style='color: #EC4899;'>System:</span> Recording...")

        self.stt_thread = DaemonSTTThread()
        self.stt_thread.status_changed.connect(self.handle_stt_status)
        self.stt_thread.start()

    @Slot(str, str)
    def handle_stt_status(self, status: str, text: str):
        if status == "recording":
            pass
        elif status == "partial_stream":
            self.update_state_ui("listening", f"● LISTENING: {text}...")
        elif status == "final":
            self.handle_transcription(text)
        elif status == "error":
            self.console_output.append(f"<span style='color: #EF4444;'>Error:</span> {text}")
            self.reset_to_idle()

    def stop_voice_recording(self):
        if not self.is_recording:
            return
        self.is_recording = False
        self.update_state_ui("thinking", "TRANSCRIBING...")
        if hasattr(self, "stt_thread") and self.stt_thread:
            self.stt_thread.stop()
            self.stt_thread.wait()
            self.stt_thread = None

    def handle_transcription(self, text: str):
        if not text:
            self.console_output.append("<span style='color: rgba(255,255,255,0.45);'>System: No speech detected.</span>")
            self.reset_to_idle()
            return

        self.reset_to_idle()
        self.console_output.append(f"<span style='color: #EC4899;'>System:</span> Transcribed: \"{text}\"")
        self.send_query(text)

    def send_query(self, text: str):
        text = text.strip()
        if not text:
            return

        self.console_output.append(f"<span style='color: #10B981;'>You:</span> {text}")
        self.session_history.append({"role": "user", "content": text})
        if len(self.session_history) > 20:
            self.session_history.pop(0)

        self.update_state_ui("thinking", "THINKING...")

        self.query_thread = DaemonQueryThread(text)
        self.query_thread.status_changed.connect(self.handle_status_change)
        self.query_thread.query_finished.connect(self.handle_brain_response)
        self.query_thread.start()

    @Slot(str)
    def handle_status_change(self, status_text: str):
        self.console_output.append(f"<span style='color: rgba(255, 255, 255, 0.45);'>System: {status_text}</span>")

    @Slot(dict)
    def handle_brain_response(self, result: dict):
        action = result.get("action")
        args = result.get("arguments", {})
        self.reset_to_idle()

        if action == "reply":
            msg = args.get("message", "")
            self.session_history.append({"role": "assistant", "content": msg})
            if len(self.session_history) > 20:
                self.session_history.pop(0)

            self.console_output.append(f"<span style='color: #818CF8;'>Elora:</span> {msg}")
            self.update_state_ui("speaking", "SPEAKING...")
            # Speech is handled by the daemon for reply actions; reset after a moment
            QTimer.singleShot(1500, self.reset_to_idle)

        elif action == "news_fetch":
            mode = args.get("mode", "skim")
            if mode == "skim":
                self.console_output.append("<span style='color: #818CF8;'>Elora:</span> Fetching top headlines...")
                # Display the full Markdown summary in the console (with titles, no raw links)
                summary = get_news_summary()
                self.console_output.append(f"<pre style='color: #D1D5DB;'>{summary}</pre>")
                self.load_news_skimmer()
                # Speak a conversational titles-only version via daemon TTS
                # (daemon already called speak_text for news_fetch, so no second call needed)
                self.update_state_ui("speaking", "SPEAKING...")
                QTimer.singleShot(500, self.reset_to_idle)

            elif mode == "deep_dive":
                idx = args.get("index")
                if idx is not None:
                    self.console_output.append(f"<span style='color: #818CF8;'>Elora:</span> Opening article {idx} in Brave...")
                    # Browser is opened by the daemon; confirm in UI
                    self.update_state_ui("speaking", "SPEAKING...")
                    QTimer.singleShot(800, self.reset_to_idle)
                else:
                    self.console_output.append("<span style='color: #EF4444;'>System: Article index missing for deep dive.</span>")

        elif action == "browser":
            url = args.get("url", "")
            if url:
                from urllib.parse import urlparse
                domain = urlparse(url).netloc or url
                self.console_output.append(f"<span style='color: #818CF8;'>Elora:</span> Opening {domain} in Brave...")
                # Browser open is handled by daemon; show speaking state briefly
                self.update_state_ui("speaking", "SPEAKING...")
                QTimer.singleShot(800, self.reset_to_idle)
            else:
                self.console_output.append("<span style='color: #EF4444;'>System: No URL provided.</span>")

        elif action == "antigravity":
            task_prompt = args.get("prompt", "")
            if task_prompt:
                message = args.get("message", "")
                if not message:
                    if len(task_prompt) < 60:
                        message = f"Okay, starting the task: {task_prompt}. I will let you know when it is finished."
                    else:
                        message = "I am launching the background agent to start the task. I will let you know once it is complete."
                
                self.session_history.append({"role": "assistant", "content": message})
                if len(self.session_history) > 20:
                    self.session_history.pop(0)
                
                self.console_output.append(f"<span style='color: #818CF8;'>Elora:</span> {message}")
                self.update_state_ui("speaking", "SPEAKING...")
                QTimer.singleShot(1500, self.reset_to_idle)
                
                session = result.get("session")
                if session:
                    self.console_output.append(f"<span style='color: #10B981;'>System: Task spawned successfully in tmux session '{session}'</span>")
                else:
                    self.console_output.append("<span style='color: #EF4444;'>System: Failed to spawn background task.</span>")

        elif action == "memory_focus":
            # Show a focus indicator in the console; actual focus state lives in daemon
            query = args.get("query", "")
            msg = args.get("message", f"Focusing on \"{query}\" now.")
            self.console_output.append(
                f"<span style='color: #2DD4BF;'>🧠 Focus:</span> "
                f"<span style='color: #D1D5DB;'>{msg}</span>"
            )
            self.update_state_ui("speaking", "SPEAKING...")
            QTimer.singleShot(1000, self.reset_to_idle)

        elif action == "reply":
            msg = args.get("message", "")
            self.session_history.append({"role": "assistant", "content": msg})
            if len(self.session_history) > 20:
                self.session_history.pop(0)
            self.console_output.append(f"<span style='color: #818CF8;'>Elora:</span> {msg}")
            self.update_state_ui("speaking", "SPEAKING...")
            QTimer.singleShot(1500, self.reset_to_idle)

    def reset_to_idle(self):
        self.update_state_ui("idle", "[ HOLD ALT TO TALK ]")

    def trigger_startup_greeting(self):
        # Sync with daemon conversation history if it exists
        from elora.daemon_client import EloraDaemonClient
        client = EloraDaemonClient()
        history = []
        try:
            res = client.send_cmd({"cmd": "get_history"})
            if res.get("status") == "history":
                history = res.get("history", [])
        except Exception:
            pass

        if history:
            self.session_history = history
            self.console_output.clear()
            
            for msg in history:
                role = msg.get("role")
                content = msg.get("content", "")
                if role == "user":
                    self.console_output.append(f"<span style='color: #10B981;'>You:</span> {content}")
                elif role == "assistant":
                    try:
                        payload = json.loads(content)
                        action_type = payload.get("action")
                        args_type = payload.get("arguments", {})
                        if action_type == "reply":
                            self.console_output.append(f"<span style='color: #818CF8;'>Elora:</span> {args_type.get('message', '')}")
                    except Exception:
                        self.console_output.append(f"<span style='color: #818CF8;'>Elora:</span> {content}")

        # Check if we have the user's name stored in memory
        user_name = "boss"
        try:
            from elora.memory import is_memory_available, search_memory
            avail, _ = is_memory_available()
            if avail:
                results = search_memory("my name is", top_k=1, threshold=0.5)
                if not results:
                    results = search_memory("call me", top_k=1, threshold=0.5)
                if results:
                    text = results[0]["text"]
                    text_lower = text.lower()
                    for pattern in ("name is", "call me"):
                        if pattern in text_lower:
                            extracted = text[text_lower.index(pattern) + len(pattern):].strip()
                            extracted = extracted.rstrip(".").rstrip("!").strip()
                            if extracted:
                                user_name = extracted
                                break
        except Exception as e:
            logger.error("Failed to recall user name from memory: %s", e)

        from datetime import datetime
        hour = datetime.now().hour
        if hour < 12:
            time_of_day = "morning"
        elif hour < 17:
            time_of_day = "afternoon"
        else:
            time_of_day = "evening"

        self.update_state_ui("thinking", "WAKING UP...")
        
        greeting_prompt = (
            f"Generate an extremely short, creative, and unique 1-sentence greeting welcoming the user. "
            f"Always greet the user as 'boss' (e.g. 'Good evening boss, what do you need?', 'What's up boss?', 'Need something done today?'). "
            f"It is currently the {time_of_day} (local time: {datetime.now().strftime('%I:%M %p')}). "
            f"Keep the greeting under 10-15 words. Be casual, creative, and ready for action. "
            f"Respond strictly with a reply action."
        )
        
        self.query_thread = DaemonQueryThread(greeting_prompt, save_history=False)
        self.query_thread.status_changed.connect(self.handle_status_change)
        self.query_thread.query_finished.connect(self.handle_brain_response)
        self.query_thread.start()

    # =====================================================================
    # Telemetry RSS Loading
    # =====================================================================
    def load_news_skimmer(self):
        self.news_list.clear()
        try:
            import feedparser
            from elora.config import load_config
            config = load_config()
            feeds = config.get("news", {}).get("feeds", [])
            
            count = 1
            for feed_url in feeds:
                parsed = feedparser.parse(feed_url)
                feed_title = parsed.feed.get("title", "News")
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
        link = item.data(Qt.ItemDataRole.UserRole)
        if link:
            self.console_output.append(f"<span style='color: #10B981;'>System:</span> Opening link in web browser...")
            open_browser_url(link)


_hud_lock_socket = None


def prevent_multiple_instances() -> bool:
    """Uses a Linux abstract namespace socket to guarantee a single HUD instance."""
    global _hud_lock_socket
    import socket
    try:
        _hud_lock_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        # Bind to a unique abstract namespace socket (starts with \0)
        # These are automatically released by the kernel when the process exits
        _hud_lock_socket.bind('\0elora_hud_instance_lock')
        return True
    except socket.error:
        return False


def start_hud():
    if not prevent_multiple_instances():
        from elora.utils import send_notification
        send_notification("Elora HUD", "Elora HUD is already running.")
        print("Elora HUD is already running. Exiting.")
        sys.exit(0)

    app = QApplication(sys.argv)
    hud = EloraHUD()
    hud.show()
    sys.exit(app.exec())
