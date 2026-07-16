"""
Elora Spotify Control Skill.
Integrates user's spotify-cli (pipx) and playerctl to control Spotify.
Bypasses web scraping by using spotify-cli's native search API, and provides playerctl fallbacks.
Supports self-healing device activation and playerctl DBus fallback for playback session errors.
Prioritizes user's owned/saved playlists and Liked Songs before falling back to global search.
"""

import logging
import subprocess
import time
import os
import json
import requests
from typing import Optional, Tuple
import difflib
import shutil

logger = logging.getLogger("elora.spotify")

# Resolve the spotify-cli executable path dynamically
SPOTIFY_CLI = shutil.which("spotify-cli") or os.path.expanduser("~/.local/bin/spotify-cli")


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


def get_spotify_access_token() -> Optional[str]:
    """
    Reads the access token from credentials.json.
    Triggers an automatic refresh if the token is expired (returns 401 on test call).
    """
    cred_path = os.path.expanduser("~/.config/spotify-cli/credentials.json")
    if not os.path.exists(cred_path):
        return None
        
    try:
        with open(cred_path) as f:
            creds = json.load(f)
        access_token = creds.get("access_token")
        
        # Test if the token is valid with a fast GET request
        headers = {"Authorization": f"Bearer {access_token}"}
        r = requests.get("https://api.spotify.com/v1/me", headers=headers, timeout=5)
        if r.status_code == 401:
            logger.info("Access token expired. Triggering refresh via spotify-cli status...")
            # Run status command to force CLI to refresh token and write back to credentials.json
            subprocess.run([SPOTIFY_CLI, "status"], capture_output=True, check=False)
            # Reload credentials
            with open(cred_path) as f:
                creds = json.load(f)
            access_token = creds.get("access_token")
            
        return access_token
    except Exception as e:
        logger.error("Failed to read/refresh access token: %s", e)
        return None


def clean_spotify_query(query: str) -> str:
    """
    Cleans up the user query by removing common filler words, prefixes,
    and suffixes to extract the core search term.
    """
    q = query.lower().strip()
    
    # Strip trailing punctuation (common in speech-to-text transcriptions)
    q = q.rstrip(".?!,;:")
    
    # 1. Strip common action prefixes
    prefixes = [
        "play track", "play song", "play playlist", "play album", "play music",
        "play a music", "play some music", "play", "search for", "search"
    ]
    for p in prefixes:
        if q.startswith(p):
            q = q[len(p):].strip()
            break
            
    # 2. Strip filler words from start/end iteratively
    fillers = ["in spotify", "on spotify", "in my", "on my", "my", "the", "a", "some", "in", "on", "by"]
    changed = True
    while changed:
        changed = False
        for f in fillers:
            if q.startswith(f + " "):
                q = q[len(f) + 1:].strip()
                changed = True
            elif q.endswith(" " + f):
                q = q[:-len(f) - 1].strip()
                changed = True
                
    # 3. Strip category suffixes/prefixes
    categories = ["playlist", "album", "song", "track", "music"]
    for c in categories:
        if q.endswith(" " + c):
            q = q[:-len(c) - 1].strip()
        elif q.startswith(c + " "):
            q = q[len(c) + 1:].strip()
            
    return q.strip()


