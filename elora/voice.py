"""
Elora Voice Engine (Gemini TTS & Local Kokoro-ONNX Fallback).
Replaces local kokoro-onnx with Gemini API native voice synthesis,
with a robust fallback to local kokoro-onnx if API limits/errors are encountered.
"""

import os
import sys
import logging
import subprocess
import wave
import urllib.request
import soundfile as sf
from typing import Optional

from elora.config import load_config
from elora.utils import play_chime

logger = logging.getLogger("elora.voice")

MODELS_DIR = os.path.expanduser("~/.config/elora/models")
TEMP_SPEECH_PATH = os.path.expanduser("~/.config/elora/speech.wav")
_active_playback_process: Optional[subprocess.Popen] = None

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


def preload_voice_model() -> None:
    """
    Preloads and caches the Kokoro-ONNX voice client in memory.
    
    Why: Prevents first-use latency by downloading required assets and 
    loading the ONNX model into memory during daemon startup rather than
    on the first voice synthesis request.
    """
    _get_kokoro_client()



def save_audio_payload(audio_data: bytes, mime_type: str, output_path: str) -> str:
    """Saves audio data to a file, wrapping in a WAV container if it is raw PCM."""
    lower_mime = mime_type.lower()
    if "pcm" in lower_mime or "l16" in lower_mime:
        wav_path = output_path
        if not wav_path.endswith(".wav"):
            wav_path = os.path.splitext(output_path)[0] + ".wav"
            
        # Parse rate and channels if specified, fallback to 24000 and 1
        rate = 24000
        channels = 1
        for part in lower_mime.split(";"):
            part = part.strip()
            if part.startswith("rate="):
                try:
                    rate = int(part.split("=")[1])
                except ValueError:
                    pass
            elif part.startswith("channels="):
                try:
                    channels = int(part.split("=")[1])
                except ValueError:
                    pass

        with wave.open(wav_path, "wb") as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(2)  # 16-bit
            wav_file.setframerate(rate)
            wav_file.writeframes(audio_data)
        return wav_path
    else:
        ext = ".mp3" if "mp3" in lower_mime else ".wav"
        file_path = os.path.splitext(output_path)[0] + ext
        with open(file_path, "wb") as f:
            f.write(audio_data)
        return file_path


def speak_text(text: str, audio_bytes: Optional[bytes] = None, mime_type: Optional[str] = None) -> None:
    """
    Plays speech audio.
    If pre-synthesized audio_bytes are provided, plays them directly.
    Otherwise, uses the local kokoro-onnx model for offline voice synthesis.
    """
    global _active_playback_process
    
    config = load_config()
    voice_config = config.get("voice", {})
    
    # Check if voice feedback is enabled
    if not voice_config.get("enabled", False):
        return
        
    # Stop any currently active speech subprocess
    if _active_playback_process is not None:
        try:
            _active_playback_process.terminate()
            _active_playback_process.wait(timeout=0.3)
        except Exception:
            pass
        _active_playback_process = None

    if not text.strip() and not audio_bytes:
        return

    # If audio bytes are pre-synthesized and provided
    if audio_bytes and mime_type:
        try:
            play_path = save_audio_payload(audio_bytes, mime_type, TEMP_SPEECH_PATH)
            _active_playback_process = play_chime(play_path)
            return
        except Exception as e:
            logger.error("Failed to play pre-synthesized audio: %s", e)

    # Use local Kokoro-ONNX voice synthesis exclusively for dynamic text
    if text.strip():
        logger.info("Synthesizing speech via local Kokoro ONNX model: '%s'", text[:60])
        client = _get_kokoro_client()
        if client is None:
            logger.warning("Local Voice client unavailable. Speech synthesis skipped.")
            return

        # Use voice name from configuration
        voice_name = voice_config.get("voice_name", "af_heart")
        if voice_name in ("Aoede", "Puck", "Charon", "Kore", "Fenrir"):
            voice_name = "af_heart"
        speed = voice_config.get("speed", 1.0)

        try:
            samples, sample_rate = client.create(
                text,
                voice=voice_name,
                speed=speed,
                lang="en-us"
            )
            sf.write(TEMP_SPEECH_PATH, samples, sample_rate)
            _active_playback_process = play_chime(TEMP_SPEECH_PATH)
        except Exception as local_err:
            logger.error("Local speech synthesis failed: %s", local_err)
