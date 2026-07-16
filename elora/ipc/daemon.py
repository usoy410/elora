"""
Elora Background Daemon.
Coordinates the Gemini execution loop, manages conversation context,
and communicates with the HUD front-end via Unix sockets.
"""

import os
import sys
import time
import socket
import json
import logging
import threading
import subprocess
import wave
import math
import struct
from typing import Optional, Dict, Any, List

# Ensure package directory is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Deferred imports helper wrappers to avoid importing heavy libraries on daemon launch.
# This makes the Unix socket creation and bind process instant.
def speak_text(text: str, audio_bytes = None, mime_type = None) -> None:
    from elora.skills.voice import speak_text as _speak_text
    _speak_text(text, audio_bytes, mime_type)

def fetch_tech_news():
    from elora.skills.news import fetch_tech_news as _fetch_tech_news
    return _fetch_tech_news()

def get_spoken_news_summary():
    from elora.skills.news import get_spoken_news_summary as _get_spoken_news_summary
    return _get_spoken_news_summary()

def open_article(index):
    from elora.skills.news import open_article as _open_article
    return _open_article(index)

# Setup daemon logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (EloraDaemon) %(message)s"
)
logger = logging.getLogger("elora.daemon")

SOCKET_PATH = "/tmp/elora.sock"
from elora.core.config import load_session_history, save_session_history
session_history: List[Dict[str, str]] = load_session_history(limit=20)
# Active focus block — injected as context prefix when set; cleared on 'clear focus'
active_focus: str = ""


def add_to_history(role: str, content: str) -> None:
    global session_history
    session_history.append({"role": role, "content": content})
    if len(session_history) > 20:  # Expanded context history to 20 messages
        session_history.pop(0)
    save_session_history(session_history, limit=20)



# Audio chunk size in bytes. 4000 bytes = 125ms at 16kHz / 16-bit / mono.
_CHUNK_BYTES = 4000


def calculate_rms(audio_data: bytes) -> float:
    """Calculates root-mean-square of raw 16-bit mono PCM audio data."""
    count = len(audio_data) // 2
    if count == 0:
        return 0.0
    format_str = f"<{count}h"
    try:
        shorts = struct.unpack(format_str, audio_data)
        sum_squares = sum(s * s for s in shorts)
        return math.sqrt(sum_squares / count)
    except Exception:
        return 0.0


class ActiveSTTThread(threading.Thread):
    """
    Handles live recording from microphone and energy-based silence detection.
    Saves output to WAV file and notifies client.
    """

    def __init__(self, conn: socket.socket):
        super().__init__(daemon=True)
        self.conn = conn
        self.running = True
        self.process = None

    def run(self):
        # Spawn arecord: 16kHz, 16-bit signed little-endian, mono, raw headerless output
        # Pass -B 100000 (100ms) to tell ALSA to use a smaller capture buffer, reducing latency.
        cmd = [
            "arecord", "-r", "16000", "-f", "S16_LE", "-c", "1", "-t", "raw", "-q",
            "-B", "100000"
        ]
        try:
            self.process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
            )
            self._send({"status": "recording"})
        except Exception as e:
            logger.error("Failed to spawn arecord: %s", e)
            self._send({"status": "error", "message": "arecord failed to launch. Check audio settings."})
            return

        # Silence detection settings
        SILENCE_THRESHOLD = 400.0
        SILENCE_TIMEOUT_SEC = 1.8
        start_time = time.monotonic()
        last_sound_time = time.monotonic()
        speech_detected = False

        try:
            pcm_frames = []

            while self.running:
                # Read 125ms worth of PCM data per iteration
                data = self.process.stdout.read(_CHUNK_BYTES)
                if not data:
                    break

                pcm_frames.append(data)
                current_time = time.monotonic()
                rms = calculate_rms(data)

                # Check if speech starts
                if rms > SILENCE_THRESHOLD:
                    if not speech_detected:
                        self._send({"status": "partial", "text": "Listening (Speech detected)..."})
                    speech_detected = True
                    last_sound_time = current_time

                # Auto-finalise when silence threshold is exceeded
                if speech_detected:
                    if current_time - last_sound_time > SILENCE_TIMEOUT_SEC:
                        logger.debug("Silence timeout reached, finalising audio file.")
                        break
                else:
                    # Timed out waiting for start of speech
                    if current_time - start_time > 5.0:
                        logger.debug("No speech detected.")
                        break

            # Save captured frames to WAV
            output_path = "/tmp/elora_user_voice.wav"
            duration = time.monotonic() - start_time
            if pcm_frames and duration >= 0.5:
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                with wave.open(output_path, "wb") as wav_file:
                    wav_file.setnchannels(1)
                    wav_file.setsampwidth(2)  # 16-bit
                    wav_file.setframerate(16000)
                    wav_file.writeframes(b"".join(pcm_frames))
                
                # Send a partial status showing that transcription is happening
                self._send({"status": "partial", "text": "Transcribing..."})
                
                from elora.core.brain import transcribe_audio
                transcribed_text = transcribe_audio(output_path)
                
                self._send({"status": "final", "text": transcribed_text})
            else:
                self._send({"status": "final", "text": ""})

        except Exception as e:
            logger.error("Error in live STT streaming loop: %s", e)
            self._send({"status": "error", "message": f"STT stream error: {e}"})
        finally:
            self.cleanup()

    def _send(self, payload: Dict[str, Any]):
        """Serialises and sends a JSON payload over the socket connection."""
        try:
            self.conn.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        except Exception:
            pass

    def stop(self):
        """Signals the run loop to stop and cleans up the arecord subprocess."""
        self.running = False
        self.cleanup()

    def cleanup(self):
        """Terminates the arecord process if it is still alive."""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=1)
            except Exception:
                pass
            self.process = None


