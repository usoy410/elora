"""
Procedural Sci-Fi audio visualizer component for Elora HUD.
Renders real-time animated equalizer bars responsive to core application states.
"""

import math
import random

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import QWidget


class CavaVisualizer(QWidget):
    """
    A custom QWidget that procedurally draws Cava-style equalizer bars.
    Animates differently based on states: 'idle', 'listening', 'thinking', 'speaking'.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = "idle"
        self.phase = 0.0
        self.bar_count = 20
        self.bar_values = [0.0] * self.bar_count
        self.target_values = [0.0] * self.bar_count
        
        self.setMinimumSize(180, 80)
        
        # Animation timer (30 FPS matches low-overhead visual guideline)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick_animation)
        self.timer.start(33)

    def set_state(self, state: str) -> None:
        """Updates the visualizer state to adapt the procedural wave generator."""
        self.state = state
        self.update()

    @Slot()
    def tick_animation(self) -> None:
        """Calculates procedural bar target heights and interpolates current heights."""
        self.phase += 0.1
        
        if self.state == "idle":
            # Slow, rhythmic breathing pattern across the bars
            for i in range(self.bar_count):
                offset = i * 0.3
                self.target_values[i] = 10.0 + 8.0 * math.sin(self.phase + offset)
                
        elif self.state == "listening":
            # Energetic, highly spiky randomized frequency bands
            for i in range(self.bar_count):
                base_rand = random.uniform(0.1, 1.0)
                # Boost middle frequencies
                multiplier = 45.0 if 5 <= i <= 15 else 20.0
                self.target_values[i] = base_rand * multiplier
                
        elif self.state == "thinking":
            # A scanning sine wave traversing the panel from left to right
            for i in range(self.bar_count):
                dist = abs(i - (self.bar_count / 2) - (self.bar_count / 2) * math.sin(self.phase * 0.7))
                factor = max(0.0, 1.0 - (dist / 4.0))
                self.target_values[i] = 5.0 + 35.0 * factor
                
        elif self.state == "speaking":
            # Balanced voice visualizer curve with fast fluctuations
            for i in range(self.bar_count):
                center_dist = abs(i - self.bar_count / 2)
                bell_curve = math.exp(-0.2 * (center_dist ** 2))
                voice_bounce = 0.5 + 0.5 * math.sin(self.phase * 2.0 + i)
                self.target_values[i] = 5.0 + 40.0 * bell_curve * voice_bounce * random.uniform(0.8, 1.2)
        
        # Smooth interpolation to target heights (decay/ease filter)
        lerp_speed = 0.25 if self.state in ("listening", "speaking") else 0.15
        for i in range(self.bar_count):
            self.bar_values[i] += (self.target_values[i] - self.bar_values[i]) * lerp_speed
            
        self.update()

    def paintEvent(self, event) -> None:
        """Paints the dynamic gradient columns based on active values."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        width = self.width()
        height = self.height()
        
        bar_w = 3
        gap = 4
        total_w = self.bar_count * bar_w + (self.bar_count - 1) * gap
        start_x = (width - total_w) / 2
        
        # Set gradients based on active state (minimalist monochromatic white)
        gradient = QLinearGradient(0, height, 0, 0)
        if self.state == "listening":
            gradient.setColorAt(0.0, QColor(255, 255, 255, 30))
            gradient.setColorAt(1.0, QColor(255, 255, 255, 180))
        elif self.state == "thinking":
            gradient.setColorAt(0.0, QColor(255, 255, 255, 20))
            gradient.setColorAt(1.0, QColor(255, 255, 255, 140))
        elif self.state == "speaking":
            gradient.setColorAt(0.0, QColor(255, 255, 255, 30))
            gradient.setColorAt(1.0, QColor(255, 255, 255, 180))
        else:
            gradient.setColorAt(0.0, QColor(255, 255, 255, 15))
            gradient.setColorAt(1.0, QColor(255, 255, 255, 90))
            
        painter.setBrush(gradient)
        painter.setPen(Qt.PenStyle.NoPen)
        
        for i in range(self.bar_count):
            val = self.bar_values[i]
            x = start_x + i * (bar_w + gap)
            y = height - val - 5  # Give a slight 5px margin at the bottom
            painter.drawRoundedRect(x, y, bar_w, val, 1.5, 1.5)
