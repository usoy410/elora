"""
Elora's action execution module.
Manages spawning background agent tasks in tmux and launching system browsers.
"""

import subprocess
import shlex
import logging
from elora.utils import send_notification, play_chime

logger = logging.getLogger("elora.actions")


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

