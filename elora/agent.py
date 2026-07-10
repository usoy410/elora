"""
Elora ReAct Agent Execution Loop.
Manages multi-turn query loops, tool execution, and history updates.
"""

import logging
from typing import Dict, Any, List, Callable, Optional

from elora.brain import query_elora
from elora.skills import search_duckduckgo, scrape_webpage, run_local_command

logger = logging.getLogger("elora.agent")


def run_agent_loop(
    initial_prompt: str,
    history: List[Dict[str, str]],
    status_callback: Optional[Callable[[str], None]] = None
) -> Dict[str, Any]:
    """
    Executes the multi-turn ReAct reasoning loop.
    Runs up to 5 steps, executing intermediate skills (search, scrape, shell command)
    and feeding results back to the brain before delivering a final reply.
    
    Why: Equips Elora with autonomous problem solving and real-time research capabilities.
    """
    current_prompt = initial_prompt
    loop_history = list(history)  # Shallow copy history to modify locally
    
    step_count = 0
    max_steps = 5
    
    while step_count < max_steps:
        step_count += 1
        logger.info("Agent Loop Turn %d/%d", step_count, max_steps)
        
        # Query the Ollama brain
        result = query_elora(current_prompt, history=loop_history)
        action = result.get("action")
        args = result.get("arguments", {})
        
        # End loop on terminal actions
        if action in ("reply", "browser", "news_fetch", "antigravity"):
            return result
            
        elif action == "web_search":
            query = args.get("query", "")
            if not query:
                msg = "Tool execution skipped: Query parameter missing."
                logger.warning(msg)
                loop_history.append({"role": "user", "content": f"System Alert: {msg}"})
                current_prompt = "Provide your next action block."
                continue
                
            status_msg = f"Searching the web for: '{query}'"
            logger.info(status_msg)
            if status_callback:
                status_callback(status_msg)
                
            # Run DuckDuckGo search
            search_result = search_duckduckgo(query)
            
            # Feed back to the LLM context
            loop_history.append({"role": "user", "content": f"System Tool Output (web_search for '{query}'):\n{search_result}"})
            current_prompt = f"Analyze the search results for '{query}' and determine your next action."
            
        elif action == "web_scrape":
            url = args.get("url", "")
            if not url:
                msg = "Tool execution skipped: URL parameter missing."
                logger.warning(msg)
                loop_history.append({"role": "user", "content": f"System Alert: {msg}"})
                current_prompt = "Provide your next action block."
                continue
                
            status_msg = f"Scraping webpage text from: {url}"
            logger.info(status_msg)
            if status_callback:
                status_callback(status_msg)
                
            # Run webpage text scraper
            scrape_result = scrape_webpage(url)
            
            # Feed back to LLM context
            loop_history.append({"role": "user", "content": f"System Tool Output (web_scrape of {url}):\n{scrape_result}"})
            current_prompt = f"Analyze the scraped webpage content from {url} and determine your next action."
            
        elif action == "command_run":
            cmd = args.get("command", "")
            if not cmd:
                msg = "Tool execution skipped: Command parameter missing."
                logger.warning(msg)
                loop_history.append({"role": "user", "content": f"System Alert: {msg}"})
                current_prompt = "Provide your next action block."
                continue
                
            status_msg = f"Executing shell command: '{cmd}'"
            logger.info(status_msg)
            if status_callback:
                status_callback(status_msg)
                
            # Execute local shell utility command
            command_result = run_local_command(cmd)
            
            # Feed output back
            loop_history.append({"role": "user", "content": f"System Tool Output (command_run for '{cmd}'):\n{command_result}"})
            current_prompt = f"Analyze the command output of '{cmd}' and determine your next action."
            
        else:
            logger.warning("Agent encountered unknown action: %s", action)
            return result
            
    # Fallback response if loop iteration limit is hit
    return {
        "action": "reply",
        "arguments": {
            "message": "I've conducted extensive background research but hit my execution step limit before compiling the answer. Please try asking a more focused question."
        }
    }
