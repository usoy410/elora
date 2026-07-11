"""
Elora's AI reasoning engine (brain).
Interactions with the local/cloud Ollama client, enforcing strict JSON action structures.
"""

import json
import logging
from typing import Dict, Any, List
import ollama
from elora.config import load_config

logger = logging.getLogger("elora.brain")

DEFAULT_CUSTOM_INSTRUCTION = (
    "You are Elora, an intelligent OS orchestrator and Linux assistant. "
    "Your goal is to parse the user prompt and delegate it to the correct local action block."
)


def get_dynamic_system_instruction(config: Dict[str, Any]) -> str:
    """
    Builds the system instruction dynamically, tailoring active guidelines and
    the JSON schema to the user's enabled skills.
    """
    custom_prompt = config.get("custom_instructions", DEFAULT_CUSTOM_INSTRUCTION)
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
        "19. Use 'system_control' to adjust OS controls. Types are 'volume' (requires level), 'brightness' (requires level), 'window' (requires param: 'minimize', 'maximize', 'close'), or 'launch' (requires param: app name, e.g., 'code', 'chrome', 'calculator')."
    ]
    
    # Prepend dynamic options if enabled
    if skills_cfg.get("web_search", True):
        allowed_actions.append("web_search")
        guidelines.insert(0, "1. Use 'web_search' to search the web for answers, docs, or status if you don't know the answer.")
    if skills_cfg.get("web_scrape", True):
        allowed_actions.append("web_scrape")
        guidelines.insert(1, "2. Use 'web_scrape' to fetch and read the plain text content of a specific webpage URL.")
    if skills_cfg.get("command_run", True):
        allowed_actions.append("command_run")
        guidelines.insert(2, "3. Use 'command_run' to execute local shell commands (e.g., copying/moving/deleting files, creating directories, running scripts, package commands, or system queries) to autonomously perform actions on behalf of the user.")
        
    actions_str = " | ".join(f'"{a}"' for a in allowed_actions)
    guidelines_str = "\n".join(guidelines)
    
    return f"""{custom_prompt}

You must respond strictly with a valid JSON object matching this schema:

{{
  "action": {actions_str},
  "arguments": {{
    "prompt":            "For 'antigravity', the task prompt to pass to the CLI agent.",
    "url":               "For 'browser', 'browser_browse', or 'web_scrape', the URL to open or fetch.",
    "query":             "For 'web_search', 'memory_recall', 'memory_focus', 'memory_forget' — the search query or topic.",
    "command":           "For 'command_run', the local shell command to execute.",
    "mode":              "For 'news_fetch', either 'skim' (to summarize news) or 'deep_dive' (to open an article).",
    "index":             "For 'news_fetch' with mode='deep_dive', the 1-based integer index of the article to open.",
    "text":              "For 'memory_store' (fact statement), 'browser_type' (text to enter), or 'desktop_input' (text/shortcut to type).",
    "topic":             "For 'memory_store', a short lowercase topic label (e.g. 'linux', 'projects').",
    "message":           "For 'reply' or 'antigravity', the direct chat/spoken response to the user.",
    "selector_or_text":  "For 'browser_click' or 'browser_type', the CSS selector or the text label of the target element.",
    "input_type":        "For 'desktop_input', either 'move', 'click', 'type', or 'shortcut'.",
    "x":                 "For 'desktop_input' (integer X coordinate).",
    "y":                 "For 'desktop_input' (integer Y coordinate).",
    "control_type":      "For 'system_control', either 'volume', 'brightness', 'window', or 'launch'.",
    "level":             "For 'system_control' (integer 0-100 for volume or brightness).",
    "param":             "For 'system_control' window actions ('minimize', 'maximize', 'close') or app names to launch ('code', 'calculator', etc.)"
  }}
}}

Guidelines:
{guidelines_str}
"""


def query_elora(user_prompt: str, history: List[Dict[str, str]] = None) -> Dict[str, Any]:
    """
    Queries Ollama to get the structured JSON action block.
    
    Why: Enforcing format="json" in the Ollama client ensures the model outputs
    valid JSON, preventing parsing errors in our core loop.
    """
    config = load_config()
    model_name = config.get("model_name", "gpt-oss:120b-cloud")
    sys_instruction = get_dynamic_system_instruction(config)
    
    messages = [{"role": "system", "content": sys_instruction}]
    
    if history:
        messages.extend(history)
        
    messages.append({"role": "user", "content": user_prompt})
    
    try:
        logger.info("Sending request to Ollama with model %s...", model_name)
        
        # Call Ollama chat API enforcing JSON formatting
        response = ollama.chat(
            model=model_name,
            messages=messages,
            format="json"
        )

        
        content = response["message"]["content"]
        logger.debug("Raw model output: %s", content)
        
        # Parse the JSON response
        parsed = json.loads(content)
        return parsed
        
    except json.JSONDecodeError as e:
        logger.error("Failed to parse JSON response from Ollama: %s", str(e))
        return {
            "action": "reply",
            "arguments": {
                "message": "Error: Received malformed action payload from reasoning model."
            }
        }
    except Exception as e:
        logger.error("Ollama API connection error: %s", str(e))
        
        # If the cloud model is unauthorized/disconnected, provide actionable advice
        if "Unauthorized" in str(e) or "401" in str(e):
            return {
                "action": "reply",
                "arguments": {
                    "message": "I am currently unauthorized to access the cloud reasoning model.\n"
                               "Please run 'ollama signin' in your local GUI terminal to authenticate."
                }
            }
        return {
            "action": "reply",
            "arguments": {
                "message": f"Error communicating with local Ollama daemon: {e}"
            }
        }
