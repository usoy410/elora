"""
Elora GUI HUD (Heads-Up Display).
Presents a transparent frameless overlay containing an animated pulsing AI orb,
glassmorphism telemetry cards, and a text input prompt.
Uses PySide6 QThread to ensure non-blocking LLM execution.
"""

import sys
import json
import math
from PySide6.QtCore import Qt, QThread, Signal, Slot, QTimer, QPoint, QRectF
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
    QLineEdit, QLabel, QGraphicsDropShadowEffect
)
from PySide6.QtGui import QPainter, QColor, QRadialGradient, QPen, QFont, QPainterPath

from elora.brain import query_elora
from elora.actions import execute_agent_task, open_browser_url
from elora.news import get_news_summary, open_article


class WorkerThread(QThread):
    """
    Background worker thread to run Ollama and local queries.
    
    Why: Keeps the PySide6 main event loop completely responsive and fluid
    while waiting for network or cloud reasoning model replies.
    """
    # Signal emitted when the query completes, carrying the parsed JSON action block
    query_finished = Signal(dict)
    
    def __init__(self, prompt: str):
        super().__init__()
        self.prompt = prompt
        
    def run(self):
        # Trigger the main query_elora LLM parser
        result = query_elora(self.prompt)
        self.query_finished.emit(result)


