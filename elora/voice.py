"""
Elora Voice Engine.
Integrates kokoro-onnx for local, lightweight voice synthesis.
Handles automated model downloads and async speech playback.
"""

import os
import sys
import logging
import urllib.request
import soundfile as sf
from typing import Optional

from elora.config import load_config
from elora.utils import play_chime

logger = logging.getLogger("elora.voice")

MODELS_DIR = os.path.expanduser("~/.config/elora/models")
TEMP_SPEECH_PATH = os.path.expanduser("~/.config/elora/speech.wav")

# GitHub release endpoints for the INT8 model and voices binary
MODEL_INT8_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx"
VOICES_BIN_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"

# Cached global Kokoro client to avoid repeating costly initialization operations
_kokoro_client: Optional[object] = None


def _download_progress(count: int, block_size: int, total_size: int) -> None:
    """Callback to display progress percentages in the terminal during downloads."""
    percent = min(100, int(count * block_size * 100 / total_size))
    sys.stdout.write(f"\rElora: Downloading voice engine assets... {percent}%")
    sys.stdout.flush()


def download_voice_assets() -> tuple[str, str]:
    """
    Checks if model and voice assets are present locally, downloading them if missing.
    
    Why: Keeps installation self-contained and avoids manual user setups.
    """
    os.makedirs(MODELS_DIR, exist_ok=True)
    
    model_path = os.path.join(MODELS_DIR, "kokoro-v1.0.int8.onnx")
    voices_path = os.path.join(MODELS_DIR, "voices-v1.0.bin")
    
    # Download voices binary if missing (~20 MB)
    if not os.path.exists(voices_path):
        print(f"\nElora: Voices binary missing. Downloading from {VOICES_BIN_URL}...")
        try:
            urllib.request.urlretrieve(VOICES_BIN_URL, voices_path, _download_progress)
            print("\nElora: Voices binary downloaded successfully.")
        except Exception as e:
            logger.error("Failed to download voices binary: %s", e)
            raise e
            
    # Download quantized model if missing (~85 MB)
    if not os.path.exists(model_path):
        print(f"\nElora: Quantized INT8 model weights missing. Downloading from {MODEL_INT8_URL}...")
        try:
            urllib.request.urlretrieve(MODEL_INT8_URL, model_path, _download_progress)
            print("\nElora: Model weights downloaded successfully.")
        except Exception as e:
            logger.error("Failed to download model weights: %s", e)
            if os.path.exists(model_path):
                os.remove(model_path)
            raise e
            
    return model_path, voices_path


def _get_kokoro_client() -> Optional[object]:
    """
    Initializes and returns the cached Kokoro client (lazy loaded).
    
    Why: Ensures the startup time of Elora remains rapid, postponing
    the model loading cost until a voice command is actually executed.
    """
    global _kokoro_client
    if _kokoro_client is not None:
        return _kokoro_client
        
    try:
        # Check and download files if missing
        model_path, voices_path = download_voice_assets()
        
        from kokoro_onnx import Kokoro
        logger.info("Initializing Kokoro ONNX model: %s", model_path)
        _kokoro_client = Kokoro(model_path, voices_path)
        return _kokoro_client
    except Exception as e:
        logger.error("Failed to initialize Kokoro client: %s", e)
        return None


def speak_text(text: str) -> None:
    """
    Synthesizes text to speech and plays the audio asynchronously.
    
    Why: Saving to a temporary WAV and calling the existing play_chime() player
    prevents blocking execution threads in either the CLI loop or GUI window.
    """
    config = load_config()
    voice_config = config.get("voice", {})
    
    # Check if voice feedback is enabled
    if not voice_config.get("enabled", False):
        return
        
    client = _get_kokoro_client()
    if client is None:
        logger.warning("Voice client unavailable. Speech synthesis skipped.")
        return
        
    voice_name = voice_config.get("voice_name", "af_heart")
    speed = voice_config.get("speed", 1.0)
    
    try:
        logger.info("Synthesizing speech for text: '%s' using voice '%s'", text, voice_name)
        
        # Run local ONNX inference
        samples, sample_rate = client.create(
            text,
            voice=voice_name,
            speed=speed,
            lang="en-us"
        )
        
        # Write temporary WAV file
        sf.write(TEMP_SPEECH_PATH, samples, sample_rate)
        logger.debug("Speech WAV written to %s", TEMP_SPEECH_PATH)
        
        # Play the temporary WAV file using our async ALSA player
        play_chime(TEMP_SPEECH_PATH)
    except Exception as e:
        logger.error("Failed to synthesize or play speech: %s", e)
