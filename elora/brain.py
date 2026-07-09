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

SYSTEM_INSTRUCTION = """You are Elora, an intelligent OS orchestrator and Linux assistant.
Your goal is to parse the user prompt and delegate it to the correct local action block.
You must respond strictly with a valid JSON object matching this schema:

{
  "action": "antigravity" | "browser" | "news_fetch" | "reply",
  "arguments": {
    "prompt": "For 'antigravity', the task prompt to pass to the CLI agent.",
    "url": "For 'browser', the URL to open.",
    "mode": "For 'news_fetch', either 'skim' (to summarize news) or 'deep_dive' (to open an article).",
    "index": "For 'news_fetch' with mode='deep_dive', the 1-based integer index of the article to open.",
    "message": "For 'reply', the direct chat response to the user."
  }
}

Guidelines:
1. Use 'antigravity' for coding, workspace automation, or heavy calculations.
2. Use 'browser' to open a web page directly (e.g. "go to github.com").
3. Use 'news_fetch' with mode 'skim' when asked for tech news or updates.
4. Use 'news_fetch' with mode 'deep_dive' and the 'index' number when asked to open a specific news article from a previous list.
5. Use 'reply' for simple questions, greetings, or explanations.
"""


def query_elora(user_prompt: str, history: List[Dict[str, str]] = None) -> Dict[str, Any]:
    """
    Queries Ollama to get the structured JSON action block.
    
    Why: Enforcing format="json" in the Ollama client ensures the model outputs
    valid JSON, preventing parsing errors in our core loop.
    """
    config = load_config()
    model_name = config.get("model_name", "gpt-oss:120b-cloud")
    
    messages = [{"role": "system", "content": SYSTEM_INSTRUCTION}]
    
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
