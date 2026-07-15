"""
Elora Spotify Control Skill.
Integrates user's spotify-cli (pipx) and playerctl to control Spotify.
Bypasses web scraping by using spotify-cli's native search API, and provides playerctl fallbacks.
Supports self-healing device activation and playerctl DBus fallback for playback session errors.
"""

import logging
import subprocess
import time
import os
import json
from typing import Optional, Tuple

logger = logging.getLogger("elora.spotify")

SPOTIFY_CLI = "/home/usoy/.local/bin/spotify-cli"


def run_spotify_cli(args: list) -> Tuple[bool, str]:
    """
    Runs the spotify-cli with given arguments.
    Returns (success, output_string).
    """
    if not os.path.exists(SPOTIFY_CLI):
        return False, "Error: spotify-cli executable not found at ~/.local/bin/spotify-cli."

    cmd = [SPOTIFY_CLI] + args
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        output = (res.stdout + "\n" + res.stderr).strip()
        
        # Check for authentication error
        if "CLI not authenticated" in output or "auth login" in output:
            return False, "Error: Spotify CLI is not authenticated. Please run 'spotify-cli auth login' in your terminal."
            
        if res.returncode != 0:
            return False, output or f"Error running spotify-cli (code {res.returncode})"
            
        return True, output
    except Exception as e:
        logger.error("Failed to run spotify-cli: %s", e)
        return False, f"Failed to execute spotify-cli command: {e}"


def is_spotify_running() -> bool:
    """Checks if Spotify is currently registered as an active player with playerctl."""
    try:
        output = subprocess.check_output(["playerctl", "-l"], stderr=subprocess.DEVNULL).decode().strip()
        players = [p.lower() for p in output.split("\n") if p.strip()]
        return any("spotify" in p for p in players)
    except Exception:
        return False


