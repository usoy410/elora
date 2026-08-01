"""
Elora OS-Level Mouse & Keyboard Input Control.
Uses PyAutoGUI / xdotool and listens globally for the Ctrl+Alt+C abort hotkey.
"""

import logging
import threading
import time

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
                on_press=lambda k: hotkey.press(listener.canonical(k)),
                on_release=lambda k: hotkey.release(listener.canonical(k))
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
    
    Why: Equips the agent with real-time visual context of the screen.
    Attempts the following screenshot options in order:
    1. XDG Desktop Portal Screenshot API (supports all modern Wayland/X11 systems, e.g. GNOME, KDE, XFCE).
    2. GNOME Shell DBus API (legacy private interface).
    3. gnome-screenshot CLI tool.
    4. spectacle CLI tool (KDE).
    5. xfce4-screenshooter CLI tool (XFCE).
    6. cinnamon-screenshot CLI tool (Cinnamon).
    7. mate-screenshot CLI tool (MATE).
    8. grim CLI tool (wlroots Wayland, e.g. Sway, Hyprland).
    9. maim CLI tool (X11).
    10. scrot CLI tool (X11).
    11. PyAutoGUI screenshot capture fallback.
    """
    import os
    import subprocess
    
    # 1. Try XDG Desktop Portal (robust modern Wayland & X11 standard)
    try:
        import shutil
        import sys

        from PySide6.QtCore import (
            SLOT,
            QCoreApplication,
            QEventLoop,
            QObject,
            QTimer,
            Slot,
        )
        from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage

        class PortalHelper(QObject):
            def __init__(self, out_path, loop):
                super().__init__()
                self.out_path = out_path
                self.loop = loop
                self.success = False
                self.bus = QDBusConnection.sessionBus()
                
            def run(self):
                if not self.bus.isConnected():
                    self.loop.quit()
                    return
                    
                self.bus.registerObject("/", self)
                
                self.interface = QDBusInterface(
                    "org.freedesktop.portal.Desktop",
                    "/org/freedesktop/portal/desktop",
                    "org.freedesktop.portal.Screenshot",
                    self.bus
                )
                
                # Request screenshot silently if possible (uses cached permission if granted)
                options = {"interactive": False}
                reply = self.interface.call("Screenshot", "", options)
                if reply.type() == QDBusMessage.MessageType.ErrorMessage:
                    logger.debug("Portal DBus call failed: %s", reply.errorMessage())
                    self.loop.quit()
                    return
                    
                try:
                    request_path = reply.arguments()[0].path()
                except Exception as e:
                    logger.debug("Failed to parse request path from portal: %s", e)
                    self.loop.quit()
                    return
                    
                connected = self.bus.connect(
                    "org.freedesktop.portal.Desktop",
                    request_path,
                    "org.freedesktop.portal.Request",
                    "Response",
                    self,
                    SLOT("handle_response(uint,QVariantMap)")
                )
                if not connected:
                    logger.debug("Failed to connect portal Response signal.")
                    self.loop.quit()
                    return
                    
                # Limit wait to 6 seconds to prevent blocking the agent loop if user does not react
                self.timer = QTimer(self)
                self.timer.setSingleShot(True)
                self.timer.timeout.connect(self.timeout)
                self.timer.start(6000)

            @Slot("uint", "QVariantMap")
            def handle_response(self, response_code, results):
                if response_code == 0:
                    uri = results.get("uri")
                    if uri and uri.startswith("file://"):
                        local_path = uri[7:]
                        if os.path.exists(local_path):
                            try:
                                shutil.copy(local_path, self.out_path)
                                self.success = True
                                logger.info("Captured screenshot via XDG Desktop Portal.")
                            except Exception as e:
                                logger.error("Failed to copy portal screenshot file: %s", e)
                self.loop.quit()

            def timeout(self):
                logger.debug("Portal screenshot timed out waiting for user confirmation.")
                self.loop.quit()

        app_created = False
        app = QCoreApplication.instance()
        if app is None:
            app = QCoreApplication(sys.argv)
            app_created = True

        loop = QEventLoop()
        helper = PortalHelper(output_path, loop)
        QTimer.singleShot(0, helper.run)
        loop.exec()
        
        if helper.success and os.path.exists(output_path):
            return True
    except Exception as e:
        logger.debug("XDG Desktop Portal screenshot method failed: %s", e)

    # 2. Try GNOME DBus screenshot
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

    # 3. Try gnome-screenshot command utility
    try:
        cmd = ["gnome-screenshot", "-f", output_path]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3.0)
        if res.returncode == 0 and os.path.exists(output_path):
            logger.info("Captured screenshot via gnome-screenshot.")
            return True
    except Exception as e:
        logger.debug("gnome-screenshot failed: %s", e)

    # 4. Try spectacle (KDE)
    try:
        cmd = ["spectacle", "-b", "-n", "-o", output_path]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3.0)
        if res.returncode == 0 and os.path.exists(output_path):
            logger.info("Captured screenshot via spectacle.")
            return True
    except Exception as e:
        logger.debug("spectacle failed: %s", e)

    # 5. Try xfce4-screenshooter (XFCE)
    try:
        cmd = ["xfce4-screenshooter", "-f", "-s", output_path]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3.0)
        if res.returncode == 0 and os.path.exists(output_path):
            logger.info("Captured screenshot via xfce4-screenshooter.")
            return True
    except Exception as e:
        logger.debug("xfce4-screenshooter failed: %s", e)

    # 6. Try cinnamon-screenshot (Cinnamon)
    try:
        cmd = ["cinnamon-screenshot", "-f", output_path]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3.0)
        if res.returncode == 0 and os.path.exists(output_path):
            logger.info("Captured screenshot via cinnamon-screenshot.")
            return True
    except Exception as e:
        logger.debug("cinnamon-screenshot failed: %s", e)

    # 7. Try mate-screenshot (MATE)
    try:
        cmd = ["mate-screenshot", "-f", output_path]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3.0)
        if res.returncode == 0 and os.path.exists(output_path):
            logger.info("Captured screenshot via mate-screenshot.")
            return True
    except Exception as e:
        logger.debug("mate-screenshot failed: %s", e)

    # 8. Try grim (Wayland wlroots/Niri)
    try:
        cmd = ["grim", output_path]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3.0)
        if res.returncode == 0 and os.path.exists(output_path):
            logger.info("Captured screenshot via grim.")
            return True
    except Exception as e:
        logger.debug("grim failed: %s", e)

    # 9. Try maim (X11)
    try:
        cmd = ["maim", "-u", output_path]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3.0)
        if res.returncode == 0 and os.path.exists(output_path):
            logger.info("Captured screenshot via maim.")
            return True
    except Exception as e:
        logger.debug("maim failed: %s", e)

    # 10. Try scrot (X11)
    try:
        cmd = ["scrot", "-z", output_path]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3.0)
        if res.returncode == 0 and os.path.exists(output_path):
            logger.info("Captured screenshot via scrot.")
            return True
    except Exception as e:
        logger.debug("scrot failed: %s", e)

    # 11. Try pyautogui fallback
    try:
        screenshot = pyautogui.screenshot()
        screenshot.save(output_path)
        if os.path.exists(output_path):
            logger.info("Captured screenshot via PyAutoGUI.")
            return True
    except Exception as e:
        logger.error("All screenshot capture methods failed: %s", e)

    return False


