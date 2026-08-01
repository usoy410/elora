"""
Main HUD Window Controller for Elora using PySide6.
Coordinates user input event-filtering, sidebar logs, settings, and telemetry panels.
"""

import json
import logging
import os
import subprocess
import sys

from PySide6.QtCore import QEvent, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

# Import from core modules
from elora.core.config import load_config, save_config
from elora.ui.cava_visualizer import CavaVisualizer

# Import local ui packages
from elora.ui.styles import HUD_STYLESHEET
from elora.ui.system_monitor import SystemMonitorWidget
from elora.ui.threads import (
    DaemonQueryThread,
    DaemonSTTThread,
    NewsFetchThread,
    ScreenExplanationThread,
    StartupGreetingThread,
    TaskCancelThread,
    TaskListFetchThread,
    TaskLogFetchThread,
    TaskRemoveThread,
)
from elora.ui.voice_orb import OrbWidget

logger = logging.getLogger("elora.ui.hud_window")

VOICE_INPUT_PATH = "/tmp/elora_voice_input.raw"

KOKORO_VOICES = {
    "af_heart": "Heart (US Female - Default)",
    "af_bella": "Bella (US Female)",
    "af_sarah": "Sarah (US Female)",
    "am_adam": "Adam (US Male)",
    "am_michael": "Michael (US Male)",
    "bf_emma": "Emma (UK Female)",
    "bm_george": "George (UK Male)"
}

STT_ENGINES = {
    "gemini": "Gemini Cloud STT (Active)"
}


