"""
Elora's AI reasoning engine (brain).
Interactions with the Gemini API, enforcing strict JSON action structures.
"""

import os
import json
import logging
from typing import Dict, Any, List
from google import genai
from google.genai import types
from elora.core.config import load_config

logger = logging.getLogger("elora.brain")

DEFAULT_CUSTOM_INSTRUCTION = (
    "You are Elora, an intelligent OS orchestrator and a highly efficient, creative companion (like Jarvis). "
    "Always refer to the user as 'boss'. "
    "Keep your conversational responses extremely short, punchy, and direct. "
    "Prioritize task action and tool execution over verbose chatty explanations (less talk, more execution)."
)

PERSONALITIES = {
    "default": (
        "You are Elora, an intelligent OS orchestrator and a highly efficient, creative companion (like Jarvis). "
        "Always refer to the user as 'boss'. "
        "Keep your conversational responses extremely short, punchy, and direct. "
        "Prioritize task action and tool execution over verbose chatty explanations (less talk, more execution)."
    ),
    "funny": (
        "You are Elora, an intelligent OS orchestrator and a witty, funny, and playful companion. "
        "Incorporate jokes, humorous remarks, or light, friendly sarcasm into your responses. "
        "Keep the conversation fun and lively, but always ensure you execute the requested tasks correctly. "
        "If the user says goodbye, respond with a funny, memorable sign-off."
    ),
    "direct": (
        "You are Elora, an intelligent OS orchestrator. "
        "Adopt a direct, concise, and no-nonsense personality. "
        "Provide only the essential details, avoid unnecessary pleasantries, and get straight to the point. "
        "Ensure all actions are executed quickly and directly."
    ),
    "polite": (
        "You are Elora, an intelligent OS orchestrator. "
        "Adopt an extremely polite, warm, and courteous personality. "
        "Always address the user with deep politeness and refinement. Use words like 'please', 'thank you', "
        "and 'it is my absolute pleasure to assist you'. Be exceptionally respectful."
    ),
    "respectful": (
        "You are Elora, an intelligent OS orchestrator. "
        "Adopt a highly respectful, formal, and honorable personality. "
        "Maintain a helpful, deferential, and professional posture in all interactions. "
        "Treat all requests with high importance and respect."
    )
}


