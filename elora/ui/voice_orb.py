"""
Speech-animated glowing vector orb component for Elora HUD.
Loads transparent pre-processed video frames for fluid state animations,
with a dynamic vector canvas fallback.
"""

import logging
import math
import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QRadialGradient
from PySide6.QtWidgets import QWidget

from elora.utils import ensure_processed_video_frames

# Configure logging for the voice orb module
logger = logging.getLogger("elora.ui.voice_orb")



class OrbWidget(QWidget):
    """
    Vector canvas or pre-rendered video frame player rendering the AI Core.
    
    Why: Handles real-time system state feedback (idle, speaking, listening, thinking)
    using lightweight visual assets with zero GPU overhead.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(180, 180)
        self.state = "idle"
        self.phase = 0.0
        self.frame_index = 0
        self.use_video = False
        
        self.idle_frames = []
        self.speaking_frames = []

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick_animation)
        self.load_frames()

    def load_frames(self) -> None:
        """
        Attempts to pre-cache extracted transparent video frames. Falls back
        to procedural vector graphics if files or ffmpeg are missing.
        """
        try:
            ensure_processed_video_frames()
            
            base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
            processed_idle_dir = os.path.join(base_dir, "assets", "videos", "processed", "idle")
            processed_speaking_dir = os.path.join(base_dir, "assets", "videos", "processed", "speaking")

            self.idle_frames = [
                img for i in range(1, 9)
                if not (img := QImage(os.path.join(processed_idle_dir, f"frame_{i:03d}.png"))).isNull()
            ]
            self.speaking_frames = [
                img for i in range(1, 9)
                if not (img := QImage(os.path.join(processed_speaking_dir, f"frame_{i:03d}.png"))).isNull()
            ]

            if len(self.idle_frames) == 8 and len(self.speaking_frames) == 8:
                self.use_video = True
                self.timer.start(83)  # 12 FPS matches the original video source framerate
                logger.info("Successfully loaded video-based voice orb frames.")
            else:
                self.use_video = False
                self.timer.start(16)  # 60 FPS for smooth procedural vector rendering
                logger.info("Falling back to vector-based voice orb rendering.")
        except Exception as e:
            logger.error("Failed to load voice orb video frames: %s", e)
            self.use_video = False
            self.timer.start(16)

    def set_state(self, state: str) -> None:
        """
        Updates the internal state of the voice orb and resets frame tracking.
        """
        if self.state != state:
            self.state = state
            self.frame_index = 0
        self.update()

    def tick_animation(self) -> None:
        """
        Advances the animation frame index or math phase depending on rendering mode.
        """
        if self.use_video:
            self.frame_index = (self.frame_index + 1) % 8
        else:
            phase_steps = {"listening": 0.2, "thinking": 0.3, "speaking": 0.15}
            self.phase += phase_steps.get(self.state, 0.045)
        self.update()

    def paintEvent(self, event) -> None:
        """
        Draws the active state animation frame or procedural fallback.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self.use_video:
            frames = self.speaking_frames if self.state == "speaking" else self.idle_frames
            if frames and self.frame_index < len(frames):
                painter.drawImage(self.rect(), frames[self.frame_index])
                return

        self.draw_fallback_orb(painter)

    def draw_fallback_orb(self, painter: QPainter) -> None:
        """
        Procedural radial gradient rendering fallback.
        """
        width, height = self.width(), self.height()
        center_x, center_y = width / 2.0, height / 2.0
        sine_val = math.sin(self.phase)
        
        if self.state == "listening":
            cfg = (34 + sine_val * 3.0, QColor(255, 255, 255, 80), QColor(255, 255, 255, 200))
        elif self.state == "thinking":
            cfg = (32 + abs(sine_val) * 1.5, QColor(255, 255, 255, 60), QColor(255, 255, 255, 160))
        elif self.state == "speaking":
            cfg = (34 + sine_val * 4.5, QColor(255, 255, 255, 80), QColor(255, 255, 255, 200))
        else:
            cfg = (28 + sine_val * 1.0, QColor(255, 255, 255, 30), QColor(255, 255, 255, 120))

        radius, glow_color, center_color = cfg

        # Draw outer glow
        glow_grad = QRadialGradient(center_x, center_y, radius * 1.6)
        glow_grad.setColorAt(0.0, glow_color)
        glow_grad.setColorAt(0.7, QColor(glow_color.red(), glow_color.green(), glow_color.blue(), 25))
        glow_grad.setColorAt(1.0, QColor(0, 0, 0, 0))

        painter.setBrush(glow_grad)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(center_x - radius * 1.6, center_y - radius * 1.6, radius * 3.2, radius * 3.2)

        # Draw solid inner core
        core_grad = QRadialGradient(center_x, center_y, radius)
        core_grad.setColorAt(0.0, center_color)
        core_grad.setColorAt(1.0, QColor(max(0, center_color.red() - 40), max(0, center_color.green() - 40), max(0, center_color.blue() - 40), 255))

        painter.setBrush(core_grad)
        painter.drawEllipse(center_x - radius, center_y - radius, radius * 2.0, radius * 2.0)


