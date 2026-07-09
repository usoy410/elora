"""
Elora Speech-to-Text (STT) Engine.
Integrates Vosk for local, lightweight voice recognition.
Pipes audio directly from arecord into Vosk for zero-dependency capture.
"""

import os
import sys
import logging
import urllib.request
import zipfile
import subprocess
import json

logger = logging.getLogger("elora.stt")

MODELS_DIR = os.path.expanduser("~/.config/elora/models")
VOSK_MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
VOSK_ZIP_PATH = os.path.join(MODELS_DIR, "vosk-model-small-en-us-0.15.zip")
VOSK_EXTRACT_DIR = os.path.join(MODELS_DIR, "vosk-model-small-en-us-0.15")

# Cached Vosk Model instance
_stt_model = None


def _download_progress(count: int, block_size: int, total_size: int) -> None:
    """Displays STT model download progress."""
    percent = min(100, int(count * block_size * 100 / total_size))
    sys.stdout.write(f"\rElora: Downloading Speech-to-Text model... {percent}%")
    sys.stdout.flush()


def download_stt_model() -> str:
    """
    Verifies and downloads the Vosk small en-us speech model if not present.
    
    Why: Keeps STT installation automated and local.
    """
    os.makedirs(MODELS_DIR, exist_ok=True)
    
    if not os.path.exists(VOSK_EXTRACT_DIR):
        if not os.path.exists(VOSK_ZIP_PATH):
            print(f"\nElora: Voice recognition model missing. Downloading from {VOSK_MODEL_URL}...")
            try:
                urllib.request.urlretrieve(VOSK_MODEL_URL, VOSK_ZIP_PATH, _download_progress)
                print("\nElora: Download complete. Extracting model weights...")
            except Exception as e:
                logger.error("Failed to download Vosk model: %s", e)
                if os.path.exists(VOSK_ZIP_PATH):
                    os.remove(VOSK_ZIP_PATH)
                raise e
        
        # Unzip the weights archive
        try:
            with zipfile.ZipFile(VOSK_ZIP_PATH, 'r') as zip_ref:
                zip_ref.extractall(MODELS_DIR)
            print("Elora: Voice recognition model successfully extracted.")
        except Exception as e:
            logger.error("Failed to extract Vosk model: %s", e)
            raise e
        finally:
            # Clean up the zip file to save disk space
            if os.path.exists(VOSK_ZIP_PATH):
                os.remove(VOSK_ZIP_PATH)
                
    return VOSK_EXTRACT_DIR


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
    Captures audio from default input using `arecord` and translates it to text.
    Automatically stops and returns the text once silence is detected.
    
    Why: Bypasses C audio recording libraries, leveraging native OS capture tools.
    """
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
        while True:
            # Read 4000 bytes (125ms of 16kHz 16-bit mono audio)
            data = process.stdout.read(4000)
            if not data:
                break
                
            if rec.AcceptWaveform(data):
                # Silence after voice detected
                res = json.loads(rec.Result())
                text = res.get("text", "").strip()
                if text:
                    process.terminate()
                    process.wait()
                    return text
                    
        # Final fallback flush
        res = json.loads(rec.FinalResult())
        return res.get("text", "").strip()
        
    except KeyboardInterrupt:
        print("\nElora: Listening cancelled.")
        process.terminate()
        process.wait()
        return ""
    except Exception as e:
        logger.error("Error in speech recognizer: %s", e)
        process.terminate()
        process.wait()
        return ""
