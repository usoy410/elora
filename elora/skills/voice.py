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

from elora.core.config import load_config
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


def warmup_cloud_tts(space_url: str, token: Optional[str] = None) -> None:
    """
    Warms up a Hugging Face Space by triggering a GET request to wake it up.
    
    Why: Free Hugging Face Spaces on default tiers go to sleep after inactivity.
    Calling them on daemon startup ensures they start booting up early to minimize 
    first-use cold start latency.
    """
    import requests
    import time

    base_url = space_url.rstrip('/')
    if "/gradio_api" not in base_url:
        info_url = f"{base_url}/gradio_api/info"
    else:
        info_url = f"{base_url}/info"

    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    logger.info("Warming up cloud TTS Hugging Face Space: %s", info_url)
    
    # We will attempt to contact the space. A sleeping space might trigger a rebuild or wake-up,
    # causing requests to block or return transient errors (e.g. 503 Service Unavailable).
    # We poll a few times with low timeouts in a background-friendly manner.
    max_retries = 15
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(info_url, headers=headers, timeout=5.0)
            if response.status_code == 200:
                logger.info("Cloud TTS Space is warm and ready (responded 200 OK after %d attempts)", attempt)
                return
            else:
                logger.info(
                    "Cloud TTS Space warming up: status %d (attempt %d/%d)", 
                    response.status_code, 
                    attempt, 
                    max_retries
                )
        except requests.RequestException as e:
            logger.debug("Cloud TTS Space warmup attempt %d/%d failed with exception: %s", attempt, max_retries, e)
        
        time.sleep(4.0)

    logger.warning("Cloud TTS Space warmup completed without confirmation (timed out or unreachable).")


def preload_voice_model() -> None:
    """
    Preloads and caches the Kokoro-ONNX voice client in memory,
    and wakes up the cloud Hugging Face Space if configured.
    
    Why: Prevents first-use latency by downloading required assets and 
    loading the ONNX model into memory during daemon startup, and wakes up
    suspended Hugging Face Spaces to avoid cold-start delays.
    """
    config = load_config()
    voice_config = config.get("voice", {})
    provider = voice_config.get("provider", "local")
    space_url = voice_config.get("hf_space_url")

    if provider == "cloud" and space_url:
        token = voice_config.get("hf_token")
        if not token:
            token = os.environ.get("HF_TOKEN")
        if not token:
            token = _get_git_hf_token(space_url)
        
        try:
            warmup_cloud_tts(space_url, token)
        except Exception as e:
            logger.error("Failed to warmup cloud TTS space: %s", e)
    else:
        # Only preload the local voice model if running in local mode
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


