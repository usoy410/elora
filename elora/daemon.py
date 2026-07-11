"""
Elora Background Daemon.
Preloads voice synthesis and speech recognition models, manages conversation context,
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
from typing import Optional, Dict, Any, List

# Ensure package directory is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from elora.stt import _get_stt_model
from elora.voice import _get_kokoro_client, speak_text
from elora.brain import query_elora
from elora.config import load_config
from elora.news import get_spoken_news_summary, fetch_tech_news, open_article

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


# Seconds of silence (no committed text arriving) before auto-finalising the utterance.
_SILENCE_TIMEOUT_SEC = 1.8

# Audio chunk size in bytes. 4000 bytes = 125ms at 16kHz / 16-bit / mono.
# Why: Reduces polling/chunk latency from 250ms to 125ms for faster, more responsive updates.
_CHUNK_BYTES = 4000


class ActiveSTTThread(threading.Thread):
    """
    Handles live raw PCM recording via arecord and streams partial/final results
    back to the connected client socket.

    Optimisations vs. the previous implementation:
    - Larger 250ms audio chunks reduce Python-level loop iterations and give Vosk
      more acoustic context per decode call.
    - Silence-based auto-stop: after _SILENCE_TIMEOUT_SEC of no new committed
      text the thread finalises automatically so the caller does not need to
      poll or send an explicit stop command.
    - Duplicate partial_stream suppression: only sends a socket message when the
      in-progress transcription text actually changes.
    """

    def __init__(self, conn: socket.socket, model):
        super().__init__(daemon=True)
        self.conn = conn
        self.model = model
        self.running = True
        self.process = None

    def run(self):
        from vosk import KaldiRecognizer
        rec = KaldiRecognizer(self.model, 16000)

        # arecord: mono raw PCM 16kHz S16_LE, device-default, quiet
        # Why: Pass -B 100000 (100ms) to tell ALSA to use a smaller capture buffer, reducing latency.
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

        try:
            committed_texts = []
            last_partial_sent = ""
            last_commit_time = time.monotonic()

            while self.running:
                # Read 250ms worth of PCM data per iteration
                data = self.process.stdout.read(_CHUNK_BYTES)
                if not data:
                    break

                if rec.AcceptWaveform(data):
                    # Vosk detected end-of-word boundary with silence
                    res = json.loads(rec.Result())
                    text = res.get("text", "").strip()
                    if text:
                        committed_texts.append(text)
                        last_commit_time = time.monotonic()
                        combined = " ".join(committed_texts)
                        # Emit a confirmed partial so the UI can update immediately
                        self._send({"status": "partial", "text": combined})
                        last_partial_sent = combined
                else:
                    # Emit live in-progress transcription, but only when it changed
                    partial = json.loads(rec.PartialResult())
                    partial_text = partial.get("partial", "").strip()
                    combined = " ".join(committed_texts + ([partial_text] if partial_text else [])).strip()
                    if combined and combined != last_partial_sent:
                        self._send({"status": "partial_stream", "text": combined})
                        last_partial_sent = combined

                # Auto-finalise when silence threshold is exceeded
                if (
                    committed_texts
                    and time.monotonic() - last_commit_time > _SILENCE_TIMEOUT_SEC
                ):
                    logger.debug("STT: silence timeout reached, auto-finalising.")
                    break

            # Flush any remaining partial text and emit final result
            res = json.loads(rec.FinalResult())
            final_segment = res.get("text", "").strip()
            full_text = " ".join(committed_texts + ([final_segment] if final_segment else [])).strip()
            self._send({"status": "final", "text": full_text})

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
                self.process.wait(timeout=2)
            except Exception:
                pass
            self.process = None


def handle_client(conn: socket.socket):
    """Processes incoming IPC requests from client interfaces."""
    global session_history, active_focus
    active_stt: Optional[ActiveSTTThread] = None
    
    # Read client payloads line by line
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
                        
                    elif cmd == "preload":
                        logger.info("Daemon preloading models...")
                        _get_stt_model()
                        _get_kokoro_client()
                        conn.sendall(b'{"status": "ready"}\n')
                        
                    elif cmd == "start_listen":
                        if active_stt and active_stt.is_alive():
                            active_stt.stop()
                        stt_model = _get_stt_model()
                        active_stt = ActiveSTTThread(conn, stt_model)
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
                        # message so the LLM can reference it without injecting it into
                        # the permanent session history.
                        effective_history = list(session_history)
                        if active_focus:
                            effective_history.insert(
                                max(0, len(effective_history) - 1),
                                {"role": "user", "content": active_focus}
                            )

                        def status_cb(status_text: str):
                            try:
                                conn.sendall((json.dumps({"status": "brain_status", "text": status_text}) + "\n").encode("utf-8"))
                            except Exception:
                                pass

                        from elora.agent import run_agent_loop
                        result = run_agent_loop(text, effective_history, status_cb)
                        action = result.get("action")
                        args = result.get("arguments", {})
                        
                        if action == "reply":
                            # Standard conversational response — speak the message directly
                            msg = args.get("message", "")
                            add_to_history("assistant", json.dumps(result))
                            speak_text(msg)

                        elif action == "news_fetch":
                            mode = args.get("mode", "skim")
                            add_to_history("assistant", json.dumps(result))

                            if mode == "skim":
                                # Fetch articles (populates cache), then speak titles-only summary
                                fetch_tech_news()
                                spoken = get_spoken_news_summary()
                                speak_text(spoken)

                            elif mode == "deep_dive":
                                idx = args.get("index")
                                if idx is not None:
                                    from elora.news import _article_cache
                                    try:
                                        article_idx = int(idx) - 1
                                        article = _article_cache[article_idx] if _article_cache else {}
                                        title = article.get("title", "the article")
                                        url = article.get("link", "")
                                    except Exception:
                                        title, url = "the article", ""

                                    speak_text(f"Opening {title} in your browser now. Tell me if you want a summary or details about it.")
                                    open_article(idx)

                                    # Inject context so follow-up questions ("summarize it",
                                    # "give me the key points") can resolve "it" to this article.
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
                                from elora.actions import open_browser_url
                                open_browser_url(url)

                                # Inject context so follow-up questions about this page work
                                add_to_history(
                                    "user",
                                    f"[System info: Navigated Brave browser to {url}.]"
                                )
                            else:
                                speak_text("No URL was provided.")

                        elif action == "memory_focus":
                            # Set the session's active focus block — persists until cleared
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
                            
                            from elora.actions import execute_agent_task
                            session = execute_agent_task(prompt)
                            result["session"] = session

                        conn.sendall((json.dumps({"status": "response", "result": result}) + "\n").encode("utf-8"))


                    elif cmd == "clear_focus":
                        # Wipe active focus so it's no longer prepended to queries
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
    logger.info("Pre-warming models and browser...")
    try:
        _get_stt_model()
        _get_kokoro_client()
        logger.info("Vosk and Kokoro models successfully loaded in background.")
    except Exception as e:
        logger.error("Failed to prewarm ML models: %s", e)
        
    try:
        from elora.browser_control import launch_brave_with_debugging
        if launch_brave_with_debugging():
            logger.info("Brave browser pre-warmed successfully.")
        else:
            logger.warning("Brave browser pre-warming failed.")
    except Exception as e:
        logger.error("Failed to prewarm Brave: %s", e)

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
