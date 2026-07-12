"""
Transparent/Translucent modal card backdrop widgets for Elora HUD.
Centers configurations, logs, and settings overlays cleanly on screen.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout


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
