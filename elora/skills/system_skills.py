"""
Elora System Skills.
Provides controls for volume, brightness, active window states, and app launching on Linux.
"""

import logging
import os
import shlex
import subprocess

logger = logging.getLogger("elora.system_skills")


def set_system_volume(level: int) -> str:
    """Adjusts the master volume percentage using amixer."""
    if not (0 <= level <= 100):
        return "Error: Volume level must be between 0 and 100."

    cmd = f"amixer -q sset Master {level}%"
    try:
        subprocess.run(shlex.split(cmd), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return f"System volume set to {level}%"
    except Exception as e:
        logger.error("Failed to set volume: %s", e)
        # Try pulse audio alternative
        try:
            subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{level}%"], check=True)
            return f"System volume set to {level}% via PulseAudio"
        except Exception as pe:
            return f"Failed to set volume via ALSA ({e}) or PulseAudio ({pe})"


def set_system_brightness(level: int) -> str:
    """Adjusts display brightness percentage using brightnessctl or xbacklight."""
    if not (0 <= level <= 100):
        return "Error: Brightness level must be between 0 and 100."

    # Try brightnessctl first
    try:
        subprocess.run(["brightnessctl", "set", f"{level}%"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return f"Display brightness set to {level}%"
    except Exception:
        # Try xbacklight
        try:
            subprocess.run(["xbacklight", "-set", str(level)], check=True)
            return f"Display brightness set to {level}% via xbacklight"
        except Exception as e:
            return f"Failed to set brightness. Ensure brightnessctl or xbacklight is installed. Error: {e}"


def perform_window_action(action: str) -> str:
    """Performs actions on the active window using xdotool."""
    action = action.lower().strip()
    try:
        if action == "minimize":
            # Find active window and minimize it
            active_win = subprocess.check_output(["xdotool", "getactivewindow"]).decode().strip()
            subprocess.run(["xdotool", "windowminimize", active_win], check=True)
            return "Active window minimized successfully."
            
        elif action == "maximize":
            # Simulate Alt+F10 or Super+Up which maximizes in most Linux window managers
            subprocess.run(["xdotool", "key", "Super+Up"], check=True)
            # Alternate wmctrl method
            try:
                subprocess.run(["wmctrl", "-r", ":ACTIVE:", "-b", "add,maximized_vert,maximized_horz"], check=True)
            except Exception:
                pass
            return "Active window maximized."
            
        elif action == "close":
            # Send Alt+F4 to close current window
            subprocess.run(["xdotool", "key", "alt+F4"], check=True)
            return "Sent close shortcut (Alt+F4) to active window."
            
        else:
            return f"Error: Unknown window action '{action}'. Supported: minimize, maximize, close."
            
    except Exception as e:
        logger.error("Window action '%s' failed: %s", action, e)
        return f"Failed to perform window action: {e}"


def launch_application(app_name: str) -> str:
    """Launches a desktop application in the background."""
    app_name = app_name.lower().strip()
    
    # Map common alias names to executables
    app_map = {
        "chrome": "google-chrome",
        "browser": "brave",
        "terminal": "kitty",  # Fallbacks
        "editor": "code",
        "code": "code",
        "files": "nautilus",
        "calculator": "gnome-calculator"
    }
    
    executable = app_map.get(app_name, app_name)
    
    try:
        # Check if executable exists on PATH
        subprocess.run(["which", executable], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Launch detached
        subprocess.Popen(
            [executable],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setpgrp if hasattr(os, "setpgrp") else None
        )
        return f"Successfully launched {app_name} ({executable}) in the background."
    except Exception:
        # Try running directly as a fallback
        try:
            subprocess.Popen([executable], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return f"Requested startup for {app_name}."
        except Exception as e:
            return f"Failed to launch application '{app_name}'. Executable '{executable}' not found on PATH. Error: {e}"
