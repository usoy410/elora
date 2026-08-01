"""Elora Telegram Bot Integration.

Enables remote commanding of Elora through Telegram messages, voice notes,
and photos. Supports file transfer, screenshot capture, project zipping,
and background task monitoring.

Why: Provides a secure remote control channel for Elora from any device
with Telegram installed, without requiring local terminal access.
"""
from __future__ import annotations

import socket

# Force IPv4 only to bypass hanging on unreachable IPv6 addresses in some Linux environments.
# Why: httpx can get stuck attempting connections to IPv6 addresses that have no route,
# whereas curl falls back to IPv4 immediately.
_orig_getaddrinfo = socket.getaddrinfo
def _ipv4_only_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if family == socket.AF_UNSPEC:
        family = socket.AF_INET
    return _orig_getaddrinfo(host, port, family, type, proto, flags)
socket.getaddrinfo = _ipv4_only_getaddrinfo

import asyncio
import json
import logging
import os
import tempfile
import zipfile
from typing import Any

from elora.skills.email import load_env_credential
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from elora.core.agent import run_agent_loop
from elora.core.config import load_config, load_session_history, save_session_history
from elora.skills.actions import _load_tasks_registry, execute_agent_task
from elora.skills.news import get_news_summary
from elora.skills.os_control import capture_desktop_screenshot

logger = logging.getLogger("elora.telegram")

# Maximum number of conversation history entries to maintain
_MAX_HISTORY = 20


def _is_authorized(user_id: int, allowed_ids: set[int]) -> bool:
    """
    Check if a user is authorized to interact with the bot.

    Why: Security is critical for a remote control bot. We only allow explicitly whitelisted users.

    Args:
        user_id: The Telegram user ID to check.
        allowed_ids: A set of allowed user IDs.

    Returns:
        True if authorized, False otherwise.
    """
    return user_id in allowed_ids


def _split_message(text: str, max_len: int = 4096) -> list[str]:
    """
    Split a long message into chunks that fit within Telegram's character limit.

    Why: Telegram has a strict 4096 character limit per message. Long agent responses must be paginated.

    Args:
        text: The text to split.
        max_len: The maximum length of each chunk.

    Returns:
        A list of string chunks.
    """
    if len(text) <= max_len:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        # Find the last newline within the limit to split cleanly
        split_idx = text.rfind('\n', 0, max_len)
        if split_idx == -1:
            split_idx = max_len
        chunks.append(text[:split_idx])
        text = text[split_idx:].lstrip('\n')
    return chunks


def _cleanup_temp_file(path: str) -> None:
    """
    Delete a temporary file silently.

    Why: We generate temp files for audio and zip archives. We need a robust way to clean them up without crashing if they don't exist.

    Args:
        path: The path to the file to delete.
    """
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception as e:
        logger.error(f"Failed to cleanup temp file {path}: {e}")


def _zip_directory(dir_path: str, exclude_patterns: list[str]) -> str:
    """
    Zip a directory into a temporary file.

    Why: To allow users to download project directories over Telegram, while excluding heavy build/cache folders.

    Args:
        dir_path: Path to the directory to zip.
        exclude_patterns: List of directory/file names to exclude.

    Returns:
        The path to the generated zip file.
    """
    temp_zip = tempfile.mktemp(suffix=".zip")
    with zipfile.ZipFile(temp_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(dir_path):
            # Mutate dirs in place to prevent os.walk from traversing excluded directories
            dirs[:] = [d for d in dirs if d not in exclude_patterns]
            for file in files:
                if file in exclude_patterns:
                    continue
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, dir_path)
                zipf.write(file_path, arcname)
    return temp_zip


def _get_allowed_ids() -> set[int]:
    """
    Load allowed user IDs from config.

    Why: Centralize config loading for authorization checks.
    """
    config = load_config()
    allowed = config.get("telegram", {}).get("allowed_user_ids", [])
    return set(allowed)