def search_user_library(query: str, search_type: str) -> Optional[str]:
    """
    Searches the user's owned/saved playlists and Liked Songs (saved tracks).
    Returns the URI of the matching item if found, otherwise None.
    """
    token = get_spotify_access_token()
    if not token:
        logger.warning("No Spotify access token available for library search.")
        return None
        
    headers = {"Authorization": f"Bearer {token}"}
    
    # Clean the query using the normalizer
    q_match = clean_spotify_query(query)
    if not q_match:
        return None
        
    is_playlist_req = "playlist" in search_type or "playlist" in query.lower()
    
    # Helper to scan user's playlists
    def check_playlists() -> Optional[str]:
        try:
            url = "https://api.spotify.com/v1/me/playlists?limit=50"
            r = requests.get(url, headers=headers, timeout=5)
            if r.status_code == 200:
                items = r.json().get("items", [])
                
                # Tier 1: Exact match (case insensitive)
                for item in items:
                    name = item.get("name", "").lower()
                    if name == q_match:
                        logger.info("Found exact user playlist match: %s -> %s", item["name"], item["uri"])
                        return item["uri"]
                        
                # Tier 2: Close match using difflib
                playlist_names = [item.get("name", "").lower() for item in items if item.get("name")]
                matches = difflib.get_close_matches(q_match, playlist_names, n=1, cutoff=0.6)
                if matches:
                    best_match = matches[0]
                    for item in items:
                        if item.get("name", "").lower() == best_match:
                            logger.info("Found close user playlist match via difflib: %s -> %s", item["name"], item["uri"])
                            return item["uri"]
                            
                # Tier 3: Substring match
                for item in items:
                    name = item.get("name", "").lower()
                    if q_match in name or name in q_match:
                        logger.info("Found fuzzy user playlist match: %s -> %s", item["name"], item["uri"])
                        return item["uri"]
        except Exception as e:
            logger.error("Failed querying user playlists: %s", e)
        return None

    # Helper to scan user's Liked Songs
    def check_liked_songs() -> Optional[str]:
        try:
            url = "https://api.spotify.com/v1/me/tracks?limit=50"
            r = requests.get(url, headers=headers, timeout=5)
            if r.status_code == 200:
                items = r.json().get("items", [])
                
                # Tier 1: Exact match (case insensitive) on track name or artist name
                for item in items:
                    track = item.get("track", {})
                    name = track.get("name", "").lower()
                    artist = track.get("artists", [{}])[0].get("name", "").lower() if track.get("artists") else ""
                    if name == q_match or artist == q_match:
                        logger.info("Found exact user Liked Song match: %s by %s -> %s", track["name"], artist, track["uri"])
                        return track["uri"]
                        
                # Tier 2: Close match using difflib on track names
                track_names = [item.get("track", {}).get("name", "").lower() for item in items if item.get("track", {}).get("name")]
                matches = difflib.get_close_matches(q_match, track_names, n=1, cutoff=0.6)
                if matches:
                    best_match = matches[0]
                    for item in items:
                        track = item.get("track", {})
                        if track.get("name", "").lower() == best_match:
                            logger.info("Found close user Liked Song match via difflib: %s -> %s", track["name"], track["uri"])
                            return track["uri"]
                            
                # Tier 3: Substring match
                for item in items:
                    track = item.get("track", {})
                    name = track.get("name", "").lower()
                    artist = track.get("artists", [{}])[0].get("name", "").lower() if track.get("artists") else ""
                    if q_match in name or q_match in artist or name in q_match:
                        logger.info("Found fuzzy user Liked Song match: %s by %s -> %s", track["name"], artist, track["uri"])
                        return track["uri"]
        except Exception as e:
            logger.error("Failed querying user liked tracks: %s", e)
        return None

    if is_playlist_req:
        logger.info("Searching user playlists first for: %s", q_match)
        uri = check_playlists()
        if uri:
            return uri
    else:
        logger.info("Searching user Liked Songs first for: %s", q_match)
        uri = check_liked_songs()
        if uri:
            return uri
        # Fallback to check playlists if not found in Liked Songs
        logger.info("Not found in Liked Songs. Checking user playlists for: %s", q_match)
        uri = check_playlists()
        if uri:
            return uri
            
    return None


def search_spotify_uri_via_api(query: str, search_type: str = "--playlist") -> Optional[str]:
    """
    Searches Spotify via raw JSON API and returns the first result's URI.
    Does not require an active playback session.
    """
    if not query or not query.strip():
        return None
        
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
    """Searches Spotify (prioritizing user's library) and plays the result."""
    if not query or not query.strip():
        return "Error: Search query cannot be empty."
        
    ensure_spotify_running()
    
    # Clean query to determine type and terms
    clean_query = clean_spotify_query(query)
    if not clean_query:
        return "Error: Cleaned search query is empty."
    
    search_type = "--track"
    # Detect category keyword
    if "playlist" in query.lower():
        search_type = "--playlist"
    elif "album" in query.lower():
        search_type = "--album"
        
    logger.info("Searching and playing: %s (%s)", clean_query, search_type)
    
    # 1. Search user's owned/saved library first
    uri = search_user_library(clean_query, search_type)
    
    # 2. Fall back to robust global API search if not found in library
    if not uri:
        logger.info("Not found in library. Performing global API search for: %s", clean_query)
        uri = search_spotify_uri_via_api(clean_query, search_type)
        
    if not uri:
        return f"Could not find any Spotify {search_type.replace('--', '')} matching '{clean_query}'."
        
    # Play the resolved URI
    return play_spotify_uri(uri)


