"""
Elora's action execution module.
Manages spawning background agent tasks in tmux and launching system browsers.
"""

import os
import subprocess
import shlex
import logging
import threading
import time
from elora.utils import send_notification, play_chime

logger = logging.getLogger("elora.actions")


def _find_new_html_files(start_time: float) -> list[str]:
    """
    Finds recursively any .html files in the current working directory modified after start_time.
    Avoids virtual environments and git folders to keep scanning extremely fast.
    """
    import os
    html_files = []
    ignored_dirs = {".venv", ".git", "__pycache__", ".agents"}
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in ignored_dirs and not d.startswith(".")]
        for file in files:
            if file.endswith(".html"):
                full_path = os.path.join(root, file)
                try:
                    mtime = os.path.getmtime(full_path)
                    if mtime >= start_time:
                        html_files.append(os.path.abspath(full_path))
                except Exception:
                    pass
    return html_files


def _monitor_session(session_name: str, task_prompt: str, start_time: float) -> None:
    """
    Background worker that polls tmux to check if the session is still active.
    When the session exits, it alerts the user via desktop notification and sound.
    
    Why: Keeps Elora informed of background task completion, establishing a complete feedback loop.
    """
    logger.info("Started watcher thread for session: %s", session_name)
    
    # Poll every 2 seconds
    while True:
        time.sleep(2)
        check_cmd = ["tmux", "has-session", "-t", session_name]
        res = subprocess.run(check_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode != 0:
            # Session no longer exists (meaning the task finished and the shell exited)
            break
            
    logger.info("Watcher detected exit for session: %s", session_name)
    
    # Check if any new HTML files were created/modified during the task execution
    new_htmls = _find_new_html_files(start_time)
    preview_opened = False
    opened_file_name = ""
    
    if new_htmls:
        # Sort by modification time descending to get the newest
        new_htmls.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        target_html = new_htmls[0]
        opened_file_name = os.path.basename(target_html)
        file_url = f"file://{target_html}"
        logger.info("Auto-previewing newly created HTML file: %s", file_url)
        preview_opened = open_browser_url(file_url)
    
    # Send success chime and notification
    send_notification("Task Completed", f"The background agent finished the task: {task_prompt}")
    play_chime()
    
    # Speak completion alert out loud dynamically
    try:
        from elora.voice import speak_text
        if preview_opened:
            speak_text(f"Task complete. Opening the preview of {opened_file_name} in your browser.")
        else:
            speak_text("Task complete. The background agent finished the task.")
    except Exception as e:
        logger.error("Failed to speak task completion confirmation: %s", e)


def _get_unique_tmux_session(base_name: str = "elora-dev") -> str:
    """
    Checks for active tmux sessions and returns a unique session name to avoid collisions.
    
    Why: Prevents hijacking or overwriting active developer agent sessions.
    """
    session_name = base_name
    index = 1
    
    while True:
        # Check if the tmux session name already exists
        check_cmd = ["tmux", "has-session", "-t", session_name]
        res = subprocess.run(check_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode != 0:
            # Session name is free
            return session_name
        
        session_name = f"{base_name}-{index}"
        index += 1


def execute_agent_task(prompt: str) -> str:
    """
    Spawns the Antigravity CLI (agy) in a background tmux session for complex task execution.
    
    Why: Relieves Elora from writing hundreds of lines of code locally, handing off
    the work to a specialized agent in a separate tmux window.
    """
    session_name = _get_unique_tmux_session("elora-dev")
    start_time = time.time()
    
    # Locate the absolute path of agy
    agy_path = "/home/usoy/.local/bin/agy"
    
    # Construct the tmux shell invocation command safely using shlex.quote
    escaped_prompt = shlex.quote(prompt)
    cmd_str = f"{agy_path} --prompt-interactive {escaped_prompt}"
    
    # tmux command: tmux new-session -d -s session_name "cmd"
    tmux_cmd = ["tmux", "new-session", "-d", "-s", session_name, cmd_str]
    
    try:
        logger.info("Spawning background agent task in tmux: %s", session_name)
        subprocess.Popen(tmux_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Spawn daemon watcher thread to play completion alerts when the tmux task exits
        t = threading.Thread(target=_monitor_session, args=(session_name, prompt, start_time), daemon=True)
        t.start()
        
        # Notify the user
        notify_title = "Task Delegated"
        notify_msg = f"Task handed over to Antigravity CLI in background session '{session_name}'. I'll alert you when it's done."
        send_notification(notify_title, notify_msg)
        
        # Optional: play a notification chime
        play_chime()
        
        return session_name
    except Exception as e:
        err_msg = f"Failed to spawn agent task in tmux: {e}"
        logger.error(err_msg)
        send_notification("Delegation Error", err_msg)
        return ""



def open_browser_url(url: str) -> bool:
    """
    Opens the default browser for a specific URL instantly.
    
    Why: Handles standard navigation requests.
    """
    from elora.config import load_config
    config = load_config()
    browser_cmd = config.get("browser", {}).get("default_command", "xdg-open")
    
    try:
        logger.info("Opening URL in browser: %s using %s", url, browser_cmd)
        subprocess.Popen([browser_cmd, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception as e:
        logger.error("Failed to launch system browser: %s", str(e))
        return False