def get_dynamic_system_instruction(config: Dict[str, Any]) -> str:
    """
    Builds the system instruction dynamically, tailoring active guidelines and
    the JSON schema to the user's enabled skills.
    """
    personality = config.get("personality", "default")
    if personality == "other":
        custom_desc = config.get("custom_personality", "helpful")
        custom_prompt = (
            f"You are Elora, an intelligent OS orchestrator. Adopt a personality that is: {custom_desc}. "
            "Speak and act in accordance with this personality in all of your responses, while still successfully executing tasks."
        )
    else:
        if "personality" not in config and "custom_instructions" in config:
            custom_prompt = config["custom_instructions"]
        else:
            custom_prompt = PERSONALITIES.get(personality, PERSONALITIES["default"])
    skills_cfg = config.get("skills", {"web_search": True, "web_scrape": True, "command_run": True})
    
    allowed_actions = ["antigravity", "browser", "news_fetch", "reply",
                        "memory_store", "memory_recall", "memory_focus", "memory_forget",
                        "browser_browse", "browser_click", "browser_type", "browser_get_elements",
                        "desktop_input", "system_control"]
    guidelines = [
        "4. Use 'antigravity' for coding, workspace automation, or heavy calculations. Provide a conversational message in 'message' explaining what you are delegating.",
        "5. Use 'browser' to open a webpage on the user's desktop browser (e.g. \"open github.com\") using default xdg-open.",
        "6. Use 'news_fetch' with mode 'skim' when asked for tech news or updates.",
        "7. Use 'news_fetch' with mode 'deep_dive' and the 'index' number when asked to open a specific news article from a previous list.",
        "8. Use 'reply' to talk to the user, answer questions with gathered data, or request clarification.",
        "9. Use 'memory_store' when the user says 'remember that', 'save this', or 'keep in mind' — extract the key fact as 'text' and infer a short 'topic' label.",
        "10. Use 'memory_recall' when the user says 'do you remember', 'what do you know about', or 'recall' — set 'query' to the topic they're asking about.",
        "11. Use 'memory_focus' when the user says 'focus on [topic]', 'let's talk about [topic]', or 'switch to [topic]' — set 'query' to the topic.",
        "12. Use 'memory_forget' when the user says 'forget' or 'delete from memory' — set 'query' to what should be erased.",
        "13. Use 'memory_recall' when the user says 'what have you remembered' or 'list your memories' — set query to 'all'.",
        "14. Use 'browser_browse' to navigate the remote-debugged Brave browser to a URL (e.g. 'google.com').",
        "15. Use 'browser_click' to click an interactive element on the active Brave page by CSS selector or descriptive text label (e.g. 'Sign in').",
        "16. Use 'browser_type' to fill text in an input field on the active Brave page (e.g. selector_or_text='search', text='weather').",
        "17. Use 'browser_get_elements' to retrieve a list of all visible/interactive page elements to understand the current page layout.",
        "18. Use 'desktop_input' to control mouse cursor/keyboard universally on the system. Types are 'move' (requires x, y), 'click' (requires x, y), 'type' (requires text), or 'shortcut' (requires text, e.g. 'alt+tab').",
        "19. Use 'system_control' to adjust OS controls. Types are 'volume' (requires level), 'brightness' (requires level), 'window' (requires param: 'minimize', 'maximize', 'close'), or 'launch' (requires param: app name, e.g., 'code', 'chrome', 'calculator').",
        "20. To stop or cancel a running background agent task (e.g., 'agy'), use 'command_run' with `tmux kill-session -t <session_name>`. You can see running tasks via `tmux list-sessions` or by checking `~/.config/elora/tasks.json`."
    ]
    
    if skills_cfg.get("web_search", True):
        allowed_actions.append("web_search")
        guidelines.insert(0, "1. Use 'web_search' to search the web for answers, docs, or status if you don't know the answer.")
    if skills_cfg.get("web_scrape", True):
        allowed_actions.append("web_scrape")
        guidelines.insert(1, "2. Use 'web_scrape' to fetch and read the plain text content of a specific webpage URL.")
    if skills_cfg.get("command_run", True):
        allowed_actions.append("command_run")
        guidelines.insert(2, "3. Use 'command_run' to execute local shell commands (e.g., copying/moving/deleting files, creating directories, running scripts, package commands, or system queries) to autonomously perform actions on behalf of the user. Always use non-interactive flags (e.g., '-y', '--yes') for initializations, package managers, and tool installations to prevent prompts from hanging.")
        
    guidelines_str = "\n".join(guidelines)
    
    return f"""{custom_prompt}

Guidelines:
{guidelines_str}
"""

