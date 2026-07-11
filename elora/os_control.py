"""
Elora OS-Level Mouse & Keyboard Input Control.
Uses PyAutoGUI / xdotool and listens globally for the Ctrl+Alt+C abort hotkey.
"""

import time
import logging
import threading
import pyautogui
from pynput import keyboard

logger = logging.getLogger("elora.os_control")

# Global safety flags
abort_requested = False
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
