"""
Elora Speech-to-Text (STT) Recorder.
Deprecates local Vosk offline model and replaces it with raw PCM recording
and energy-based silence detection. Saves captured audio to a local WAV file
to be passed directly to the Gemini API.
"""

import os
import sys
import time
import logging
import subprocess
import wave
import math
import struct
from typing import Optional

logger = logging.getLogger("elora.stt")

USER_VOICE_PATH = "/tmp/elora_user_voice.wav"
_CHUNK_BYTES = 4000  # 125ms of 16kHz / 16-bit mono PCM


def calculate_rms(audio_data: bytes) -> float:
    """Calculates Root-Mean-Square (RMS) amplitude of raw 16-bit mono PCM audio data."""
    count = len(audio_data) // 2
    if count == 0:
        return 0.0
    # Unpack little-endian signed 16-bit shorts
    format_str = f"<{count}h"
    try:
        shorts = struct.unpack(format_str, audio_data)
        sum_squares = sum(s * s for s in shorts)
        return math.sqrt(sum_squares / count)
    except Exception as e:
        logger.debug("RMS calculation error: %s", e)
        return 0.0


def listen_voice(output_path: str = USER_VOICE_PATH) -> str:
    """
    Captures audio from the default input using `arecord`.
    Detects silence using energy-based RMS thresholding.
    Saves the captured PCM stream to a WAV file.
    
    Returns the file path of the saved WAV file, or empty string on cancel/error.
    """
    # Silence detection settings
    # RMS threshold: ambient is usually < 150; speech is > 1000. 400 is a safe threshold.
    SILENCE_THRESHOLD = 400.0
    SILENCE_TIMEOUT_SEC = 1.8
    MIN_RECORD_DURATION_SEC = 0.5

    # arecord: 16kHz, 16-bit S16_LE, mono, raw headerless PCM, low latency buffer
    cmd = [
        "arecord", "-r", "16000", "-f", "S16_LE", "-c", "1", "-t", "raw", "-q",
        "-B", "100000"
    ]

    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        logger.error("arecord binary not found. Please install alsa-utils.")
        print("\nElora: 'arecord' binary not found. Please install 'alsa-utils' for audio capture.")
        return ""

    print("\nElora: Listening... (Speak now)")

    pcm_frames = []
    start_time = time.monotonic()
    last_sound_time = time.monotonic()
    speech_detected = False

    try:
        while True:
            # Read 125ms worth of raw PCM per iteration
            data = process.stdout.read(_CHUNK_BYTES)
            if not data:
                break

            pcm_frames.append(data)
            current_time = time.monotonic()
            rms = calculate_rms(data)

            # Detect if user started speaking
            if rms > SILENCE_THRESHOLD:
                if not speech_detected:
                    logger.debug("Speech threshold crossed. RMS: %.2f", rms)
                speech_detected = True
                last_sound_time = current_time

            # Silence check: if speech was detected, auto-stop after SILENCE_TIMEOUT_SEC of silence
            if speech_detected:
                if current_time - last_sound_time > SILENCE_TIMEOUT_SEC:
                    logger.debug("Silence timeout reached, finalising audio file.")
                    break
            else:
                # If no speech is detected at all, allow maximum of 5 seconds before giving up
                if current_time - start_time > 5.0:
                    logger.debug("No speech detected. Exiting loop.")
                    return ""

    except KeyboardInterrupt:
        print("\nElora: Listening cancelled.")
        return ""
    except Exception as e:
        logger.error("Error in audio recording loop: %s", e)
        return ""
    finally:
        process.terminate()
        try:
            process.wait(timeout=1)
        except Exception:
            pass

    # Save to a standard WAV file if we captured frames
    duration = time.monotonic() - start_time
    if pcm_frames and duration >= MIN_RECORD_DURATION_SEC:
        try:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with wave.open(output_path, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)  # 16-bit
                wav_file.setframerate(16000)
                wav_file.writeframes(b"".join(pcm_frames))
            logger.info("Saved raw audio recording to %s (duration: %.2fs)", output_path, duration)
            return output_path
        except Exception as e:
            logger.error("Failed to write WAV file: %s", e)
            return ""
    
    return ""
