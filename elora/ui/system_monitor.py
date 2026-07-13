"""
System Diagnostics and Telemetry monitor widget for Elora HUD.
Displays CPU, RAM, and Background Task usage using styled visual progress bars.
"""

import logging
import subprocess
from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtWidgets import QFrame, QVBoxLayout, QLabel, QProgressBar

logger = logging.getLogger("elora.ui.system_monitor")


class SystemMonitorWidget(QFrame):
    """
    Vertical dashboard displaying real-time system stats (CPU, RAM, Background tasks).
    Uses lightweight QProgressBar and labels styled for a tech panel HUD.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SystemMonitorPanel")
        
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(15, 15, 15, 15)
        self.layout.setSpacing(12)
        
        # Panel Title
        self.lbl_title = QLabel("SYSTEM MONITOR", self)
        self.lbl_title.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 10px; font-weight: bold; color: rgba(255, 255, 255, 0.85); letter-spacing: 1px;")
        self.layout.addWidget(self.lbl_title)
        
        # CPU Monitor
        self.lbl_cpu_title = QLabel("CPU LOAD", self)
        self.lbl_cpu_title.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 8px; color: rgba(255, 255, 255, 0.45);")
        self.layout.addWidget(self.lbl_cpu_title)
        
        self.pb_cpu = QProgressBar(self)
        self.pb_cpu.setRange(0, 100)
        self.pb_cpu.setValue(0)
        self.pb_cpu.setTextVisible(True)
        self.pb_cpu.setFormat("%p%")
        self.layout.addWidget(self.pb_cpu)
        
        # RAM Monitor
        self.lbl_ram_title = QLabel("RAM USAGE: --", self)
        self.lbl_ram_title.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 8px; color: rgba(255, 255, 255, 0.45);")
        self.layout.addWidget(self.lbl_ram_title)
        
        self.pb_ram = QProgressBar(self)
        self.pb_ram.setRange(0, 100)
        self.pb_ram.setValue(0)
        self.pb_ram.setTextVisible(True)
        self.pb_ram.setFormat("%p%")
        self.layout.addWidget(self.pb_ram)
        
        # Tasks Monitor
        self.lbl_tasks_title = QLabel("ACTIVE AGENTS", self)
        self.lbl_tasks_title.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 8px; color: rgba(255, 255, 255, 0.45);")
        self.layout.addWidget(self.lbl_tasks_title)
        
        self.pb_tasks = QProgressBar(self)
        self.pb_tasks.setRange(0, 10)  # Max 10 tasks showing progress
        self.pb_tasks.setValue(0)
        self.pb_tasks.setTextVisible(True)
        self.pb_tasks.setFormat("%v active")
        self.layout.addWidget(self.pb_tasks)
        
        # Extra Stats Frame
        self.lbl_status = QLabel("HUD CONNECTION: ACTIVE\nGATE GUARD: STANDBY\nCORE ENGINE: READY", self)
        self.lbl_status.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 8px; color: rgba(255, 255, 255, 0.35); line-height: 14px;")
        self.layout.addWidget(self.lbl_status)
        
        # Telemetry Timer
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_telemetry)
        self.timer.start(1000)
        self.update_telemetry()

    @Slot()
    def update_telemetry(self) -> None:
        """
        Polls the Linux OS file system (/proc) to update current CPU and RAM workloads.
        Does not spin up heavy processes, ensuring low-resource execution.
        """
        # 1. Update RAM telemetry
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
                for line in lines:
                    if line.startswith("MemFree:"):
                        mem_avail = int(line.split()[1])
            used = mem_total - mem_avail
            ram_pct = int(used * 100 / mem_total) if mem_total > 0 else 0
            
            self.pb_ram.setValue(ram_pct)
            self.lbl_ram_title.setText(f"RAM USAGE: {used / (1024*1024):.1f}G / {mem_total / (1024*1024):.1f}G")
        except Exception as e:
            logger.debug("Failed to read RAM info: %s", e)
            
        # 2. Update CPU telemetry
        try:
            with open("/proc/loadavg", "r") as f:
                load = f.read().split()
            cpu_val = float(load[0])
            # Normalize load to a percentage estimate (assuming multi-core baseline load of 8.0 is 100%)
            cpu_pct = min(100, int(cpu_val * 100 / 8.0))
            self.pb_cpu.setValue(cpu_pct)
            self.lbl_cpu_title.setText(f"CPU LOAD: {load[0]} {load[1]}")
        except Exception as e:
            logger.debug("Failed to read CPU info: %s", e)
            
        # 3. Update Background Tasks telemetry
        tasks_count = 0
        try:
            output = subprocess.check_output(["tmux", "list-sessions"], stderr=subprocess.DEVNULL).decode()
            tasks_count = len([line for line in output.strip().split("\n") if line.strip().startswith("elora-dev")])
        except Exception:
            pass
        self.pb_tasks.setValue(min(10, tasks_count))
        self.lbl_tasks_title.setText(f"ACTIVE AGENTS ({tasks_count} TOTAL)")
