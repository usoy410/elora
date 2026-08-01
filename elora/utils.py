"""
Utility modules for Elora's Linux desktop integrations.
Provides system notifications via notify-send and audio cues via aplay.
"""

import logging
import subprocess

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


def play_chime(sound_path: str | None = None) -> subprocess.Popen | None:
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
    import os
    import shlex
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



def ensure_processed_video_frames() -> None:
    """
    Verifies if chroma-keyed PNG frames exist in assets/videos/processed.
    If frames are missing, automatically extracts them using ffmpeg and applies
    a soft-edge chroma keying algorithm using NumPy to remove the white background.
    
    Why: Keeps asset creation fully automated and localized to runtime needs,
    preventing large binary image dumps in source control while keeping startup light.
    """
    import os
    import shutil
    import subprocess
    import tempfile

    import numpy as np
    from PySide6.QtGui import QImage

    # Locate project root dynamically relative to this file
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    processed_idle_dir = os.path.join(base_dir, "assets", "videos", "processed", "idle")
    processed_speaking_dir = os.path.join(base_dir, "assets", "videos", "processed", "speaking")

    # Check if 8 frames are already successfully processed and cached
    idle_done = os.path.exists(processed_idle_dir) and len(os.listdir(processed_idle_dir)) >= 8
    speaking_done = os.path.exists(processed_speaking_dir) and len(os.listdir(processed_speaking_dir)) >= 8

    if idle_done and speaking_done:
        return  # Cache hit, skip processing

    # Check if system has ffmpeg installed to extract frame sequences
    if not shutil.which("ffmpeg"):
        logger.warning("ffmpeg is not installed on the system; falling back to procedural vector rendering.")
        return

    video_configs = [
        ("assets/videos/Idle-thinking-proccessing_State.mp4", processed_idle_dir),
        ("assets/videos/Speaking_State.mp4", processed_speaking_dir)
    ]

    for rel_video_path, dest_dir in video_configs:
        video_path = os.path.join(base_dir, rel_video_path)
        if not os.path.exists(video_path):
            logger.warning("Source video asset not found: %s", video_path)
            continue

        os.makedirs(dest_dir, exist_ok=True)

        with tempfile.TemporaryDirectory() as tmp_dir:
            # Extract video frames into temp dir as raw PNG files
            cmd = ["ffmpeg", "-y", "-i", video_path, os.path.join(tmp_dir, "frame_%03d.png")]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

            # Crop square dimensions (580x580 centered at 640,360)
            crop_x, crop_y, crop_size = 350, 70, 580

            for i in range(1, 9):
                src_frame = os.path.join(tmp_dir, f"frame_{i:03d}.png")
                dest_frame = os.path.join(dest_dir, f"frame_{i:03d}.png")

                if not os.path.exists(src_frame):
                    continue

                img = QImage(src_frame)
                if img.isNull():
                    continue

                # Crop active orb content
                cropped_img = img.copy(crop_x, crop_y, crop_size, crop_size)
                cropped_img = cropped_img.convertToFormat(QImage.Format.Format_ARGB32)

                w, h = cropped_img.width(), cropped_img.height()
                ptr = cropped_img.bits()
                arr = np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 4))

                # Extract R, G, B channels to calculate brightness (Black BG = 0)
                b = arr[:, :, 0].astype(float)
                g = arr[:, :, 1].astype(float)
                r = arr[:, :, 2].astype(float)
                brightness = (r + g + b) / 3.0

                # Define soft-feather chroma key ramp
                # Fully transparent if brightness <= 2.0, fully opaque if >= 20.0
                alpha = np.ones_like(brightness) * 255.0
                low_thresh = 2.0
                high_thresh = 20.0

                mask_mid = (brightness > low_thresh) & (brightness < high_thresh)
                alpha[mask_mid] = 255.0 * (brightness[mask_mid] - low_thresh) / (high_thresh - low_thresh)

                mask_low = (brightness <= low_thresh)
                alpha[mask_low] = 0.0

                # Apply modifications to alpha channel
                arr[:, :, 3] = alpha.astype(np.uint8)

                # Save transparent frame to cache folder
                cropped_img.save(dest_frame)




