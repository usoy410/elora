"""
Background threads and workers for Elora HUD.
Uses QThread to prevent freezing the PySide6 UI event loop.
"""

import json
import logging
from PySide6.QtCore import QThread, Signal

logger = logging.getLogger("elora.ui.threads")


class DaemonSTTThread(QThread):
    """Background thread delegating live audio recording and Vosk STT to the daemon."""
    status_changed = Signal(str, str)  # status, text payload

    def __init__(self):
        super().__init__()
        self.client = None

    def run(self):
        from elora.ipc.daemon_client import EloraDaemonClient
        self.client = EloraDaemonClient()

        def callback(res: dict):
            status = res.get("status")
            if status == "recording":
                self.status_changed.emit("recording", "")
            elif status == "partial_stream":
                self.status_changed.emit("partial_stream", res.get("text", ""))
            elif status == "partial":
                self.status_changed.emit("partial", res.get("text", ""))
            elif status == "final":
                self.status_changed.emit("final", res.get("text", ""))
            elif status == "error":
                self.status_changed.emit("error", res.get("message", ""))

        self.client.start_voice_listening(callback)

    def stop(self):
        if self.client:
            self.client.stop_voice_listening()


class DaemonQueryThread(QThread):
    """Background thread delegating LLM brain querying to the daemon."""
    status_changed = Signal(str)
    query_finished = Signal(dict)
    telemetry_received = Signal(dict)
    confirm_requested = Signal(str, dict)
    screenshot_requested = Signal()

    def __init__(self, prompt: str, save_history: bool = True):
        super().__init__()
        self.prompt = prompt
        self.save_history = save_history
        import threading
        self.confirm_event = threading.Event()
        self.confirm_decision = False
        self.screenshot_event = threading.Event()
        self.screenshot_success = False

    def run(self):
        from elora.ipc.daemon_client import EloraDaemonClient
        client = EloraDaemonClient()
        if not client.connect():
            self.query_finished.emit({
                "action": "reply",
                "arguments": {"message": "Elora background daemon is not running."}
            })
            return

        try:
            # Query the brain via Unix socket line protocol
            payload = json.dumps({"cmd": "query_brain", "text": self.prompt, "save_history": self.save_history}) + "\n"
            client.sock.sendall(payload.encode("utf-8"))

            f = client.sock.makefile("r", encoding="utf-8")
            while True:
                line = f.readline()
                if not line:
                    break
                res = json.loads(line.strip())
                status = res.get("status")
                if status == "brain_status":
                    self.status_changed.emit(res.get("text", ""))
                elif status == "brain_telemetry":
                    self.telemetry_received.emit(res.get("telemetry", {}))
                elif status == "confirm_request":
                    self.confirm_event.clear()
                    self.confirm_requested.emit(res.get("action", ""), res.get("arguments", {}))
                    self.confirm_event.wait()
                    resp_payload = json.dumps({"cmd": "confirm_response", "approved": self.confirm_decision}) + "\n"
                    client.sock.sendall(resp_payload.encode("utf-8"))
                elif status == "screenshot_request":
                    self.screenshot_event.clear()
                    self.screenshot_requested.emit()
                    self.screenshot_event.wait()
                    resp_payload = json.dumps({"cmd": "screenshot_response", "success": self.screenshot_success}) + "\n"
                    client.sock.sendall(resp_payload.encode("utf-8"))
                elif status == "response":
                    self.query_finished.emit(res.get("result", {}))
                    break
                elif status == "error":
                    self.query_finished.emit({
                        "action": "reply",
                        "arguments": {"message": f"Daemon error: {res.get('message')}"}
                    })
                    break
        except Exception as e:
            logger.error("IPC query thread error: %s", e)
            self.query_finished.emit({
                "action": "reply",
                "arguments": {"message": f"Daemon communication failed: {e}"}
            })
        finally:
            client.close()


