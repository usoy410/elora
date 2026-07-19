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

    def __init__(self, silence_detection: bool = True):
        super().__init__()
        self.silence_detection = silence_detection
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

        self.client.start_voice_listening(callback, silence_detection=self.silence_detection)

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
        # Why: We delegate task status checks and memory search to the background daemon
        # to prevent loading heavy modules (like PyTorch and ChromaDB) in this GUI process.
        from elora.ipc.daemon_client import EloraDaemonClient
        
        client = EloraDaemonClient()
        try:
            res = client.send_cmd({"cmd": "get_greeting"})
            if res.get("status") == "greeting_result":
                result = res.get("result", {})
                self.greeting_finished.emit(result)
                return
        except Exception as e:
            logger.error("Failed to query daemon for greeting: %s", e)

        # Fallback if connection failed or error returned
        fallback_result = {
            "type": "fresh_greeting",
            "greeting": "Hello boss. Systems are ready. How can I assist you?"
        }
        self.greeting_finished.emit(fallback_result)