async def _check_auth_and_log(update: Update) -> bool:
    """
    Verify authorization and log the incoming message.

    Why: Standardize auth checking and logging across all handlers.
    """
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)
    allowed_ids = _get_allowed_ids()

    if not _is_authorized(user_id, allowed_ids):
        logger.warning(f"Unauthorized access attempt from user {username} ({user_id})")
        if update.message:
            await update.message.reply_text("⛔ Unauthorized.")
        return False

    message_type = "text"
    if update.message.photo:
        message_type = "photo"
    elif update.message.voice or update.message.audio:
        message_type = "voice"
    elif update.message.document:
        message_type = "document"
        
    logger.info(f"Received {message_type} message from {username} ({user_id})")
    return True


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for the /start command."""
    if not await _check_auth_and_log(update):
        return
    
    welcome = (
        "Welcome to the ELORA Remote Control Bot!\n\n"
        "You can chat with me directly, send voice messages, or photos.\n"
        "Use /help to see available commands."
    )
    await update.message.reply_text(welcome)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for the /help command."""
    if not await _check_auth_and_log(update):
        return
        
    help_text = (
        "Available commands:\n"
        "/start - Show welcome message\n"
        "/help - Show this help message\n"
        "/screenshot - Capture and send desktop screenshot\n"
        "/tasks - List background tasks\n"
        "/zip <path> - Zip and send a directory\n"
        "/sendfile <path> - Send a specific file"
    )
    await update.message.reply_text(help_text)


async def screenshot_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for the /screenshot command."""
    if not await _check_auth_and_log(update):
        return

    await update.message.reply_chat_action(ChatAction.UPLOAD_PHOTO)
    try:
        loop = asyncio.get_event_loop()
        # Run sync function in executor
        await loop.run_in_executor(None, capture_desktop_screenshot)
        screenshot_path = "/tmp/elora_screenshot.png"
        if os.path.exists(screenshot_path):
            with open(screenshot_path, 'rb') as f:
                await update.message.reply_photo(photo=f, caption="Desktop Screenshot")
        else:
            await update.message.reply_text("Failed to capture screenshot: File not found.")
    except Exception as e:
        logger.error(f"Error in /screenshot: {e}")
        await update.message.reply_text(f"Error capturing screenshot: {e}")


async def tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for the /tasks command."""
    if not await _check_auth_and_log(update):
        return

    try:
        loop = asyncio.get_event_loop()
        tasks = await loop.run_in_executor(None, _load_tasks_registry)
        
        if not tasks:
            await update.message.reply_text("No background tasks found.")
            return

        response = "Background Tasks:\n\n"
        for session_name, task_info in tasks.items():
            status = task_info.get("status", "unknown")
            prompt = task_info.get("prompt", "No prompt")
            response += f"🔹 {session_name} [{status}]\nPrompt: {prompt}\n\n"
            
        for chunk in _split_message(response):
            await update.message.reply_text(chunk)
    except Exception as e:
        logger.error(f"Error in /tasks: {e}")
        await update.message.reply_text(f"Error fetching tasks: {e}")