def ensure_spotify_running() -> bool:
    """Ensures Spotify is running. Launches it if not."""
    if is_spotify_running():
        return True

    logger.info("Spotify is not running. Launching application...")
    try:
        # Launch detached
        subprocess.Popen(
            ["spotify"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setpgrp if hasattr(os, "setpgrp") else None
        )
        # Wait a short moment for Spotify to start
        for _ in range(5):
            time.sleep(1.0)
            if is_spotify_running():
                return True
        return False
    except Exception as e:
        logger.error("Failed to launch Spotify: %s", e)
        return False


def activate_first_device() -> bool:
    """
    Attempts to find and activate the first available Spotify device.
    Useful when there is no active playback session.
    """
    ok, out = run_spotify_cli(["devices"])
    if not ok or "No active devices" in out or "Only devices with" in out:
        return False
        
    lines = out.strip().split("\n")
    devices = []
    for line in lines:
        line_clean = line.strip()
        if not line_clean or line_clean.startswith("Usage:") or line_clean.startswith("Manage"):
            continue
        # Remove leading * if present
        if line_clean.startswith("*"):
            line_clean = line_clean[1:].strip()
        # Device name is usually before the first '('
        if "(" in line_clean:
            name = line_clean.split("(")[0].strip()
        else:
            name = line_clean
        if name:
            devices.append(name)
            
    if not devices:
        return False
        
    # Switch to the first device
    logger.info("Attempting to activate Spotify device: %s", devices[0])
    switch_ok, switch_out = run_spotify_cli(["devices", "-s", devices[0]])
    return switch_ok


def search_spotify_uri_via_api(query: str, search_type: str = "--playlist") -> Optional[str]:
    """
    Searches Spotify via raw JSON API and returns the first result's URI.
    Does not require an active playback session.
    """
    ok, out = run_spotify_cli(["search", search_type, "--raw", query])
    if not ok:
        logger.warning("Spotify raw search failed: %s", out)
        return None
    try:
        data = json.loads(out)
        if "items" in data and len(data["items"]) > 0:
            uri = data["items"][0].get("uri")
            logger.info("Resolved Spotify query '%s' to URI: %s", query, uri)
            return uri
    except Exception as e:
        logger.error("Failed to parse raw search json: %s", e)
    return None


def play_spotify_uri(uri: str) -> str:
    """Plays a specific Spotify URI (track, playlist, album, artist)."""
    if not uri.startswith("spotify:"):
        return f"Error: Invalid Spotify URI format '{uri}'."

    # Try playing via spotify-cli first
    ok, out = run_spotify_cli(["play", "--uri", uri])
    if not ok:
        # Self-heal playback session errors
        if "No playback session" in out or "No active device" in out:
            ensure_spotify_running()
            time.sleep(1.5)
            # Try activating device
            if activate_first_device():
                ok, out = run_spotify_cli(["play", "--uri", uri])
                if ok:
                    return f"Playing URI via Spotify CLI after activating device: {uri}"
            
            # If activating device failed (e.g. still no active session),
            # fall back to playerctl open URI which tells Spotify desktop locally via DBus!
            try:
                logger.info("Falling back to playerctl open for URI: %s", uri)
                subprocess.run(["playerctl", "-p", "spotify", "open", uri], check=True)
                return f"Opened and playing URI locally via playerctl: {uri}"
            except Exception as e:
                return f"Failed to play Spotify URI locally: {e}\n(Web API error: {out})"
                
        # Handle auth errors or other errors
        return out
                
    return out


def search_and_play_spotify(query: str) -> str:
    """Searches Spotify using raw API search and plays the result."""
    ensure_spotify_running()
    
    # Determine the search type
    search_type = "--track"
    clean_query = query.lower().strip()
    
    # Strip common filler phrases
    for phrase in ["play track", "play song", "play playlist", "play album", "play"]:
        if clean_query.startswith(phrase):
            clean_query = clean_query[len(phrase):].strip()
            
    # Detect category keyword
    if "playlist" in query.lower():
        search_type = "--playlist"
        clean_query = clean_query.replace("playlist", "").strip()
    elif "album" in query.lower():
        search_type = "--album"
        clean_query = clean_query.replace("album", "").strip()
        
    logger.info("Searching and playing: %s (%s)", clean_query, search_type)
    
    # Resolve search query to a Spotify URI first (this does not require a playback session)
    uri = search_spotify_uri_via_api(clean_query, search_type)
    if not uri:
        return f"Could not find any Spotify {search_type.replace('--', '')} matching '{clean_query}'."
        
    # Play the resolved URI
    return play_spotify_uri(uri)


def control_spotify(action: str, value: Optional[str] = None) -> str:
    """
    Controls Spotify playback.
    Actions: play, pause, toggle (play-pause), next, previous, shuffle, volume, status.
    """
    action = action.lower().strip()

    if action in ("play", "pause", "toggle", "next", "previous"):
        # Map to CLI command
        cli_map = {
            "play": "play",
            "pause": "pause",
            "toggle": "toggle",
            "next": "next",
            "previous": "previous"
        }
        cli_cmd = cli_map[action]
        
        ok, out = run_spotify_cli([cli_cmd])
        if ok:
            return f"Spotify command '{action}' executed."
            
        # Fall back to playerctl
        ensure_spotify_running()
        playerctl_map = {
            "play": "play",
            "pause": "pause",
            "toggle": "play-pause",
            "next": "next",
            "previous": "previous"
        }
        playerctl_cmd = playerctl_map[action]
        try:
            subprocess.run(["playerctl", "-p", "spotify", playerctl_cmd], check=True)
            return f"Spotify command '{action}' executed via playerctl fallback.\n(Note: {out})"
        except Exception as e:
            return f"Failed to execute '{action}': {e}\n(Note: {out})"

    elif action == "shuffle":
        if not value:
            return "Error: Shuffle action requires a state value ('on', 'off', or 'toggle')."
        
        state = value.lower().strip()
        if state not in ("on", "off", "toggle"):
            return f"Error: Invalid shuffle state '{value}'. Must be 'on', 'off', or 'toggle'."

        if state == "toggle":
            ensure_spotify_running()
            try:
                subprocess.run(["playerctl", "-p", "spotify", "shuffle", "Toggle"], check=True)
                return "Spotify shuffle toggled."
            except Exception as e:
                return f"Failed to toggle shuffle: {e}"

        ok, out = run_spotify_cli(["shuffle", state])
        if ok:
            return f"Spotify shuffle set to {state}."
            
        # Fall back to playerctl
        ensure_spotify_running()
        playerctl_state = "On" if state == "on" else "Off"
        try:
            subprocess.run(["playerctl", "-p", "spotify", "shuffle", playerctl_state], check=True)
            return f"Spotify shuffle set to {playerctl_state} via playerctl fallback.\n(Note: {out})"
        except Exception as e:
            return f"Failed to set shuffle: {e}\n(Note: {out})"

    elif action == "volume":
        if not value:
            return "Error: Volume action requires a value."

        val_str = value.strip()
        
        # Try relative or absolute change via CLI
        if val_str.startswith("+") or val_str.startswith("-"):
            direction = "up" if val_str.startswith("+") else "down"
            amount = val_str.replace("+", "").replace("-", "").replace("%", "").strip()
            ok, out = run_spotify_cli(["volume", direction, amount])
        else:
            amount = val_str.replace("%", "").strip()
            ok, out = run_spotify_cli(["volume", "to", amount])
            
        if ok:
            return f"Spotify volume set to {value}."

        # Fall back to playerctl
        ensure_spotify_running()
        try:
            if val_str.startswith("+") or val_str.startswith("-"):
                diff = float(val_str.replace("%", "")) / 100.0
                sign = "+" if diff > 0 else "-"
                diff_abs = abs(diff)
                subprocess.run(["playerctl", "-p", "spotify", "volume", f"{diff_abs:.2f}{sign}"], check=True)
                return f"Spotify volume adjusted by {value} via playerctl fallback.\n(Note: {out})"
            else:
                level = float(val_str.replace("%", "")) / 100.0
                subprocess.run(["playerctl", "-p", "spotify", "volume", f"{level:.2f}"], check=True)
                return f"Spotify volume set to {val_str}% via playerctl fallback.\n(Note: {out})"
        except Exception as e:
            return f"Failed to adjust volume: {e}\n(Note: {out})"

    elif action == "status":
        ok, out = run_spotify_cli(["status"])
        if ok:
            return out
            
        # Fall back to playerctl metadata
        if not is_spotify_running():
            return f"Spotify is not running.\n(Note: {out})"
            
        try:
            status = subprocess.check_output(["playerctl", "-p", "spotify", "status"]).decode().strip()
            artist = subprocess.check_output(["playerctl", "-p", "spotify", "metadata", "artist"]).decode().strip()
            title = subprocess.check_output(["playerctl", "-p", "spotify", "metadata", "title"]).decode().strip()
            return f"Spotify Status (playerctl): {status}\nPlaying: {title} by {artist}\n(Note: {out})"
        except Exception:
            return f"Spotify is running, but failed to fetch metadata.\n(Note: {out})"

    else:
        return f"Error: Unknown Spotify action '{action}'."
