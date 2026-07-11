"""
Configuration manager for Elora.
Loads and saves configuration parameters from ~/.config/elora/config.json.
Provides fallbacks if the configuration is missing or corrupted.
"""

import os
import json
import logging
from typing import Dict, Any

logger = logging.getLogger("elora.config")

# Standard XDG configuration directory
CONFIG_DIR = os.path.expanduser("~/.config/elora")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

# Default system settings if no config file exists
DEFAULT_SETTINGS: Dict[str, Any] = {
    "model_name": "gemini-2.5-flash",
    "gemini_api_key": "",
    "news": {
        "feeds": [
            "https://news.ycombinator.com/rss",
            "https://www.phoronix.com/rss.php",
            "https://techcrunch.com/feed/",
            "https://news.google.com/rss"
        ],
        "limit_per_feed": 3,
        "custom_blogs": []
    },
    "sound": {
        "enabled": True,
        "chime_path": "/home/usoy/Documents/antigravity/elora/assets/sounds/success-chime.mp3"
    },
    "voice": {
        "enabled": False,
        "voice_name": "af_heart",
        "speed": 1.0,
        "quantized": True
    },
    "browser": {
        "default_command": "xdg-open"
    },
    "personality": "default",
    "custom_personality": ""
}


def load_config() -> Dict[str, Any]:
    """
    Loads configuration settings from ~/.config/elora/config.json.
    Creates default configuration file if it does not exist.
    
    Why: Separates environment parameters from execution codebase,
    allowing clean and easy personalization.
    """
    if not os.path.exists(CONFIG_PATH):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(CONFIG_PATH, "w") as f:
                json.dump(DEFAULT_SETTINGS, f, indent=2)
            logger.info("Created default configuration file at %s", CONFIG_PATH)
            return DEFAULT_SETTINGS
        except Exception as e:
            logger.error("Failed to create default configuration file: %s", e)
            return DEFAULT_SETTINGS
            
    try:
        with open(CONFIG_PATH, "r") as f:
            user_config = json.load(f)
            
        # Merge dictionary structures recursively to support partial user configuration updates
        merged = DEFAULT_SETTINGS.copy()
        for key, val in user_config.items():
            if isinstance(val, dict) and key in merged and isinstance(merged[key], dict):
                # Update nested dictionary
                merged_sub = merged[key].copy()
                merged_sub.update(val)
                merged[key] = merged_sub
            else:
                merged[key] = val
                
        # Apply in-memory overrides
        for key, val in _override_config.items():
            if isinstance(val, dict) and key in merged and isinstance(merged[key], dict):
                merged[key].update(val)
            else:
                merged[key] = val
                
        logger.debug("Successfully loaded user configuration from %s", CONFIG_PATH)
        return merged
    except Exception as e:
        logger.error("Failed to read user configuration from %s (falling back to defaults): %s", CONFIG_PATH, e)
        # Apply in-memory overrides to defaults as well
        fallback = DEFAULT_SETTINGS.copy()
        for key, val in _override_config.items():
            if isinstance(val, dict) and key in fallback and isinstance(fallback[key], dict):
                fallback[key].update(val)
            else:
                fallback[key] = val
        return fallback


_override_config: Dict[str, Any] = {}


def set_config_override(key: str, value: Any) -> None:
    """Sets an in-memory override value for dynamic application configurations."""
    global _override_config
    _override_config[key] = value


def save_config(config_updates: Dict[str, Any]) -> None:
    """
    Saves configuration updates to ~/.config/elora/config.json.
    Merges updates into the actual file on disk, avoiding dynamically overridden session keys.
    """
    disk_config = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                disk_config = json.load(f)
        except Exception:
            disk_config = DEFAULT_SETTINGS.copy()
    else:
        disk_config = DEFAULT_SETTINGS.copy()

    # Update values (deep copy merge)
    for key, val in config_updates.items():
        if isinstance(val, dict) and key in disk_config and isinstance(disk_config[key], dict):
            disk_config[key].update(val)
        else:
            disk_config[key] = val

    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(disk_config, f, indent=2)
        logger.info("Saved configuration file to %s", CONFIG_PATH)
    except Exception as e:
        logger.error("Failed to save configuration: %s", e)


