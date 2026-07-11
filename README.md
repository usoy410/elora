# Elora: Linux Desktop OS Orchestrator

Elora is a lightweight, low-resource command-line loop and desktop assistant. It leverages local and cloud-hosted reasoning models via Ollama to determine actions, and executes them instantly using local Linux utilities without freezing your terminal.

---

## Features

*   **Low-Resource Architecture**: Runs as a lightweight local Python loop. Offloads heavy intelligence tasks to the `gpt-oss:120b-cloud` model.
*   **Background Agent Delegation**: Automatically delegates complex coding or research tasks to the Antigravity CLI (`agy`) inside a detached background `tmux` session, releasing your terminal instantly.
*   **RSS News Aggregator (Skim & Deep Dive)**: Fetches and parses popular tech feeds locally. Prints summaries directly in Markdown and launches articles on-demand in the default system browser via `xdg-open`.
*   **System Notifications**: Uses `notify-send` and auditory chimes (`aplay`) to send non-blocking task alerts.

---

## Getting Started

### Prerequisites

Ensure the following tools are installed on your Linux system:
*   [uv](https://github.com/astral-sh/uv) (Python package manager)
*   `tmux` (terminal multiplexer)
*   `notify-send` (libnotify)
*   `aplay` (ALSA sound player)
*   `ollama` (local model daemon)

### Setup & Authentication

1.  Sync dependencies using `uv`:
    ```bash
    uv sync
    ```
2.  Authenticate with Ollama to access cloud-offloaded models:
    ```bash
    ollama signin
    ```

---

## Usage

Elora can be executed in five modes:

### 1. Interactive REPL Mode
Start an interactive conversational loop:
```bash
uv run python main.py
```

### 2. Direct Query Mode
Send a single query directly from the shell:
```bash
uv run python main.py "Fetch tech news"
```

### 3. Piped Input Mode
Pipe commands directly into Elora:
```bash
echo "Open the article for number 3" | uv run python main.py
```

### 4. Hands-Free Voice Mode
Start a hands-free voice loop that listens to your speech, auto-detects when you finish speaking, and responds out loud:
```bash
uv run python main.py --voice
```

### 5. Centralized HUD Dashboard Mode
Launch a gorgeous graphical overlay panel showing chat logs, running background tasks, and active RSS telemetry. Hold down the `Spacebar` to speak, and release it to execute. Features a low-latency 100ms ALSA capture buffer and a 450ms PTT release delay to guarantee the final word is not cut off:
```bash
uv run python main.py --hud
```

---

## Configuration

Elora settings are stored dynamically in `~/.config/elora/config.json`. You can modify them directly inside the HUD Settings panel, or manually customize the file:

```json
{
  "stt": {
    "model_name": "vosk-model-en-us-0.22-lgraph"
  }
}
```

*   **stt.model_name**: Sets the active speech recognition (STT) model directory.
    *   `vosk-model-en-us-0.22-lgraph` — Accuracy: desktop-grade model.
    *   `vosk-model-small-en-us-0.15` — Speed: highly responsive, lower CPU footprint.


---

## Project Structure

*   [main.py](file:///home/usoy/Documents/antigravity/elora/main.py) — Core CLI listener and loop router.
*   **`elora/`** (Package):
    *   [elora/brain.py](file:///home/usoy/Documents/antigravity/elora/elora/brain.py) — Handles prompt payloads and enforces JSON tool schema response formats.
    *   [elora/actions.py](file:///home/usoy/Documents/antigravity/elora/elora/actions.py) — Manages browser redirection and unique `tmux` session spawning.
    *   [elora/news.py](file:///home/usoy/Documents/antigravity/elora/elora/news.py) — Lightweight RSS news engine using `feedparser`.
    *   [elora/voice.py](file:///home/usoy/Documents/antigravity/elora/elora/voice.py) — Lightweight local TTS synthesis using `kokoro-onnx` and `soundfile`.
    *   [elora/stt.py](file:///home/usoy/Documents/antigravity/elora/elora/stt.py) — Local offline Speech-to-Text using `vosk` and `arecord` subprocess streaming.
    *   [elora/hud.py](file:///home/usoy/Documents/antigravity/elora/elora/hud.py) — Centralized UI card widget overlay supporting Spacebar Hold-to-Talk.
    *   [elora/utils.py](file:///home/usoy/Documents/antigravity/elora/elora/utils.py) — Linux desktop notifications and sound effects.

