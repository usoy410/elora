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
import json
from elora.utils import send_notification, play_chime

logger = logging.getLogger("elora.actions")

REGISTRY_PATH = os.path.expanduser("~/.config/elora/tasks.json")


def _load_tasks_registry() -> dict:
    """Loads background tasks registry from user configuration directory."""
    if not os.path.exists(REGISTRY_PATH):
        return {}
    try:
        with open(REGISTRY_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.error("Failed to load tasks registry: %s", e)
        return {}


def _save_tasks_registry(registry: dict) -> None:
    """Saves background tasks registry back to disk."""
    try:
        os.makedirs(os.path.dirname(REGISTRY_PATH), exist_ok=True)
        with open(REGISTRY_PATH, "w") as f:
            json.dump(registry, f, indent=2)
    except Exception as e:
        logger.error("Failed to save tasks registry: %s", e)


def register_task(session_name: str, prompt: str) -> None:
    """Registers a newly spawned tmux agent task to the registry."""
    registry = _load_tasks_registry()
    registry[session_name] = {
        "prompt": prompt,
        "started_at": time.time(),
        "status": "running"
    }
    _save_tasks_registry(registry)


def cancel_tmux_session(session_name: str) -> bool:
    """
    Kills the specified tmux session and updates its registry status to 'cancelled'.
    """
    kill_cmd = ["tmux", "kill-session", "-t", session_name]
    res = subprocess.run(kill_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    registry = _load_tasks_registry()
    if session_name in registry:
        registry[session_name]["status"] = "cancelled"
        _save_tasks_registry(registry)
        
    return res.returncode == 0



def _find_new_html_files(start_time: float, search_dir: str = ".") -> list[str]:
    """
    Finds recursively any .html files in the target directory modified after start_time.
    Avoids virtual environments and git folders to keep scanning extremely fast.
    """
    import os
    html_files = []
    ignored_dirs = {".venv", ".git", "__pycache__", ".agents"}
    for root, dirs, files in os.walk(search_dir):
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


def _monitor_session(session_name: str, task_prompt: str, start_time: float, project_dir: str = ".") -> None:
    """
    Background worker that polls tmux to check if the session is still active.
    When the session exits, it updates the task status in the registry and alerts
    the user via desktop notification and sound.
    
    Why: Keeps Elora informed of background task completion, establishing a complete
    feedback loop and updating registry state dynamically.
    """
    logger.info("Started watcher thread for session: %s", session_name)
    
    # Poll every 2 seconds to check if tmux session is still active
    while True:
        time.sleep(2)
        check_cmd = ["tmux", "has-session", "-t", session_name]
        res = subprocess.run(check_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode != 0:
            # Session no longer exists (meaning the task finished and the shell exited)
            break
            
    logger.info("Watcher detected exit for session: %s", session_name)
    
    # Reload registry to check if the task was cancelled manually
    # Why: Avoids duplicate alerts or overwriting manual cancellations
    registry = _load_tasks_registry()
    if registry.get(session_name, {}).get("status") == "cancelled":
        logger.info("Session %s was cancelled manually. Skipping completion alerts.", session_name)
        return

    # Check the exit code of agy
    # Why: agy writes its exit code to this file before the tmux session ends,
    # letting us distinguish between success (0) and failure (non-zero).
    exit_file = os.path.expanduser(f"~/.config/elora/logs/{session_name}.exit")
    exit_code = -1
    if os.path.exists(exit_file):
        try:
            with open(exit_file, "r") as f:
                exit_code = int(f.read().strip())
        except Exception as e:
            logger.error("Failed to read exit code from %s: %s", exit_file, e)
            
    status = "completed"
    if exit_code != 0 and exit_code != -1:
        status = "failed"
        
    # Update task registry status
    if session_name in registry:
        if registry[session_name].get("status") == "running":
            registry[session_name]["status"] = status
            _save_tasks_registry(registry)
    
    # Check if any new HTML files were created/modified during the task execution
    new_htmls = _find_new_html_files(start_time, project_dir)
    preview_opened = False
    opened_file_name = ""
    
    if status == "completed" and new_htmls:
        # Sort by modification time descending to get the newest
        new_htmls.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        target_html = new_htmls[0]
        opened_file_name = os.path.basename(target_html)
        file_url = f"file://{target_html}"
        logger.info("Auto-previewing newly created HTML file: %s", file_url)
        preview_opened = open_browser_url(file_url)
    
    # Send success chime and notification
    if status == "completed":
        send_notification("Task Completed", f"The background agent finished the task: {task_prompt}")
    else:
        send_notification("Task Failed", f"The background agent failed the task: {task_prompt}")
    play_chime()
    
    # Speak completion alert out loud dynamically
    try:
        from elora.skills.voice import speak_text
        if status == "completed":
            if preview_opened:
                speak_text(f"Task complete. Opening the preview of {opened_file_name} in your browser.")
            else:
                speak_text("Task complete. The background agent finished the task.")
        else:
            speak_text("Task failed. The background agent encountered an error.")
    except Exception as e:
        logger.error("Failed to speak task completion status: %s", e)


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
    
    # Determine base directory based on prompt context (classroom vs coding/projects)
    classroom_keywords = ["classroom", "homework", "assignment", "class", "lecture", "student", "teacher", "coursework", "school", "study", "exam", "quiz"]
    is_classroom = any(kw in prompt.lower() for kw in classroom_keywords)
    
    if is_classroom:
        base_dir = os.path.expanduser("~/Documents/elora/elora_classroom")
    else:
        base_dir = os.path.expanduser("~/Documents/elora/elora_projects")
        
    # Extract or generate a clean project directory name
    import re
    project_name = None
    
    # Check for "project called/named <name>"
    match = re.search(r'\bproject\s+(?:called|named)\s+([a-zA-Z0-9_-]+)', prompt, re.IGNORECASE)
    if match:
        project_name = match.group(1)
    else:
        # Check for "project: <name>" or "project <name>" (excluding filler words)
        match = re.search(r'\bproject[\s:]+([a-zA-Z0-9_-]+)', prompt, re.IGNORECASE)
        if match and match.group(1).lower() not in ("called", "named", "a", "an", "the", "for", "in", "to", "of", "with"):
            project_name = match.group(1)
        else:
            # Check for "called/named <name>" (excluding filler words)
            match = re.search(r'\b(?:called|named)[\s:]+([a-zA-Z0-9_-]+)', prompt, re.IGNORECASE)
            if match and match.group(1).lower() not in ("a", "an", "the", "for", "in", "to", "of", "with", "project"):
                project_name = match.group(1)
            
    if not project_name:
        # Generate slug from first 4 alphanumeric words of prompt, excluding common filler words
        words = [w for w in re.findall(r'[a-zA-Z0-9]+', prompt) if w.lower() not in (
            "create", "make", "build", "write", "do", "fix", "a", "an", "the", "in", "for", "to", "project", "task", "session"
        )]
        if not words:
            words = re.findall(r'[a-zA-Z0-9]+', prompt)[:4]
        if words:
            project_name = "_".join(words[:4]).lower()
        else:
            project_name = f"task_{int(time.time())}"
            
    project_dir = os.path.join(base_dir, project_name)
    try:
        os.makedirs(project_dir, exist_ok=True)
        logger.info("Created project directory: %s", project_dir)
    except Exception as e:
        logger.error("Failed to create project directory %s: %s", project_dir, e)
        # Fallback to base_dir
        project_dir = base_dir
        os.makedirs(project_dir, exist_ok=True)
        
    # Locate the absolute path of agy
    import shutil
    agy_path = shutil.which("agy") or "/usr/bin/agy"
    
    # Construct the tmux shell invocation command safely using shlex.quote
    # We append a workspace hint to the prompt so the background agent knows to initialize
    # files directly inside the current workspace directory (using '.') instead of nesting them.
    hint = f"\n\n[Workspace Hint: You are executing directly inside the designated project directory at '{project_dir}'. Please create, edit, or initialize files directly in this directory. For example, if initializing a React/Next.js/Vite app, run the generator tool with target '.' or the current directory rather than creating a nested subfolder.]"
    prompt_with_hint = prompt + hint
    escaped_prompt = shlex.quote(prompt_with_hint)
    
    # Define log files inside the ~/.config/elora/logs directory
    # Why: Since tmux sessions close automatically when the running command ends (with --print),
    # we pipe stdout/stderr through tee and log to disk so log history is preserved and accessible.
    log_dir = os.path.expanduser("~/.config/elora/logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"{session_name}.log")
    exit_file = os.path.join(log_dir, f"{session_name}.exit")
    
    # Run agy with --print (instead of --prompt-interactive) so it exits automatically when done.
    # We pipe outputs through tee to log_file, capture agy's exit code, write it to exit_file,
    # and exit the shell with the same code.
    # We cd to project_dir first before running agy so the agent works in the target folder.
    inner_cmd = f"{agy_path} --dangerously-skip-permissions --mode accept-edits --print-timeout 20m --print {escaped_prompt}"
    bash_cmd = f"cd {shlex.quote(project_dir)} && {inner_cmd} 2>&1 | tee {shlex.quote(log_file)}; exit_status=${{PIPESTATUS[0]}}; echo \\$exit_status > {shlex.quote(exit_file)}; exit \\$exit_status"
    
    # tmux command: tmux new-session -d -s session_name -c starting_dir "cmd"
    # We wrap in bash -c to ensure the redirection, pipe, and exit code capture logic is executed.
    tmux_cmd = ["tmux", "new-session", "-d", "-s", session_name, "-c", project_dir, f"bash -c {shlex.quote(bash_cmd)}"]
    
    try:
        logger.info("Spawning background agent task in tmux: %s", session_name)
        subprocess.Popen(tmux_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Register the task in tasks.json
        register_task(session_name, prompt)
        
        # Spawn daemon watcher thread to play completion alerts when the tmux task exits
        t = threading.Thread(target=_monitor_session, args=(session_name, prompt, start_time, project_dir), daemon=True)
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
    # Expand shell variables and shortcuts (e.g. $(pwd), $PWD, ~, $HOME)
    for term in ("$(pwd)", "$(PWD)", "$pwd", "$PWD"):
        if term in url:
            url = url.replace(term, os.getcwd())
            
    for term in ("$home", "$HOME"):
        if term in url:
            url = url.replace(term, os.path.expanduser("~"))
            
    if "file://~" in url:
        url = url.replace("file://~", "file://" + os.path.expanduser("~"))
    elif url.startswith("~"):
        url = os.path.expanduser(url)

    from elora.core.config import load_config
    config = load_config()
    browser_cmd = config.get("browser", {}).get("default_command", "xdg-open")
    
    try:
        logger.info("Opening URL in browser: %s using %s", url, browser_cmd)
        subprocess.Popen([browser_cmd, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception as e:
        logger.error("Failed to launch system browser: %s", str(e))
        return False


def remove_task_from_registry(session_name: str) -> bool:
    """
    Removes the specified task from the registry if it exists and is not running.
    """
    check_cmd = ["tmux", "has-session", "-t", session_name]
    res = subprocess.run(check_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if res.returncode == 0:
        # Task is still running, do not remove
        return False

    registry = _load_tasks_registry()
    if session_name in registry:
        del registry[session_name]
        _save_tasks_registry(registry)
        return True
    return False


