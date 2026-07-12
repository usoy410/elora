"""
Speech-animated glowing vector orb component for Elora HUD.
Renders canvas animations based on voice/thought system states.
"""

import math
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPainter, QColor, QRadialGradient


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
