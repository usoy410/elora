"""
Elora Speech-to-Text (STT) Engine.
Integrates Vosk for local, lightweight voice recognition.
Pipes audio directly from arecord into Vosk for zero-dependency capture.
"""

import os
import sys
import time
import logging
import urllib.request
import zipfile
import subprocess
import json

logger = logging.getLogger("elora.stt")

MODELS_DIR = os.path.expanduser("~/.config/elora/models")

# Cached Vosk Model instance
_stt_model = None


def _download_progress(count: int, block_size: int, total_size: int) -> None:
    """Displays STT model download progress."""
    percent = min(100, int(count * block_size * 100 / total_size))
    sys.stdout.write(f"\rElora: Downloading Speech-to-Text model... {percent}%")
    sys.stdout.flush()


def download_stt_model() -> str:
    """
    Verifies and downloads the selected Vosk speech model if not present.
    
    Why: Keeps STT installation automated, local, and configurable.
    """
    os.makedirs(MODELS_DIR, exist_ok=True)
    
    from elora.config import load_config
    config = load_config()
    stt_cfg = config.get("stt", {})
    # Default to the highly accurate desktop lgraph model rather than the basic small model
    model_name = stt_cfg.get("model_name", "vosk-model-en-us-0.22-lgraph")
    
    model_dir = os.path.join(MODELS_DIR, model_name)
    zip_path = os.path.join(MODELS_DIR, f"{model_name}.zip")
    model_url = f"https://alphacephei.com/vosk/models/{model_name}.zip"
    
    if not os.path.exists(model_dir):
        if not os.path.exists(zip_path):
            print(f"\nElora: Speech recognition model '{model_name}' missing. Downloading...")
            try:
                urllib.request.urlretrieve(model_url, zip_path, _download_progress)
                print("\nElora: Download complete. Extracting model weights...")
            except Exception as e:
                logger.error("Failed to download Vosk model: %s", e)
                if os.path.exists(zip_path):
                    os.remove(zip_path)
                raise e
        
        # Unzip the weights archive
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(MODELS_DIR)
            print(f"Elora: Voice recognition model '{model_name}' successfully extracted.")
        except Exception as e:
            logger.error("Failed to extract Vosk model: %s", e)
            raise e
        finally:
            # Clean up the zip file to save disk space
            if os.path.exists(zip_path):
                os.remove(zip_path)
                
    return model_dir


def _get_stt_model():
    """Lazy loaded singleton pattern for the Vosk Model client."""
    global _stt_model
    if _stt_model is None:
        model_path = download_stt_model()
        from vosk import Model
        logger.info("Loading Vosk Model from %s", model_path)
        _stt_model = Model(model_path)
    return _stt_model


def listen_voice() -> str:
    """
    Captures audio from the default input using ``arecord`` and transcribes it via Vosk.
    Returns when the speaker finishes (silence auto-detected) or Ctrl-C is pressed.

    Why: Bypasses C audio recording libraries, leveraging the native OS capture tool.
    Optimisation: 250ms chunks instead of 125ms halve the Python loop iterations;
    a 1.8-second silence timeout means the call returns quickly after the user stops speaking.
    """
    # Silence duration (seconds) with committed text before auto-returning
    _SILENCE_TIMEOUT_SEC = 1.8
    # 250ms of 16kHz / 16-bit / mono audio
    _CHUNK_BYTES = 8000

    try:
        model = _get_stt_model()
    except Exception as e:
        logger.error("Failed to load STT model: %s", e)
        print("\nElora: Voice input model failed to load.")
        return ""

    from vosk import KaldiRecognizer
    rec = KaldiRecognizer(model, 16000)

    # Spawn arecord: 16kHz, 16-bit signed little-endian, mono, raw headerless output
    cmd = ["arecord", "-r", "16000", "-f", "S16_LE", "-c", "1", "-t", "raw", "-q"]

    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        logger.error("arecord binary not found. Please install alsa-utils.")
        print("\nElora: 'arecord' binary not found. Please install 'alsa-utils' for audio capture.")
        return ""

    print("\nElora: Listening... (Speak now)")

    try:
        committed_texts = []
        last_commit_time = time.monotonic()

        while True:
            # Read 250ms worth of raw PCM per iteration
            data = process.stdout.read(_CHUNK_BYTES)
            if not data:
                break

            if rec.AcceptWaveform(data):
                res = json.loads(rec.Result())
                text = res.get("text", "").strip()
                if text:
                    committed_texts.append(text)
                    last_commit_time = time.monotonic()

            # Auto-stop after silence threshold once at least one phrase is captured
            if committed_texts and time.monotonic() - last_commit_time > _SILENCE_TIMEOUT_SEC:
                break

        # Flush any remaining partial
        res = json.loads(rec.FinalResult())
        final_segment = res.get("text", "").strip()
        return " ".join(committed_texts + ([final_segment] if final_segment else [])).strip()

    except KeyboardInterrupt:
        print("\nElora: Listening cancelled.")
        return ""
    except Exception as e:
        logger.error("Error in speech recognizer: %s", e)
        return ""
    finally:
        process.terminate()
        try:
            process.wait(timeout=2)
        except Exception:
            pass