class OrbWidget(QWidget):
    """
    Custom painting widget representing Elora's core entity orb.
    
    Why: Custom QPainter rendering allows us to draw smooth, vector-based
    radial gradients and breathing pulses at 60 FPS without high asset costs.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(120, 120)
        self.state = "idle" # States: idle, listening, processing
        self.animation_phase = 0.0
        
        # Frame timer to update pulse animation phase (~60 FPS)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_animation)
        self.timer.start(16)
        
    def set_state(self, state: str):
        """Sets the visual state of the orb to update colors and pulse speed."""
        self.state = state
        self.update()
        
    def update_animation(self):
        # Update animation phase depending on state speed
        if self.state == "listening":
            self.animation_phase += 0.08
        elif self.state == "processing":
            self.animation_phase += 0.15
        else:
            self.animation_phase += 0.03 # Slow breath for idle
            
        self.update() # Triggers paintEvent
        
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        width = self.width()
        height = self.height()
        center_x = width / 2
        center_y = height / 2
        
        # Calculate dynamic size using sine wave for breathing effect
        base_radius = 40.0
        pulse_amplitude = 6.0
        
        if self.state == "listening":
            pulse_amplitude = 12.0
            
        pulse = math.sin(self.animation_phase) * pulse_amplitude
        radius = base_radius + pulse
        
        # Determine orb color profile based on state
        if self.state == "listening":
            # Pulsing pink/purple glow
            glow_color = QColor(219, 39, 119, 180) # Saturation < 80%
            center_color = QColor(244, 63, 94, 255)
        elif self.state == "processing":
            # Pulsing blue/indigo glow
            glow_color = QColor(79, 70, 229, 180)
            center_color = QColor(99, 102, 241, 255)
        else:
            # Idle breathing silver/neutral glow
            glow_color = QColor(156, 163, 175, 80)
            center_color = QColor(209, 213, 219, 200)
            
        # Draw soft outer glow using a radial gradient
        glow_grad = QRadialGradient(center_x, center_y, radius * 1.5)
        glow_grad.setColorAt(0.0, glow_color)
        glow_grad.setColorAt(0.8, QColor(glow_color.red(), glow_color.green(), glow_color.blue(), 20))
        glow_grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        
        painter.setBrush(glow_grad)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(center_x - radius * 1.5, center_y - radius * 1.5, radius * 3.0, radius * 3.0)
        
        # Draw solid inner core
        core_grad = QRadialGradient(center_x, center_y, radius)
        core_grad.setColorAt(0.0, center_color)
        core_grad.setColorAt(1.0, QColor(center_color.red() - 20, center_color.green() - 20, center_color.blue() - 20, 255))
        
        painter.setBrush(core_grad)
        painter.drawEllipse(center_x - radius, center_y - radius, radius * 2.0, radius * 2.0)


class EloraGUI(QWidget):
    """
    Main transparent frameless HUD window for Elora.
    
    Why: Transparent backgrounds, glassmorphism panel stylings, and centered
    alignments compose a premium, seamless OS-integrated console overlay.
    """
    def __init__(self):
        super().__init__()
        
        # Configure window traits:
        # - FramelessWindowHint: removes Linux window headers/decoration.
        # - WindowStaysOnTopHint: locks the assistant overlay above active workspaces.
        # - SubWindow: prevents taskbar listing if desired (optional).
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        
        # WA_TranslucentBackground makes regions without stylesheets fully transparent
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(750, 480)
        
        # Global UI Layout Setup
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(15, 15, 15, 15)
        
        # Glassmorphism Container Widget (Simulates physical frosted glass panel)
        self.glass_panel = QWidget(self)
        self.glass_panel.setObjectName("GlassPanel")
        # Sleek dark translucent base with a light 1px border outline for depth
        self.glass_panel.setStyleSheet("""
            QWidget#GlassPanel {
                background-color: rgba(15, 16, 26, 0.85); /* Tinted off-black */
                border: 1px solid rgba(255, 255, 255, 0.12); /* Subtle highlight border */
                border-radius: 20px;
            }
        """)
        
        # Drop shadow for elevation styling
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(25)
        shadow.setColor(QColor(0, 0, 0, 180))
        shadow.setOffset(0, 8)
        self.glass_panel.setGraphicsEffect(shadow)
        
        self.panel_layout = QVBoxLayout(self.glass_panel)
        self.panel_layout.setContentsMargins(25, 25, 25, 25)
        
        # 1. Header (Dynamic state indicator)
        self.state_label = QLabel("SYSTEM STANDBY", self)
        self.state_label.setStyleSheet("color: rgba(255, 255, 255, 0.4); font-family: 'JetBrains Mono'; font-size: 11px; font-weight: bold; letter-spacing: 2px;")
        self.panel_layout.addWidget(self.state_label, alignment=Qt.AlignmentFlag.AlignLeft)
        
        # 2. Centered Animated Core
        self.orb = OrbWidget(self)
        self.panel_layout.addWidget(self.orb, alignment=Qt.AlignmentFlag.AlignCenter)
        
        # 3. Telemetry Output Area
        self.output_label = QLabel("Welcome to Elora. Type your instruction below to begin.", self)
        self.output_label.setWordWrap(True)
        self.output_label.setStyleSheet("color: #E5E7EB; font-family: 'Inter', 'Segoe UI', sans-serif; font-size: 15px; line-height: 22px;")
        self.panel_layout.addWidget(self.output_label, alignment=Qt.AlignmentFlag.AlignVCenter)
        self.panel_layout.addStretch()
        
        # 4. Glassmorphism Input Field
        self.input_field = QLineEdit(self)
        self.input_field.setPlaceholderText("Ask Elora anything (e.g. 'Fetch news', 'Delegate task')...")
        self.input_field.setStyleSheet("""
            QLineEdit {
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 10px;
                padding: 12px 16px;
                color: #FFFFFF;
                font-size: 14px;
                font-family: 'Inter', sans-serif;
            }
            QLineEdit:focus {
                border: 1px solid rgba(99, 102, 241, 0.6); /* Violet accent border */
                background-color: rgba(255, 255, 255, 0.08);
            }
        """)
        self.input_field.returnPressed.connect(self.submit_prompt)
        self.panel_layout.addWidget(self.input_field)
        
        self.main_layout.addWidget(self.glass_panel)
        
        # Background worker pointer
        self.worker = None
        
        # Center the window on the active monitor screen
        self.center_on_screen()
        
    def center_on_screen(self):
        screen = QApplication.primaryScreen().geometry()
        size = self.geometry()
        self.move((screen.width() - size.width()) / 2, (screen.height() - size.height()) / 2)
        
    def keyPressEvent(self, event):
        # Escape key closes the HUD overlay instantly
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)
            
    def submit_prompt(self):
        """Sends the line input to the background querying thread."""
        prompt = self.input_field.text().strip()
        if not prompt:
            return
            
        self.input_field.clear()
        self.input_field.setEnabled(False)
        
        # Update UI state to processing
        self.state_label.setText("THINKING")
        self.orb.set_state("processing")
        self.output_label.setText(f"Querying: \"{prompt}\"")
        
        # Start non-blocking QThread
        self.worker = WorkerThread(prompt)
        self.worker.query_finished.connect(self.handle_response)
        self.worker.start()
        
    @Slot(dict)
    def handle_response(self, result: dict):
        """Processes the parsed JSON action block returned from the background worker."""
        self.input_field.setEnabled(True)
        self.input_field.setFocus()
        
        action = result.get("action")
        args = result.get("arguments", {})
        
        # Reset state to idle
        self.state_label.setText("SYSTEM STANDBY")
        self.orb.set_state("idle")
        
        if action == "reply":
            msg = args.get("message", "")
            self.output_label.setText(msg)
            
        elif action == "news_fetch":
            mode = args.get("mode", "skim")
            if mode == "skim":
                self.output_label.setText("Elora: Aggregating technical news feeds...")
                summary = get_news_summary()
                self.output_label.setText(summary)
            elif mode == "deep_dive":
                idx = args.get("index")
                if idx is not None:
                    self.output_label.setText(f"Elora: Opening article index {idx} in your browser...")
                    success = open_article(int(idx))
                    if success:
                        self.output_label.setText(f"Elora: Launched article {idx} successfully.")
                    else:
                        self.output_label.setText("Elora: Failed to open article. Please check the index number.")
                else:
                    self.output_label.setText("Elora: Article index missing.")
                    
        elif action == "browser":
            url = args.get("url", "")
            if url:
                self.output_label.setText(f"Elora: Opening website {url}...")
                success = open_browser_url(url)
                if success:
                    self.output_label.setText(f"Elora: Browser opened URL: {url}")
                else:
                    self.output_label.setText("Elora: Failed to open browser.")
            else:
                self.output_label.setText("Elora: URL missing.")
                
        elif action == "antigravity":
            task_prompt = args.get("prompt", "")
            if task_prompt:
                self.output_label.setText("Elora: Spawning background agent...")
                session = execute_agent_task(task_prompt)
                if session:
                    self.output_label.setText(
                        f"Elora: Spelled task in background tmux session '{session}'.\n"
                        f"Task: \"{task_prompt}\"\n"
                        f"Attach using: tmux attach -t {session}"
                    )
                else:
                    self.output_label.setText("Elora: Spawning background session failed.")
            else:
                self.output_label.setText("Elora: Task prompt missing.")
                
        else:
            self.output_label.setText(f"Elora: Unknown action payload parsed: {action}")


def start_gui():
    """Runs the main Qt event loop."""
    app = QApplication(sys.argv)
    overlay = EloraGUI()
    overlay.show()
    sys.exit(app.exec())