class NewsFetchThread(QThread):
    """Background thread to fetch RSS feeds without freezing the GUI."""
    feeds_fetched = Signal(list)

    def __init__(self, feeds: list):
        super().__init__()
        self.feeds = feeds

    def run(self):
        import feedparser
        results = []
        for feed_url in self.feeds:
            try:
                parsed = feedparser.parse(feed_url)
                feed_title = parsed.feed.get("title", "News")
                for entry in parsed.entries[:2]:
                    title = entry.get("title", "No Title")
                    link = entry.get("link", "")
                    results.append((title, feed_title, link))
            except Exception as e:
                logger.error("Failed to fetch feed %s: %s", feed_url, e)
        self.feeds_fetched.emit(results)


class TaskListFetchThread(QThread):
    """Background thread to query the daemon for active tmux tasks."""
    tasks_fetched = Signal(dict)

    def run(self):
        try:
            from elora.ipc.daemon_client import EloraDaemonClient
            client = EloraDaemonClient()
            res = client.send_cmd({"cmd": "list_tasks"})
            self.tasks_fetched.emit(res)
        except Exception as e:
            logger.error("TaskListFetchThread error: %s", e)
            self.tasks_fetched.emit({"status": "error", "message": str(e)})


class TaskLogFetchThread(QThread):
    """Background thread to query the daemon for a specific task's log."""
    log_fetched = Signal(dict)

    def __init__(self, session: str):
        super().__init__()
        self.session = session

    def run(self):
        try:
            from elora.ipc.daemon_client import EloraDaemonClient
            client = EloraDaemonClient()
            res = client.send_cmd({"cmd": "get_task_log", "session": self.session})
            self.log_fetched.emit(res)
        except Exception as e:
            logger.error("TaskLogFetchThread error for session %s: %s", self.session, e)
            self.log_fetched.emit({"status": "error", "message": str(e)})


class TaskCancelThread(QThread):
    """Background thread to query the daemon to cancel a specific task."""
    task_cancelled = Signal(dict)

    def __init__(self, session: str):
        super().__init__()
        self.session = session

    def run(self):
        try:
            from elora.ipc.daemon_client import EloraDaemonClient
            client = EloraDaemonClient()
            res = client.send_cmd({"cmd": "cancel_task", "session": self.session})
            self.task_cancelled.emit(res)
        except Exception as e:
            logger.error("TaskCancelThread error for session %s: %s", self.session, e)
            self.task_cancelled.emit({"status": "error", "message": str(e)})


class TaskRemoveThread(QThread):
    """Background thread to query the daemon to remove/clear a specific task."""
    task_removed = Signal(dict)

    def __init__(self, session: str):
        super().__init__()
        self.session = session

    def run(self):
        try:
            from elora.ipc.daemon_client import EloraDaemonClient
            client = EloraDaemonClient()
            res = client.send_cmd({"cmd": "remove_task", "session": self.session})
            self.task_removed.emit(res)
        except Exception as e:
            logger.error("TaskRemoveThread error for session %s: %s", self.session, e)
            self.task_removed.emit({"status": "error", "message": str(e)})



class ScreenExplanationThread(QThread):
    """Background thread delegating screen capture and explanation to the daemon."""
    explanation_finished = Signal(dict)

    def run(self):
        try:
            from elora.ipc.daemon_client import EloraDaemonClient
            client = EloraDaemonClient()
            res = client.explain_screen()
            self.explanation_finished.emit(res)
        except Exception as e:
            logger.error("ScreenExplanationThread error: %s", e)
            self.explanation_finished.emit({"status": "error", "message": str(e)})