def _get_git_hf_token(space_url: str) -> Optional[str]:
    """
    Tries to retrieve the Hugging Face token from Git credentials helper.
    
    Why: Prevents the user from needing to manually copy/paste their Hugging Face token into
    config.json if they already have it configured via Git credentials.
    """
    try:
        proc = subprocess.Popen(
            ["git", "credential", "fill"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        # Query credentials specifically for huggingface.co, which matches the git remote host
        stdout, _ = proc.communicate(input="url=https://huggingface.co\n", timeout=2.0)
        for line in stdout.splitlines():
            if line.startswith("password="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return None


def synthesize_cloud_speech(text: str, space_url: str, voice_name: str, speed: float, token: Optional[str] = None) -> Optional[str]:
    """
    Sends a request to the Gradio space API to synthesize text via Kokoro.
    Returns path to the downloaded audio file if successful, otherwise None.
    
    Why: Offloads heavy audio processing logic to a Hugging Face Space, preserving local CPU resources.
    """
    import requests
    import json

    base_url = space_url.rstrip('/')
    
    # Standardize endpoint prefix to /gradio_api for SSE call routing
    if "/gradio_api" not in base_url:
        call_url = f"{base_url}/gradio_api/call/generate_speech"
    else:
        call_url = f"{base_url}/call/generate_speech"
        
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
        
    payload = {
        "data": [text, voice_name, speed]
    }
    
    try:
        logger.info("Requesting cloud TTS from %s...", call_url)
        response = requests.post(call_url, json=payload, headers=headers, timeout=5.0)
        if response.status_code != 200:
            logger.warning("Cloud TTS trigger failed: %d - %s", response.status_code, response.text)
            return None
            
        event_id = response.json().get("event_id")
        if not event_id:
            logger.warning("Did not receive a valid event_id from Gradio.")
            return None
            
        # Retrieve results from the event stream
        status_url = f"{call_url}/{event_id}"
        r = requests.get(status_url, headers=headers, stream=True, timeout=8.0)
        
        result_file_url = None
        event_type = None
        for line in r.iter_lines():
            if not line:
                continue
            line_str = line.decode('utf-8').strip()
            if line_str.startswith("event:"):
                event_type = line_str.split(":", 1)[1].strip()
            elif line_str.startswith("data:"):
                data_val = line_str.split(":", 1)[1].strip()
                if event_type == "complete":
                    try:
                        data_json = json.loads(data_val)
                        if isinstance(data_json, list) and len(data_json) > 0:
                            file_info = data_json[0]
                            result_file_url = file_info.get("url")
                    except Exception as e:
                        logger.error("Failed to parse event stream payload: %s", e)
                    break
                    
        if not result_file_url:
            logger.warning("Could not locate the generated speech URL in Gradio event stream.")
            return None
            
        # Handle relative URL path from the space
        if result_file_url.startswith("/"):
            root_url = base_url.replace("/gradio_api", "")
            result_file_url = f"{root_url.rstrip('/')}{result_file_url}"
            
        logger.info("Downloading cloud TTS audio from %s...", result_file_url)
        audio_response = requests.get(result_file_url, headers=headers, timeout=5.0)
        if audio_response.status_code == 200:
            with open(TEMP_SPEECH_PATH, "wb") as f:
                f.write(audio_response.content)
            return TEMP_SPEECH_PATH
        else:
            logger.warning("Failed to download audio file: %d", audio_response.status_code)
            
    except Exception as e:
        logger.error("Error communicating with cloud Kokoro TTS Space: %s", e)
        
    return None


def speak_text(text: str, audio_bytes: Optional[bytes] = None, mime_type: Optional[str] = None) -> None:
    """
    Plays speech audio.
    If pre-synthesized audio_bytes are provided, plays them directly.
    Otherwise, uses local/cloud Kokoro voice synthesis.
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

    # Use local/cloud Kokoro voice synthesis for dynamic text
    if text.strip():
        voice_name = voice_config.get("voice_name", "af_heart")
        if voice_name in ("Aoede", "Puck", "Charon", "Kore", "Fenrir"):
            voice_name = "af_heart"
        speed = voice_config.get("speed", 1.0)
        provider = voice_config.get("provider", "local")

        played_successfully = False

        if provider == "cloud":
            space_url = voice_config.get("hf_space_url")
            token = voice_config.get("hf_token")
            if not token:
                token = os.environ.get("HF_TOKEN")
            if not token and space_url:
                token = _get_git_hf_token(space_url)

            if space_url:
                try:
                    cloud_audio_path = synthesize_cloud_speech(text, space_url, voice_name, speed, token)
                    if cloud_audio_path:
                        _active_playback_process = play_chime(cloud_audio_path)
                        played_successfully = True
                except Exception as cloud_err:
                    logger.warning("Cloud TTS failed, falling back to local: %s", cloud_err)
            else:
                logger.warning("Cloud TTS provider selected, but hf_space_url is missing. Falling back to local.")

        if not played_successfully:
            logger.info("Synthesizing speech via local Kokoro ONNX model: '%s'", text[:60])
            client = _get_kokoro_client()
            if client is None:
                logger.warning("Local Voice client unavailable. Speech synthesis skipped.")
                return

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


def is_speaking() -> bool:
    """
    Checks if the voice engine is currently playing speech audio.
    
    Why: Allows the UI to synchronize its visual speaking state with the actual audio playback.
    """
    global _active_playback_process
    if _active_playback_process is not None:
        if _active_playback_process.poll() is None:
            return True
        else:
            _active_playback_process = None
    return False

