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

from elora.skills.voice import speak_text
from elora.core.brain import query_elora
from elora.core.config import load_config
from elora.skills.news import get_spoken_news_summary, fetch_tech_news, open_article

# Setup daemon logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (EloraDaemon) %(message)s"
)
logger = logging.getLogger("elora.daemon")

SOCKET_PATH = "/tmp/elora.sock"
session_history: List[Dict[str, str]] = []
# Active focus block — injected as context prefix when set; cleared on 'clear focus'
active_focus: str = ""


def add_to_history(role: str, content: str) -> None:
    global session_history
    session_history.append({"role": role, "content": content})
    if len(session_history) > 20:  # Expanded context history to 20 messages
        session_history.pop(0)


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

                        from elora.core.agent import run_agent_loop
                        result = run_agent_loop(text, effective_history, status_cb, confirm_cb)
                        action = result.get("action")
                        args = result.get("arguments", {})
                        
                        if action == "reply":
                            msg = args.get("message", "")
                            add_to_history("assistant", json.dumps(result))
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
                        
                    elif cmd == "speak":
                        text = payload.get("text", "")
                        speak_text(text)
                        conn.sendall(b'{"status": "done"}\n')
                        
                    elif cmd == "get_history":
                        conn.sendall((json.dumps({"status": "history", "history": session_history}) + "\n").encode("utf-8"))
                        
                    elif cmd == "reset_history":
                        session_history.clear()
                        conn.sendall(b'{"status": "reset"}\n')
                        
                    elif cmd == "add_history":
                        role = payload.get("role", "")
                        content = payload.get("content", "")
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
