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
        color: #E5E7EB;
        font-family: 'Inter', sans-serif;
    }
    QTextBrowser, QTextEdit {
        background-color: rgba(255, 255, 255, 0.02);
        border: 1px solid rgba(255, 255, 255, 0.05);
        border-radius: 8px;
        color: #E5E7EB;
        font-family: 'Inter', sans-serif;
        font-size: 13px;
        padding: 10px;
    }
    QListWidget {
        background-color: rgba(255, 255, 255, 0.02);
        border: 1px solid rgba(255, 255, 255, 0.05);
        border-radius: 8px;
        color: #E5E7EB;
        font-family: 'Inter', sans-serif;
        font-size: 12px;
        padding: 6px;
    }
    QListWidget::item {
        border-bottom: 1px solid rgba(255, 255, 255, 0.02);
        padding: 6px 10px;
    }
    QListWidget::item:hover {
        background-color: rgba(255, 255, 255, 0.04);
        border-radius: 6px;
    }
    QListWidget::item:selected {
        background-color: rgba(255, 255, 255, 0.08);
        color: #FFFFFF;
        border-radius: 6px;
    }
    QPushButton {
        background-color: rgba(255, 255, 255, 0.03);
        border: 1px solid rgba(255, 255, 255, 0.06);
        border-radius: 8px;
        color: #E5E7EB;
        font-family: 'Inter', sans-serif;
        font-size: 12px;
        font-weight: 500;
        padding: 8px 14px;
    }
    QPushButton:hover {
        background-color: rgba(255, 255, 255, 0.08);
        border-color: rgba(255, 255, 255, 0.15);
        color: #FFFFFF;
    }
    QPushButton:pressed {
        background-color: rgba(255, 255, 255, 0.12);
    }
    
    /* Control Panel buttons */
    QPushButton.control-btn {
        background-color: rgba(255, 255, 255, 0.02);
        border: 1px solid rgba(255, 255, 255, 0.05);
        border-radius: 8px;
        color: #9CA3AF;
        font-family: 'JetBrains Mono', monospace;
        font-size: 11px;
        text-align: left;
        padding: 12px 16px;
    }
    QPushButton.control-btn:hover {
        background-color: rgba(255, 255, 255, 0.06);
        border-color: rgba(255, 255, 255, 0.12);
        color: #FFFFFF;
    }
    QPushButton.control-btn:checked {
        background-color: rgba(255, 255, 255, 0.09);
        border-color: rgba(255, 255, 255, 0.22);
        color: #FFFFFF;
    }

    /* Sub-tab buttons (Settings subpages) */
    QPushButton.sub-tab-btn {
        background-color: transparent;
        border: none;
        border-bottom: 2px solid transparent;
        border-radius: 0px;
        color: rgba(255, 255, 255, 0.4);
        font-family: 'Inter', sans-serif;
        font-size: 11px;
        font-weight: bold;
        padding: 6px 12px;
    }
    QPushButton.sub-tab-btn:hover {
        color: rgba(255, 255, 255, 0.8);
    }
    QPushButton.sub-tab-btn:checked {
        color: #FFFFFF;
        border-bottom: 2px solid rgba(255, 255, 255, 0.85);
    }

    /* QProgressBar Minimalist style */
    QProgressBar {
        background-color: rgba(255, 255, 255, 0.03);
        border: none;
        border-radius: 4px;
        text-align: right;
        color: rgba(255, 255, 255, 0.5);
        font-family: 'JetBrains Mono';
        font-size: 8px;
        font-weight: bold;
        height: 10px;
        padding-right: 2px;
    }
    QProgressBar::chunk {
        background-color: rgba(255, 255, 255, 0.6);
        border-radius: 4px;
    }

    QWidget#SystemMonitorPanel {
        background-color: rgba(13, 14, 18, 0.82);
        border: 1px solid rgba(255, 255, 255, 0.06);
        border-radius: 12px;
    }

    QWidget#ControlPanel {
        background-color: rgba(13, 14, 18, 0.82);
        border: 1px solid rgba(255, 255, 255, 0.06);
        border-radius: 12px;
    }

    QWidget#SidebarContainer {
        background-color: rgba(13, 14, 18, 0.82);
        border: 1px solid rgba(255, 255, 255, 0.06);
        border-radius: 12px;
    }

    QComboBox {
        background-color: rgba(255, 255, 255, 0.02);
        border: 1px solid rgba(255, 255, 255, 0.06);
        border-radius: 6px;
        color: #FFFFFF;
        padding: 6px;
        font-family: 'Inter', sans-serif;
    }
    QComboBox QAbstractItemView {
        background-color: #171822;
        border: 1px solid rgba(255, 255, 255, 0.12);
        color: #FFFFFF;
        selection-background-color: rgba(255, 255, 255, 0.08);
    }
    QLineEdit {
        background-color: rgba(255, 255, 255, 0.02);
        border: 1px solid rgba(255, 255, 255, 0.06);
        border-radius: 6px;
        color: #FFFFFF;
        font-family: 'Inter', sans-serif;
        font-size: 12px;
        padding: 6px 10px;
    }
    QLineEdit:focus {
        border-color: rgba(255, 255, 255, 0.2);
        background-color: rgba(255, 255, 255, 0.04);
    }
    QCheckBox {
        color: #E5E7EB;
        font-family: 'Inter', sans-serif;
        font-size: 12px;
        spacing: 8px;
    }

    /* QSlider Horizontal minimalist style */
    QSlider::groove:horizontal {
        height: 4px;
        background: rgba(255, 255, 255, 0.08);
        border-radius: 2px;
    }
    QSlider::sub-page:horizontal {
        background: rgba(255, 255, 255, 0.5);
        border-radius: 2px;
    }
    QSlider::handle:horizontal {
        background: #FFFFFF;
        width: 12px;
        height: 12px;
        margin-top: -4px;
        margin-bottom: -4px;
        border-radius: 6px;
    }
    QSlider::handle:horizontal:hover {
        background: #E5E7EB;
    }

    /* Elegant thin scrollbar */
    QScrollBar:vertical {
        background: transparent;
        width: 4px;
        margin: 0px;
    }
    QScrollBar::handle:vertical {
        background: rgba(255, 255, 255, 0.15);
        min-height: 20px;
        border-radius: 2px;
    }
    QScrollBar::handle:vertical:hover {
        background: rgba(255, 255, 255, 0.3);
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
        background: none;
        height: 0px;
    }
    QScrollBar::up-arrow:vertical, QScrollBar::down-arrow:vertical {
        background: none;
    }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
        background: none;
    }
"""

MODAL_OVERLAY_STYLE = """
    QWidget#ModalOverlay {
        background-color: rgba(0, 0, 0, 0.35);
    }
"""

MODAL_CARD_STYLE = """
    QWidget#ModalCard {
        background-color: rgba(13, 14, 18, 0.85);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 16px;
    }
"""