async def zip_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for the /zip command."""
    if not await _check_auth_and_log(update):
        return

    args = context.args
    if not args:
        await update.message.reply_text("Usage: /zip <path>")
        return

    dir_path = " ".join(args)
    dir_path = os.path.expanduser(dir_path)

    if not os.path.isdir(dir_path):
        await update.message.reply_text(f"Error: {dir_path} is not a valid directory.")
        return

    await update.message.reply_chat_action(ChatAction.UPLOAD_DOCUMENT)
    
    config = load_config()
    max_size_mb = config.get("telegram", {}).get("max_file_size_mb", 50)
    exclude_patterns = [
        "node_modules", ".next", ".venv", "__pycache__", ".git", 
        "dist", "build", ".cache", "target", ".gradle"
    ]

    temp_zip = ""
    try:
        loop = asyncio.get_event_loop()
        temp_zip = await loop.run_in_executor(None, _zip_directory, dir_path, exclude_patterns)
        
        size_mb = os.path.getsize(temp_zip) / (1024 * 1024)
        if size_mb > max_size_mb:
            await update.message.reply_text(f"Error: Zip file is too large ({size_mb:.1f}MB). Max allowed is {max_size_mb}MB.")
            return

        with open(temp_zip, 'rb') as f:
            await update.message.reply_document(document=f, filename=f"{os.path.basename(dir_path.rstrip('/'))}.zip")
    except Exception as e:
        logger.error(f"Error in /zip: {e}")
        await update.message.reply_text(f"Error zipping directory: {e}")
    finally:
        if temp_zip:
            _cleanup_temp_file(temp_zip)


async def sendfile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for the /sendfile command."""
    if not await _check_auth_and_log(update):
        return

    args = context.args
    if not args:
        await update.message.reply_text("Usage: /sendfile <path>")
        return

    file_path = " ".join(args)
    file_path = os.path.expanduser(file_path)

    if not os.path.isfile(file_path):
        await update.message.reply_text(f"Error: {file_path} is not a valid file.")
        return

    config = load_config()
    max_size_mb = config.get("telegram", {}).get("max_file_size_mb", 50)
    size_mb = os.path.getsize(file_path) / (1024 * 1024)
    
    if size_mb > max_size_mb:
        await update.message.reply_text(f"Error: File is too large ({size_mb:.1f}MB). Max allowed is {max_size_mb}MB.")
        return

    await update.message.reply_chat_action(ChatAction.UPLOAD_DOCUMENT)
    try:
        with open(file_path, 'rb') as f:
            await update.message.reply_document(document=f)
    except Exception as e:
        logger.error(f"Error in /sendfile: {e}")
        await update.message.reply_text(f"Error sending file: {e}")


def _sync_process_prompt(prompt: str) -> tuple[dict, bool]:
    """
    Process a prompt through the agent loop synchronously.

    Why: The agent loop is synchronous, so it must be run in an executor.
    This function wraps the interaction with session history and the agent loop.

    Returns:
        A tuple of (result_dict, screenshot_modified) where screenshot_modified
        indicates the agent captured a new screenshot during execution.
    """
    from elora.skills.os_control import capture_desktop_screenshot

    # Track screenshot modification time to detect agent-initiated captures
    screenshot_path = "/tmp/elora_screenshot.png"
    mtime_before = os.path.getmtime(screenshot_path) if os.path.exists(screenshot_path) else 0

    history = load_session_history(limit=_MAX_HISTORY)
    history.append({"role": "user", "content": prompt})

    def status_callback(event: Any) -> None:
        """Log agent loop events without printing to console."""
        if isinstance(event, dict):
            etype = event.get("type")
            if etype == "thought":
                logger.debug("Agent thought: %s", event.get("text"))
            elif etype == "tool_start":
                logger.info("Agent tool: %s", event.get("tool"))

    def confirm_callback(action: str, args: dict) -> bool:
        """Auto-approve destructive commands in Telegram mode.

        Why: There is no interactive TTY for confirmation prompts. The user
        already authenticated via Telegram user ID, so we trust intent.
        """
        logger.warning("Auto-approved destructive command via Telegram: %s %s", action, args)
        return True

    def screenshot_callback() -> bool:
        """Capture a desktop screenshot for visual queries."""
        try:
            capture_desktop_screenshot()
            return True
        except Exception as e:
            logger.error("Screenshot capture failed: %s", e)
            return False

    try:
        result = run_agent_loop(
            prompt,
            history,
            status_callback=status_callback,
            confirm_callback=confirm_callback,
            screenshot_callback=screenshot_callback
        )

        # Persist updated history with the assistant response
        history.append({"role": "assistant", "content": json.dumps(result)})
        if len(history) > _MAX_HISTORY:
            history = history[-_MAX_HISTORY:]
        save_session_history(history, limit=_MAX_HISTORY)

        mtime_after = os.path.getmtime(screenshot_path) if os.path.exists(screenshot_path) else 0
        screenshot_modified = mtime_after > mtime_before

        return result, screenshot_modified
    except Exception as e:
        logger.error("Agent loop error: %s", e)
        return {"error": str(e)}, False


