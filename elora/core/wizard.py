"""
Elora Interactive Setup Wizard.
Guides the user through setting up API keys, Spotify CLI, and Google Classroom credentials.
"""

import os
import sys
import shutil
import subprocess
import json
from typing import Dict, Any
from elora.core.config import load_config, save_config

# Terminal colors for guided UX
BLUE = "\033[94m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"

def print_header(title: str):
    """
    Prints a formatted, colorized section header to highlight the active task.
    Why: Gives a professional, structured look to the terminal UI during setup.
    """
    print(f"\n{BOLD}{BLUE}=== {title} ==={RESET}\n")

def print_step(step_num: int, title: str):
    """
    Prints a numbered progress step bar to guide the user step-by-step.
    Why: Helps the user understand their exact location in the setup flow.
    """
    print(f"\n{BOLD}{BLUE}[Step {step_num}] {title}{RESET}")
    print(f"{BLUE}{'-' * 40}{RESET}")

def ask_yes_no(question: str, default: bool = False) -> bool:
    """
    Prompts the user with a binary yes/no question and defaults to the specified option on Enter.
    Why: Standardizes keyboard-driven binary choice validation.
    """
    choices = " [Y/n]: " if default else " [y/N]: "
    while True:
        ans = input(f"{BOLD}{question}{RESET}{choices}").strip().lower()
        if not ans:
            return default
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        print("Please answer yes (y) or no (n).")

