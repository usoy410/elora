"""
Elora Spotify Control Skill.
Integrates playerctl and dbus to play, pause, shuffle, adjust volume, and play specific tracks/playlists on Spotify.
"""

import logging
import subprocess
import time
import re
import shlex
import os
from typing import Optional
from elora.skills.skills import search_duckduckgo

logger = logging.getLogger("elora.spotify")


def is_spotify_running() -> bool:
    """Checks if Spotify is currently registered as an active player with playerctl."""
    try:
        output = subprocess.check_output(["playerctl", "-l"], stderr=subprocess.DEVNULL).decode().strip()
        players = [p.lower() for p in output.split("\n") if p.strip()]
        return any("spotify" in p for p in players)
    except Exception:
        return False


def ensure_spotify_running(uri: Optional[str] = None) -> bool:
    """
    Ensures Spotify is running. Launches it if not.
    If a URI is provided and Spotify is launched, launches it with that URI.
    """
    if is_spotify_running():
        return True

    logger.info("Spotify is not running. Launching application...")
    
    # Try launching Spotify desktop app
    cmd = ["spotify"]
    if uri:
        cmd.append(f"--uri={uri}")
        
    try:
        # Launch detached
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setpgrp if hasattr(os, "setpgrp") else None
        )
        # Wait a short moment for Spotify to start and register its MPRIS interface
        for _ in range(5):
            time.sleep(1.0)
            if is_spotify_running():
                logger.info("Spotify launched and registered successfully.")
                return True
        logger.warning("Launched Spotify, but it did not register with playerctl within 5 seconds.")
        return False
    except Exception as e:
        logger.error("Failed to launch Spotify: %s", e)
        return False


def search_spotify_uri(query: str) -> Optional[str]:
    """
    Searches DuckDuckGo for a Spotify track, playlist, album, or artist and returns the URI.
    """
    # Ensure query targets open.spotify.com
    search_query = query
    if "open.spotify.com" not in query:
        search_query = f"site:open.spotify.com {query}"

    logger.info("Searching Spotify URI for: %s", search_query)
    search_result = search_duckduckgo(search_query)
    
    if "Error" in search_result or "No search results" in search_result:
        logger.warning("Spotify search failed or returned no results.")
        return None

    # Look for open.spotify.com URLs
    # Format: https://open.spotify.com/{type}/{id}
    pattern = r"https?://open\.spotify\.com/(track|playlist|album|artist)/([a-zA-Z0-9]+)"
    match = re.search(pattern, search_result)
    if match:
        media_type = match.group(1)
        media_id = match.group(2)
        uri = f"spotify:{media_type}:{media_id}"
        logger.info("Found Spotify URI: %s", uri)
        return uri

    logger.warning("No Spotify open link found in search results.")
    return None