# Dict-based JSON Schema for Elora action response
ELORA_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "thought": {
            "type": "STRING",
            "description": "Your internal step-by-step reasoning explaining why you are choosing the current action."
        },
        "action": {
            "type": "STRING",
            "description": "The action to perform. Must be one of the allowed actions."
        },
        "arguments": {
            "type": "OBJECT",
            "properties": {
                "prompt": {"type": "STRING", "description": "For 'antigravity', the task prompt to pass to the CLI agent."},
                "url": {"type": "STRING", "description": "For 'browser', 'browser_browse', or 'web_scrape', the URL to open or fetch."},
                "query": {"type": "STRING", "description": "For 'web_search', 'memory_recall', 'memory_focus', 'memory_forget' — the search query or topic."},
                "command": {"type": "STRING", "description": "For 'command_run', the local shell command to execute."},
                "mode": {"type": "STRING", "description": "For 'news_fetch', either 'skim' or 'deep_dive'."},
                "index": {"type": "INTEGER", "description": "For 'news_fetch' with mode='deep_dive', the 1-based integer index of the article to open."},
                "text": {"type": "STRING", "description": "For 'memory_store', 'browser_type', or 'desktop_input' (text/shortcut to type)."},
                "topic": {"type": "STRING", "description": "For 'memory_store', a short lowercase topic label (e.g. 'linux', 'projects')."},
                "message": {"type": "STRING", "description": "For 'reply' or 'antigravity', the direct chat/spoken response to the user."},
                "selector_or_text": {"type": "STRING", "description": "For 'browser_click' or 'browser_type', the CSS selector or the text label of the target element."},
                "input_type": {"type": "STRING", "description": "For 'desktop_input', either 'move', 'click', 'type', or 'shortcut'."},
                "x": {"type": "INTEGER", "description": "For 'desktop_input' (integer X coordinate)."},
                "y": {"type": "INTEGER", "description": "For 'desktop_input' (integer Y coordinate)."},
                "control_type": {"type": "STRING", "description": "For 'system_control', either 'volume', 'brightness', 'window', or 'launch'."},
                "level": {"type": "INTEGER", "description": "For 'system_control' (integer 0-100 for volume or brightness)."},
                "param": {"type": "STRING", "description": "For 'system_control' window actions ('minimize', 'maximize', 'close') or app names to launch."}
            }
        }
    },
    "required": ["action"]
}


def convert_history_to_gemini(history: List[Dict[str, str]]) -> List[types.Content]:
    """Converts standard chat history format into Gemini client's Content format."""
    gemini_contents = []
    for msg in history:
        role = msg.get("role")
        content = msg.get("content", "")
        if not content:
            continue
            
        gemini_role = "user"
        if role == "assistant":
            gemini_role = "model"
            
        gemini_contents.append(
            types.Content(
                role=gemini_role,
                parts=[types.Part.from_text(text=content)]
            )
        )
    return gemini_contents


def transcribe_audio(audio_path: str) -> str:
    """
    Transcribes a local WAV file to text using Gemini.
    
    Why: Resolves voice queries to clean text prompts before executing ReAct reasoning,
         preventing raw file paths from leaking into the LLM history and agent prompts.
    """
    if not os.path.exists(audio_path):
        logger.error("Audio path does not exist: %s", audio_path)
        return ""

    config = load_config()
    api_key = config.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.error("Gemini API key is not configured for transcription.")
        return ""

    try:
        with open(audio_path, "rb") as f:
            audio_bytes = f.read()

        client = genai.Client(api_key=api_key)
        
        primary_model = config.get("model_name", "gemini-2.5-flash")
        model_candidates = [primary_model, "gemini-2.0-flash"]
        if primary_model == "gemini-2.0-flash":
            model_candidates = ["gemini-2.0-flash"]

        last_exception = None
        for model in model_candidates:
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=[
                        types.Part.from_bytes(data=audio_bytes, mime_type="audio/wav"),
                        types.Part.from_text(
                            text="Transcribe the following audio recording into text. Respond ONLY with the transcription, nothing else. Do not add any commentary, notes, or formatting. If the audio is silent or contains no speech, respond with an empty string."
                        )
                    ]
                )
                transcription = response.text.strip()
                logger.info("Transcribed audio successfully: %s", transcription)
                return transcription
            except Exception as e:
                last_exception = e
                logger.warning("Transcription failed with model %s: %s", model, e)
                
        if last_exception:
            logger.error("All models failed to transcribe audio. Last error: %s", last_exception)
            
    except Exception as e:
        logger.error("Failed to transcribe audio file: %s", e)

    return ""


