"""
Elora CLI & Core OS Orchestrator.
Entry point that handles interactive loop inputs, piped streams, and direct arguments.
"""

import sys
import os
import json
import logging
from typing import List, Dict

# Set logger configuration
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("elora.main")

from elora.core.brain import query_elora
from elora.skills.actions import execute_agent_task, open_browser_url
from elora.skills.news import get_news_summary, open_article
from elora.utils import send_notification

# Store a short message history to maintain context during interactive sessions
session_history: List[Dict[str, str]] = []


def add_to_history(role: str, content: str) -> None:
    """
    Appends a message to the history list, keeping it bounded to the last 10 entries.
    """
    global session_history
    session_history.append({"role": role, "content": content})
    if len(session_history) > 10:
        session_history.pop(0)


def process_action(payload: Dict[str, any]) -> None:
    """
    Routes the structured action JSON to the corresponding local execution script.
    
    Why: Keeps action execution fully isolated from the LLM prompt cycle.
    """
    action = payload.get("action")
    args = payload.get("arguments", {})
    
    if action == "reply":
        message = args.get("message", "")
        print(f"\nElora: {message}\n")
        add_to_history("assistant", json.dumps(payload))
        
        # Trigger speech synthesis dynamically if enabled
        from elora.skills.voice import speak_text
        speak_text(message)
        
        
    elif action == "news_fetch":
        mode = args.get("mode", "skim")
        if mode == "skim":
            print("\nElora: Fetching latest technical news feeds...")
            summary = get_news_summary()
            print(f"\n{summary}\n")
            # Store summary back in history so model remembers what indices represent
            add_to_history("assistant", json.dumps({
                "action": "reply",
                "arguments": {"message": f"Showed news skim: {summary}"}
            }))
        elif mode == "deep_dive":
            idx = args.get("index")
            if idx is not None:
                print(f"\nElora: Opening article number {idx} in your browser...")
                success = open_article(int(idx))
                if success:
                    print("Elora: Browser launched successfully.\n")
                else:
                    print("Elora: Failed to open article. Please check the index number.\n")
            else:
                print("\nElora: Missing article index to open.\n")
                
    elif action == "browser":
        url = args.get("url", "")
        if url:
            print(f"\nElora: Opening website {url}...")
            success = open_browser_url(url)
            if success:
                print("Elora: Navigation requested.\n")
            else:
                print("Elora: Unable to launch default web browser.\n")
        else:
            print("\nElora: Target URL not specified.\n")
            
    elif action == "antigravity":
        prompt = args.get("prompt", "")
        if prompt:
            message = args.get("message", "")
            if not message:
                if len(prompt) < 60:
                    message = f"Okay, starting the task: {prompt}. I will let you know when it is finished."
                else:
                    message = "I am launching the background agent to start the task. I will let you know once it is complete."
            
            print(f"\nElora: {message}\n")
            
            # Trigger speech synthesis dynamically if enabled
            from elora.skills.voice import speak_text
            speak_text(message)
            
            session = execute_agent_task(prompt)
            if session:
                print(f"Elora: Task active in background tmux session '{session}'.")
                print(f"       Attach anytime with: tmux attach -t {session}\n")
            else:
                print("Elora: Delegation to background session failed.\n")
        else:
            print("\nElora: Delegated task prompt cannot be empty.\n")
            
    else:
        print(f"\nElora: Unknown action requested: {action}\n")


def execute_single_prompt(prompt: str) -> None:
    """
    Runs a single prompt through the ReAct agent loop and processes the final action.
    """
    from elora.agent import run_agent_loop
    
    def print_status(event: Any):
        if isinstance(event, dict):
            etype = event.get("type")
            if etype == "thought":
                print(f"\n[Thinking] {event.get('text')}")
            elif etype == "tool_start":
                tool = event.get("tool")
                args = event.get("arguments", {})
                if tool == "command_run":
                    print(f"[*] Executing command: {args.get('command')}")
                elif tool == "web_search":
                    print(f"[*] Searching web for: '{args.get('query')}'")
                elif tool == "web_scrape":
                    print(f"[*] Scraping webpage: {args.get('url')}")
                elif tool.startswith("browser_"):
                    print(f"[*] Browser action: {tool} with args {args}")
                elif tool == "desktop_input":
                    print(f"[*] Desktop input: {args.get('input_type')}")
                elif tool == "system_control":
                    print(f"[*] System control: {args.get('control_type')}")
                else:
                    print(f"[*] Starting action '{tool}'...")
            elif etype == "tool_output":
                tool = event.get("tool")
                output = event.get("output", "")
                if output:
                    lines = str(output).strip().splitlines()
                    snippet = lines[0] if lines else ""
                    if len(lines) > 1:
                        snippet += f" ... ({len(lines)-1} more lines)"
                    print(f"[-] Tool '{tool}' returned: {snippet}")
                else:
                    print(f"[-] Tool '{tool}' completed.")
            elif etype == "confirm_request":
                # In CLI execute_single_prompt, run_agent_loop will fallback to input() if confirm_callback is None,
                # but we can print a warning notice here anyway
                pass
        else:
            print(f"[*] {event}")
        
    add_to_history("user", prompt)
    
    # We must import Any for type annotations inside execute_single_prompt
    from typing import Any
    result = run_agent_loop(prompt, session_history, print_status)
    process_action(result)


