"""
Elora Daemon IPC Client.
Exposes standard methods for front-end HUD windows to communicate with the background daemon.
"""

import json
import logging
import os
import socket
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("elora.daemon_client")
SOCKET_PATH = "/tmp/elora.sock"


class EloraDaemonClient:
    def __init__(self):
        self.sock = None
        self.listen_thread = None
        self.listening = False

    def connect(self) -> bool:
        """Establishes connection to background daemon Unix socket."""
        if not os.path.exists(SOCKET_PATH):
            return False
        try:
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.connect(SOCKET_PATH)
            
            # Sync key graphical session environment variables from client to daemon
            env_to_sync = {
                k: v for k, v in os.environ.items()
                if k in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS")
            }
            if env_to_sync:
                try:
                    payload = json.dumps({"cmd": "update_env", "env": env_to_sync}) + "\n"
                    self.sock.sendall(payload.encode("utf-8"))
                    # Consume the response line
                    f = self.sock.makefile("r", encoding="utf-8")
                    f.readline()
                except Exception as e:
                    logger.warning("Failed to sync environment variables to daemon: %s", e)
            return True
        except Exception as e:
            logger.error("Failed to connect to daemon socket: %s", e)
            self.sock = None
            return False

    def close(self):
        """Closes the current socket connection."""
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def send_cmd(self, cmd_dict: dict[str, Any]) -> dict[str, Any]:
        """Sends a command to the daemon and returns the single response object."""
        # Always connect/reconnect on standard commands to ensure connection is fresh
        if not self.connect():
            return {"status": "error", "message": "Elora background daemon is not running."}
        try:
            payload = json.dumps(cmd_dict) + "\n"
            self.sock.sendall(payload.encode("utf-8"))
            
            # Read single-line JSON response
            f = self.sock.makefile("r", encoding="utf-8")
            line = f.readline()
            if not line:
                self.close()
                return {"status": "error", "message": "Daemon connection closed prematurely."}
            return json.loads(line.strip())
        except Exception as e:
            self.close()
            return {"status": "error", "message": f"IPC error: {e}"}
        finally:
            self.close()

    def start_voice_listening(self, callback: Callable[[dict[str, Any]], None], silence_detection: bool = True):
        """Initiates recording stream on the daemon and routes real-time transcriptions to callback."""
        if not self.connect():
            callback({"status": "error", "message": "Elora background daemon is not running."})
            return
        
        try:
            payload = json.dumps({"cmd": "start_listen", "silence_detection": silence_detection}) + "\n"
            self.sock.sendall(payload.encode("utf-8"))
            self.listening = True
            
            def read_loop():
                try:
                    f = self.sock.makefile("r", encoding="utf-8")
                    while self.listening:
                        line = f.readline()
                        if not line:
                            break
                        res = json.loads(line.strip())
                        callback(res)
                        if res.get("status") in ("final", "error"):
                            break
                except Exception as e:
                    callback({"status": "error", "message": f"Streaming socket error: {e}"})
                finally:
                    self.listening = False
                    self.close()

            self.listen_thread = threading.Thread(target=read_loop, daemon=True)
            self.listen_thread.start()
        except Exception as e:
            self.listening = False
            self.close()
            callback({"status": "error", "message": f"Failed to start audio stream: {e}"})

    def stop_voice_listening(self):
        """Sends stop command down the streaming channel, stopping arecord and returning final text."""
        if self.sock and self.listening:
            try:
                payload = json.dumps({"cmd": "stop_listen"}) + "\n"
                self.sock.sendall(payload.encode("utf-8"))
            except Exception as e:
                logger.error("Failed to send stop command: %s", e)

    def explain_screen(self) -> dict[str, Any]:
        """Tells the daemon to capture and explain the screen contents, returning the explanation."""
        return self.send_cmd({"cmd": "explain_screen"})