def run_setup_wizard():
    """
    Runs the full configuration walkthrough flow.
    Why: Consolidates third-party API configurations, Spotify client checks/logins, and local system paths
    into a single step-by-step interactive CLI interface.
    """
    print_header("Elora Interactive Configuration Wizard")
    print("This wizard will help you set up Elora's integrations:")
    print("1. Google Gemini API Key (Required for core functionality)")
    print("2. Speech Feedback (Kokoro Local/Cloud)")
    print("3. Spotify CLI (Music search & playback control)")
    print("4. Google Classroom API (School course work sync)")
    
    config = load_config()
    
    # ----------------------------------------------------
    # Step 1: Gemini API Key
    # ----------------------------------------------------
    print_step(1, "Google Gemini API Key Setup")
    print("Elora utilizes Gemini (e.g. gemini-2.5-flash) for visual parsing, transcription,")
    print("and intelligent task orchestration. You can get a free key at https://aistudio.google.com/")
    
    existing_key = config.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY", "")
    if existing_key:
        masked_key = existing_key[:6] + "..." + existing_key[-4:] if len(existing_key) > 10 else "configured"
        print(f"\n{GREEN}✓ Gemini API key is already configured ({masked_key}).{RESET}")
        if ask_yes_no("Would you like to overwrite it?", default=False):
            key = input(f"{BOLD}Enter your Gemini API Key:{RESET} ").strip()
            if key:
                config["gemini_api_key"] = key
                save_config({"gemini_api_key": key})
                print(f"{GREEN}✓ Saved Gemini API key.{RESET}")
    else:
        key = input(f"\n{BOLD}Enter your Gemini API Key (or press Enter to skip):{RESET} ").strip()
        if key:
            config["gemini_api_key"] = key
            save_config({"gemini_api_key": key})
            print(f"{GREEN}✓ Saved Gemini API key.{RESET}")
        else:
            print(f"{YELLOW}! Skipped. Please set the GEMINI_API_KEY environment variable later.{RESET}")

    # ----------------------------------------------------
    # Step 2: Voice Feedback
    # ----------------------------------------------------
    print_step(2, "Voice Feedback Setup")
    print("Elora can respond to your commands out loud using Kokoro speech synthesis.")
    print("You can run Kokoro locally (downloads ~90MB weights and runs offline) or")
    print("host it in a Hugging Face Space (Cloud) to save local CPU/GPU memory.")
    
    voice_cfg = config.get("voice", {})
    voice_enabled = voice_cfg.get("enabled", False)
    
    status_str = "ENABLED" if voice_enabled else "DISABLED"
    print(f"\nVoice feedback is currently: {BOLD}{status_str}{RESET}")
    
    enable_voice = ask_yes_no("Would you like to enable Voice Feedback?", default=voice_enabled)
    if enable_voice:
        # Default choices
        provider = voice_cfg.get("provider", "local")
        print("\nSelect voice provider:")
        print(f"  1. Local offline engine (Kokoro-ONNX, uses CPU/RAM) [current: {'Active' if provider == 'local' else 'Inactive'}]")
        print(f"  2. Cloud Hugging Face Space (Saves local resources) [current: {'Active' if provider == 'cloud' else 'Inactive'}]")
        
        choice = input(f"\n{BOLD}Choose provider (1 or 2) [1]:{RESET} ").strip()
        selected_provider = "cloud" if choice == "2" else "local"
        
        voice_updates = {
            "enabled": True,
            "provider": selected_provider
        }
        
        if selected_provider == "cloud":
            hf_space = voice_cfg.get("hf_space_url", "")
            hf_token = voice_cfg.get("hf_token", "")
            
            print(f"\n{BOLD}Hugging Face Cloud Config:{RESET}")
            new_space = input(f"Enter Hugging Face Space URL [{hf_space}]: ").strip()
            if new_space:
                voice_updates["hf_space_url"] = new_space
            elif not hf_space:
                # Default fallback space url
                voice_updates["hf_space_url"] = "https://huggingface.co/spaces/hexgrad/Kokoro-TTS"
                print(f"Using default Space: {voice_updates['hf_space_url']}")
                
            new_token = input(f"Enter Hugging Face User Access Token (optional/press Enter) [{hf_token[:4] + '...' if hf_token else 'none'}]: ").strip()
            if new_token:
                voice_updates["hf_token"] = new_token
        
        # Save voice configs
        save_config({"voice": voice_updates})
        print(f"{GREEN}✓ Voice Feedback configured successfully.{RESET}")
    else:
        save_config({"voice": {"enabled": False}})
        print(f"{YELLOW}! Voice Feedback disabled.{RESET}")

    # ----------------------------------------------------
    # Step 3: Spotify Music Control
    # ----------------------------------------------------
    print_step(3, "Spotify CLI & Control Setup")
    print("Elora can control Spotify playback and perform fuzzy searches on your Liked Songs")
    print("and playlists. This requires `playerctl` and `spotify-cli` to be installed and authenticated.")
    
    if ask_yes_no("Would you like to configure Spotify control?", default=True):
        # 1. Resolve spotify-cli path
        spotify_path = shutil.which("spotify-cli") or os.path.expanduser("~/.local/bin/spotify-cli")
        is_installed = os.path.exists(spotify_path)
        
        if not is_installed:
            print(f"\n{YELLOW}spotify-cli is not detected on your system.{RESET}")
            if ask_yes_no("Would you like to install spotify-cli via uv now?", default=True):
                print(f"\n{BLUE}Installing spotify-cli...{RESET}")
                try:
                    subprocess.run(["uv", "tool", "install", "spotify-cli"], check=True)
                    # Resolve path again after installation
                    spotify_path = shutil.which("spotify-cli") or os.path.expanduser("~/.local/bin/spotify-cli")
                    is_installed = os.path.exists(spotify_path)
                    if is_installed:
                        print(f"{GREEN}✓ spotify-cli installed successfully!{RESET}")
                except Exception as e:
                    print(f"{RED}Error installing via uv: {e}{RESET}")
                    print("Attempting fallback with pipx...")
                    try:
                        subprocess.run(["pipx", "install", "spotify-cli"], check=True)
                        spotify_path = shutil.which("spotify-cli") or os.path.expanduser("~/.local/bin/spotify-cli")
                        is_installed = os.path.exists(spotify_path)
                        if is_installed:
                            print(f"{GREEN}✓ spotify-cli installed successfully via pipx!{RESET}")
                    except Exception as pe:
                        print(f"{RED}Failed to install spotify-cli. Error: {pe}{RESET}")
                        print("Please install it manually: `pip install --user spotify-cli` or `pipx install spotify-cli`")
        
        if is_installed:
            print(f"\n{GREEN}✓ spotify-cli is installed.{RESET}")
            print(f"To authenticate, a browser window will open. You will need to log in to Spotify,")
            print(f"authorize the application, and paste the redirected URL or token back into the terminal.")
            
            if ask_yes_no("Would you like to authenticate spotify-cli now?", default=True):
                print(f"\n{BLUE}Launching spotify-cli authentication...{RESET}")
                try:
                    # Run spotify-cli auth login interactively
                    subprocess.run([spotify_path, "auth", "login"], check=True)
                    print(f"{GREEN}✓ Spotify CLI authentication completed successfully!{RESET}")
                except Exception as e:
                    print(f"{RED}Failed to run spotify-cli authentication: {e}{RESET}")
                    print("You can always authenticate manually later by running:")
                    print(f"  {spotify_path} auth login")
        else:
            print(f"{YELLOW}! Skipped Spotify authentication since spotify-cli is not installed.{RESET}")

    # ----------------------------------------------------
    # Step 4: Google Classroom API Setup
    # ----------------------------------------------------
    print_step(4, "Google Classroom Integration Setup")
    print("Elora can list pending school assignments and download materials automatically.")
    print("This requires an OAuth Desktop Credentials JSON file from Google Cloud Console.")
    
    dest_credentials_path = os.path.expanduser("~/.config/elora/classroom_credentials.json")
    
    if os.path.exists(dest_credentials_path):
        print(f"\n{GREEN}✓ Google Classroom OAuth credentials already configured.{RESET}")
        setup_classroom = ask_yes_no("Would you like to replace the credentials?", default=False)
    else:
        setup_classroom = ask_yes_no("Would you like to configure Google Classroom credentials now?", default=False)
        
    if setup_classroom:
        print("\nSetup instructions:")
        print("1. Go to the Google Cloud Console (https://console.cloud.google.com/)")
        print("2. Create a project and enable 'Google Classroom API', 'Google Drive API', and 'Google Calendar API'.")
        print("3. Configure the OAuth Consent Screen and create OAuth 2.0 Client ID credentials (type: Desktop App).")
        print("4. Download the JSON credentials file to your local computer.")
        
        input_path = input(f"\n{BOLD}Enter the path to your downloaded credentials JSON file:{RESET} ").strip()
        if input_path:
            resolved_path = os.path.abspath(os.path.expanduser(input_path))
            if os.path.exists(resolved_path):
                try:
                    os.makedirs(os.path.dirname(dest_credentials_path), exist_ok=True)
                    shutil.copy2(resolved_path, dest_credentials_path)
                    print(f"{GREEN}✓ Google Classroom credentials copied to {dest_credentials_path}{RESET}")
                    print("Note: The browser authentication flow will launch automatically the first time you run")
                    print("a Google Classroom command in Elora.")
                except Exception as e:
                    print(f"{RED}Failed to copy credentials: {e}{RESET}")
            else:
                print(f"{RED}File not found: {resolved_path}. Skipping credentials setup.{RESET}")
        else:
            print(f"{YELLOW}! Skipped.{RESET}")
            
    print_header("Configuration Setup Finished!")
    print(f"{GREEN}All configured settings have been saved.{RESET}")
    print("You can modify these configurations at any time in the HUD Settings panel")
    print(f"or by editing {BOLD}~/.config/elora/config.json{RESET}")
    print("--------------------------------------------------------\n")

if __name__ == "__main__":
    run_setup_wizard()