def start_interactive_loop() -> None:
    """
    Runs the interactive CLI loop for live conversational control.
    """
    print("===========================================")
    print("   ELORA: Linux Desktop OS Orchestrator    ")
    print("===========================================")
    print("Type your commands below (e.g. 'Fetch news', 'Go to google.com')")
    print("Press Ctrl+C or type 'exit' or 'quit' to close.\n")
    
    while True:
        try:
            user_input = input("Elora > ").strip()
            if not user_input:
                continue
                
            if user_input.lower() in ("exit", "quit"):
                print("Goodbye!")
                break
                
            execute_single_prompt(user_input)
            
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye!")
            break


def start_voice_assistant_loop() -> None:
    """
    Runs a hands-free conversational voice assistant loop.
    Repeatedly listens for voice input, executes prompt, and speaks response.
    """
    from elora.skills.stt import listen_voice
    from elora.core.config import set_config_override
    from elora.utils import play_chime
    import os
    
    chime_path = "/home/usoy/Documents/antigravity/elora/assets/sounds/success-chime.mp3"
    
    print("===========================================")
    print("  ELORA Voice Assistant Loop (Hands-Free)  ")
    print("===========================================")
    print("Make sure your microphone is unmuted.")
    print("Speak clearly. Elora auto-detects silence when you finish.")
    print("Press Ctrl+C at any time to exit.\n")
    
    # Dynamically force-enable voice output for this session
    set_config_override("voice", {"enabled": True})
    
    # Dynamic startup greeting
    print("Elora: Standing by...")
    try:
        from elora.skills.voice import speak_text
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
            f"Good {time_of_day} boss, Elora is standing by.",
            "Hello boss. Systems are ready. How can I assist you?",
            "Elora online. Standing by for your command, boss.",
            f"Welcome back boss. What shall we do this {time_of_day}?"
        ]
        msg = random.choice(greetings)
        print(f"Elora: {msg}\n")
        speak_text(msg)
        add_to_history("assistant", json.dumps({"action": "reply", "arguments": {"message": msg}}))
    except Exception as e:
        logger.error("Failed to generate voice loop startup greeting: %s", e)
        fallback = "Hello, I am Elora. Standing by."
        print(f"Elora: {fallback}\n")
        from elora.skills.voice import speak_text
        speak_text(fallback)
        add_to_history("assistant", json.dumps({"action": "reply", "arguments": {"message": fallback}}))
    
    while True:
        try:
            # Play a short alert chime so the user knows they can speak
            if os.path.exists(chime_path):
                play_chime(chime_path)
                
            voice_path = listen_voice()
            if not voice_path:
                continue
                
            print("\nElora: Transcribing...")
            from elora.core.brain import transcribe_audio
            transcribed_text = transcribe_audio(voice_path)
            if not transcribed_text:
                print("Elora: Could not transcribe audio or no speech detected.")
                continue
                
            print(f"\nYou said: \"{transcribed_text}\"")
            
            # Execute the prompt
            execute_single_prompt(transcribed_text)
            
        except KeyboardInterrupt:
            print("\nGoodbye!")
            break


def ensure_daemon_running() -> None:
    """Verifies if the background daemon is running. If not, spawns it as a subprocess."""
    import socket
    import subprocess
    import time
    
    SOCKET_PATH = "/tmp/elora.sock"
    try:
        # Check connection
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(SOCKET_PATH)
        s.close()
        return
    except Exception:
        print("Elora: Background daemon not running. Spawning daemon in background...")
        base_dir = os.path.dirname(os.path.abspath(__file__))
        
        # Detach child from parent process group so it runs persistent
        subprocess.Popen(
            [sys.executable, "-m", "elora.ipc.daemon"],
            cwd=base_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setpgrp
        )
        
        # Block briefly to wait for Unix socket creation
        for _ in range(50):
            if os.path.exists(SOCKET_PATH):
                try:
                    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    s.connect(SOCKET_PATH)
                    s.close()
                    print("Elora: Background daemon started successfully.")
                    return
                except Exception:
                    pass
            time.sleep(0.1)
        print("Elora Warning: Spawning daemon timed out. Models might load slowly.")


def main() -> None:
    # Check if arguments are passed directly
    if len(sys.argv) > 1:
        # Check if the user wants to start the daemon process directly
        if sys.argv[1] == "--daemon":
            from elora.ipc.daemon import run_daemon
            run_daemon()
            return

        # Check if the user wants to launch the voice assistant loop
        if sys.argv[1] == "--voice":
            ensure_daemon_running()
            start_voice_assistant_loop()
            return
            
        # Check if the user wants to launch the centralized HUD v2 window
        if sys.argv[1] == "--hud":
            ensure_daemon_running()
            from elora.hud import start_hud
            start_hud()
            return
            
        prompt = " ".join(sys.argv[1:])
        execute_single_prompt(prompt)
        return
        
    # Check if input is piped into standard input (non-interactive stream)
    if not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()
        if prompt:
            execute_single_prompt(prompt)
        return
        
    # Default to interactive REPL
    start_interactive_loop()


if __name__ == "__main__":
    main()
