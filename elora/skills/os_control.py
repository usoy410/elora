"""
Elora OS-Level Mouse & Keyboard Input Control.
Uses PyAutoGUI / xdotool and listens globally for the Ctrl+Alt+C abort hotkey.
"""

import time
import logging
import threading

logger = logging.getLogger("elora.os_control")

pyautogui = None
try:
    import pyautogui
except Exception as e:
    logger.warning("Failed to import pyautogui: %s. OS automation actions may fail.", e)

keyboard = None
try:
    from pynput import keyboard
except Exception as e:
    logger.warning("Failed to import pynput: %s. Safety lock hotkey will be disabled.", e)

# Global safety flags
abort_requested = False
if pyautogui is not None:
    pyautogui.FAILSAFE = False  # Controlled by our custom Ctrl+Alt+C hotkey instead


def on_abort_activated():
    """Triggered when the user presses Ctrl+Alt+C."""
    global abort_requested
    abort_requested = True
    logger.warning("SAFETY LOCKOUT: User triggered Ctrl+Alt+C safety abort.")
    print("\n[SAFETY ALERT] OS Automation aborted by user hotkey (Ctrl+Alt+C)!\n")


def start_safety_listener():
    """Starts the background safety hotkey listener."""
    # Listen globally for <ctrl>+<alt>+c
    hotkey = keyboard.HotKey(
        keyboard.HotKey.parse("<ctrl>+<alt>+c"),
        on_abort_activated
    )
    
    def run_listener():
        try:
            with keyboard.Listener(
                on_press=lambda k: hotkey.press(keyboard.Listener.canonical(k)),
                on_release=lambda k: hotkey.release(keyboard.Listener.canonical(k))
            ) as listener:
                listener.join()
        except Exception as e:
            logger.error("Global hotkey listener error: %s", e)

    t = threading.Thread(target=run_listener, name="EloraSafetyListener", daemon=True)
    t.start()
    logger.info("Elora Safety Hotkey Listener started (Ctrl+Alt+C).")


# Auto-start safety listener upon importing
start_safety_listener()


def check_abort():
    """Checks if the safety abort hotkey was pressed, resetting the flag and raising an error."""
    global abort_requested
    if abort_requested:
        abort_requested = False
        raise InterruptedError("Safety Interrupted: OS Automation aborted by user keypress (Ctrl+Alt+C).")


def move_mouse_smoothly(target_x: int, target_y: int, duration: float = 0.6) -> str:
    """Moves the host cursor smoothly to targets while checking for abort key presses."""
    check_abort()
    if pyautogui is None:
        return "Failed to move mouse: pyautogui is not available (check DISPLAY environment variable)."
    try:
        start_x, start_y = pyautogui.position()
        steps = 15
        delay = duration / steps

        for i in range(1, steps + 1):
            check_abort()
            t = i / steps
            # Smooth ease-in-out interpolation curve
            ease_t = t * t * (3 - 2 * t)
            
            curr_x = int(start_x + (target_x - start_x) * ease_t)
            curr_y = int(start_y + (target_y - start_y) * ease_t)
            
            pyautogui.moveTo(curr_x, curr_y)
            time.sleep(delay)

        pyautogui.moveTo(target_x, target_y)
        return f"Mouse moved successfully to coordinates ({target_x}, {target_y})"
    except InterruptedError as e:
        return str(e)
    except Exception as e:
        return f"Failed to move mouse: {e}"


def click_mouse_at(x: int, y: int, button: str = "left") -> str:
    """Moves the cursor and performs a click."""
    check_abort()
    if pyautogui is None:
        return "Failed to click: pyautogui is not available (check DISPLAY environment variable)."
    try:
        move_res = move_mouse_smoothly(x, y, duration=0.4)
        if "Safety Interrupted" in move_res:
            return move_res
            
        check_abort()
        pyautogui.click(button=button)
        return f"Clicked {button} mouse button at coordinates ({x}, {y})"
    except InterruptedError as e:
        return str(e)
    except Exception as e:
        return f"Failed to perform click: {e}"


def type_keyboard_text(text: str) -> str:
    """Simulates physical keyboard typing."""
    check_abort()
    if pyautogui is None:
        return "Failed to type text: pyautogui is not available (check DISPLAY environment variable)."
    try:
        # Check if the text matches a special key shortcut
        if "+" in text and len(text) <= 15:
            # Assume it is a key shortcut (e.g. "ctrl+t", "alt+tab")
            keys = [k.strip().lower() for k in text.split("+")]
            pyautogui.hotkey(*keys)
            return f"Simulated shortcut key combination: {text}"
        else:
            # Standard text typing
            for char in text:
                check_abort()
                pyautogui.write(char)
                time.sleep(0.01)  # Slight typing delay for realism
            return f"Typed text successfully: '{text}'"
    except InterruptedError as e:
        return str(e)
    except Exception as e:
        return f"Failed to type keyboard input: {e}"


def capture_desktop_screenshot(output_path: str = "/tmp/elora_screenshot.png") -> bool:
    """
    Captures a screenshot of the desktop.
    First tries GNOME shell DBus screenshot (since user is on GNOME Wayland).
    Falls back to gnome-screenshot, grim (Niri Wayland), and finally PyAutoGUI.
    """
    import subprocess
    import os
    
    # 1. Try GNOME DBus screenshot
    try:
        cmd = [
            "gdbus", "call", "--session", 
            "--dest", "org.gnome.Shell.Screenshot", 
            "--object-path", "/org/gnome/Shell/Screenshot", 
            "--method", "org.gnome.Shell.Screenshot.Screenshot", 
            "true", "false", output_path
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3.0)
        if res.returncode == 0 and os.path.exists(output_path):
            logger.info("Captured screenshot via GNOME Shell DBus API.")
            return True
    except Exception as e:
        logger.debug("GNOME DBus screenshot failed: %s", e)

    # 2. Try gnome-screenshot command utility
    try:
        cmd = ["gnome-screenshot", "-f", output_path]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3.0)
        if res.returncode == 0 and os.path.exists(output_path):
            logger.info("Captured screenshot via gnome-screenshot.")
            return True
    except Exception as e:
        logger.debug("gnome-screenshot failed: %s", e)

    # 3. Try grim (Wayland wlroots/Niri)
    try:
        cmd = ["grim", output_path]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3.0)
        if res.returncode == 0 and os.path.exists(output_path):
            logger.info("Captured screenshot via grim.")
            return True
    except Exception as e:
        logger.debug("grim failed: %s", e)

    # 4. Try pyautogui fallback
    try:
        screenshot = pyautogui.screenshot()
        screenshot.save(output_path)
        if os.path.exists(output_path):
            logger.info("Captured screenshot via PyAutoGUI.")
            return True
    except Exception as e:
        logger.error("All screenshot capture methods failed: %s", e)

    return False