def play_spotify_uri(uri: str) -> str:
    """Plays a specific Spotify URI (track, playlist, album, artist)."""
    # Validate URI format
    if not uri.startswith("spotify:"):
        return f"Error: Invalid Spotify URI format '{uri}'."

    # If not running, launch it directly with the URI
    if not is_spotify_running():
        success = ensure_spotify_running(uri)
        if success:
            return f"Launched Spotify and playing URI: {uri}"
        else:
            return "Failed to launch Spotify client."

    # Spotify is running, use playerctl to open the URI
    try:
        subprocess.run(["playerctl", "-p", "spotify", "open", uri], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return f"Playing URI: {uri}"
    except Exception as e:
        logger.error("Failed to play URI via playerctl: %s", e)
        # Try fallback using dbus-send
        try:
            dbus_cmd = [
                "dbus-send", "--print-reply", "--dest=org.mpris.MediaPlayer2.spotify",
                "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player.OpenUri",
                f"string:{uri}"
            ]
            subprocess.run(dbus_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return f"Playing URI via DBus fallback: {uri}"
        except Exception as dbus_err:
            return f"Failed to play Spotify URI: {e} (DBus error: {dbus_err})"


def search_and_play_spotify(query: str) -> str:
    """Searches Spotify for the query and plays the first result."""
    uri = search_spotify_uri(query)
    if not uri:
        return f"Could not find a Spotify link for query: '{query}'."
    return play_spotify_uri(uri)


def control_spotify(action: str, value: Optional[str] = None) -> str:
    """
    Controls Spotify playback.
    Actions: play, pause, toggle (play-pause), next, previous, shuffle, volume, status.
    """
    action = action.lower().strip()

    if action in ("play", "pause", "toggle", "next", "previous"):
        if not is_spotify_running():
            # If they want to play/toggle, we can launch Spotify
            if action in ("play", "toggle"):
                ensure_spotify_running()
                # Give it a second to play
                time.sleep(1)
            else:
                return "Spotify is not running."

        # Map to playerctl commands
        cmd_map = {
            "play": "play",
            "pause": "pause",
            "toggle": "play-pause",
            "next": "next",
            "previous": "previous"
        }
        playerctl_cmd = cmd_map[action]
        try:
            subprocess.run(["playerctl", "-p", "spotify", playerctl_cmd], check=True)
            return f"Spotify command '{action}' executed."
        except Exception as e:
            return f"Failed to execute '{action}': {e}"

    elif action == "shuffle":
        if not value:
            return "Error: Shuffle action requires a state value ('on', 'off', or 'toggle')."
        
        if not ensure_spotify_running():
            return "Spotify is not running and could not be started."

        state = value.lower().strip()
        if state == "on":
            playerctl_state = "On"
        elif state == "off":
            playerctl_state = "Off"
        elif state == "toggle":
            playerctl_state = "Toggle"
        else:
            return f"Error: Invalid shuffle state '{value}'. Must be 'on', 'off', or 'toggle'."

        try:
            subprocess.run(["playerctl", "-p", "spotify", "shuffle", playerctl_state], check=True)
            return f"Spotify shuffle set to {playerctl_state}."
        except Exception as e:
            return f"Failed to set shuffle: {e}"

    elif action == "volume":
        if not value:
            return "Error: Volume action requires a value."
            
        if not is_spotify_running():
            return "Spotify is not running."

        val_str = value.strip()
        
        # Parse volume changes (e.g. +10, -10, or absolute 0-100)
        try:
            if val_str.startswith("+") or val_str.startswith("-"):
                # Relative change
                diff = float(val_str.replace("%", "")) / 100.0
                sign = "+" if diff > 0 else "-"
                diff_abs = abs(diff)
                subprocess.run(["playerctl", "-p", "spotify", "volume", f"{diff_abs:.2f}{sign}"], check=True)
                return f"Spotify volume adjusted by {value}."
            else:
                # Absolute change (0 to 100)
                level = float(val_str.replace("%", "")) / 100.0
                if not (0.0 <= level <= 1.0):
                    return "Error: Volume level must be between 0 and 100."
                subprocess.run(["playerctl", "-p", "spotify", "volume", f"{level:.2f}"], check=True)
                return f"Spotify volume set to {val_str}%."
        except Exception as e:
            return f"Failed to set volume: {e}"

    elif action == "status":
        if not is_spotify_running():
            return "Spotify is not running."
        try:
            status = subprocess.check_output(["playerctl", "-p", "spotify", "status"]).decode().strip()
            artist = subprocess.check_output(["playerctl", "-p", "spotify", "metadata", "artist"]).decode().strip()
            title = subprocess.check_output(["playerctl", "-p", "spotify", "metadata", "title"]).decode().strip()
            return f"Spotify Status: {status}\nPlaying: {title} by {artist}"
        except Exception:
            try:
                # Fallback to general playerctl status if metadata fetch fails
                status = subprocess.check_output(["playerctl", "-p", "spotify", "status"]).decode().strip()
                return f"Spotify Status: {status}"
            except Exception as e:
                return f"Failed to get Spotify status: {e}"

    else:
        return f"Error: Unknown Spotify action '{action}'."