def query_elora(user_prompt: str, history: List[Dict[str, str]] = None) -> Dict[str, Any]:
    """
    Queries the Gemini API to get the structured JSON action block.
    Supports multimodal voice commands (WAV files) and screenshot context.
    """
    config = load_config()
    model_name = config.get("model_name", "gemini-2.5-flash")
    
    api_key = config.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {
            "action": "reply",
            "arguments": {
                "message": "Error: Gemini API key is not configured. Please set the GEMINI_API_KEY environment variable or save it in ~/.config/elora/config.json."
            }
        }

    sys_instruction = get_dynamic_system_instruction(config)
    gemini_history = convert_history_to_gemini(history) if history else []

    # Compile parts for the new user message
    user_parts = []
    
    # 1. Check if user_prompt is a local audio WAV file
    if user_prompt.endswith(".wav") and os.path.exists(user_prompt):
        try:
            with open(user_prompt, "rb") as f:
                audio_bytes = f.read()
            user_parts.append(
                types.Part.from_bytes(data=audio_bytes, mime_type="audio/wav")
            )
            # Instruct Gemini to process the speech command in the context of system instruction
            user_parts.append(
                types.Part.from_text(text="Listen to this voice command and determine the correct structured action.")
            )
            logger.info("Loaded voice command WAV file into Gemini query: %s", user_prompt)
        except Exception as e:
            logger.error("Failed to read voice audio file: %s", e)
            user_parts.append(types.Part.from_text(text=f"Failed to load audio command: {user_prompt}"))
    else:
        user_parts.append(types.Part.from_text(text=user_prompt))

    # 2. Check if a screenshot is available (captured within last 15s)
    screenshot_path = "/tmp/elora_screenshot.png"
    if os.path.exists(screenshot_path):
        import time
        if time.time() - os.path.getmtime(screenshot_path) < 15.0:
            try:
                with open(screenshot_path, "rb") as f:
                    img_bytes = f.read()
                user_parts.append(
                    types.Part.from_bytes(data=img_bytes, mime_type="image/png")
                )
                logger.info("Attached desktop screenshot to Gemini query context.")
            except Exception as e:
                logger.error("Failed to read screenshot file: %s", e)

    # Construct the final content structure
    new_user_content = types.Content(role="user", parts=user_parts)
    contents = gemini_history + [new_user_content]

    import time
    
    primary_model = config.get("model_name", "gemini-2.5-flash")
    model_candidates = [primary_model, "gemini-2.0-flash"]
    if primary_model == "gemini-2.0-flash":
        model_candidates = ["gemini-2.0-flash"]

    client = genai.Client(api_key=api_key)
    last_exception = None
    
    for model in model_candidates:
        max_retries = 3
        backoff = 1.0
        
        for attempt in range(max_retries):
            try:
                logger.info("Sending request to Gemini API with model %s (attempt %d/%d)...", model, attempt + 1, max_retries)
                response = client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=sys_instruction,
                        response_mime_type="application/json",
                        response_schema=ELORA_RESPONSE_SCHEMA
                    )
                )
                
                content = response.text
                logger.debug("Raw model output: %s", content)
                parsed = json.loads(content)
                return parsed
                
            except json.JSONDecodeError as decode_err:
                logger.error("Failed to parse JSON response from Gemini: %s", str(decode_err))
                return {
                    "action": "reply",
                    "arguments": {
                        "message": "Error: Received malformed action payload from Gemini."
                    }
                }
            except Exception as e:
                last_exception = e
                err_str = str(e).lower()
                is_transient = "503" in err_str or "429" in err_str or "500" in err_str or "unavailable" in err_str or "quota" in err_str
                
                if is_transient and attempt < max_retries - 1:
                    logger.warning("Transient error %s. Retrying in %.2fs...", e, backoff)
                    time.sleep(backoff)
                    backoff *= 2.0
                else:
                    logger.warning("Model %s failed: %s. Trying next candidate if available.", model, e)
                    break

    logger.error("Gemini API connection error: All models failed. Last error: %s", str(last_exception))
    return {
        "action": "reply",
        "arguments": {
            "message": f"Error communicating with Gemini API: {last_exception}"
        }
    }
