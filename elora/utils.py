"""
Utility modules for Elora's Linux desktop integrations.
Provides system notifications via notify-send and audio cues via aplay.
"""

import subprocess
import logging
from typing import Optional

# Setup logger for the package
logger = logging.getLogger("elora.utils")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")


def send_notification(title: str, message: str) -> None:
    """
    Sends a system-wide desktop notification using notify-send.
    
    Why: Using notify-send allows asynchronous alerts without blocking 
    the execution loop or occupying GUI display frames directly.
    """
    try:
        # Run notify-send as a non-blocking background process
        subprocess.Popen(
            ["notify-send", "-a", "Elora", "-i", "utilities-terminal", title, message],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        logger.info("Notification sent: %s - %s", title, message)
    except FileNotFoundError:
        # Fallback if notify-send is not installed
        logger.warning("notify-send utility not found. Notification skipped: %s - %s", title, message)


def play_chime(sound_path: Optional[str] = None) -> Optional[subprocess.Popen]:
    """
    Plays a subtle audio chime using the system's aplay tool.
    
    Why: Auditory feedback allows the user to know when long-running tasks
    complete in the background without needing to look at notifications.
    """
    from elora.core.config import load_config
    config = load_config()
    sound_config = config.get("sound", {})
    
    # Check if sound alerts are disabled in config
    if not sound_config.get("enabled", True):
        logger.info("Sound notifications are disabled in user configuration.")
        return None
        
    # Use configured chime path or parameter override
    if not sound_path:
        sound_path = sound_config.get("chime_path", "/usr/share/sounds/alsa/Front_Center.wav")
        
    try:
        if sound_path.lower().endswith((".mp3", ".ogg")):
            # mpv is used to play mp3/ogg formats headlessly
            proc = subprocess.Popen(
                ["mpv", "--no-video", "--volume=80", sound_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        else:
            # aplay is the standard light-weight ALSA player on Linux for WAVs
            proc = subprocess.Popen(
                ["aplay", "-q", sound_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        logger.info("Audio chime played: %s", sound_path)
        return proc
    except FileNotFoundError as e:
        logger.warning("Required audio player utility not found. Audio chime skipped: %s", e)
        return None


def is_destructive_command(cmd: str) -> bool:
    """
    Checks if a shell command contains potentially destructive operations.
    Excludes safe subcommands (e.g., 'systemctl status').
    
    Why: Prevents accidental system damage while allowing harmless info-gathering tasks.
    """
    import shlex
    import os
    cmd_lower = cmd.lower()
    
    # 1. Check for dangerous executables
    destructive_tokens = {
        "rm", "dd", "mkfs", "chown", "chmod", "reboot", "shutdown", 
        "init", "systemctl", "kill", "killall", "pkill"
    }
    
    try:
        tokens = shlex.split(cmd_lower)
    except Exception:
        tokens = cmd_lower.split()
        
    for token in tokens:
        # Check for direct match or path containing the destructive command
        base_token = os.path.basename(token)
        if base_token in destructive_tokens:
            # Allow safe systemctl subcommands
            if base_token == "systemctl" and any(sub in tokens for sub in ["status", "is-active", "show", "list-units"]):
                continue
            return True
            
    # 2. Check for redirect modifications of system/config files (e.g. > /etc/...)
    if ">" in cmd_lower or ">>" in cmd_lower:
        for path in ["/etc/", "/var/", "/boot/", "/sys/", "/proc/"]:
            if path in cmd_lower:
                return True
                
    return False