class StartupGreetingThread(QThread):
    """Background thread to construct greeting/status message and fetch memory user name without freezing the GUI."""
    greeting_finished = Signal(dict)

    def __init__(self, config: dict):
        super().__init__()
        self.config = config

    def run(self):
        import time
        import random
        import json
        from datetime import datetime
        from elora.ipc.daemon_client import EloraDaemonClient
        
        client = EloraDaemonClient()
        
        # 1. Fetch active tasks from the daemon
        running_tasks = []
        try:
            res = client.send_cmd({"cmd": "list_tasks"})
            if res.get("status") == "tasks_list":
                running_tasks = res.get("tasks", [])
        except Exception as e:
            logger.error("Failed to query tasks list for greeting: %s", e)

        # Filter to actual running sessions
        active_running = [t for t in running_tasks if t.get("status") == "running"]

        if active_running:
            # Focus on the first active running task
            task = active_running[0]
            session = task.get("session")
            prompt = task.get("prompt", "")
            started_at = task.get("started_at", 0.0)
            
            latest_line = ""
            try:
                log_res = client.send_cmd({"cmd": "get_task_log", "session": session})
                if log_res.get("status") == "task_log":
                    raw_log = log_res.get("log", "")
                    from elora.skills.skills import strip_ansi_codes
                    cleaned_log = strip_ansi_codes(raw_log).strip()
                    if cleaned_log:
                        # Get the last 2 non-empty lines of the log
                        lines = [l.strip() for l in cleaned_log.split("\n") if l.strip()]
                        if lines:
                            latest_line = lines[-1]
                            if len(lines) > 1 and (latest_line.startswith("[") or len(latest_line) < 15):
                                latest_line = f"{lines[-2]} | {latest_line}"
            except Exception as e:
                logger.error("Failed to fetch log for greeting update: %s", e)

            elapsed = ""
            if started_at > 0:
                sec = int(time.time() - started_at)
                if sec < 60:
                    elapsed = f"{sec} seconds"
                elif sec < 3600:
                    elapsed = f"{sec//60} minutes and {sec%60} seconds"
                else:
                    elapsed = f"{sec//3600} hours and {(sec%3600)//60} minutes"
            else:
                elapsed = "some time"

            # Clean and truncate prompt for voice / output
            voice_prompt = prompt[:80] + "..." if len(prompt) > 80 else prompt
            
            if len(active_running) > 1:
                update_text = f"I am currently running {len(active_running)} background tasks. The primary task is: '{voice_prompt}', started {elapsed} ago."
            else:
                update_text = f"I am currently running the task: '{voice_prompt}', started {elapsed} ago."
                
            if latest_line:
                speech_latest = latest_line[:120] + "..." if len(latest_line) > 120 else latest_line
                update_text += f" The latest progress is: {speech_latest}"
            else:
                update_text += " No progress logs are available yet."

            result = {
                "type": "active_tasks",
                "update_text": update_text,
            }
            self.greeting_finished.emit(result)
            return

        # 2. No active running tasks, proceed with fresh greeting and reset history
        user_name = "boss"
        try:
            from elora.core.memory import is_memory_available, search_memory
            avail, _ = is_memory_available()
            if avail:
                results = search_memory("my name is", top_k=1, threshold=0.5)
                if not results:
                    results = search_memory("call me", top_k=1, threshold=0.5)
                if results:
                    text = results[0]["text"]
                    text_lower = text.lower()
                    for pattern in ("name is", "call me"):
                        if pattern in text_lower:
                            extracted = text[text_lower.index(pattern) + len(pattern):].strip()
                            extracted = extracted.rstrip(".").rstrip("!").strip()
                            if extracted:
                                user_name = extracted
                                break
        except Exception as e:
            logger.error("Failed to recall user name from memory: %s", e)

        hour = datetime.now().hour
        if hour < 12:
            time_of_day = "morning"
        elif hour < 17:
            time_of_day = "afternoon"
        else:
            time_of_day = "evening"

        greetings = [
            f"Good {time_of_day} {user_name}, Elora standing by.",
            f"Hello {user_name}. Systems are green and ready.",
            f"Welcome back {user_name}. What is your command?",
            f"System initialized. How can I assist you this {time_of_day}, {user_name}?",
            f"Greetings {user_name}. Standing by for instructions.",
            f"Elora online, {user_name}. What shall we work on?"
        ]
        local_greeting = random.choice(greetings)

        result = {
            "type": "fresh_greeting",
            "greeting": local_greeting,
        }
        self.greeting_finished.emit(result)
