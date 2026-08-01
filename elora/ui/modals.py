import logging
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QListWidget, QFrame, QScrollArea, QListWidgetItem, QSizePolicy,
    QStackedWidget, QRadioButton, QButtonGroup
)
from PySide6.QtCore import Qt, Signal, QPoint, QSize
from PySide6.QtGui import QPixmap, QIcon, QMouseEvent
from PySide6.QtMultimedia import QCamera, QMediaCaptureSession, QImageCapture
from PySide6.QtMultimediaWidgets import QVideoWidget

logger = logging.getLogger("elora.ui.modals")

class DraggableHeader(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.window().raise_()
            self.window().activateWindow()
            window = self.window().windowHandle()
            if window:
                window.startSystemMove()
            event.accept()

class DraggableModal(QFrame):
    """Base class for floating, draggable, frameless modals inside the HUD."""
    
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        
        self.setObjectName("DraggableModalBase")
        self.setStyleSheet("""
            QFrame#DraggableModalBase {
                background-color: rgba(13, 14, 18, 0.82);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 12px;
            }
        """)
        
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)
        
        # Header
        self.header = DraggableHeader(self)
        self.header.setStyleSheet("background-color: rgba(255, 255, 255, 0.05); border-bottom: 1px solid rgba(255, 255, 255, 0.1); border-radius: 12px; border-bottom-left-radius: 0px; border-bottom-right-radius: 0px;")
        self.header.setFixedHeight(40)
        
        self.header_layout = QHBoxLayout(self.header)
        self.header_layout.setContentsMargins(15, 0, 10, 0)
        
        self.lbl_title = QLabel(title, self.header)
        self.lbl_title.setStyleSheet("color: white; font-weight: bold; font-family: 'JetBrains Mono'; font-size: 12px; border: none; background: transparent;")
        self.lbl_title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        
        self.btn_close = QPushButton("✕", self.header)
        self.btn_close.setFixedSize(24, 24)
        self.btn_close.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                color: rgba(255, 255, 255, 0.5);
                font-weight: bold;
            }
            QPushButton:hover {
                color: #EF4444;
            }
        """)
        self.btn_close.clicked.connect(self.hide)
        
        self.header_layout.addWidget(self.lbl_title)
        self.header_layout.addStretch()
        self.header_layout.addWidget(self.btn_close)
        
        self.main_layout.addWidget(self.header)
        
        # Content Area
        self.content_area = QWidget(self)
        self.content_area.setStyleSheet("background: transparent; border: none;")
        self.content_layout = QVBoxLayout(self.content_area)
        self.content_layout.setContentsMargins(15, 15, 15, 15)
        self.content_layout.setSpacing(10)
        
        self.main_layout.addWidget(self.content_area)

    def mousePressEvent(self, event):
        self.raise_()
        self.activateWindow()
        super().mousePressEvent(event)


class ScreenshotModal(DraggableModal):
    def __init__(self, parent=None):
        super().__init__("Vision & Screenshot", parent)
        self.resize(350, 320)
        
        # Source Toggle
        self.toggle_layout = QHBoxLayout()
        self.radio_screen = QRadioButton("Desktop Screen")
        self.radio_camera = QRadioButton("Live Camera")
        self.radio_screen.setChecked(True)
        self.radio_screen.setStyleSheet("color: white;")
        self.radio_camera.setStyleSheet("color: white;")
        self.toggle_group = QButtonGroup(self)
        self.toggle_group.addButton(self.radio_screen)
        self.toggle_group.addButton(self.radio_camera)
        self.toggle_layout.addWidget(self.radio_screen)
        self.toggle_layout.addWidget(self.radio_camera)
        self.toggle_layout.addStretch()
        self.content_layout.addLayout(self.toggle_layout)
        
        # Stacked Widget for Viewers
        self.view_stack = QStackedWidget(self)
        self.view_stack.setFixedSize(350, 200)
        
        self.lbl_screenshot = QLabel("No screenshot captured yet.")
        self.lbl_screenshot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_screenshot.setStyleSheet("background-color: rgba(0,0,0,0.5); border-radius: 8px;")
        self.lbl_screenshot.setScaledContents(False)
        self.view_stack.addWidget(self.lbl_screenshot)
        
        # Camera Viewer
        self.video_widget = QVideoWidget()
        self.camera = QCamera()
        self.capture_session = QMediaCaptureSession()
        self.image_capture = QImageCapture()
        self.capture_session.setCamera(self.camera)
        self.capture_session.setVideoOutput(self.video_widget)
        self.capture_session.setImageCapture(self.image_capture)
        self.view_stack.addWidget(self.video_widget)
        
        self.content_layout.addWidget(self.view_stack)
        
        self.radio_screen.toggled.connect(self.on_source_changed)
        self.on_source_changed() # init state
        
        # Expandable controls
        self.controls_widget = QWidget(self)
        self.controls_layout = QHBoxLayout(self.controls_widget)
        self.controls_layout.setContentsMargins(0, 0, 0, 0)
        
        self.btn_refresh = QPushButton("Refresh View", self)
        self.btn_refresh.setStyleSheet("background-color: rgba(255,255,255,0.1); border-radius: 4px; padding: 4px;")
        self.btn_abort = QPushButton("Abort Automation", self)
        self.btn_abort.setStyleSheet("background-color: rgba(239,68,68,0.2); color: #EF4444; border-radius: 4px; padding: 4px;")
        
        self.controls_layout.addWidget(self.btn_refresh)
        self.controls_layout.addWidget(self.btn_abort)
        self.content_layout.addWidget(self.controls_widget)
        self.controls_widget.hide()
        
        self.is_expanded = False
        self.lbl_screenshot.mousePressEvent = self.toggle_controls

    def on_source_changed(self):
        if self.radio_camera.isChecked():
            self.view_stack.setCurrentWidget(self.video_widget)
            self.camera.start()
        else:
            self.view_stack.setCurrentWidget(self.lbl_screenshot)
            self.camera.stop()

    def get_vision_source(self):
        return "camera" if self.radio_camera.isChecked() else "screen"

    def toggle_controls(self, event):
        self.is_expanded = not self.is_expanded
        self.controls_widget.setVisible(self.is_expanded)
        if self.is_expanded:
            self.resize(self.width(), self.height() + 40)
        else:
            self.resize(self.width(), self.height() - 40)

class TaskItemWidget(QWidget):
    action_requested = Signal(str, dict)
    
    def __init__(self, task_data, parent=None):
        super().__init__(parent)
        self.task_data = task_data
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        
        # Header (always visible)
        self.header = QWidget(self)
        self.header_layout = QHBoxLayout(self.header)
        self.header_layout.setContentsMargins(5, 5, 5, 5)
        self.lbl_name = QLabel(task_data.get("name", "Unknown Task"))
        self.lbl_status = QLabel(task_data.get("status", "Running"))
        self.header_layout.addWidget(self.lbl_name)
        self.header_layout.addStretch()
        self.header_layout.addWidget(self.lbl_status)
        self.main_layout.addWidget(self.header)
        
        # Controls (hidden by default)
        self.controls = QWidget(self)
        self.controls_layout = QHBoxLayout(self.controls)
        self.controls_layout.setContentsMargins(5, 0, 5, 5)
        
        self.btn_kill = QPushButton("Kill", self)
        self.btn_logs = QPushButton("Logs", self)
        self.btn_term = QPushButton("Terminal", self)
        
        self.btn_kill.clicked.connect(lambda: self.action_requested.emit("kill", self.task_data))
        self.btn_logs.clicked.connect(lambda: self.action_requested.emit("logs", self.task_data))
        self.btn_term.clicked.connect(lambda: self.action_requested.emit("term", self.task_data))
        
        for btn in [self.btn_kill, self.btn_logs, self.btn_term]:
            btn.setStyleSheet("background-color: rgba(255,255,255,0.1); border-radius: 4px; padding: 2px;")
            self.controls_layout.addWidget(btn)
            
        self.main_layout.addWidget(self.controls)
        self.controls.hide()
        
        self.is_expanded = False
        self.header.mousePressEvent = self.toggle_controls
        
    def toggle_controls(self, event):
        self.is_expanded = not self.is_expanded
        self.controls.setVisible(self.is_expanded)
        
class TasksModal(DraggableModal):
    task_action = Signal(str, dict)

    def __init__(self, parent=None):
        super().__init__("Active Tasks", parent)
        self.resize(350, 400)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("background: transparent; border: none;")
        
        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background: transparent;")
        self.items_layout = QVBoxLayout(self.scroll_content)
        self.items_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.scroll_content)
        self.content_layout.addWidget(self.scroll)

    def populate_tasks(self, tasks_list):
        # Clear existing
        while self.items_layout.count():
            child = self.items_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
                
        for task in tasks_list:
            item = TaskItemWidget(task, self)
            item.action_requested.connect(self.task_action.emit)
            self.items_layout.addWidget(item)

class NewsModal(DraggableModal):
    def __init__(self, parent=None):
        super().__init__("News Telemetry", parent)
        self.resize(350, 400)
        self.list_widget = QListWidget(self)
        self.list_widget.setStyleSheet("background: transparent; border: none; color: white;")
        self.list_widget.setWordWrap(True)
        self.content_layout.addWidget(self.list_widget)

