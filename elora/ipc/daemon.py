"""
Elora Background Daemon.
Coordinates the Gemini execution loop, manages conversation context,
and communicates with the HUD front-end via Unix sockets.
"""

import socket

# Force IPv4 only to bypass hanging on unreachable IPv6 addresses in some Linux environments.
# Why: httpx can get stuck attempting connections to IPv6 addresses that have no route,
# whereas curl falls back to IPv4 immediately.
_orig_getaddrinfo = socket.getaddrinfo
def _ipv4_only_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if family == socket.AF_UNSPEC:
        family = socket.AF_INET
    return _orig_getaddrinfo(host, port, family, type, proto, flags)
socket.getaddrinfo = _ipv4_only_getaddrinfo

import os
import sys
import time
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

    def __init__(self, conn: socket.socket, silence_detection: bool = True):
        super().__init__(daemon=True)
        self.conn = conn
        self.silence_detection = silence_detection
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
                if self.silence_detection:
                    if speech_detected:
                        if current_time - last_sound_time > SILENCE_TIMEOUT_SEC:
                            logger.debug("Silence timeout reached, finalising audio file.")
                            break
                    else:
                        # Timed out waiting for start of speech
                        if current_time - start_time > 5.0:
                            logger.debug("No speech detected.")
                            break
                else:
                    # Why: In Push-to-Talk (PTT) mode, silence timeouts are disabled so the user
                    # isn't cut off while holding Alt. We use a 120s safety limit to prevent runaways.
                    if current_time - start_time > 120.0:
                        logger.debug("Absolute PTT timeout reached, finalising audio file.")
                        break

            # Save captured frames to WAV
            output_path = "/tmp/elora_user_voice.wav"
            duration = time.monotonic() - start_time
            # Explain the "Why" as required by rules:
            # We only run transcription if speech was actually detected, preventing slow network API calls on silence.
            if speech_detected and pcm_frames and duration >= 0.5:
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
                        from elora.skills.voice import duck_media
                        duck_media()
                        if active_stt and active_stt.is_alive():
                            active_stt.stop()
                        # Extract silence_detection (default to True if not specified)
                        silence_detection = payload.get("silence_detection", True)
                        active_stt = ActiveSTTThread(conn, silence_detection=silence_detection)
                        active_stt.start()
                        
                    elif cmd == "stop_listen":
                        if active_stt:
                            active_stt.stop()
                            active_stt.join()
                            active_stt = None
                        else:
                            conn.sendall(b'{"status": "error", "message": "No active capture stream to stop"}\n')
                        from elora.skills.voice import unduck_media
                        unduck_media()
                            
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
                        
                        triggered_speech = False
                        
                        if action == "reply":
                            msg = args.get("message", "")
                            add_to_history("assistant", json.dumps(result))
                            if msg and not result.get("spoke_already", False):
                                speak_text(msg)
                                triggered_speech = True

                        elif action == "news_fetch":
                            mode = args.get("mode", "skim")
                            add_to_history("assistant", json.dumps(result))

                            if mode == "skim":
                                fetch_tech_news()
                                spoken = get_spoken_news_summary()
                                if spoken:
                                    speak_text(spoken)
                                    triggered_speech = True

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
                                    triggered_speech = True
                                    open_article(idx)

                                    if url:
                                        add_to_history(
                                            "user",
                                            f"[System info: Opened news article titled '{title}' (URL: {url}).]"
                                        )
                                else:
                                    speak_text("I couldn't find which article you wanted to open.")
                                    triggered_speech = True

                        elif action == "browser":
                            url = args.get("url", "")
                            add_to_history("assistant", json.dumps(result))
                            if url:
                                from urllib.parse import urlparse
                                domain = urlparse(url).netloc or url
                                speak_text(f"Opening {domain} in Brave. Tell me if you want a summary or details about it.")
                                triggered_speech = True
                                from elora.skills.actions import open_browser_url
                                open_browser_url(url)

                                add_to_history(
                                    "user",
                                    f"[System info: Navigated Brave browser to {url}.]"
                                )
                            else:
                                speak_text("No URL was provided.")
                                triggered_speech = True

                        elif action == "memory_focus":
                            active_focus = args.get("memory_block", "")
                            msg = args.get("message", f"Focusing on \"{args.get('query', '')}\" now.")
                            add_to_history("assistant", json.dumps(result))
                            speak_text(msg)
                            triggered_speech = True

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
                            triggered_speech = True
                            
                            from elora.skills.actions import execute_agent_task
                            session = execute_agent_task(prompt)
                            result["session"] = session

                        if not triggered_speech:
                            from elora.skills.voice import unduck_media
                            unduck_media()

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

                    elif cmd == "get_greeting":
                        # Why: We generate the greeting on the daemon to avoid loading heavy 
                        # ML/DL libraries (like PyTorch and SentenceTransformers) inside the short-lived GUI process.
                        try:
                            # 1. Fetch active tasks using internal tmux state & registry
                            from elora.skills.actions import _load_tasks_registry
                            registry = _load_tasks_registry()
                            
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

                            running_tasks = []
                            for sname, info in registry.items():
                                if sname in active_sessions and info.get("status") == "running":
                                    running_tasks.append({
                                        "session": sname,
                                        "prompt": info.get("prompt", "Unknown background agent task"),
                                        "started_at": info.get("started_at", 0.0),
                                        "status": "running"
                                    })
                            
                            active_running = running_tasks

                            if active_running:
                                # Retrieve status details from the first active running task
                                task = active_running[0]
                                session = task.get("session")
                                prompt = task.get("prompt", "")
                                started_at = task.get("started_at", 0.0)
                                
                                latest_line = ""
                                try:
                                    capture_cmd = ["tmux", "capture-pane", "-pt", session]
                                    res_log = subprocess.run(capture_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                                    if res_log.returncode == 0:
                                        raw_log = res_log.stdout.decode("utf-8", errors="replace")
                                        from elora.skills.skills import strip_ansi_codes
                                        cleaned_log = strip_ansi_codes(raw_log).strip()
                                        if cleaned_log:
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
                                conn.sendall((json.dumps({"status": "greeting_result", "result": result}) + "\n").encode("utf-8"))
                            else:
                                # 2. No active running tasks, query user memory for name
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

                                from datetime import datetime
                                import random
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
                                conn.sendall((json.dumps({"status": "greeting_result", "result": result}) + "\n").encode("utf-8"))
                        except Exception as e:
                            logger.error("Failed to generate greeting: %s", e)
                            conn.sendall(b'{"status": "error", "message": "Failed to generate greeting"}\n')


                        
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
            
            new_assignments = []
            missing_assignments = []
            due_soon_24h_to_notify = []

            for assignment in assignments:
                wid = assignment["id"]
                title = assignment["title"]
                course_name = assignment["course_name"]
                due_date_str = assignment.get("due_date")
                creation_time_str = assignment.get("creation_time")
                work_type = assignment.get("work_type", "ASSIGNMENT")
                
                # Check cache history
                cached_item = cached_assignments.get(wid, {})
                notified_created = cached_item.get("notified_created", False)
                notified_deadline_24h = cached_item.get("notified_deadline_24h", False)
                calendar_synced = cached_item.get("calendar_synced", False)
                
                due_dt = None
                if due_date_str:
                    try:
                        due_dt = datetime.datetime.fromisoformat(due_date_str)
                    except Exception:
                        pass
                
                # An assignment is missing (overdue) if the due date is in the past
                if due_dt and due_dt < now:
                    missing_assignments.append({
                        "id": wid,
                        "title": title,
                        "course_name": course_name,
                        "due_date": due_dt
                    })
                
                # 1. Detect New Assignment
                # Explain the "Why" as required by rules:
                # We filter new assignments to ensure we only notify on assignments created recently (within 7 days)
                # and whose due date is in the future, avoiding reporting old, already ended tasks.
                if last_checked and wid not in cached_assignments:
                    is_recent = True
                    if creation_time_str:
                        try:
                            creation_dt = datetime.datetime.fromisoformat(creation_time_str)
                            if creation_dt < now - datetime.timedelta(days=7):
                                is_recent = False
                        except Exception:
                            pass
                    
                    not_ended = True
                    if due_dt and due_dt < now:
                        not_ended = False
                        
                    if is_recent and not_ended:
                        new_assignments.append({
                            "id": wid,
                            "title": title,
                            "course_name": course_name,
                            "work_type": work_type,
                            "due_date": due_dt
                        })
                    notified_created = True
                elif not last_checked:
                    notified_created = True
                else:
                    # Mark previously unchecked assignments as processed/notified to prevent re-checks
                    notified_created = True
                    
                # 2. Check Upcoming Deadline (within 24 hours)
                if due_dt:
                    try:
                        time_left = due_dt - now
                        if datetime.timedelta(hours=0) < time_left <= datetime.timedelta(hours=24):
                            if not notified_deadline_24h:
                                due_soon_24h_to_notify.append({
                                    "title": title,
                                    "course_name": course_name,
                                    "hours_left": int(time_left.total_seconds() // 3600)
                                })
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
                    "work_type": work_type,
                    "creation_time": creation_time_str,
                    "notified_created": notified_created,
                    "notified_deadline_24h": notified_deadline_24h,
                    "calendar_synced": calendar_synced
                }

            # Deliver unified report for new assignments if any are found
            if new_assignments:
                # Helper functions for formatting assignment details
                def get_work_type_friendly(wt: Optional[str]) -> str:
                    if not wt:
                        return "Assignment"
                    wt_upper = wt.upper()
                    if "SHORT_ANSWER" in wt_upper or "QUESTION" in wt_upper:
                        return "Question"
                    if "ASSIGNMENT" in wt_upper:
                        return "Assignment"
                    return wt_upper.replace("_", " ").title()

                def format_due_date(dt: Optional[datetime.datetime]) -> str:
                    if not dt:
                        return "No due date"
                    return dt.strftime("%b %d, %I:%M %p")

                # Build formatted notification elements
                new_lines = []
                for a in new_assignments:
                    wt_friendly = get_work_type_friendly(a["work_type"])
                    due_friendly = format_due_date(a["due_date"])
                    new_lines.append(f"• [{a['course_name']}] {wt_friendly}: <i>{a['title']}</i> ({due_friendly})")

                missing_lines = []
                for a in missing_assignments:
                    due_friendly = format_due_date(a["due_date"])
                    missing_lines.append(f"• [{a['course_name']}] <i>{a['title']}</i> ({due_friendly})")

                msg_parts = ["<b>🔔 New:</b>"]
                msg_parts.extend(new_lines)

                if missing_lines:
                    msg_parts.append("")
                    msg_parts.append("<b>⚠️ Overdue/Missing:</b>")
                    msg_parts.extend(missing_lines)

                notification_body = "\n".join(msg_parts)
                send_notification("Google Classroom Update", notification_body)

                # Speak a clean verbal summary
                new_courses = list(set(a['course_name'] for a in new_assignments))
                if len(new_courses) == 1:
                    courses_phrase = f"in {new_courses[0]}"
                else:
                    courses_phrase = f"across {len(new_courses)} courses"

                speech_parts = []
                if len(new_assignments) == 1:
                    speech_parts.append(f"Boss, you have a new assignment {courses_phrase}.")
                else:
                    speech_parts.append(f"Boss, you have {len(new_assignments)} new assignments {courses_phrase}.")

                if missing_lines:
                    if len(missing_lines) == 1:
                        speech_parts.append("You also have one missing assignment.")
                    else:
                        speech_parts.append(f"You also have {len(missing_lines)} missing assignments.")

                speak_text(" ".join(speech_parts))

            # Deliver consolidated due soon notifications
            if due_soon_24h_to_notify:
                msg_parts = []
                speech_parts = ["Notice, boss:"]
                for a in due_soon_24h_to_notify:
                    msg_parts.append(f"• [{a['course_name']}] <i>{a['title']}</i> (due in {a['hours_left']} hours)")
                    speech_parts.append(f"the assignment, {a['title']}, is due in {a['hours_left']} hours.")
                
                send_notification("Assignments Due Soon", "\n".join(msg_parts))
                speak_text(" ".join(speech_parts))
                
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


def email_scheduler_loop():
    """
    Background loop that polls the user's email inbox for new unread emails.
    Triggers desktop notifications and speaks the details when new emails arrive.
    """
    import datetime
    import imaplib
    import email
    import email.utils
    import json
    from elora.core.config import load_config
    from elora.skills.email import decode_mime_header, load_env_credential
    from elora.utils import send_notification

    logger.info("Email background scheduler thread started.")
    
    # Wait for daemon startup to settle
    time.sleep(15)

    CACHE_DIR = os.path.expanduser("~/.cache/elora")
    CACHE_PATH = os.path.join(CACHE_DIR, "email_cache.json")

    while True:
        try:
            config = load_config()
            email_cfg = config.get("email", {})
            if not email_cfg.get("enabled", True):
                time.sleep(300)
                continue

            email_address = email_cfg.get("email_address", "")
            password_env = email_cfg.get("password_env_var", "ELORA_EMAIL_PASSWORD")
            imap_server = email_cfg.get("imap_server", "imap.gmail.com")
            imap_port = email_cfg.get("imap_port", 993)

            if not email_address:
                time.sleep(300)
                continue

            password = load_env_credential(password_env) or load_env_credential("ELORA_EMAIL_PASSWORD")
            if not password:
                logger.warning("Email scheduler: Password not configured. Set %s in ~/.env or environment.", password_env)
                time.sleep(300)
                continue

            # Load seen emails cache
            cache = {"seen_uids": []}
            if os.path.exists(CACHE_PATH):
                try:
                    with open(CACHE_PATH, "r") as f:
                        cache = json.load(f)
                except Exception:
                    pass

            seen_uids = cache.get("seen_uids", [])
            # Why: On the very first run (when seen_uids cache is empty), we mark all existing unread emails as seen
            # to avoid flooding the user with notification spam upon configuration.
            is_first_run = len(seen_uids) == 0

            # Connect to IMAP and wrap operations in finally to guarantee logout
            # Explain the "Why" as required by rules:
            # We use try/finally for the IMAP connection block to ensure the TCP connection and session 
            # are logged out cleanly even if network actions fail mid-cycle.
            mail = None
            try:
                mail = imaplib.IMAP4_SSL(imap_server, imap_port)
                mail.login(email_address, password)
                mail.select("INBOX")

                # Search unread (UNSEEN) emails using UID search to get persistent UIDs
                status, response_data = mail.uid('search', None, "UNSEEN")
                if status != "OK":
                    continue

                msg_uids = [uid.decode("utf-8") for uid in response_data[0].split() if uid]
                if not msg_uids:
                    continue

                new_uids_to_notify = []
                for uid in msg_uids:
                    if uid not in seen_uids:
                        seen_uids.append(uid)
                        new_uids_to_notify.append(uid)

                if new_uids_to_notify and not is_first_run:
                    # Group fetched email details
                    # Explain the "Why" as required by rules:
                    # We fetch headers of all new emails first, then build a consolidated notification report 
                    # instead of firing multiple notifications/spoken statements sequentially.
                    fetched_emails = []
                    for uid in new_uids_to_notify:
                        try:
                            res, data = mail.uid('fetch', uid, '(BODY[HEADER.FIELDS (FROM SUBJECT)])')
                            if res == "OK" and data[0]:
                                raw_headers = data[0][1]
                                msg = email.message_from_bytes(raw_headers)
                                sender = msg.get('From', '')
                                subject = msg.get('Subject', '')

                                sender_decoded = decode_mime_header(sender)
                                subject_decoded = decode_mime_header(subject)

                                realname, email_addr = email.utils.parseaddr(sender_decoded)
                                sender_name = realname or email_addr
                                
                                fetched_emails.append({
                                    "sender": sender_name,
                                    "subject": subject_decoded
                                })
                        except Exception as email_fetch_err:
                            logger.error("Failed to fetch headers for email UID %s: %s", uid, email_fetch_err)

                    if fetched_emails:
                        # 1. Build Consolidated Desktop Notification
                        notification_lines = ["<b>📬 New Emails Received:</b>"]
                        for em in fetched_emails:
                            subj_snippet = em["subject"][:60] + "..." if len(em["subject"]) > 60 else em["subject"]
                            notification_lines.append(f"• From: <b>{em['sender']}</b> - <i>{subj_snippet}</i>")
                        
                        send_notification("Unread Emails", "\n".join(notification_lines))

                        # 2. Build Consolidated Spoken Report
                        count = len(fetched_emails)
                        if count == 1:
                            speak_text(f"You have a new email from {fetched_emails[0]['sender']} about {fetched_emails[0]['subject']}.")
                        elif count <= 3:
                            speech_parts = [f"You have {count} new emails:"]
                            for i, em in enumerate(fetched_emails):
                                if i == count - 1:
                                    speech_parts.append(f"and one from {em['sender']} about {em['subject']}.")
                                else:
                                    speech_parts.append(f"one from {em['sender']} about {em['subject']},")
                            speak_text(" ".join(speech_parts))
                        else:
                            senders = list(dict.fromkeys(em['sender'] for em in fetched_emails)) # Unique senders
                            if len(senders) == 1:
                                speak_text(f"You have {count} new emails from {senders[0]}.")
                            elif len(senders) == 2:
                                speak_text(f"You have {count} new emails from {senders[0]} and {senders[1]}.")
                            else:
                                speak_text(f"You have {count} new emails from {senders[0]}, {senders[1]}, and others.")

                # Update cache file
                cache["seen_uids"] = seen_uids
                os.makedirs(CACHE_DIR, exist_ok=True)
                with open(CACHE_PATH, "w") as f:
                    json.dump(cache, f, indent=2)

            finally:
                if mail:
                    try:
                        mail.logout()
                    except Exception:
                        pass

        except Exception as e:
            logger.error("Error in email scheduler cycle: %s", e)

        # Poll every 5 minutes (300 seconds)
        time.sleep(300)


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

    # Start background email scheduler thread
    try:
        threading.Thread(target=email_scheduler_loop, name="EmailSchedulerThread", daemon=True).start()
        logger.info("Email scheduler thread started successfully.")
    except Exception as e:
        logger.error("Failed to start email scheduler thread: %s", e)

    # Start background Telegram bot listener if enabled
    try:
        from elora.skills.telegram_bot import start_telegram_bot
        threading.Thread(target=start_telegram_bot, name="TelegramBotThread", daemon=True).start()
        logger.info("Telegram Bot listener thread started successfully.")
    except Exception as e:
        logger.error("Failed to start Telegram Bot thread: %s", e)

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