async def _monitor_task(bot, chat_id: int, session_name: str, prompt: str) -> None:
    """
    Monitor an antigravity background task and notify the user upon completion.
    
    Why: Long-running tasks execute in tmux. We need to poll for completion to notify the user.
    """
    try:
        while True:
            # Check if tmux session exists
            proc = await asyncio.create_subprocess_shell(
                f"tmux has-session -t {session_name}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            
            if proc.returncode != 0:
                # Session no longer exists
                break
                
            await asyncio.sleep(5)
            
        # Check exit code
        exit_file = os.path.expanduser(f"~/.config/elora/logs/{session_name}.exit")
        success = False
        if os.path.exists(exit_file):
            with open(exit_file, "r") as f:
                exit_code = f.read().strip()
                success = (exit_code == "0")
                
        status_msg = f"✅ Task completed successfully: {session_name}" if success else f"❌ Task failed: {session_name}"
        await bot.send_message(chat_id=chat_id, text=status_msg)
        
    except Exception as e:
        logger.error(f"Error monitoring task {session_name}: {e}")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming text messages."""
    if not await _check_auth_and_log(update):
        return

    text = update.message.text
    await _process_prompt_async(text, update, context)


async def _process_prompt_async(prompt: str, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Process a text prompt asynchronously, managing chat actions and agent interactions.

    Why: Shared logic for text messages and transcribed voice messages.
    Routes agent results to appropriate Telegram responses.
    """

    await update.message.reply_chat_action(ChatAction.TYPING)

    loop = asyncio.get_event_loop()
    result, screenshot_modified = await loop.run_in_executor(None, _sync_process_prompt, prompt)

    if "error" in result:
        await update.message.reply_text(f"⚠️ Error: {result['error']}")
        return

    action = result.get("action")
    args = result.get("arguments", {})
    response_msg = args.get("message", "")

    # Handle specific actions that have side effects
    if action == "antigravity":
        # Use the agent's extracted task prompt, not the raw user message
        task_prompt = args.get("prompt", prompt)
        session_name = await loop.run_in_executor(None, execute_agent_task, task_prompt)
        if session_name:
            if response_msg:
                response_msg += f"\n\n🚀 Background task started: `{session_name}`"
            else:
                response_msg = f"🚀 Background task started: `{session_name}`"
            # Monitor the task in the background and notify when done
            asyncio.create_task(_monitor_task(context.bot, update.effective_chat.id, session_name, task_prompt))
        else:
            response_msg += "\n\n❌ Failed to start background task."

    elif action == "browser":
        # Can't open a browser remotely — send the URL as a clickable link
        url = args.get("url", "")
        if url:
            response_msg += f"\n\n🔗 URL: {url}"

    elif action == "news_fetch":
        mode = args.get("mode", "skim")
        if mode == "skim":
            news = await loop.run_in_executor(None, get_news_summary)
            if news:
                response_msg = response_msg + "\n\n" + news if response_msg else news

    # Send text response
    if response_msg:
        for chunk in _split_message(response_msg):
            await update.message.reply_text(chunk)
    else:
        await update.message.reply_text("✅ Done.")

    # Auto-send screenshot if the agent captured one during execution
    if screenshot_modified:
        screenshot_path = "/tmp/elora_screenshot.png"
        try:
            with open(screenshot_path, "rb") as f:
                await update.message.reply_photo(photo=f, caption="📸 Screenshot captured during execution")
        except Exception as e:
            logger.error("Failed to send modified screenshot: %s", e)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming voice or audio messages."""
    if not await _check_auth_and_log(update):
        return

    await update.message.reply_chat_action(ChatAction.TYPING)
    
    audio_file = update.message.voice or update.message.audio
    if not audio_file:
        return
        
    file = await context.bot.get_file(audio_file.file_id)
    
    # Download to temp file
    temp_ogg = tempfile.mktemp(suffix=".ogg")
    temp_wav = tempfile.mktemp(suffix=".wav")

    try:
        await file.download_to_drive(temp_ogg)

        # Convert OGG/Opus to 16kHz mono WAV for Gemini transcription
        # Why: Gemini's audio API expects standard WAV format at reasonable sample rates
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", temp_ogg, "-ar", "16000", "-ac", "1", temp_wav,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await process.communicate()

        if process.returncode != 0:
            await update.message.reply_text("❌ Failed to convert audio format.")
            return

        # Transcribe using Gemini
        from elora.core.brain import transcribe_audio
        loop = asyncio.get_event_loop()
        transcript = await loop.run_in_executor(None, transcribe_audio, temp_wav)

        if not transcript:
            await update.message.reply_text("❌ Could not transcribe audio — no speech detected.")
            return

        # Show what was heard, then process
        await update.message.reply_text(f"🎙️ _{transcript}_", parse_mode="Markdown")
        await _process_prompt_async(transcript, update, context)

    except Exception as e:
        logger.error("Error handling voice message: %s", e)
        await update.message.reply_text(f"⚠️ Error processing audio: {e}")
    finally:
        _cleanup_temp_file(temp_ogg)
        _cleanup_temp_file(temp_wav)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming photo messages."""
    if not await _check_auth_and_log(update):
        return

    await update.message.reply_chat_action(ChatAction.TYPING)
    
    # Get highest resolution photo
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    
    screenshot_path = "/tmp/elora_screenshot.png"
    try:
        await file.download_to_drive(screenshot_path)
        
        prompt = update.message.caption or "Describe what you see in this image."
        await _process_prompt_async(prompt, update, context)
        
    except Exception as e:
        logger.error(f"Error handling photo: {e}")
        await update.message.reply_text(f"Error processing photo: {e}")


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler for unhandled exceptions in Telegram handlers.

    Why: Prevents the bot from crashing on unexpected errors and logs them.
    """
    logger.error("Unhandled exception in Telegram handler: %s", context.error, exc_info=context.error)
    if isinstance(update, Update) and update.message:
        try:
            await update.message.reply_text("⚠️ An internal error occurred. Please try again.")
        except Exception:
            pass


def start_telegram_bot() -> None:
    """Entry point for starting the Telegram bot. Called from main.py.

    Why: Initializes the application, registers all handlers, and starts
    long-polling against the Telegram Bot API.
    """
    from elora.core.config import load_config

    config = load_config()
    telegram_cfg = config.get("telegram", {})

    if not telegram_cfg.get("enabled", False):
        print("\nElora: Telegram bot is disabled in config.")
        print("       Enable it with: elora --setup")
        print("       Or set '\"telegram\": {\"enabled\": true}' in ~/.config/elora/config.json\n")
        return

    token_env_var = telegram_cfg.get("token_env_var", "TELEGRAM_BOT_TOKEN")
    token = load_env_credential(token_env_var)

    if not token:
        print("\nElora: Telegram bot token not found.")
        print(f"       Set {token_env_var}=your_token in ~/.env\n")
        return

    allowed_ids = telegram_cfg.get("allowed_user_ids", [])
    if not allowed_ids:
        print("\nElora: No allowed_user_ids configured. The bot will reject ALL messages.")
        print("       Add your Telegram user ID to 'telegram.allowed_user_ids' in ~/.config/elora/config.json")
        print("       Tip: Message @userinfobot on Telegram to find your user ID.\n")

    print("\n" + "=" * 50)
    print("  ELORA Telegram Bot (Remote Control)")
    print("=" * 50)
    print(f"  Authorized users: {len(allowed_ids)}")
    print(f"  Token env var:    {token_env_var}")
    print("  Press Ctrl+C to stop.")
    print("=" * 50 + "\n")

    from telegram.request import HTTPXRequest
    # Set generous timeouts to prevent connection, read, write, or pool timeouts during file uploads or slower sessions
    request = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0, write_timeout=30.0, pool_timeout=30.0)
    app = Application.builder().token(token).request(request).build()

    # Register command handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("screenshot", screenshot_command))
    app.add_handler(CommandHandler("tasks", tasks_command))
    app.add_handler(CommandHandler("zip", zip_command))
    app.add_handler(CommandHandler("sendfile", sendfile_command))

    # Register message handlers
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # Global error handler
    app.add_error_handler(_error_handler)

    logger.info("Telegram bot starting with %d authorized user(s).", len(allowed_ids))

    try:
        # Pass stop_signals=None to support running polling within a daemon background thread
        app.run_polling(allowed_updates=Update.ALL_TYPES, stop_signals=None)
    except KeyboardInterrupt:
        print("\nElora: Telegram bot stopped.")
    except Exception as e:
        logger.error("Telegram bot crashed: %s", e)