def control_spotify(action: str, value: Optional[str] = None) -> str:
    """
    Controls Spotify playback.
    Actions: play, pause, toggle (play-pause), next, previous, shuffle, volume, status.
    
    Why: Prioritizes local DBus/playerctl control when the local Spotify client is active.
    This avoids roundtrip delay and potential active device sync issues with the Web API.
    """
    action = action.lower().strip()

    if action in ("play", "pause", "toggle", "next", "previous"):
        playerctl_map = {
            "play": "play",
            "pause": "pause",
            "toggle": "play-pause",
            "next": "next",
            "previous": "previous"
        }
        playerctl_cmd = playerctl_map[action]

        # Prioritize local playerctl control if Spotify client is running locally
        if is_spotify_running():
            try:
                subprocess.run(["playerctl", "-p", "spotify", playerctl_cmd], check=True)
                return f"Spotify command '{action}' executed locally via playerctl."
            except Exception as e:
                logger.warning("playerctl control failed locally: %s. Falling back to spotify-cli.", e)

        # Fallback to spotify-cli (Web API)
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
            return f"Spotify command '{action}' executed via spotify-cli."
            
        # If both fail and Spotify wasn't running, start it and try playerctl one last time
        if not is_spotify_running():
            ensure_spotify_running()
            try:
                subprocess.run(["playerctl", "-p", "spotify", playerctl_cmd], check=True)
                return f"Spotify command '{action}' executed via playerctl fallback after starting client.\n(Note: {out})"
            except Exception as e:
                return f"Failed to execute '{action}': {e}\n(Note: {out})"
        return f"Failed to execute '{action}': {out}"

    elif action == "shuffle":
        if not value:
            return "Error: Shuffle action requires a state value ('on', 'off', or 'toggle')."
        
        state = value.lower().strip()
        if state not in ("on", "off", "toggle"):
            return f"Error: Invalid shuffle state '{value}'. Must be 'on', 'off', or 'toggle'."

        if state == "toggle":
            if is_spotify_running():
                try:
                    subprocess.run(["playerctl", "-p", "spotify", "shuffle", "Toggle"], check=True)
                    return "Spotify shuffle toggled locally via playerctl."
                except Exception as e:
                    logger.warning("Failed to toggle shuffle locally: %s. Falling back to start client.", e)
            
            ensure_spotify_running()
            try:
                subprocess.run(["playerctl", "-p", "spotify", "shuffle", "Toggle"], check=True)
                return "Spotify shuffle toggled."
            except Exception as e:
                return f"Failed to toggle shuffle: {e}"

        # Handle 'on' or 'off'
        playerctl_state = "On" if state == "on" else "Off"
        if is_spotify_running():
            try:
                subprocess.run(["playerctl", "-p", "spotify", "shuffle", playerctl_state], check=True)
                return f"Spotify shuffle set to {playerctl_state} locally via playerctl."
            except Exception as e:
                logger.warning("Failed to set shuffle locally via playerctl: %s. Falling back to spotify-cli.", e)

        ok, out = run_spotify_cli(["shuffle", state])
        if ok:
            return f"Spotify shuffle set to {state}."
            
        # Fall back to playerctl after starting client if not running
        if not is_spotify_running():
            ensure_spotify_running()
            try:
                subprocess.run(["playerctl", "-p", "spotify", "shuffle", playerctl_state], check=True)
                return f"Spotify shuffle set to {playerctl_state} via playerctl fallback.\n(Note: {out})"
            except Exception as e:
                return f"Failed to set shuffle: {e}\n(Note: {out})"
        return f"Failed to set shuffle: {out}"

    elif action == "volume":
        if not value:
            return "Error: Volume action requires a value."

        val_str = value.strip()
        
        # Prioritize local playerctl volume control if running
        if is_spotify_running():
            try:
                if val_str.startswith("+") or val_str.startswith("-"):
                    diff = float(val_str.replace("%", "")) / 100.0
                    sign = "+" if diff > 0 else "-"
                    diff_abs = abs(diff)
                    subprocess.run(["playerctl", "-p", "spotify", "volume", f"{diff_abs:.2f}{sign}"], check=True)
                    return f"Spotify volume adjusted by {value} locally via playerctl."
                else:
                    level = float(val_str.replace("%", "")) / 100.0
                    subprocess.run(["playerctl", "-p", "spotify", "volume", f"{level:.2f}"], check=True)
                    return f"Spotify volume set to {val_str}% locally via playerctl."
            except Exception as e:
                logger.warning("Failed to adjust volume locally via playerctl: %s. Falling back to spotify-cli.", e)

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

        # Fall back to playerctl and launch client if needed
        if not is_spotify_running():
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
        return f"Failed to adjust volume: {out}"

    elif action == "status":
        # Prioritize local playerctl status if running
        if is_spotify_running():
            try:
                status = subprocess.check_output(["playerctl", "-p", "spotify", "status"]).decode().strip()
                artist = subprocess.check_output(["playerctl", "-p", "spotify", "metadata", "artist"]).decode().strip()
                title = subprocess.check_output(["playerctl", "-p", "spotify", "metadata", "title"]).decode().strip()
                return f"Spotify Status (playerctl): {status}\nPlaying: {title} by {artist}"
            except Exception as e:
                logger.warning("Failed to fetch status locally via playerctl: %s. Falling back to spotify-cli.", e)

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
