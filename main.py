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

from elora.brain import query_elora
from elora.actions import execute_agent_task, open_browser_url
from elora.news import get_news_summary, open_article
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
            print(f"\nElora: delegating task to background session...")
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
    Runs a single prompt non-interactively and processes the output.
    """
    result = query_elora(prompt, history=session_history)
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
            add_to_history("user", user_input)
            
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye!")
            break


def main() -> None:
    # Check if arguments are passed directly
    if len(sys.argv) > 1:
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
