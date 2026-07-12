"""
Visual styling and QSS stylesheets for Elora HUD.
Aggregates visual rules to maintain an obsidian design theme.
"""

HUD_STYLESHEET = """
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
    
    /* Control Panel Cyber buttons */
    QPushButton.control-btn {
        background-color: rgba(15, 17, 26, 0.4);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 6px;
        color: #9CA3AF;
        font-family: 'JetBrains Mono';
        font-size: 11px;
        text-align: left;
        padding: 11px 15px;
    }
    QPushButton.control-btn:hover {
        background-color: rgba(0, 240, 255, 0.1);
        border-color: rgba(0, 240, 255, 0.4);
        color: #00F0FF;
    }
    QPushButton.control-btn:checked {
        background-color: rgba(129, 140, 248, 0.15);
        border-color: rgba(129, 140, 248, 0.6);
        color: #FFFFFF;
    }

    /* QProgressBar Cyberpunk style */
    QProgressBar {
        background-color: rgba(255, 255, 255, 0.05);
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 4px;
        text-align: right;
        color: rgba(255, 255, 255, 0.75);
        font-family: 'JetBrains Mono';
        font-size: 9px;
        font-weight: bold;
        height: 14px;
        padding-right: 4px;
    }
    QProgressBar::chunk {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #818CF8, stop:1 #00F0FF);
        border-radius: 3px;
    }

    QWidget#SystemMonitorPanel {
        background-color: rgba(10, 12, 18, 0.6);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 12px;
    }

    QWidget#ControlPanel {
        background-color: rgba(10, 12, 18, 0.6);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 12px;
    }

    QWidget#SidebarContainer {
        background-color: rgba(10, 12, 18, 0.7);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 12px;
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
"""

MODAL_OVERLAY_STYLE = """
    QWidget#ModalOverlay {
        background-color: rgba(0, 0, 0, 0.45);
    }
"""

MODAL_CARD_STYLE = """
    QWidget#ModalCard {
        background-color: rgba(15, 17, 26, 0.85);
        border: 1px solid rgba(255, 255, 255, 0.15);
        border-radius: 16px;
    }
"""