def handle_client(conn: socket.socket):
    """Processes incoming IPC requests from client interfaces."""
    global session_history, active_focus
    active_stt: Optional[ActiveSTTThread] = None
    
    buffer = ""
    try:
        while True:
            data = conn.recv(4096).decode("utf-8")
            if not data:
                break
            buffer += data
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                
                try:
                    payload = json.loads(line)
                    cmd = payload.get("cmd")
                    
                    if cmd == "ping":
                        conn.sendall(b'{"status": "pong"}\n')
                        
                    elif cmd == "update_env":
                        env = payload.get("env", {})
                        logger.info("Updating daemon environment from client: %s", list(env.keys()))
                        for k, v in env.items():
                            os.environ[k] = v
                        conn.sendall(b'{"status": "env_updated"}\n')
                        
                    elif cmd == "preload":
                        logger.info("Daemon preloaded. Initiating model preloading...")
                        try:
                            from elora.skills.voice import preload_voice_model
                            threading.Thread(target=preload_voice_model, name="EloraVoicePreloadThread", daemon=True).start()
                        except Exception as e:
                            logger.error("Failed to preload voice model on cmd: %s", e)
                        conn.sendall(b'{"status": "ready"}\n')
                        
                    elif cmd == "start_listen":
                        if active_stt and active_stt.is_alive():
                            active_stt.stop()
                        active_stt = ActiveSTTThread(conn)
                        active_stt.start()
                        
                    elif cmd == "stop_listen":
                        if active_stt:
                            active_stt.stop()
                            active_stt.join()
                            active_stt = None
                        else:
                            conn.sendall(b'{"status": "error", "message": "No active capture stream to stop"}\n')
                            
                    elif cmd == "query_brain":
                        text = payload.get("text", "")
                        save_history = payload.get("save_history", True)
                        session_history = load_session_history(limit=20)
                        if save_history:
                            add_to_history("user", text)


                        # If a memory focus is active, prepend it as a system context
                        effective_history = list(session_history)
                        if active_focus:
                            effective_history.insert(
                                max(0, len(effective_history) - 1),
                                {"role": "user", "content": active_focus}
                            )

                        def status_cb(event: Any):
                            try:
                                if isinstance(event, dict):
                                    conn.sendall((json.dumps({"status": "brain_telemetry", "telemetry": event}) + "\n").encode("utf-8"))
                                else:
                                    conn.sendall((json.dumps({"status": "brain_status", "text": str(event)}) + "\n").encode("utf-8"))
                            except Exception as e:
                                logger.error("Failed to send status update: %s", e)

                        def confirm_cb(action: str, args: Dict[str, Any]) -> bool:
                            try:
                                # Send confirmation request over the socket
                                payload = {
                                    "status": "confirm_request",
                                    "action": action,
                                    "arguments": args
                                }
                                conn.sendall((json.dumps(payload) + "\n").encode("utf-8"))
                                
                                # Read response synchronously
                                response_bytes = b""
                                while b"\n" not in response_bytes:
                                    chunk = conn.recv(1024)
                                    if not chunk:
                                        return False
                                    response_bytes += chunk
                                    
                                line = response_bytes.split(b"\n")[0].decode("utf-8").strip()
                                resp = json.loads(line)
                                if resp.get("cmd") == "confirm_response":
                                    return resp.get("approved", False)
                                return False
                            except Exception as e:
                                logger.error("Error waiting for user confirmation via IPC: %s", e)
                                return False

                        def screenshot_cb() -> bool:
                            try:
                                # Send screenshot request over the socket
                                payload = {"status": "screenshot_request"}
                                conn.sendall((json.dumps(payload) + "\n").encode("utf-8"))
                                
                                # Read response synchronously
                                response_bytes = b""
                                while b"\n" not in response_bytes:
                                    chunk = conn.recv(1024)
                                    if not chunk:
                                        return False
                                    response_bytes += chunk
                                    
                                line = response_bytes.split(b"\n")[0].decode("utf-8").strip()
                                resp = json.loads(line)
                                if resp.get("cmd") == "screenshot_response":
                                    return resp.get("success", False)
                                return False
                            except Exception as e:
                                logger.error("Error waiting for screenshot response via IPC: %s", e)
                                return False

                        from elora.core.agent import run_agent_loop
                        result = run_agent_loop(text, effective_history, status_cb, confirm_cb, screenshot_cb)
                        action = result.get("action")
                        args = result.get("arguments", {})
                        
                        if action == "reply":
                            msg = args.get("message", "")
                            add_to_history("assistant", json.dumps(result))
                            if msg and not result.get("spoke_already", False):
                                speak_text(msg)

                        elif action == "news_fetch":
                            mode = args.get("mode", "skim")
                            add_to_history("assistant", json.dumps(result))

                            if mode == "skim":
                                fetch_tech_news()
                                spoken = get_spoken_news_summary()
                                speak_text(spoken)

                            elif mode == "deep_dive":
                                idx = args.get("index")
                                if idx is not None:
                                    from elora.skills.news import _article_cache
                                    try:
                                        article_idx = int(idx) - 1
                                        article = _article_cache[article_idx] if _article_cache else {}
                                        title = article.get("title", "the article")
                                        url = article.get("link", "")
                                    except Exception:
                                        title, url = "the article", ""

                                    speak_text(f"Opening {title} in your browser now. Tell me if you want a summary or details about it.")
                                    open_article(idx)

                                    if url:
                                        add_to_history(
                                            "user",
                                            f"[System info: Opened news article titled '{title}' (URL: {url}).]"
                                        )
                                else:
                                    speak_text("I couldn't find which article you wanted to open.")

                        elif action == "browser":
                            url = args.get("url", "")
                            add_to_history("assistant", json.dumps(result))
                            if url:
                                from urllib.parse import urlparse
                                domain = urlparse(url).netloc or url
                                speak_text(f"Opening {domain} in Brave. Tell me if you want a summary or details about it.")
                                from elora.skills.actions import open_browser_url
                                open_browser_url(url)

                                add_to_history(
                                    "user",
                                    f"[System info: Navigated Brave browser to {url}.]"
                                )
                            else:
                                speak_text("No URL was provided.")

                        elif action == "memory_focus":
                            active_focus = args.get("memory_block", "")
                            msg = args.get("message", f"Focusing on \"{args.get('query', '')}\" now.")
                            add_to_history("assistant", json.dumps(result))
                            speak_text(msg)

                        elif action == "antigravity":
                            prompt = args.get("prompt", "")
                            message = args.get("message", "")
                            if not message:
                                if len(prompt) < 60:
                                    message = f"Okay, starting the task: {prompt}. I will let you know when it is finished."
                                else:
                                    message = "I am launching the background agent to start the task. I will let you know once it is complete."
                            
                            add_to_history("assistant", json.dumps(result))
                            speak_text(message)
                            
                            from elora.skills.actions import execute_agent_task
                            session = execute_agent_task(prompt)
                            result["session"] = session

                        conn.sendall((json.dumps({"status": "response", "result": result}) + "\n").encode("utf-8"))

                    elif cmd == "clear_focus":
                        active_focus = ""
                        conn.sendall(b'{"status": "focus_cleared"}\n')
                        speak_text("Focus cleared. Back to normal conversation.")
                        
                    elif cmd == "list_tasks":
                        try:
                            # 1. Query active tmux sessions
                            active_sessions = set()
                            try:
                                output = subprocess.check_output(["tmux", "list-sessions"], stderr=subprocess.DEVNULL).decode()
                                for line in output.strip().split("\n"):
                                    if line:
                                        parts = line.split(":", 1)
                                        if parts:
                                            sname = parts[0].strip()
                                            if sname.startswith("elora-dev"):
                                                active_sessions.add(sname)
                            except Exception:
                                pass
                            
                            # 2. Match with registry
                            from elora.skills.actions import _load_tasks_registry, _save_tasks_registry
                            registry = _load_tasks_registry()
                            
                            # Automatically sync tasks that ended while the daemon wasn't active
                            changed = False
                            for sname, info in list(registry.items()):
                                if info.get("status") == "running" and sname not in active_sessions:
                                    exit_file = os.path.expanduser(f"~/.config/elora/logs/{sname}.exit")
                                    status = "completed"
                                    if os.path.exists(exit_file):
                                        try:
                                            with open(exit_file, "r") as f:
                                                exit_code = int(f.read().strip())
                                                if exit_code != 0:
                                                    status = "failed"
                                        except Exception:
                                            pass
                                    info["status"] = status
                                    changed = True
                                    
                            if changed:
                                _save_tasks_registry(registry)
                                
                            # 3. Separate active and finished tasks
                            running_tasks = []
                            historical_tasks = []
                            for sname, info in registry.items():
                                task_info = {
                                    "session": sname,
                                    "prompt": info.get("prompt", "Unknown background agent task"),
                                    "started_at": info.get("started_at", 0.0),
                                    "status": info.get("status", "completed")
                                }
                                # Double check active tmux state
                                if sname in active_sessions:
                                    task_info["status"] = "running"
                                    running_tasks.append(task_info)
                                else:
                                    historical_tasks.append(task_info)
                                    
                            # Sort by start time descending
                            running_tasks.sort(key=lambda t: t["started_at"], reverse=True)
                            historical_tasks.sort(key=lambda t: t["started_at"], reverse=True)
                            
                            # Combine, capping history at 15
                            tasks_list = running_tasks + historical_tasks[:15]
                            
                            conn.sendall((json.dumps({"status": "tasks_list", "tasks": tasks_list}) + "\n").encode("utf-8"))
                        except Exception as e:
                            logger.error("Failed to list tasks: %s", e)
                            conn.sendall((json.dumps({"status": "error", "message": str(e)}) + "\n").encode("utf-8"))

                    elif cmd == "cancel_task":
                        session_name = payload.get("session")
                        if session_name:
                            from elora.skills.actions import cancel_tmux_session
                            success = cancel_tmux_session(session_name)
                            conn.sendall((json.dumps({"status": "task_cancelled", "session": session_name, "success": success}) + "\n").encode("utf-8"))
                        else:
                            conn.sendall(b'{"status": "error", "message": "Missing session name"}\n')

                    elif cmd == "remove_task":
                        session_name = payload.get("session")
                        if session_name:
                            from elora.skills.actions import remove_task_from_registry
                            success = remove_task_from_registry(session_name)
                            conn.sendall((json.dumps({"status": "task_removed", "session": session_name, "success": success}) + "\n").encode("utf-8"))
                        else:
                            conn.sendall(b'{"status": "error", "message": "Missing session name"}\n')

                    elif cmd == "get_task_log":
                        session_name = payload.get("session")
                        if session_name:
                            log_text = ""
                            try:
                                # 1. Try to capture from active tmux session
                                capture_cmd = ["tmux", "capture-pane", "-pt", session_name]
                                res = subprocess.run(capture_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                                if res.returncode == 0:
                                    log_text = res.stdout.decode("utf-8", errors="replace")
                                else:
                                    # 2. Try reading from disk log file if tmux session ended
                                    log_file = os.path.expanduser(f"~/.config/elora/logs/{session_name}.log")
                                    if os.path.exists(log_file):
                                        with open(log_file, "r", errors="replace") as f:
                                            log_text = f.read()
                                    else:
                                        # Check if the tmux session actually exists.
                                        # Why: Avoids confusing "exit status 1" errors when a task completes or has not fully initialized.
                                        check_cmd = ["tmux", "has-session", "-t", session_name]
                                        check_res = subprocess.run(check_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                                        if check_res.returncode != 0:
                                            log_text = "Task completed or session not found."
                                        else:
                                            stderr_msg = res.stderr.decode("utf-8", errors="replace").strip()
                                            if "can't find pane" in stderr_msg or "no pane" in stderr_msg:
                                                log_text = "Initializing task..."
                                            elif "no server running" in stderr_msg:
                                                log_text = "Tmux server is not running."
                                            else:
                                                log_text = f"Waiting for task log output... ({stderr_msg})"
                            except Exception as e:
                                log_text = f"Error capturing pane/log: {e}"
                            conn.sendall((json.dumps({"status": "task_log", "session": session_name, "log": log_text}) + "\n").encode("utf-8"))
                        else:
                            conn.sendall(b'{"status": "error", "message": "Missing session name"}\n')

                    elif cmd == "speak":
                        text = payload.get("text", "")
                        speak_text(text)
                        conn.sendall(b'{"status": "done"}\n')

                    elif cmd == "explain_screen":
                        from elora.core.brain import explain_screen_content
                        explanation = explain_screen_content(capture=False)
                        speak_text(explanation)
                        # Add explanation to session history as assistant response
                        add_to_history("assistant", json.dumps({
                            "action": "reply",
                            "arguments": {"message": explanation}
                        }))
                        conn.sendall((json.dumps({"status": "explanation", "text": explanation}) + "\n").encode("utf-8"))

                    elif cmd == "is_speaking":
                        from elora.skills.voice import is_speaking
                        conn.sendall((json.dumps({"status": "speaking_status", "is_speaking": is_speaking()}) + "\n").encode("utf-8"))
                        
                    elif cmd == "get_history":
                        session_history = load_session_history(limit=20)
                        conn.sendall((json.dumps({"status": "history", "history": session_history}) + "\n").encode("utf-8"))
                        
                    elif cmd == "reset_history":
                        session_history.clear()
                        save_session_history(session_history, limit=20)
                        conn.sendall(b'{"status": "reset"}\n')
                        
                    elif cmd == "add_history":
                        role = payload.get("role", "")
                        content = payload.get("content", "")
                        session_history = load_session_history(limit=20)
                        add_to_history(role, content)
                        conn.sendall(b'{"status": "added"}\n')


                        
                except Exception as e:
                    logger.error("Error parsing/handling payload line: %s", e)
                    try:
                        conn.sendall((json.dumps({"status": "error", "message": str(e)}) + "\n").encode("utf-8"))
                    except Exception:
                        pass
    except Exception as e:
        logger.error("Connection handler exception: %s", e)
    finally:
        if active_stt:
            active_stt.stop()
        conn.close()


def classroom_scheduler_loop():
    """
    Background loop that polls Google Classroom for new assignments and upcoming deadlines.
    Syncs them to Google Calendar if enabled, and pushes desktop notifications.
    """
    import datetime
    logger.info("Classroom background scheduler thread started.")
    
    # Wait for daemon startup to settle
    time.sleep(10)
    
    from elora.skills.classroom import TOKEN_PATH, get_pending_assignments_raw, sync_assignment_to_calendar
    from elora.utils import send_notification
    from elora.skills.voice import speak_text
    
    CACHE_PATH = os.path.expanduser("~/.config/elora/classroom_cache.json")
    
    # Main polling loop
    while True:
        try:
            # Check if oauth token exists. If not, do not poll to avoid consent screen in background.
            if not os.path.exists(TOKEN_PATH):
                logger.debug("Classroom token not found. Skipping polling cycle.")
                time.sleep(1800)
                continue
                
            logger.info("Starting Classroom scheduler check...")
            
            # Fetch raw pending coursework items
            assignments = get_pending_assignments_raw()
            if assignments is None:
                logger.info("Classroom API connection offline or credentials invalid. Skipping cycle.")
                time.sleep(1800)
                continue
                
            # Load existing notification cache
            cache = {}
            if os.path.exists(CACHE_PATH):
                try:
                    with open(CACHE_PATH, "r") as f:
                        cache = json.load(f)
                except Exception as e:
                    logger.warning("Failed to load classroom cache: %s", e)
                    
            cached_assignments = cache.get("assignments", {})
            last_checked = cache.get("last_checked")
            
            updated_cache_assignments = {}
            now = datetime.datetime.now()
            
            for assignment in assignments:
                wid = assignment["id"]
                title = assignment["title"]
                course_name = assignment["course_name"]
                due_date_str = assignment.get("due_date")
                
                # Check cache history
                cached_item = cached_assignments.get(wid, {})
                notified_created = cached_item.get("notified_created", False)
                notified_deadline_24h = cached_item.get("notified_deadline_24h", False)
                calendar_synced = cached_item.get("calendar_synced", False)
                
                # 1. Detect New Assignment
                if last_checked and wid not in cached_assignments:
                    msg = f"New assignment in {course_name}: {title}"
                    logger.info("Notifying new assignment: %s", msg)
                    send_notification("New Assignment", msg)
                    speak_text(f"Boss, you have a new assignment in {course_name}: {title}")
                    notified_created = True
                elif not last_checked:
                    notified_created = True
                    
                # 2. Check Upcoming Deadline (within 24 hours)
                if due_date_str:
                    try:
                        due_dt = datetime.datetime.fromisoformat(due_date_str)
                        time_left = due_dt - now
                        if datetime.timedelta(hours=0) < time_left <= datetime.timedelta(hours=24):
                            if not notified_deadline_24h:
                                msg = f"Due in {int(time_left.total_seconds() // 3600)} hours: {title}"
                                logger.info("Notifying urgent deadline: %s", msg)
                                send_notification("Assignment Due Soon", msg)
                                speak_text(f"Notice, boss: the assignment, {title}, is due in less than 24 hours.")
                                notified_deadline_24h = True
                        else:
                            if time_left > datetime.timedelta(hours=24):
                                notified_deadline_24h = False
                    except Exception as date_err:
                        logger.warning("Error parsing due date for alert check: %s", date_err)
                        
                # 3. Google Calendar Sync
                if not calendar_synced and due_date_str:
                    logger.info("Syncing assignment %s to Google Calendar...", wid)
                    success = sync_assignment_to_calendar(assignment)
                    if success:
                        calendar_synced = True
                        
                updated_cache_assignments[wid] = {
                    "title": title,
                    "course_name": course_name,
                    "due_date": due_date_str,
                    "state": assignment["state"],
                    "notified_created": notified_created,
                    "notified_deadline_24h": notified_deadline_24h,
                    "calendar_synced": calendar_synced
                }
                
            # Save updated states to cache
            cache["last_checked"] = now.isoformat()
            cache["assignments"] = updated_cache_assignments
            
            os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
            with open(CACHE_PATH, "w") as f:
                json.dump(cache, f, indent=2)
                
            logger.info("Classroom scheduler check completed successfully.")
            
        except Exception as e:
            logger.error("Error in classroom scheduler cycle: %s", e)
            
        time.sleep(1800)


def run_daemon():
    """Starts the Unix socket daemon server."""
    if os.path.exists(SOCKET_PATH):
        try:
            os.remove(SOCKET_PATH)
        except Exception:
            pass

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET_PATH)
    server.listen(5)
    os.chmod(SOCKET_PATH, 0o777)
    
    logger.info("Elora Daemon active on Unix socket: %s", SOCKET_PATH)

    # Preload Kokoro voice engine model in background to avoid latency on first request
    try:
        from elora.skills.voice import preload_voice_model
        threading.Thread(target=preload_voice_model, name="EloraVoicePreloadThread", daemon=True).start()
    except Exception as e:
        logger.error("Failed to initiate voice model preloading: %s", e)

    # Start background classroom scheduler thread
    try:
        threading.Thread(target=classroom_scheduler_loop, name="ClassroomSchedulerThread", daemon=True).start()
    except Exception as e:
        logger.error("Failed to start classroom scheduler thread: %s", e)

    try:
        while True:
            conn, _ = server.accept()
            client_thread = threading.Thread(target=handle_client, args=(conn,), daemon=True)
            client_thread.start()
    except KeyboardInterrupt:
        logger.info("Daemon shutting down.")
    finally:
        if os.path.exists(SOCKET_PATH):
            os.remove(SOCKET_PATH)


if __name__ == "__main__":
    run_daemon()