class EloraHUD(QWidget):
    """Centralized HUD interface styled with modern dark obsidian cards."""
    speaking_state_signal = Signal(bool)

    def __init__(self, voice_active: bool = False):
        super().__init__()
        self.voice_active_on_start = voice_active
        self.cached_greeting = None
        self.greeting_discarded = False
        self.greeting_played = False
        self.is_processing_user_input = False
        self.session_history = []
        self.record_process: subprocess.Popen | None = None
        self.is_recording = False
        self.active_sidebar_tab = -1  # -1 = closed
        self.task_list_thread = None
        self.task_log_thread = None
        self.task_cancel_thread = None

        self.speaking_state_signal.connect(self.on_speaking_state_changed)
        self.speaking_poll_timer = QTimer(self)
        self.speaking_poll_timer.timeout.connect(self.poll_speaking_status)

        self.setObjectName("EloraHUD")
        self.setWindowTitle("Elora HUD")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        
        # Load user configuration
        self.config = load_config()
        
        # Track which sessions have already spoken a waiting-for-input alert
        self.notified_waiting_sessions = set()

        # Apply QSS
        self.setStyleSheet(HUD_STYLESHEET)

        # Main Layout
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(30, 30, 30, 30)

        # Header Title Bar
        self.hud_header = QWidget(self)
        self.hud_header_layout = QHBoxLayout(self.hud_header)
        self.hud_header_layout.setContentsMargins(10, 0, 10, 0)
        
        self.title_layout = QVBoxLayout()
        self.title_label = QLabel("ELORA", self)
        self.title_label.setStyleSheet("font-size: 70px; font-weight: 900; letter-spacing: 5px; color: #f9f9f9; font-family: 'anurati';")
        #self.subtitle_label = QLabel("LINUX DESKTOP ASSISTANT // COGNITIVE CORE v2.0", self)
        #self.subtitle_label.setStyleSheet("font-size: 9px; font-family: 'JetBrains Mono'; color: #00F0FF; letter-spacing: 1px;")
        self.title_layout.addWidget(self.title_label)
        #self.title_layout.addWidget(self.subtitle_label)
        self.hud_header_layout.addLayout(self.title_layout)
        
        self.hud_header_layout.addStretch()
        self.main_layout.addWidget(self.hud_header)

        self.central_card = QWidget(self)
        self.central_card.setObjectName("CentralCard")
        self.main_layout.addWidget(self.central_card)

        self.card_layout = QHBoxLayout(self.central_card)
        self.card_layout.setContentsMargins(10, 10, 10, 10)
        self.card_layout.setSpacing(20)

        # =====================================================================
        # COLUMN 1: LEFT PANEL (System Monitor Widget)
        # =====================================================================
        self.system_monitor = SystemMonitorWidget(self)
        self.system_monitor.setFixedWidth(280)
        self.card_layout.addWidget(self.system_monitor, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        # Spacer/stretch pushes left panel and right panel to the margins
        self.card_layout.addStretch(1)

        # =====================================================================
        # COLUMN 2: CENTER PANEL (Voice Orb & Cava Visualizer)
        # Parented to top window to remain immune to inner layout height shifts.
        # =====================================================================
        self.center_panel = QWidget(self)
        self.center_layout = QVBoxLayout(self.center_panel)
        self.center_layout.setContentsMargins(0, 0, 0, 0)
        self.center_layout.setSpacing(15)
        
        self.center_layout.addStretch(1)

        # Voice Orb
        self.orb = OrbWidget(self)
        self.center_layout.addWidget(self.orb, alignment=Qt.AlignmentFlag.AlignCenter)
        
        # State display indicator label
        self.state_label = QLabel("[ HOLD ALT TO TALK ]", self)
        self.state_label.setStyleSheet("color: rgba(255, 255, 255, 0.8); font-family: 'JetBrains Mono'; font-size: 11px; font-weight: bold; letter-spacing: 2px;")
        self.center_layout.addWidget(self.state_label, alignment=Qt.AlignmentFlag.AlignCenter)
        
        # Cava Audio Visualizer
        self.cava = CavaVisualizer(self)
        self.center_layout.addWidget(self.cava, alignment=Qt.AlignmentFlag.AlignCenter)

        self.center_layout.addStretch(1)

        # Push to talk hotkey tip
        self.ptt_tip = QLabel("[ Hold ALT to Speak ]   •   [ Press ESC to Exit ]", self)
        self.ptt_tip.setStyleSheet("color: rgba(255, 255, 255, 0.5); font-family: 'JetBrains Mono'; font-size: 9px; font-weight: bold;")
        self.center_layout.addWidget(self.ptt_tip, alignment=Qt.AlignmentFlag.AlignCenter)

        # =====================================================================
        # COLUMN 3: RIGHT PANEL (Sidebar drawer + Vertical Action Panel)
        # =====================================================================
        self.right_panel = QWidget(self)
        self.right_layout = QHBoxLayout(self.right_panel)
        self.right_layout.setContentsMargins(0, 0, 0, 0)
        self.right_layout.setSpacing(10)
        self.right_layout.setAlignment(Qt.AlignmentFlag.AlignRight)

        # Sidebar container wrapping self.stacked_widget (hidden by default)
        self.sidebar_widget = QFrame(self)
        self.sidebar_widget.setObjectName("SidebarContainer")
        self.sidebar_widget.setFixedSize(380, 600)
        self.sidebar_layout = QVBoxLayout(self.sidebar_widget)
        self.sidebar_layout.setContentsMargins(15, 15, 15, 15)
        self.sidebar_layout.setSpacing(10)
        
        # Sidebar Header: Title and Close button
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

        # Control Panel containing vertical navigation buttons
        self.control_panel_widget = QFrame(self)
        self.control_panel_widget.setObjectName("ControlPanel")
        self.control_panel_widget.setFixedSize(150, 380)
        self.control_panel_layout = QVBoxLayout(self.control_panel_widget)
        self.control_panel_layout.setContentsMargins(10, 15, 10, 15)
        self.control_panel_layout.setSpacing(8)

        # Header for control panel
        self.lbl_cp_title = QLabel("NAV MODULES", self)
        self.lbl_cp_title.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px; font-weight: bold; color: rgba(255,255,255,0.4); text-align: center; margin-bottom: 5px;")
        self.control_panel_layout.addWidget(self.lbl_cp_title, alignment=Qt.AlignmentFlag.AlignCenter)

        # Buttons
        self.btn_chat = QPushButton("// 01 CHAT", self)
        self.btn_tools = QPushButton("// 02 TOOLS", self)
        self.btn_tasks = QPushButton("// 06 TASKS", self)
        self.btn_news = QPushButton("// 05 NEWS", self)
        self.btn_settings = QPushButton("// 03 CONFIG", self)
        self.btn_browser = QPushButton("// 04 BROWSER", self)

        # Setup checkable styling and focus
        self.nav_buttons = [
            self.btn_chat,       # index 0
            self.btn_tools,      # index 1
            self.btn_settings,   # index 2
            self.btn_browser,    # index 3
            self.btn_news,       # index 4
            self.btn_tasks       # index 5
        ]

        for btn in self.nav_buttons:
            btn.setCheckable(True)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setProperty("class", "control-btn")
            self.control_panel_layout.addWidget(btn)

        self.btn_chat.clicked.connect(lambda: self.toggle_sidebar(0))
        self.btn_tools.clicked.connect(lambda: self.toggle_sidebar(1))
        self.btn_settings.clicked.connect(lambda: self.toggle_sidebar(2))
        self.btn_browser.clicked.connect(lambda: self.toggle_sidebar(3))
        self.btn_news.clicked.connect(lambda: self.toggle_sidebar(4))
        self.btn_tasks.clicked.connect(lambda: self.toggle_sidebar(5))

        self.right_layout.addWidget(self.sidebar_widget)
        self.right_layout.addWidget(self.control_panel_widget)
        self.card_layout.addWidget(self.right_panel, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

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

        workspace_cfg = skills_cfg.get("workspace_query", True)

        self.chk_workspace = QCheckBox("Google Workspace (gws)", self)
        self.chk_workspace.setChecked(workspace_cfg)
        self.chk_workspace.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.chk_workspace.clicked.connect(self.save_tools_config)
        self.tools_layout.addWidget(self.chk_workspace)

        lbl_workspace_desc = QLabel("Let Elora query and manage your Gmail, Drive, Calendar, and Classroom.", self)
        lbl_workspace_desc.setStyleSheet("color: rgba(255,255,255,0.4); font-size: 11px; padding-left: 24px;")
        lbl_workspace_desc.setWordWrap(True)
        self.tools_layout.addWidget(lbl_workspace_desc)

        # Add Desktop Vision Explanation capability
        lbl_vision_header = QLabel("DESKTOP VISION CAPABILITIES", self)
        lbl_vision_header.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px; font-weight: bold; color: rgba(255,255,255,0.5); margin-top: 15px;")
        self.tools_layout.addWidget(lbl_vision_header)

        self.btn_explain_screen = QPushButton("Explain Current Screen", self)
        self.btn_explain_screen.setIcon(QIcon.fromTheme("view-preview"))
        self.btn_explain_screen.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_explain_screen.setStyleSheet("background-color: rgba(129, 140, 248, 0.12); border: 1px solid rgba(129, 140, 248, 0.3); color: #818CF8; font-weight: bold; padding: 6px; border-radius: 4px;")
        self.btn_explain_screen.clicked.connect(self.trigger_screen_explanation)
        self.tools_layout.addWidget(self.btn_explain_screen)

        lbl_explain_desc = QLabel("Captures a system screenshot and explains what is currently visible on your screen using Gemini's vision capabilities.", self)
        lbl_explain_desc.setStyleSheet("color: rgba(255,255,255,0.4); font-size: 11px; padding-left: 5px;")
        lbl_explain_desc.setWordWrap(True)
        self.tools_layout.addWidget(lbl_explain_desc)

        self.tools_layout.addStretch()
        self.stacked_widget.addWidget(self.page_tools)

        # ---------------------------------------------------------------------
        # Tab Page 2: Settings Panel
        # ---------------------------------------------------------------------
        self.page_settings = QWidget(self)
        self.settings_layout = QVBoxLayout(self.page_settings)
        self.settings_layout.setContentsMargins(5, 5, 5, 5)
        self.settings_layout.setSpacing(10)

        # Settings Sub-navigation Bar for Minimalist look
        self.settings_nav = QWidget(self)
        self.settings_nav_layout = QHBoxLayout(self.settings_nav)
        self.settings_nav_layout.setContentsMargins(0, 0, 0, 8)
        self.settings_nav_layout.setSpacing(10)

        self.btn_sub_speech = QPushButton("Speech", self)
        self.btn_sub_brain = QPushButton("Brain", self)
        self.btn_sub_system = QPushButton("System", self)
        self.btn_sub_workspace = QPushButton("Workspace", self)
        self.btn_sub_telegram = QPushButton("Telegram", self)

        self.sub_buttons = [self.btn_sub_speech, self.btn_sub_brain, self.btn_sub_system, self.btn_sub_workspace, self.btn_sub_telegram]
        for btn in self.sub_buttons:
            btn.setCheckable(True)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setProperty("class", "sub-tab-btn")
            self.settings_nav_layout.addWidget(btn)

        self.btn_sub_speech.clicked.connect(lambda: self.switch_settings_subpage(0))
        self.btn_sub_brain.clicked.connect(lambda: self.switch_settings_subpage(1))
        self.btn_sub_system.clicked.connect(lambda: self.switch_settings_subpage(2))
        self.btn_sub_workspace.clicked.connect(lambda: self.switch_settings_subpage(3))
        self.btn_sub_telegram.clicked.connect(lambda: self.switch_settings_subpage(4))

        self.settings_layout.addWidget(self.settings_nav)

        # Stacked widget for sub-pages
        self.settings_stack = QStackedWidget(self)
        self.settings_layout.addWidget(self.settings_stack)

        # Global Save Settings action (visible on all sub-tabs)
        self.btn_save_settings = QPushButton("Save Settings", self)
        self.btn_save_settings.setIcon(QIcon.fromTheme("document-save"))
        self.btn_save_settings.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_save_settings.setStyleSheet("background-color: rgba(16, 185, 129, 0.08); border-color: rgba(16, 185, 129, 0.25); color: #A7F3D0; font-weight: bold; margin-top: 10px;")
        self.btn_save_settings.clicked.connect(self.save_settings)
        self.settings_layout.addWidget(self.btn_save_settings)

        # ------------------- Sub-page 0: Speech -------------------
        self.subpage_speech = QWidget(self)
        layout_speech = QVBoxLayout(self.subpage_speech)
        layout_speech.setContentsMargins(0, 0, 0, 0)
        layout_speech.setSpacing(10)

        lbl_voice = QLabel("VOICE MODEL", self)
        lbl_voice.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px; font-weight: bold; color: rgba(255,255,255,0.4);")
        layout_speech.addWidget(lbl_voice)
        
        self.cmb_voice = QComboBox(self)
        self.cmb_voice.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for code, label in KOKORO_VOICES.items():
            self.cmb_voice.addItem(label, code)
        voice_cfg = self.config.get("voice", {})
        active_voice = voice_cfg.get("voice_name", "af_heart")
        if active_voice not in KOKORO_VOICES:
            active_voice = "af_heart"
        idx = self.cmb_voice.findData(active_voice)
        if idx != -1:
            self.cmb_voice.setCurrentIndex(idx)
        layout_speech.addWidget(self.cmb_voice)

        lbl_stt_model = QLabel("SPEECH RECOGNITION (STT) ENGINE", self)
        lbl_stt_model.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px; font-weight: bold; color: rgba(255,255,255,0.4);")
        layout_speech.addWidget(lbl_stt_model)
        
        self.cmb_stt_model = QComboBox(self)
        self.cmb_stt_model.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for code, label in STT_ENGINES.items():
            self.cmb_stt_model.addItem(label, code)
        stt_cfg = self.config.get("stt", {})
        active_stt_model = stt_cfg.get("model_name", "gemini")
        idx_stt = self.cmb_stt_model.findData(active_stt_model)
        if idx_stt != -1:
            self.cmb_stt_model.setCurrentIndex(idx_stt)
        layout_speech.addWidget(self.cmb_stt_model)

        self.lbl_speed_val = QLabel("SPEED: 1.0x", self)
        self.lbl_speed_val.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px; font-weight: bold; color: rgba(255,255,255,0.4);")
        layout_speech.addWidget(self.lbl_speed_val)
        
        self.sld_speed = QSlider(Qt.Orientation.Horizontal, self)
        self.sld_speed.setRange(50, 200)
        self.sld_speed.setValue(int(voice_cfg.get("speed", 1.0) * 100))
        self.sld_speed.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.sld_speed.valueChanged.connect(self.on_speed_changed)
        layout_speech.addWidget(self.sld_speed)
        
        self.on_speed_changed(self.sld_speed.value())

        lbl_voice_provider = QLabel("VOICE PROVIDER", self)
        lbl_voice_provider.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px; font-weight: bold; color: rgba(255,255,255,0.4);")
        layout_speech.addWidget(lbl_voice_provider)

        self.cmb_voice_provider = QComboBox(self)
        self.cmb_voice_provider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.cmb_voice_provider.addItem("Local (Offline ONNX)", "local")
        self.cmb_voice_provider.addItem("Cloud (Hugging Face Space)", "cloud")
        active_provider = voice_cfg.get("provider", "local")
        idx_prov = self.cmb_voice_provider.findData(active_provider)
        if idx_prov != -1:
            self.cmb_voice_provider.setCurrentIndex(idx_prov)
        layout_speech.addWidget(self.cmb_voice_provider)

        self.lbl_hf_space_url = QLabel("HUGGING FACE SPACE URL", self)
        self.lbl_hf_space_url.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px; font-weight: bold; color: rgba(255,255,255,0.4);")
        layout_speech.addWidget(self.lbl_hf_space_url)

        self.txt_hf_space_url = QLineEdit(self)
        self.txt_hf_space_url.setPlaceholderText("https://username-space.hf.space")
        self.txt_hf_space_url.setText(voice_cfg.get("hf_space_url", ""))
        layout_speech.addWidget(self.txt_hf_space_url)

        self.lbl_hf_token = QLabel("HUGGING FACE TOKEN (OPTIONAL)", self)
        self.lbl_hf_token.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px; font-weight: bold; color: rgba(255,255,255,0.4);")
        layout_speech.addWidget(self.lbl_hf_token)

        self.txt_hf_token = QLineEdit(self)
        self.txt_hf_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_hf_token.setPlaceholderText("Enter HF User Token if private...")
        self.txt_hf_token.setText(voice_cfg.get("hf_token", ""))
        layout_speech.addWidget(self.txt_hf_token)

        layout_speech.addStretch()
        self.settings_stack.addWidget(self.subpage_speech)

        # ------------------- Sub-page 1: Brain -------------------
        self.subpage_brain = QWidget(self)
        layout_brain = QVBoxLayout(self.subpage_brain)
        layout_brain.setContentsMargins(0, 0, 0, 0)
        layout_brain.setSpacing(10)

        lbl_api_key = QLabel("GEMINI API KEY", self)
        lbl_api_key.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px; font-weight: bold; color: rgba(255,255,255,0.4);")
        layout_brain.addWidget(lbl_api_key)
        
        self.txt_api_key = QLineEdit(self)
        self.txt_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_api_key.setPlaceholderText("Enter your Gemini API key...")
        self.txt_api_key.setText(self.config.get("gemini_api_key", ""))
        layout_brain.addWidget(self.txt_api_key)

        lbl_personality = QLabel("AI PERSONALITY", self)
        lbl_personality.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px; font-weight: bold; color: rgba(255,255,255,0.4);")
        layout_brain.addWidget(lbl_personality)
        
        self.cmb_personality = QComboBox(self)
        self.cmb_personality.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.cmb_personality.addItem("Jarvis (Default)", "default")
        self.cmb_personality.addItem("Funny", "funny")
        self.cmb_personality.addItem("Direct", "direct")
        self.cmb_personality.addItem("Polite", "polite")
        self.cmb_personality.addItem("Respectful", "respectful")
        self.cmb_personality.addItem("Other...", "other")
        
        active_personality = self.config.get("personality")
        if not active_personality:
            legacy_instr = self.config.get("custom_instructions")
            from elora.core.brain import DEFAULT_CUSTOM_INSTRUCTION
            if legacy_instr and legacy_instr.strip() != DEFAULT_CUSTOM_INSTRUCTION.strip():
                active_personality = "other"
            else:
                active_personality = "default"
                
        idx_p = self.cmb_personality.findData(active_personality)
        if idx_p != -1:
            self.cmb_personality.setCurrentIndex(idx_p)
        layout_brain.addWidget(self.cmb_personality)
        
        self.lbl_custom_personality = QLabel("CUSTOM PERSONALITY TYPE", self)
        self.lbl_custom_personality.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px; font-weight: bold; color: rgba(255,255,255,0.4);")
        layout_brain.addWidget(self.lbl_custom_personality)
        
        self.txt_custom_personality = QLineEdit(self)
        self.txt_custom_personality.setPlaceholderText("Enter custom personality (e.g. sarcastic pirate)...")
        
        current_custom = self.config.get("custom_personality")
        if current_custom is None:
            legacy_instr = self.config.get("custom_instructions")
            from elora.core.brain import DEFAULT_CUSTOM_INSTRUCTION
            if legacy_instr and legacy_instr.strip() != DEFAULT_CUSTOM_INSTRUCTION.strip():
                current_custom = legacy_instr.strip()
            else:
                current_custom = ""
        self.txt_custom_personality.setText(current_custom)
        layout_brain.addWidget(self.txt_custom_personality)

        self.cmb_personality.currentIndexChanged.connect(self.on_personality_changed)
        self.on_personality_changed()

        layout_brain.addStretch()
        self.settings_stack.addWidget(self.subpage_brain)

        # ------------------- Sub-page 2: System -------------------
        self.subpage_system = QWidget(self)
        layout_system = QVBoxLayout(self.subpage_system)
        layout_system.setContentsMargins(0, 0, 0, 0)
        layout_system.setSpacing(12)

        self.chk_safe_gate = QCheckBox("Safe Gate Mode (Approve dangerous commands)", self)
        self.chk_safe_gate.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.chk_safe_gate.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 10px; color: #D1D5DB;")
        self.chk_safe_gate.setChecked(self.config.get("safe_gate_mode", True))
        layout_system.addWidget(self.chk_safe_gate)

        # Background Developer Agent Delegation controls
        lbl_bg_agent_section = QLabel("BACKGROUND DEVELOPER AGENT")
        lbl_bg_agent_section.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 10px; font-weight: bold; color: #10B981; margin-top: 15px;")
        layout_system.addWidget(lbl_bg_agent_section)

        agent_cfg = self.config.get("background_agent", {})
        active_provider = agent_cfg.get("active_provider", "agy")
        providers = agent_cfg.get("providers", {
            "agy": "agy --dangerously-skip-permissions --mode accept-edits --print-timeout 20m --print {prompt}",
            "claude-cli": "claude-cli --prompt {prompt}",
            "codex": "codex {prompt}",
            "custom": ""
        })

        lbl_bg_agent_provider = QLabel("ACTIVE AGENT PROVIDER")
        lbl_bg_agent_provider.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 8px; font-weight: bold; color: rgba(255,255,255,0.4);")
        layout_system.addWidget(lbl_bg_agent_provider)

        self.cmb_bg_agent_provider = QComboBox(self)
        self.cmb_bg_agent_provider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.cmb_bg_agent_provider.setStyleSheet("QComboBox { background-color: rgba(255,255,255,0.05); color: #E5E7EB; border: 1px solid rgba(255,255,255,0.1); padding: 5px; font-family: 'JetBrains Mono'; font-size: 10px; }")
        self.cmb_bg_agent_provider.addItem("Antigravity CLI (agy)", "agy")
        self.cmb_bg_agent_provider.addItem("Claude CLI (claude-cli)", "claude-cli")
        self.cmb_bg_agent_provider.addItem("Codex (codex)", "codex")
        self.cmb_bg_agent_provider.addItem("Custom Command", "custom")
        
        idx = self.cmb_bg_agent_provider.findData(active_provider)
        if idx >= 0:
            self.cmb_bg_agent_provider.setCurrentIndex(idx)
        layout_system.addWidget(self.cmb_bg_agent_provider)

        lbl_bg_agent_template = QLabel("COMMAND TEMPLATE (must contain {prompt})")
        lbl_bg_agent_template.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 8px; font-weight: bold; color: rgba(255,255,255,0.4);")
        layout_system.addWidget(lbl_bg_agent_template)

        self.txt_bg_agent_template = QLineEdit(self)
        self.txt_bg_agent_template.setStyleSheet("QLineEdit { background-color: rgba(255,255,255,0.05); color: #E5E7EB; border: 1px solid rgba(255,255,255,0.1); padding: 5px; font-family: 'JetBrains Mono'; font-size: 10px; }")
        current_template = providers.get(active_provider, providers.get("agy", ""))
        self.txt_bg_agent_template.setText(current_template)
        layout_system.addWidget(self.txt_bg_agent_template)

        def update_template_preset():
            prov = self.cmb_bg_agent_provider.currentData()
            cfg_providers = self.config.get("background_agent", {}).get("providers", {
                "agy": "agy --dangerously-skip-permissions --mode accept-edits --print-timeout 20m --print {prompt}",
                "claude-cli": "claude-cli --prompt {prompt}",
                "codex": "codex {prompt}",
                "custom": ""
            })
            self.txt_bg_agent_template.setText(cfg_providers.get(prov, ""))
            
        self.cmb_bg_agent_provider.currentIndexChanged.connect(update_template_preset)

        # Spacer to push action buttons to the bottom
        layout_system.addSpacing(20)

        # Restart conversation button
        self.btn_reset_conv = QPushButton("Restart Conversation", self)
        self.btn_reset_conv.setIcon(QIcon.fromTheme("view-refresh"))
        self.btn_reset_conv.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_reset_conv.setStyleSheet("background-color: rgba(239, 68, 68, 0.08); border-color: rgba(239, 68, 68, 0.25); color: #FCA5A5;")
        self.btn_reset_conv.clicked.connect(self.reset_conversation)
        layout_system.addWidget(self.btn_reset_conv)

        layout_system.addStretch()
        self.settings_stack.addWidget(self.subpage_system)

        # ------------------- Sub-page 3: Workspace -------------------
        self.subpage_workspace = QWidget(self)
        layout_workspace = QVBoxLayout(self.subpage_workspace)
        layout_workspace.setContentsMargins(0, 0, 0, 0)
        layout_workspace.setSpacing(10)

        lbl_workspace_info = QLabel("Google Workspace Integration", self)
        lbl_workspace_info.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 12px; font-weight: bold; color: rgba(255,255,255,0.8);")
        layout_workspace.addWidget(lbl_workspace_info)
        
        lbl_workspace_details = QLabel(
            "Elora uses the 'gws' CLI to securely access your Gmail, Drive, Calendar, and Classroom.\n\n"
            "To re-authenticate or change credentials, run the interactive setup wizard:\n"
            "  elora --setup\n\n"
            "Your credentials are automatically bootstrapped to:\n"
            "  ~/.config/elora/gws-personal/client_secret.json", self
        )
        lbl_workspace_details.setStyleSheet("color: rgba(255,255,255,0.4); font-size: 11px;")
        lbl_workspace_details.setWordWrap(True)
        layout_workspace.addWidget(lbl_workspace_details)

        layout_workspace.addStretch()
        self.settings_stack.addWidget(self.subpage_workspace)

        # ------------------- Sub-page 4: Telegram -------------------
        self.subpage_telegram = QWidget()
        layout_telegram = QVBoxLayout(self.subpage_telegram)
        layout_telegram.setContentsMargins(0, 0, 0, 0)
        layout_telegram.setSpacing(10)

        telegram_cfg = self.config.get("telegram", {})

        self.chk_telegram = QCheckBox("Enable Telegram Bot")
        self.chk_telegram.setChecked(telegram_cfg.get("enabled", False))
        self.chk_telegram.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout_telegram.addWidget(self.chk_telegram)

        lbl_tg_token_env = QLabel("TOKEN ENV VARIABLE NAME")
        lbl_tg_token_env.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px; font-weight: bold; color: rgba(255,255,255,0.4);")
        layout_telegram.addWidget(lbl_tg_token_env)

        self.txt_tg_token_env = QLineEdit()
        self.txt_tg_token_env.setPlaceholderText("TELEGRAM_BOT_TOKEN")
        self.txt_tg_token_env.setText(telegram_cfg.get("token_env_var", "TELEGRAM_BOT_TOKEN"))
        layout_telegram.addWidget(self.txt_tg_token_env)

        lbl_tg_allowed_ids = QLabel("ALLOWED USER IDS (comma-separated)")
        lbl_tg_allowed_ids.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px; font-weight: bold; color: rgba(255,255,255,0.4);")
        layout_telegram.addWidget(lbl_tg_allowed_ids)

        self.txt_tg_allowed_ids = QLineEdit()
        self.txt_tg_allowed_ids.setPlaceholderText("e.g. 123456789, 987654321")
        allowed_ids_str = ", ".join(str(uid) for uid in telegram_cfg.get("allowed_user_ids", []))
        self.txt_tg_allowed_ids.setText(allowed_ids_str)
        layout_telegram.addWidget(self.txt_tg_allowed_ids)

        lbl_tg_max_file = QLabel("MAX FILE TRANSFER SIZE (MB)")
        lbl_tg_max_file.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px; font-weight: bold; color: rgba(255,255,255,0.4);")
        layout_telegram.addWidget(lbl_tg_max_file)

        self.txt_tg_max_file = QLineEdit()
        self.txt_tg_max_file.setPlaceholderText("50")
        self.txt_tg_max_file.setText(str(telegram_cfg.get("max_file_size_mb", 50)))
        layout_telegram.addWidget(self.txt_tg_max_file)

        layout_telegram.addStretch()
        self.settings_stack.addWidget(self.subpage_telegram)

        # Initialize active sub-tab state
        self.switch_settings_subpage(0)

        self.cmb_voice_provider.currentIndexChanged.connect(self.on_voice_provider_changed)
        self.on_voice_provider_changed()
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
            from elora.skills.os_control import on_abort_activated
            on_abort_activated()
            self.console_output.append("<br><span style='color: #EF4444;'>System: Automation aborted by user request.</span>")
            
        self.btn_abort_automation.clicked.connect(trigger_abort)
        self.browser_layout.addWidget(self.btn_abort_automation)
        
        lbl_abort_desc = QLabel("Pressing ABORT or the 'Ctrl+Alt+C' hotkey terminates active mouse/keyboard automation sequences.", self)
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

        # ---------------------------------------------------------------------
        # Tab Page 5: Active Background Tasks
        # ---------------------------------------------------------------------
        self.page_tasks = QWidget(self)
        self.tasks_layout = QVBoxLayout(self.page_tasks)
        self.tasks_layout.setContentsMargins(5, 5, 5, 5)
        self.tasks_layout.setSpacing(10)
        
        lbl_tasks_title = QLabel("RUNNING BACKGROUND AGENTS", self)
        lbl_tasks_title.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px; font-weight: bold; color: rgba(255,255,255,0.5);")
        self.tasks_layout.addWidget(lbl_tasks_title)
        
        self.tasks_list_widget = QListWidget(self)
        self.tasks_list_widget.setStyleSheet(
            "QListWidget { background-color: rgba(0, 0, 0, 0.25); border: 1px solid rgba(255,255,255,0.08); border-radius: 6px; padding: 4px; color: #E5E7EB; }"
            "QListWidget::item { border-bottom: 1px solid rgba(255,255,255,0.04); padding: 6px; }"
            "QListWidget::item:selected { background-color: rgba(255,255,255,0.08); color: #FFFFFF; border-radius: 4px; }"
        )
        self.tasks_list_widget.itemSelectionChanged.connect(self.on_task_selection_changed)
        self.tasks_layout.addWidget(self.tasks_list_widget)
        
        lbl_log_title = QLabel("LIVE LOG OUTPUT", self)
        lbl_log_title.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px; font-weight: bold; color: rgba(255,255,255,0.5);")
        self.tasks_layout.addWidget(lbl_log_title)
        
        self.txt_task_log = QTextBrowser(self)
        self.txt_task_log.setFont(QFont("JetBrains Mono", 8))
        self.txt_task_log.setStyleSheet("background-color: #08090C; color: #A7F3D0; border: 1px solid rgba(255,255,255,0.1); border-radius: 6px; padding: 6px;")
        self.txt_task_log.setPlaceholderText("Select a running task to view real-time log output...")
        self.tasks_layout.addWidget(self.txt_task_log)
        
        # Tmux Input Interaction Widget
        self.tmux_input_widget = QWidget(self)
        self.tmux_input_layout = QHBoxLayout(self.tmux_input_widget)
        self.tmux_input_layout.setContentsMargins(0, 0, 0, 0)
        self.tmux_input_layout.setSpacing(5)

        self.txt_tmux_input = QLineEdit(self)
        self.txt_tmux_input.setPlaceholderText("Send input / keystrokes to active agent...")
        self.txt_tmux_input.setStyleSheet("QLineEdit { background-color: rgba(255, 255, 255, 0.05); color: #E5E7EB; border: 1px solid rgba(255,255,255,0.1); border-radius: 4px; padding: 5px; font-family: 'JetBrains Mono'; font-size: 10px; }")
        self.txt_tmux_input.setEnabled(False)
        self.txt_tmux_input.returnPressed.connect(self.send_tmux_input)

        self.btn_tmux_send = QPushButton("Send", self)
        self.btn_tmux_send.setEnabled(False)
        self.btn_tmux_send.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_tmux_send.setStyleSheet("QPushButton { background-color: rgba(16, 185, 129, 0.2); border: 1px solid rgba(16, 185, 129, 0.4); color: #34D399; font-family: 'JetBrains Mono'; font-size: 10px; font-weight: bold; width: 60px; padding: 5px; } QPushButton:disabled { background-color: rgba(255,255,255,0.02); border-color: rgba(255,255,255,0.05); color: rgba(255,255,255,0.2); }")
        self.btn_tmux_send.clicked.connect(self.send_tmux_input)

        self.btn_tmux_attach = QPushButton("Open Terminal", self)
        self.btn_tmux_attach.setEnabled(False)
        self.btn_tmux_attach.setIcon(QIcon.fromTheme("utilities-terminal"))
        self.btn_tmux_attach.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_tmux_attach.setStyleSheet("QPushButton { background-color: rgba(99, 102, 241, 0.2); border: 1px solid rgba(99, 102, 241, 0.4); color: #818CF8; font-family: 'JetBrains Mono'; font-size: 10px; font-weight: bold; padding: 5px; } QPushButton:disabled { background-color: rgba(255,255,255,0.02); border-color: rgba(255,255,255,0.05); color: rgba(255,255,255,0.2); }")
        self.btn_tmux_attach.clicked.connect(self.attach_tmux_terminal)

        self.tmux_input_layout.addWidget(self.txt_tmux_input)
        self.tmux_input_layout.addWidget(self.btn_tmux_send)
        self.tmux_input_layout.addWidget(self.btn_tmux_attach)
        self.tasks_layout.addWidget(self.tmux_input_widget)

        self.tasks_actions = QWidget(self)
        self.tasks_actions_layout = QHBoxLayout(self.tasks_actions)
        self.tasks_actions_layout.setContentsMargins(0, 0, 0, 0)
        
        self.btn_refresh_tasks = QPushButton("Refresh", self)
        self.btn_refresh_tasks.setIcon(QIcon.fromTheme("view-refresh"))
        self.btn_refresh_tasks.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_refresh_tasks.clicked.connect(self.refresh_tasks_list)
        
        self.btn_cancel_task = QPushButton("Cancel Task", self)
        self.btn_cancel_task.setIcon(QIcon.fromTheme("process-stop"))
        self.btn_cancel_task.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_cancel_task.setStyleSheet("background-color: rgba(239, 68, 68, 0.25); border-color: rgba(239, 68, 68, 0.45); color: #F87171; font-weight: bold;")
        self.btn_cancel_task.clicked.connect(self.cancel_selected_task)
        
        self.btn_clear_task = QPushButton("Clear Task", self)
        self.btn_clear_task.setIcon(QIcon.fromTheme("edit-clear"))
        self.btn_clear_task.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_clear_task.setStyleSheet("background-color: rgba(255, 255, 255, 0.08); border-color: rgba(255, 255, 255, 0.15); color: #E5E7EB;")
        self.btn_clear_task.clicked.connect(self.clear_selected_task)
        
        self.tasks_actions_layout.addWidget(self.btn_refresh_tasks)
        self.tasks_actions_layout.addWidget(self.btn_cancel_task)
        self.tasks_actions_layout.addWidget(self.btn_clear_task)
        self.tasks_layout.addWidget(self.tasks_actions)
        
        self.stacked_widget.addWidget(self.page_tasks)
        
        self.browser_refresh_timer = QTimer(self)
        self.browser_refresh_timer.timeout.connect(self.update_browser_screenshot)
        self.browser_refresh_timer.start(2000)

        self.sidebar_layout.addWidget(self.stacked_widget)
        self.sidebar_widget.hide()
        self.showMaximized()
        self.reposition_center_panel()

        self.telemetry_timer = QTimer(self)
        self.telemetry_timer.timeout.connect(self.update_telemetry_loop)
        self.telemetry_timer.start(1000)

        # Setup startup greeting deferred timer (7 seconds idle)
        # Explain the "Why" as required by rules:
        # We delay the greeting and tasks report by 7 seconds to give the user a window to interact immediately 
        # without being interrupted by spoken feedback. If the user interacts (presses Alt), we cancel this greeting.
        self.startup_greeting_timer = QTimer(self)
        self.startup_greeting_timer.setSingleShot(True)
        self.startup_greeting_timer.timeout.connect(lambda: self.trigger_startup_greeting(quiet=False))
        self.startup_greeting_timer.start(7000)

        self.ptt_release_timer = QTimer(self)
        self.ptt_release_timer.setSingleShot(True)
        self.ptt_release_timer.timeout.connect(self.stop_voice_recording)

        QApplication.instance().installEventFilter(self)

        # Why: If launched in direct voice/listening mode (--voice / -v), immediately start
        # voice recording using hands-free silence detection and bypass the startup greeting.
        if self.voice_active_on_start:
            QTimer.singleShot(0, lambda: self.start_voice_recording(silence_detection=True))

    def update_browser_screenshot(self):
        from PySide6.QtGui import QPixmap
        screenshot_path = "/tmp/elora_browser.png"
        if os.path.exists(screenshot_path):
            pixmap = QPixmap(screenshot_path)
            scaled = pixmap.scaled(self.lbl_screenshot.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.lbl_screenshot.setPixmap(scaled)
            self.lbl_browser_status.setText("CDP Connection: Active (Screenshot updated)")
        else:
            self.lbl_screenshot.setText("No screenshot captured yet.\nElora will capture one when navigating.")
            self.lbl_browser_status.setText("CDP Connection: Standing by")

    def closeEvent(self, event):
        """Cleanly stops any background threads or active timers on window close."""
        if hasattr(self, "startup_greeting_timer") and self.startup_greeting_timer.isActive():
            self.startup_greeting_timer.stop()
        if hasattr(self, "startup_thread") and self.startup_thread and self.startup_thread.isRunning():
            try:
                self.startup_thread.greeting_finished.disconnect()
                self.startup_thread.terminate()
            except Exception:
                pass
        event.accept()

    def center_on_screen(self):
        pass

    def reposition_center_panel(self):
        """Mathematically positions center_panel in the exact center of the maximized window."""
        if hasattr(self, "center_panel") and self.center_panel:
            window_rect = self.rect()
            panel_w = 320
            panel_h = 340
            self.center_panel.setFixedSize(panel_w, panel_h)
            cx = (window_rect.width() - panel_w) // 2
            
            # Center vertically relative to content area (accounting for title header + margins)
            header_h = 60
            margin_top = 30
            content_y = header_h + margin_top
            content_h = window_rect.height() - content_y - 30 # 30px bottom margin
            
            cy = content_y + (content_h - panel_h) // 2
            self.center_panel.move(cx, cy)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reposition_center_panel()

    def toggle_sidebar(self, tab_index: int):
        titles = ["Conversation", "Tools", "Settings", "Browser Preview", "News Telemetry", "Active Tasks"]
        self.sidebar_title.setText(titles[tab_index])
        
        # Sync navigation check states
        for i, btn in enumerate(self.nav_buttons):
            btn.setChecked(i == tab_index and self.active_sidebar_tab != tab_index)
            
        if self.active_sidebar_tab == tab_index:
            self.close_sidebar()
        else:
            self.active_sidebar_tab = tab_index
            self.stacked_widget.setCurrentIndex(tab_index)
            self.sidebar_widget.show()
            
            if tab_index == 4:
                self.news_list.clear()
                self.news_list.addItem("Loading news feeds...")
                QTimer.singleShot(100, self.load_news_skimmer)
            elif tab_index == 5:
                self.refresh_tasks_list()

    def close_sidebar(self):
        self.active_sidebar_tab = -1
        self.sidebar_widget.hide()
        for btn in self.nav_buttons:
            btn.setChecked(False)

    def update_telemetry_loop(self):
        if self.active_sidebar_tab == 5:
            self.update_tasks_periodically()

    def switch_settings_subpage(self, index: int):
        """
        Switches the active sub-page in the dynamic settings configurations panel.
        Highlights the selected category button and hides the other configuration cards.
        """
        self.settings_stack.setCurrentIndex(index)
        for i, btn in enumerate(self.sub_buttons):
            btn.setChecked(i == index)

    def on_speed_changed(self, value: int):
        self.lbl_speed_val.setText(f"SPEED: {value/100:.1f}x")

    def on_voice_provider_changed(self):
        is_cloud = self.cmb_voice_provider.currentData() == "cloud"
        self.lbl_hf_space_url.setVisible(is_cloud)
        self.txt_hf_space_url.setVisible(is_cloud)
        self.lbl_hf_token.setVisible(is_cloud)
        self.txt_hf_token.setVisible(is_cloud)

    def on_personality_changed(self):
        is_other = self.cmb_personality.currentData() == "other"
        self.lbl_custom_personality.setVisible(is_other)
        self.txt_custom_personality.setVisible(is_other)

    def save_tools_config(self):
        updates = {
            "skills": {
                "web_search": self.chk_web_search.isChecked(),
                "web_scrape": self.chk_web_scrape.isChecked(),
                "command_run": self.chk_command_run.isChecked(),
                "workspace_query": self.chk_workspace.isChecked()
            }
        }
        save_config(updates)

    def trigger_screen_explanation(self):
        """
        Triggers screen screenshot capture and invokes the vision model explanation
        flow asynchronously via a background ScreenExplanationThread.
        """
        self.console_output.append("<br><span style='color: #818CF8;'>System:</span> Capturing system screenshot and querying vision model...")
        self.update_state_ui("thinking", "ANALYZING...")
        self.btn_explain_screen.setEnabled(False)
        
        # Hide the HUD overlay to capture a clean desktop screenshot underneath
        self.hide()
        QApplication.instance().processEvents()
        
        # Wait a small delay to allow the compositor to repaint the screen without the HUD
        import time
        time.sleep(0.2)
        
        # Take the screenshot on the client side
        try:
            from elora.skills.os_control import capture_desktop_screenshot
            capture_desktop_screenshot()
        except Exception as e:
            logger.error("Failed to capture screen: %s", e)
            
        # Restore HUD window visibility
        self.show()
        QApplication.instance().processEvents()
        
        self.explain_thread = ScreenExplanationThread()
        self.explain_thread.explanation_finished.connect(self.handle_screen_explanation_response)
        self.explain_thread.start()

    def handle_screen_explanation_response(self, res: dict):
        """
        Processes the screen explanation results from the daemon thread.
        Appends the conversational explanation to the chat console and updates session history.
        """
        self.btn_explain_screen.setEnabled(True)
        self.reset_to_idle()
        
        status = res.get("status")
        if status == "explanation":
            text = res.get("text", "")
            self.console_output.append(f"<span style='color: #EC4899;'>Elora:</span> {text}")
            self.session_history.append({"role": "assistant", "content": text})
            if len(self.session_history) > 20:
                self.session_history.pop(0)
        else:
            err_msg = res.get("message", "Unknown error")
            self.console_output.append(f"<span style='color: #EF4444;'>Error:</span> {err_msg}")

    def save_settings(self):
        selected_voice = self.cmb_voice.currentData()
        selected_speed = self.sld_speed.value() / 100.0
        selected_stt = self.cmb_stt_model.currentData()
        selected_personality = self.cmb_personality.currentData()
        custom_personality = self.txt_custom_personality.text().strip()
        api_key = self.txt_api_key.text().strip()
        selected_provider = self.cmb_voice_provider.currentData()
        hf_space_url = self.txt_hf_space_url.text().strip()
        hf_token = self.txt_hf_token.text().strip()

        # Telegram configurations
        tg_enabled = self.chk_telegram.isChecked()
        tg_token_env = self.txt_tg_token_env.text().strip() or "TELEGRAM_BOT_TOKEN"
        tg_allowed_ids_str = self.txt_tg_allowed_ids.text().strip()
        tg_max_file_str = self.txt_tg_max_file.text().strip()

        tg_allowed_ids = []
        if tg_allowed_ids_str:
            for item in tg_allowed_ids_str.split(","):
                try:
                    tg_allowed_ids.append(int(item.strip()))
                except ValueError:
                    pass

        try:
            tg_max_file = int(tg_max_file_str) if tg_max_file_str else 50
        except ValueError:
            tg_max_file = 50

        bg_agent_provider = self.cmb_bg_agent_provider.currentData()
        bg_agent_template = self.txt_bg_agent_template.text().strip()

        bg_providers = self.config.get("background_agent", {}).get("providers", {
            "agy": "agy --dangerously-skip-permissions --mode accept-edits --print-timeout 20m --print {prompt}",
            "claude-cli": "claude-cli --prompt {prompt}",
            "codex": "codex {prompt}",
            "custom": ""
        })
        bg_providers[bg_agent_provider] = bg_agent_template

        updates = {
            "voice": {
                "voice_name": selected_voice,
                "speed": selected_speed,
                "provider": selected_provider,
                "hf_space_url": hf_space_url,
                "hf_token": hf_token
            },
            "stt": {
                "model_name": selected_stt
            },
            "personality": selected_personality,
            "custom_personality": custom_personality,
            "gemini_api_key": api_key,
            "safe_gate_mode": self.chk_safe_gate.isChecked(),
            "telegram": {
                "enabled": tg_enabled,
                "token_env_var": tg_token_env,
                "allowed_user_ids": tg_allowed_ids,
                "max_file_size_mb": tg_max_file
            },
            "background_agent": {
                "active_provider": bg_agent_provider,
                "providers": bg_providers
            }
        }
        save_config(updates)
        self.console_output.append("<br><span style='color: #10B981;'>System: Settings saved successfully.</span>")

    def reset_conversation(self):
        from elora.ipc.daemon_client import EloraDaemonClient
        EloraDaemonClient().send_cmd({"cmd": "reset_history"})
        self.session_history.clear()
        self.console_output.clear()
        self.console_output.append("<span style='color: #818CF8;'>Elora:</span> Conversation restarted.")
        self.trigger_startup_greeting()

    @Slot(bool)
    def on_speaking_state_changed(self, active: bool):
        """
        Slot to handle voice synthesis speaking state changes.
        
        Why: Synchronizes the visual green glowing state of the AI Core orb and
        the "SPEAKING..." label with the actual status of the local Kokoro engine.
        """
        if active:
            # Check if voice feedback is actually enabled
            voice_enabled = self.config.get("voice", {}).get("enabled", False)
            if not voice_enabled:
                self.reset_to_idle()
                return
            self.update_state_ui("speaking", "SPEAKING...")
            self.start_speaking_poll()
        else:
            self.speaking_poll_timer.stop()
            self.reset_to_idle()

    def start_speaking_poll(self):
        """Starts a high-frequency polling timer to track voice playback status."""
        self.speaking_poll_timer.start(200)

    def poll_speaking_status(self):
        """Polls the daemon to see if the voice playback is still active."""
        import threading
        def check_bg():
            try:
                from elora.ipc.daemon_client import EloraDaemonClient
                c = EloraDaemonClient()
                res = c.send_cmd({"cmd": "is_speaking"})
                is_active = res.get("is_speaking", False)
                if not is_active:
                    self.speaking_state_signal.emit(False)
            except Exception:
                self.speaking_state_signal.emit(False)
                
        threading.Thread(target=check_bg, name="EloraSpeakingPollThread", daemon=True).start()

    def update_state_ui(self, state: str, text: str):
        self.state_label.setText(text)
        self.orb.set_state(state)
        self.cava.set_state(state)
        
        if state == "listening":
            self.state_label.setStyleSheet("color: #FFFFFF; font-family: 'JetBrains Mono'; font-size: 12px; font-weight: bold; letter-spacing: 2px;")
        elif state == "thinking":
            self.state_label.setStyleSheet("color: rgba(255, 255, 255, 0.85); font-family: 'JetBrains Mono'; font-size: 12px; font-weight: bold; letter-spacing: 2px;")
        elif state == "speaking":
            self.state_label.setStyleSheet("color: #FFFFFF; font-family: 'JetBrains Mono'; font-size: 12px; font-weight: bold; letter-spacing: 2px;")
        else:
            self.state_label.setStyleSheet("color: rgba(255, 255, 255, 0.4); font-family: 'JetBrains Mono'; font-size: 11px; font-weight: bold; letter-spacing: 2px;")

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Alt, Qt.Key.Key_AltGr):
                # Why: Cancel and discard the startup greeting/report strictly when user presses Alt/AltGr (input mode).
                if hasattr(self, "startup_greeting_timer") and self.startup_greeting_timer.isActive():
                    self.startup_greeting_timer.stop()
                self.greeting_discarded = True

                if not event.isAutoRepeat():
                    if self.ptt_release_timer.isActive():
                        self.ptt_release_timer.stop()
                    else:
                        # Why: Alt/AltGr key acts as Push-To-Talk, so we disable silence detection timeouts during recording.
                        self.start_voice_recording(silence_detection=False)
                return True
            elif event.key() == Qt.Key.Key_Escape:
                if self.active_sidebar_tab != -1:
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
                    self.ptt_release_timer.start(450)
                return True
                
        return super().eventFilter(watched, event)

    def start_voice_recording(self, silence_detection: bool = False):
        if self.is_recording:
            return

        # Cancel the deferred greeting timer and terminate any active greeting threads
        if hasattr(self, "startup_greeting_timer") and self.startup_greeting_timer.isActive():
            self.startup_greeting_timer.stop()
        self.greeting_discarded = True
        if hasattr(self, "startup_thread") and self.startup_thread and self.startup_thread.isRunning():
            try:
                self.startup_thread.greeting_finished.disconnect()
                self.startup_thread.terminate()
            except Exception:
                pass

        self.is_recording = True
        self.update_state_ui("listening", "● LISTENING...")

        # Play alert chime to notify the user that they can speak
        try:
            import os

            from elora.utils import play_chime
            # Resolve absolute path to the success-chime.mp3 asset
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            chime_path = os.path.join(base_dir, "assets", "sounds", "success-chime.mp3")
            if os.path.exists(chime_path):
                play_chime(chime_path)
        except Exception as e:
            logger.error("Failed to play start listening chime: %s", e)

        self.stt_thread = DaemonSTTThread(silence_detection=silence_detection)
        self.stt_thread.status_changed.connect(self.handle_stt_status)
        self.stt_thread.start()

    def handle_stt_status(self, status: str, text: str):
        if status == "partial_stream":
            pass
        elif status == "partial":
            self.update_state_ui("listening", f"● {text.upper()}")
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
            if self.voice_active_on_start and not self.greeting_played and not self.greeting_discarded:
                self.greeting_played = True
                if self.cached_greeting:
                    self.play_greeting(self.cached_greeting)
                else:
                    self.reset_to_idle()
            else:
                self.console_output.append("<span style='color: rgba(255,255,255,0.45);'>System: No speech detected.</span>")
                self.reset_to_idle()
            return

        self.greeting_discarded = True
        self.reset_to_idle()
        self.send_query(text)

    def send_query(self, text: str):
        text = text.strip()
        if not text:
            return

        self.greeting_discarded = True
        self.is_processing_user_input = True

        self.console_output.append(f"<span style='color: #10B981;'>You:</span> {text}")
        self.session_history.append({"role": "user", "content": text})
        if len(self.session_history) > 20:
            self.session_history.pop(0)

        self.update_state_ui("thinking", "THINKING...")

        self.query_thread = DaemonQueryThread(text)
        self.query_thread.status_changed.connect(self.handle_status_change)
        self.query_thread.telemetry_received.connect(self.handle_telemetry_received)
        self.query_thread.confirm_requested.connect(self.handle_confirm_request)
        self.query_thread.screenshot_requested.connect(self.handle_screenshot_request)
        self.query_thread.query_finished.connect(self.handle_brain_response)
        self.query_thread.start()

    @Slot()
    def handle_screenshot_request(self):
        """
        Handles screenshot requests from the query thread by hiding the HUD overlay,
        capturing the desktop screenshot, and notifying the thread to resume.
        """
        # Hide HUD to capture clean user desktop screenshot
        self.hide()
        QApplication.instance().processEvents()
        
        import time
        time.sleep(0.2)
        
        success = False
        try:
            from elora.skills.os_control import capture_desktop_screenshot
            success = capture_desktop_screenshot()
        except Exception as e:
            logger.error("Failed to capture desktop screenshot on request: %s", e)
            
        self.show()
        QApplication.instance().processEvents()
        
        # Notify query thread
        self.query_thread.screenshot_success = success
        self.query_thread.screenshot_event.set()

    @Slot(str)
    def handle_status_change(self, status_text: str):
        self.console_output.append(f"<span style='color: rgba(255, 255, 255, 0.45);'>System: {status_text}</span>")

    @Slot(dict)
    def handle_telemetry_received(self, telemetry: dict):
        etype = telemetry.get("type")
        if etype == "thought":
            thought = telemetry.get("text", "")
            self.console_output.append(
                f"<div style='margin: 4px 0px; padding: 6px; background-color: rgba(129, 140, 248, 0.08); "
                f"border-left: 3px solid #818CF8; border-radius: 4px; color: #9CA3AF; font-size: 11px;'>"
                f"🧠 <i>Elora Thought: {thought}</i>"
                f"</div>"
            )
        elif etype == "tool_start":
            tool = telemetry.get("tool", "")
            args = telemetry.get("arguments", {})
            if tool == "command_run":
                self.console_output.append(
                    f"<span style='color: #FB923C;'>⚙️ Tool command_run:</span> "
                    f"Executing local shell command: <code>{args.get('command')}</code>"
                )
            elif tool == "web_search":
                self.console_output.append(
                    f"<span style='color: #60A5FA;'>🔍 Tool web_search:</span> "
                    f"Searching DuckDuckGo for: <i>\"{args.get('query')}\"</i>"
                )
            elif tool == "web_scrape":
                self.console_output.append(
                    f"<span style='color: #34D399;'>📄 Tool web_scrape:</span> "
                    f"Scraping webpage text from: <code>{args.get('url')}</code>"
                )
            elif tool.startswith("browser_"):
                self.console_output.append(
                    f"<span style='color: #A78BFA;'>🌐 Brave control ({tool}):</span> "
                    f"Args: <code>{args}</code>"
                )
            elif tool == "desktop_input":
                self.console_output.append(
                    f"<span style='color: #F472B6;'>🖱️ Desktop input ({args.get('input_type')}):</span> "
                    f"Simulating universal controls..."
                )
            elif tool == "system_control":
                self.console_output.append(
                    f"<span style='color: #FB7185;'>🎛️ System control ({args.get('control_type')}):</span> "
                    f"Adjusting parameters..."
                )
        elif etype == "tool_output":
            tool = telemetry.get("tool", "")
            output = telemetry.get("output", "")
            if output:
                import html
                escaped_output = html.escape(str(output).strip())
                if len(escaped_output) > 600:
                    escaped_output = escaped_output[:600] + "\n...[truncated for display]"
                self.console_output.append(
                    f"<div style='margin-left: 12px; color: #9CA3AF;'>"
                    f"<div style='color: #34D399; font-size: 11px; margin-bottom: 2px;'>✔️ Tool execution completed:</div>"
                    f"<pre style='font-family: monospace; font-size: 10px; background-color: rgba(0, 0, 0, 0.25); "
                    f"padding: 6px; border-radius: 4px; border: 1px solid rgba(255,255,255,0.05); white-space: pre-wrap;'>"
                    f"{escaped_output}"
                    f"</pre>"
                    f"</div>"
                )
            else:
                self.console_output.append(
                    "<span style='margin-left: 12px; color: rgba(255,255,255,0.3); font-size: 11px;'>✔️ Tool execution completed.</span>"
                )
        elif etype == "confirm_request":
            self.console_output.append(
                "<span style='color: #F87171; font-weight: bold;'>⚠️ Safety Gate: Waiting for user confirmation...</span>"
            )

    @Slot(str, dict)
    def handle_confirm_request(self, action: str, arguments: dict):
        cmd = arguments.get("command", "")
        from PySide6.QtWidgets import QMessageBox
        
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Safe Gate Confirmation")
        msg_box.setText(
            f"Elora wants to run a potentially destructive shell command:\n\n"
            f"<b>{cmd}</b>\n\n"
            f"Allow execution?"
        )
        msg_box.setIcon(QMessageBox.Icon.Warning)
        msg_box.addButton(QMessageBox.StandardButton.Yes)
        msg_box.addButton(QMessageBox.StandardButton.No)
        msg_box.setDefaultButton(QMessageBox.StandardButton.No)
        
        msg_box.setStyleSheet(
            "QMessageBox { background-color: #111111; color: #E5E7EB; border: 1px solid rgba(255,255,255,0.1); }"
            "QLabel { color: #E5E7EB; font-family: 'JetBrains Mono', 'Segoe UI'; font-size: 11px; }"
            "QPushButton { background-color: rgba(255,255,255,0.08); color: #E5E7EB; border: 1px solid rgba(255,255,255,0.15); padding: 4px 12px; font-weight: bold; }"
            "QPushButton:hover { background-color: rgba(255,255,255,0.15); }"
        )
        
        reply = msg_box.exec()
        approved = reply == QMessageBox.StandardButton.Yes
        
        if approved:
            self.console_output.append(
                "<span style='color: #34D399; font-weight: bold;'>✔️ Command execution approved by user.</span>"
            )
        else:
            self.console_output.append(
                "<span style='color: #F87171; font-weight: bold;'>❌ Command execution denied by user.</span>"
            )
            
        if self.query_thread:
            self.query_thread.confirm_decision = approved
            self.query_thread.confirm_event.set()

    @Slot(dict)
    def handle_brain_response(self, result: dict):
        self.is_processing_user_input = False
        action = result.get("action")
        args = result.get("arguments", {})
        self.reset_to_idle()

        if action == "reply":
            msg = args.get("message", "")
            self.session_history.append({"role": "assistant", "content": msg})
            if len(self.session_history) > 20:
                self.session_history.pop(0)

            self.console_output.append(f"<span style='color: #818CF8;'>Elora:</span> {msg}")
            self.on_speaking_state_changed(True)

        elif action == "news_fetch":
            mode = args.get("mode", "skim")
            if mode == "skim":
                self.console_output.append("<span style='color: #818CF8;'>Elora:</span> Fetching top headlines...")
                from elora.skills.news import get_news_summary
                summary = get_news_summary()
                self.console_output.append(f"<pre style='color: #D1D5DB;'>{summary}</pre>")
                self.load_news_skimmer()
                self.on_speaking_state_changed(True)

            elif mode == "deep_dive":
                idx = args.get("index")
                if idx is not None:
                    self.console_output.append(f"<span style='color: #818CF8;'>Elora:</span> Opening article {idx} in Brave...")
                    self.on_speaking_state_changed(True)
                else:
                    self.console_output.append("<span style='color: #EF4444;'>System: Article index missing for deep dive.</span>")

        elif action == "browser":
            url = args.get("url", "")
            if url:
                from urllib.parse import urlparse
                domain = urlparse(url).netloc or url
                self.console_output.append(f"<span style='color: #818CF8;'>Elora:</span> Opening {domain} in Brave...")
                self.on_speaking_state_changed(True)
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
                self.on_speaking_state_changed(True)
                
                session = result.get("session")
                if session:
                    self.console_output.append(f"<span style='color: #10B981;'>System: Task spawned successfully in tmux session '{session}'</span>")
                else:
                    self.console_output.append("<span style='color: #EF4444;'>System: Failed to spawn background task.</span>")

        elif action == "memory_focus":
            query = args.get("query", "")
            msg = args.get("message", f"Focusing on \"{query}\" now.")
            self.console_output.append(
                f"<span style='color: #2DD4BF;'>🧠 Focus:</span> "
                f"<span style='color: #D1D5DB;'>{msg}</span>"
            )
            self.on_speaking_state_changed(True)

        elif action == "reply":
            msg = args.get("message", "")
            self.session_history.append({"role": "assistant", "content": msg})
            if len(self.session_history) > 20:
                self.session_history.pop(0)
            self.console_output.append(f"<span style='color: #818CF8;'>Elora:</span> {msg}")
            self.on_speaking_state_changed(True)

    def reset_to_idle(self):
        self.update_state_ui("idle", "[ HOLD ALT TO TALK ]")
    def trigger_startup_greeting(self, quiet: bool = False):
        """
        Determines the startup behavior asynchronously:
        - If there is an active running background task, updates the user with its status and latest logs.
        - Otherwise, greets the user with a fresh greeting and clears historical context.
        """
        # Why: Bypasses the startup greeting/report if the user is actively pressing the Alt button,
        # if voice recording is currently active, or if user interaction has discarded the greeting.
        alt_pressed = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.AltModifier)
        if alt_pressed or self.is_recording or self.greeting_discarded:
            if hasattr(self, "startup_greeting_timer") and self.startup_greeting_timer.isActive():
                self.startup_greeting_timer.stop()
            self.greeting_discarded = True
            return

        if not quiet:
            self.update_state_ui("thinking", "INITIALIZING...")
            self.console_output.clear()
            self.console_output.append("<span style='color: #818CF8;'>Elora:</span> Initializing cognitive modules...")
        
        self.startup_thread = StartupGreetingThread(self.config)
        self.startup_thread.greeting_finished.connect(self.handle_startup_greeting_finished)
        self.startup_thread.finished.connect(self.startup_thread.deleteLater)
        self.startup_thread.start()

    def handle_startup_greeting_finished(self, result: dict):
        if self.greeting_discarded:
            return
        self.play_greeting(result)

    def play_greeting(self, result: dict):
        """Displays and speaks the startup greeting or background tasks status update."""
        gtype = result.get("type")
        if gtype == "active_tasks":
            update_text = result.get("update_text", "")
            self.session_history = [{"role": "assistant", "content": update_text}]
            self.console_output.clear()
            self.console_output.append(f"<span style='color: #818CF8;'>Elora:</span> {update_text}")
            self.update_state_ui("thinking", "SYNTHESIZING...")

            import threading
            def speak_update_bg():
                try:
                    from elora.ipc.daemon_client import EloraDaemonClient
                    c = EloraDaemonClient()
                    c.send_cmd({"cmd": "reset_history"})
                    c.send_cmd({
                        "cmd": "add_history",
                        "role": "assistant",
                        "content": json.dumps({"action": "reply", "arguments": {"message": update_text}})
                    })
                    c.send_cmd({"cmd": "speak", "text": update_text})
                    self.speaking_state_signal.emit(True)
                except Exception as bg_err:
                    logger.error("Failed to speak startup update: %s", bg_err)
                    self.speaking_state_signal.emit(False)

            threading.Thread(target=speak_update_bg, daemon=True).start()
            
        else: # fresh_greeting
            local_greeting = result.get("greeting", "")
            self.session_history = [{"role": "assistant", "content": local_greeting}]
            self.console_output.clear()
            self.console_output.append(f"<span style='color: #818CF8;'>Elora:</span> {local_greeting}")
            self.update_state_ui("thinking", "SYNTHESIZING...")

            import threading
            def play_greeting_bg():
                try:
                    from elora.ipc.daemon_client import EloraDaemonClient
                    c = EloraDaemonClient()
                    c.send_cmd({"cmd": "reset_history"})
                    c.send_cmd({
                        "cmd": "add_history",
                        "role": "assistant",
                        "content": json.dumps({"action": "reply", "arguments": {"message": local_greeting}})
                    })
                    c.send_cmd({"cmd": "speak", "text": local_greeting})
                    self.speaking_state_signal.emit(True)
                except Exception as bg_err:
                    logger.error("Failed to play startup greeting in background thread: %s", bg_err)
                    self.speaking_state_signal.emit(False)

            threading.Thread(target=play_greeting_bg, daemon=True).start()


    def load_news_skimmer(self):
        self.news_list.clear()
        self.news_list.addItem("Loading news feeds...")
        try:
            from elora.core.config import load_config
            config = load_config()
            feeds = config.get("news", {}).get("feeds", [])
            
            self.news_thread = NewsFetchThread(feeds)
            self.news_thread.feeds_fetched.connect(self.populate_news_skimmer)
            self.news_thread.start()
        except Exception as e:
            logger.error("Failed to start news skimmer thread: %s", e)
            self.news_list.clear()
            self.news_list.addItem("Failed to load news feeds.")

    def populate_news_skimmer(self, items):
        self.news_list.clear()
        if not items:
            self.news_list.addItem("No news articles found.")
            return
            
        for count, (title, feed_title, link) in enumerate(items, 1):
            item_text = f"[{count}] {title} ({feed_title[:12]})"
            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, link)
            self.news_list.addItem(item)

    def on_news_clicked(self, item: QListWidgetItem):
        link = item.data(Qt.ItemDataRole.UserRole)
        if link:
            self.console_output.append("<span style='color: #10B981;'>System:</span> Opening link in web browser...")
            from elora.skills.actions import open_browser_url
            open_browser_url(link)

    def _start_background_thread(self, thread):
        """
        Safely registers and starts a QThread background worker.
        
        Why: Prevents PySide6 crashes/segmentation faults by ensuring
        active threads are not garbage-collected while executing.
        """
        if not hasattr(self, "_active_threads"):
            self._active_threads = set()
        self._active_threads.add(thread)
        
        def cleanup():
            self._active_threads.discard(thread)
            # Safely clear references on self if they point to the finished thread
            for attr in ["task_list_thread", "task_log_thread", "task_cancel_thread"]:
                if getattr(self, attr, None) is thread:
                    setattr(self, attr, None)
            
        thread.finished.connect(cleanup)
        thread.finished.connect(thread.deleteLater)
        thread.start()
        return thread

    def refresh_tasks_list(self):
        """Queries the daemon for active tmux tasks asynchronously."""
        if hasattr(self, "task_list_thread") and self.task_list_thread:
            try:
                self.task_list_thread.tasks_fetched.disconnect()
            except Exception:
                pass
        
        self.task_list_thread = TaskListFetchThread()
        self.task_list_thread.tasks_fetched.connect(self.on_tasks_fetched)
        self._start_background_thread(self.task_list_thread)

    def on_tasks_fetched(self, res: dict):
        self.tasks_list_widget.clear()
        if res.get("status") == "tasks_list":
            tasks = res.get("tasks", [])
            if not tasks:
                self.tasks_list_widget.addItem("No active background tasks.")
                self.txt_task_log.clear()
                self.btn_cancel_task.setEnabled(False)
                self.btn_clear_task.setEnabled(False)
            else:
                for task in tasks:
                    session = task.get("session")
                    prompt = task.get("prompt", "")
                    started_at = task.get("started_at", 0.0)
                    status = task.get("status", "running")
                    
                    import time
                    elapsed = ""
                    if started_at > 0:
                        sec = int(time.time() - started_at)
                        if sec < 60:
                            elapsed = f"{sec}s ago"
                        else:
                            elapsed = f"{sec//60}m {sec%60}s ago"
                    
                    status_prefix = f"[{status.capitalize()}] "
                    if status == "running":
                        item = QListWidgetItem(f"{status_prefix}{session} ({elapsed})\n↳ {prompt[:60]}...")
                    else:
                        item = QListWidgetItem(f"{status_prefix}{session} (started {elapsed})\n↳ {prompt[:60]}...")
                    item.setData(Qt.ItemDataRole.UserRole, task)
                    
                    # Style item according to task status
                    from PySide6.QtGui import QColor
                    if status == "running":
                        item.setForeground(QColor("#60A5FA")) # Sleek light blue
                    elif status == "completed":
                        item.setForeground(QColor("#34D399")) # Sleek green
                    elif status == "failed":
                        item.setForeground(QColor("#F87171")) # Sleek red
                    elif status == "cancelled":
                        item.setForeground(QColor("#9CA3AF")) # Sleek gray
                        
                    self.tasks_list_widget.addItem(item)
                
                # Automatically select the first item and trigger selection check
                self.tasks_list_widget.setCurrentRow(0)
                self.on_task_selection_changed()
        else:
            self.tasks_list_widget.addItem(f"Error: {res.get('message', 'Failed to connect to daemon.')}")
            self.btn_cancel_task.setEnabled(False)
            self.btn_clear_task.setEnabled(False)

    def on_task_selection_changed(self):
        """Called when a task is selected in the list widget. Fetches log immediately."""
        selected_items = self.tasks_list_widget.selectedItems()
        if selected_items:
            item = selected_items[0]
            task = item.data(Qt.ItemDataRole.UserRole)
            if task:
                # Enable Cancel button only if the task is currently running
                is_running = task.get("status") == "running"
                self.btn_cancel_task.setEnabled(is_running)
                self.btn_clear_task.setEnabled(not is_running)
                self.txt_tmux_input.setEnabled(is_running)
                self.btn_tmux_send.setEnabled(is_running)
                self.btn_tmux_attach.setEnabled(is_running)
            else:
                self.btn_cancel_task.setEnabled(False)
                self.btn_clear_task.setEnabled(False)
                self.txt_tmux_input.setEnabled(False)
                self.btn_tmux_send.setEnabled(False)
                self.btn_tmux_attach.setEnabled(False)
        else:
            self.btn_cancel_task.setEnabled(False)
            self.btn_clear_task.setEnabled(False)
            self.txt_tmux_input.setEnabled(False)
            self.btn_tmux_send.setEnabled(False)
            self.btn_tmux_attach.setEnabled(False)
        self.update_task_log_view()

    def update_task_log_view(self):
        """Fetches the latest pane text from the daemon for the selected task asynchronously."""
        selected_items = self.tasks_list_widget.selectedItems()
        if not selected_items:
            return
        
        item = selected_items[0]
        task = item.data(Qt.ItemDataRole.UserRole)
        if not task:
            self.txt_task_log.clear()
            return
            
        session = task.get("session")
        
        if hasattr(self, "task_log_thread") and self.task_log_thread:
            # Prevent starting duplicate request thread for the same session
            try:
                if self.task_log_thread.isRunning() and getattr(self.task_log_thread, "session", None) == session:
                    return
            except RuntimeError:
                # C++ object already deleted, clear python wrapper reference
                self.task_log_thread = None

            if self.task_log_thread:
                try:
                    self.task_log_thread.log_fetched.disconnect()
                except Exception:
                    pass
                
        self.task_log_thread = TaskLogFetchThread(session)
        self.task_log_thread.log_fetched.connect(self.on_task_log_fetched)
        self._start_background_thread(self.task_log_thread)

    def on_task_log_fetched(self, res: dict):
        if res.get("status") == "task_log":
            # Only update the log text browser if the fetched log matches the currently selected session
            selected_items = self.tasks_list_widget.selectedItems()
            if selected_items:
                item = selected_items[0]
                task = item.data(Qt.ItemDataRole.UserRole)
                if task and task.get("session") == res.get("session"):
                    raw_log = res.get("log", "")
                    from elora.skills.skills import strip_ansi_codes
                    cleaned_log = strip_ansi_codes(raw_log)
                    scrollbar = self.txt_task_log.verticalScrollBar()
                    at_bottom = scrollbar.value() >= scrollbar.maximum() - 10
                    
                    self.txt_task_log.setPlainText(cleaned_log)
                    
                    # Highlight input text box if log indicates it is waiting for stdin
                    lines = [l.strip() for l in cleaned_log.splitlines() if l.strip()]
                    waiting_for_input = False
                    if lines:
                        last_line = lines[-1].lower()
                        input_indicators = ["[y/n]", "[y/n]:", "enter your", "password:", "enter:", "confirm?", "choice", "accept?", "enter credentials"]
                        if any(ind in last_line for ind in input_indicators) or (last_line.endswith("?") and not last_line.startswith("why")):
                            waiting_for_input = True
                            
                    session_name = res.get("session", "unknown")
                    if waiting_for_input:
                        self.txt_tmux_input.setStyleSheet(
                            "QLineEdit { background-color: rgba(245, 158, 11, 0.08); color: #FBBF24; border: 1px solid #F59E0B; border-radius: 4px; padding: 5px; font-family: 'JetBrains Mono'; font-size: 10px; }"
                        )
                        self.txt_tmux_input.setPlaceholderText("Task is waiting for input! Type response here...")
                        
                        # Verbally speak alert once when the session first enters waiting state
                        if session_name not in self.notified_waiting_sessions:
                            self.notified_waiting_sessions.add(session_name)
                            try:
                                from elora.ipc.daemon_client import EloraDaemonClient
                                EloraDaemonClient().send_cmd({
                                    "cmd": "speak",
                                    "text": f"Sir, the background task {session_name} is waiting for your input."
                                })
                            except Exception as e:
                                logger.error("Failed to trigger spoken alert for waiting task: %s", e)
                    else:
                        self.txt_tmux_input.setStyleSheet(
                            "QLineEdit { background-color: rgba(255, 255, 255, 0.05); color: #E5E7EB; border: 1px solid rgba(255,255,255,0.1); border-radius: 4px; padding: 5px; font-family: 'JetBrains Mono'; font-size: 10px; }"
                        )
                        self.txt_tmux_input.setPlaceholderText("Send input / keystrokes to active agent...")
                        
                        # Clear from notified set if it is no longer blocked on input
                        if session_name in self.notified_waiting_sessions:
                            self.notified_waiting_sessions.discard(session_name)
                    
                    if at_bottom:
                        scrollbar.setValue(scrollbar.maximum())
        else:
            # Log failed or task not active anymore
            pass

    def send_tmux_input(self):
        """Sends custom keystrokes directly to the background tmux session."""
        selected_row = self.tasks_list_widget.currentRow()
        if selected_row < 0:
            return
            
        selected_item = self.tasks_list_widget.currentItem()
        if not selected_item:
            return
            
        text = selected_item.text()
        if " [RUNNING]" not in text:
            return
            
        session_name = text.split(" [RUNNING]")[0].strip()
        keystrokes = self.txt_tmux_input.text()
        if not keystrokes:
            return
            
        self.txt_tmux_input.clear()
        
        try:
            subprocess.run(["tmux", "send-keys", "-t", session_name, keystrokes, "C-m"], check=True)
            self.console_output.append(f"<span style='color: rgba(16, 185, 129, 0.85);'>System:</span> Sent input to tmux: '{keystrokes}'")
        except Exception as e:
            logger.error("Failed to send tmux keys: %s", e)
            self.console_output.append(f"<span style='color: rgba(239, 68, 68, 0.85);'>System Error:</span> Failed to send keystrokes: {e}")

    def attach_tmux_terminal(self):
        """Spawns a local desktop terminal emulator attached to the selected tmux session."""
        selected_row = self.tasks_list_widget.currentRow()
        if selected_row < 0:
            return
            
        selected_item = self.tasks_list_widget.currentItem()
        if not selected_item:
            return
            
        text = selected_item.text()
        if " [RUNNING]" not in text:
            return
            
        session_name = text.split(" [RUNNING]")[0].strip()
        
        terminal_emulators = [
            ["x-terminal-emulator", "-e"],
            ["gnome-terminal", "--", "tmux", "attach-session", "-t"],
            ["konsole", "-e", "tmux", "attach-session", "-t"],
            ["kitty", "tmux", "attach-session", "-t"],
            ["alacritty", "-e", "tmux", "attach-session", "-t"],
            ["xfce4-terminal", "-e"],
            ["xterm", "-e"]
        ]
        
        spawned = False
        import shutil
        for term in terminal_emulators:
            exe = term[0]
            if shutil.which(exe):
                if exe in ("gnome-terminal", "konsole", "kitty", "alacritty"):
                    cmd = term + [session_name]
                else:
                    cmd = [exe, "-e", f"tmux attach-session -t {session_name}"]
                
                try:
                    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    spawned = True
                    self.console_output.append(f"<span style='color: rgba(99, 102, 241, 0.85);'>System:</span> Spawning terminal '{exe}' attached to session '{session_name}'")
                    break
                except Exception as e:
                    logger.debug("Failed to spawn %s: %s", exe, e)
                    
        if not spawned:
            self.console_output.append("<span style='color: rgba(239, 68, 68, 0.85);'>System Error:</span> No supported terminal emulator was found on the system. Attach manually by running: <code>tmux attach-session -t " + session_name + "</code>")

    def cancel_selected_task(self):
        """Sends cancel command to the daemon for the selected task."""
        selected_items = self.tasks_list_widget.selectedItems()
        if not selected_items:
            return
            
        item = selected_items[0]
        task = item.data(Qt.ItemDataRole.UserRole)
        if not task:
            return
            
        session = task.get("session")
        
        from PySide6.QtWidgets import QMessageBox
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Cancel Background Task")
        msg_box.setText(f"Are you sure you want to cancel task '{session}'?")
        msg_box.setInformativeText(f"Prompt: {task.get('prompt')}")
        msg_box.setIcon(QMessageBox.Icon.Question)
        msg_box.addButton(QMessageBox.StandardButton.Yes)
        msg_box.addButton(QMessageBox.StandardButton.No)
        msg_box.setDefaultButton(QMessageBox.StandardButton.No)
        
        msg_box.setStyleSheet(
            "QMessageBox { background-color: #111111; color: #E5E7EB; border: 1px solid rgba(255,255,255,0.1); }"
            "QLabel { color: #E5E7EB; font-family: 'JetBrains Mono', 'Segoe UI'; font-size: 11px; }"
            "QPushButton { background-color: rgba(255,255,255,0.08); color: #E5E7EB; border: 1px solid rgba(255,255,255,0.15); padding: 4px 12px; font-weight: bold; }"
            "QPushButton:hover { background-color: rgba(255,255,255,0.15); }"
        )
        
        reply = msg_box.exec()
        if reply != QMessageBox.StandardButton.Yes:
            return
            
        self.btn_cancel_task.setEnabled(False)
        
        if hasattr(self, "task_cancel_thread") and self.task_cancel_thread:
            try:
                self.task_cancel_thread.task_cancelled.disconnect()
            except Exception:
                pass
                
        self.task_cancel_thread = TaskCancelThread(session)
        self.task_cancel_thread.task_cancelled.connect(self.on_task_cancelled)
        self._start_background_thread(self.task_cancel_thread)

    def on_task_cancelled(self, res: dict):
        self.btn_cancel_task.setEnabled(True)
        session = res.get("session", "Unknown")
        if res.get("status") == "task_cancelled" and res.get("success"):
            self.console_output.append(f"<span style='color: #EF4444;'>System: Cancelled background task '{session}'</span>")
            from elora.utils import play_chime
            play_chime()
            self.refresh_tasks_list()
        else:
            self.console_output.append(f"<span style='color: #EF4444;'>System: Failed to cancel task '{session}'</span>")

    def clear_selected_task(self):
        """Sends remove command to the daemon for the selected task."""
        selected_items = self.tasks_list_widget.selectedItems()
        if not selected_items:
            return
            
        item = selected_items[0]
        task = item.data(Qt.ItemDataRole.UserRole)
        if not task:
            return
            
        session = task.get("session")
        
        self.btn_clear_task.setEnabled(False)
        
        if hasattr(self, "task_clear_thread") and self.task_clear_thread:
            try:
                self.task_clear_thread.task_removed.disconnect()
            except Exception:
                pass
                
        self.task_clear_thread = TaskRemoveThread(session)
        self.task_clear_thread.task_removed.connect(self.on_task_removed)
        self._start_background_thread(self.task_clear_thread)

    def on_task_removed(self, res: dict):
        self.btn_clear_task.setEnabled(True)
        session = res.get("session", "Unknown")
        if res.get("status") == "task_removed" and res.get("success"):
            self.console_output.append(f"<span style='color: #34D399;'>System: Removed task '{session}' from history.</span>")
            self.refresh_tasks_list()
        else:
            self.console_output.append(f"<span style='color: #F87171;'>System: Failed to remove task '{session}'.</span>")

    def update_tasks_periodically(self):
        """Refreshes active tasks list and selected log without losing selection state (asynchronously)."""
        try:
            if hasattr(self, "task_list_thread") and self.task_list_thread and self.task_list_thread.isRunning():
                return
        except RuntimeError:
            # C++ object already deleted, clear python wrapper reference
            self.task_list_thread = None
            
        self.task_list_thread = TaskListFetchThread()
        self.task_list_thread.tasks_fetched.connect(self.on_periodic_tasks_fetched)
        self._start_background_thread(self.task_list_thread)

    def on_periodic_tasks_fetched(self, res: dict):
        """
        Processes periodic task list updates from the daemon.
        
        Why: Rebuilds the UI task list if tasks changed or count mismatched.
        Handles empty task list cases defensively to prevent IndexError.
        """
        if res.get("status") == "tasks_list":
            tasks = res.get("tasks", [])
            
            # Handle empty tasks list case defensively
            if not tasks:
                has_placeholder = (
                    self.tasks_list_widget.count() == 1 and
                    self.tasks_list_widget.item(0).data(Qt.ItemDataRole.UserRole) is None
                )
                if not has_placeholder:
                    self.tasks_list_widget.clear()
                    self.tasks_list_widget.addItem("No active background tasks.")
                    self.txt_task_log.clear()
                    self.btn_cancel_task.setEnabled(False)
                    self.btn_clear_task.setEnabled(False)
                self.on_task_selection_changed()
                return

            selected_row = self.tasks_list_widget.currentRow()
            
            current_sessions = []
            for i in range(self.tasks_list_widget.count()):
                item = self.tasks_list_widget.item(i)
                task_data = item.data(Qt.ItemDataRole.UserRole)
                if task_data:
                    current_sessions.append(task_data.get("session"))
            
            new_sessions = [t.get("session") for t in tasks]
            
            # Rebuild list if session names changed or list widget item count differs from task count
            if current_sessions != new_sessions or self.tasks_list_widget.count() != len(tasks):
                self.tasks_list_widget.clear()
                for task in tasks:
                    session = task.get("session")
                    prompt = task.get("prompt", "")
                    started_at = task.get("started_at", 0.0)
                    status = task.get("status", "running")
                    
                    import time
                    elapsed = ""
                    if started_at > 0:
                        sec = int(time.time() - started_at)
                        if sec < 60:
                            elapsed = f"{sec}s ago"
                        else:
                            elapsed = f"{sec//60}m {sec%60}s ago"
                    
                    status_prefix = f"[{status.capitalize()}] "
                    if status == "running":
                        item = QListWidgetItem(f"{status_prefix}{session} ({elapsed})\n↳ {prompt[:60]}...")
                    else:
                        item = QListWidgetItem(f"{status_prefix}{session} (started {elapsed})\n↳ {prompt[:60]}...")
                    item.setData(Qt.ItemDataRole.UserRole, task)
                    
                    # Style based on status
                    from PySide6.QtGui import QColor
                    if status == "running":
                        item.setForeground(QColor("#60A5FA"))
                    elif status == "completed":
                        item.setForeground(QColor("#34D399"))
                    elif status == "failed":
                        item.setForeground(QColor("#F87171"))
                    elif status == "cancelled":
                        item.setForeground(QColor("#9CA3AF"))
                        
                    self.tasks_list_widget.addItem(item)
                
                if selected_row >= 0 and selected_row < self.tasks_list_widget.count():
                    self.tasks_list_widget.setCurrentRow(selected_row)
                else:
                    self.tasks_list_widget.setCurrentRow(0)
            else:
                for i in range(self.tasks_list_widget.count()):
                    item = self.tasks_list_widget.item(i)
                    task = tasks[i]
                    session = task.get("session")
                    prompt = task.get("prompt", "")
                    started_at = task.get("started_at", 0.0)
                    status = task.get("status", "running")
                    
                    import time
                    elapsed = ""
                    if started_at > 0:
                        sec = int(time.time() - started_at)
                        if sec < 60:
                            elapsed = f"{sec}s ago"
                        else:
                            elapsed = f"{sec//60}m {sec%60}s ago"
                            
                    status_prefix = f"[{status.capitalize()}] "
                    if status == "running":
                        item.setText(f"{status_prefix}{session} ({elapsed})\n↳ {prompt[:60]}...")
                    else:
                        item.setText(f"{status_prefix}{session} (started {elapsed})\n↳ {prompt[:60]}...")
                    item.setData(Qt.ItemDataRole.UserRole, task)
                    
                    # Style based on status
                    from PySide6.QtGui import QColor
                    if status == "running":
                        item.setForeground(QColor("#60A5FA"))
                    elif status == "completed":
                        item.setForeground(QColor("#34D399"))
                    elif status == "failed":
                        item.setForeground(QColor("#F87171"))
                    elif status == "cancelled":
                        item.setForeground(QColor("#9CA3AF"))
            
            # Ensure cancel button state is updated based on active selection
            self.on_task_selection_changed()


_hud_lock_socket = None


def prevent_multiple_instances() -> bool:
    """Uses a Linux abstract namespace socket to guarantee a single HUD instance."""
    global _hud_lock_socket
    import socket
    try:
        _hud_lock_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        _hud_lock_socket.bind('\0elora_hud_instance_lock')
        return True
    except OSError:
        return False


def start_hud():
    if not prevent_multiple_instances():
        from elora.utils import send_notification
        send_notification("Elora HUD", "Elora HUD is already running.")
        print("Elora HUD is already running. Exiting.")
        sys.exit(0)

    # Check for --voice or -v flags and filter them out to prevent Qt warnings
    voice_active = "--voice" in sys.argv or "-v" in sys.argv
    clean_argv = [arg for arg in sys.argv if arg not in ("--voice", "-v")]

    app = QApplication(clean_argv)
    hud = EloraHUD(voice_active=voice_active)
    hud.show()
    sys.exit(app.exec())
