"""
Elora ReAct Agent Execution Loop.
Manages multi-turn query loops, tool execution, and history updates.
"""

import logging
from typing import Dict, Any, List, Callable, Optional

from elora.brain import query_elora
from elora.skills import search_duckduckgo, scrape_webpage, run_local_command
from elora.browser_control import execute_browser_action
from elora.os_control import move_mouse_smoothly, click_mouse_at, type_keyboard_text
from elora.system_skills import set_system_volume, set_system_brightness, perform_window_action, launch_application
from elora.memory import (
    store_memory,
    search_memory,
    list_memory_topics,
    delete_memories,
    format_for_llm,
    is_memory_available,
    clear_all_memories,
)

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
        
        # Capture a fresh screenshot of the desktop/active window so the model has real-time visual context
        try:
            from elora.os_control import capture_desktop_screenshot
            capture_desktop_screenshot()
        except Exception as e:
            logger.debug("Failed to capture screenshot: %s", e)

        # Query the Ollama brain
        result = query_elora(current_prompt, history=loop_history)
        action = result.get("action")
        args = result.get("arguments", {})
        
        # End loop on terminal actions
        if action in ("reply", "browser", "news_fetch", "antigravity",
                      "memory_store", "memory_recall", "memory_focus", "memory_forget"):
            # Handle memory actions inline and return a synthesised reply
            if action == "memory_store":
                return _handle_memory_store(args)
            elif action == "memory_recall":
                return _handle_memory_recall(args)
            elif action == "memory_focus":
                # memory_focus returns raw hits so daemon can set active_focus;
                # wrap them in the result so daemon can consume them.
                return _handle_memory_focus(args)
            elif action == "memory_forget":
                return _handle_memory_forget(args)
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
            
        elif action == "browser_browse":
            url = args.get("url", "")
            status_msg = f"Browsing Brave to URL: {url}"
            if status_callback:
                status_callback(status_msg)
            res = execute_browser_action("browse", url=url)
            loop_history.append({"role": "user", "content": f"System Tool Output (browser_browse of {url}): {res}"})
            current_prompt = f"Analyze navigation outcome for {url} and determine next action."

        elif action == "browser_click":
            sel = args.get("selector_or_text", "")
            status_msg = f"Clicking element matching '{sel}' on Brave page"
            if status_callback:
                status_callback(status_msg)
            res = execute_browser_action("click", selector_or_text=sel)
            loop_history.append({"role": "user", "content": f"System Tool Output (browser_click on '{sel}'): {res}"})
            current_prompt = f"Analyze click outcome for '{sel}' and determine next action."

        elif action == "browser_type":
            sel = args.get("selector_or_text", "")
            val = args.get("text", "")
            status_msg = f"Typing '{val}' into input '{sel}' on Brave page"
            if status_callback:
                status_callback(status_msg)
            res = execute_browser_action("type", selector_or_text=sel, text=val)
            loop_history.append({"role": "user", "content": f"System Tool Output (browser_type '{val}' into '{sel}'): {res}"})
            current_prompt = f"Analyze input typing outcome for '{sel}' and determine next action."

        elif action == "browser_get_elements":
            status_msg = "Extracting visible elements from Brave page"
            if status_callback:
                status_callback(status_msg)
            res = execute_browser_action("get_elements")
            loop_history.append({"role": "user", "content": f"System Tool Output (browser_get_elements): {res}"})
            current_prompt = "Identify target elements from page structure and determine next action."

        elif action == "desktop_input":
            itype = args.get("input_type", "")
            x_coord = args.get("x", 0)
            y_coord = args.get("y", 0)
            text_val = args.get("text", "")
            
            status_msg = f"Simulating OS input: {itype}"
            if status_callback:
                status_callback(status_msg)
                
            res = ""
            if itype == "move":
                res = move_mouse_smoothly(int(x_coord), int(y_coord))
            elif itype == "click":
                res = click_mouse_at(int(x_coord), int(y_coord))
            elif itype in ("type", "shortcut"):
                res = type_keyboard_text(text_val)
            else:
                res = f"Error: Unknown input type '{itype}'"
                
            loop_history.append({"role": "user", "content": f"System Tool Output (desktop_input: {itype}): {res}"})
            current_prompt = f"Analyze desktop input outcome and determine next action."

        elif action == "system_control":
            ctype = args.get("control_type", "")
            lvl = args.get("level", 0)
            param_val = args.get("param", "")
            
            status_msg = f"Adjusting system control: {ctype}"
            if status_callback:
                status_callback(status_msg)
                
            res = ""
            if ctype == "volume":
                res = set_system_volume(int(lvl))
            elif ctype == "brightness":
                res = set_system_brightness(int(lvl))
            elif ctype == "window":
                res = perform_window_action(param_val)
            elif ctype == "launch":
                res = launch_application(param_val)
            else:
                res = f"Error: Unknown control type '{ctype}'"
                
            loop_history.append({"role": "user", "content": f"System Tool Output (system_control: {ctype}): {res}"})
            current_prompt = f"Analyze system control adjustment outcome and determine next action."
            
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


# ── Memory action handlers ─────────────────────────────────────────────────────

def _handle_memory_store(args: dict) -> dict:
    """
    Stores a new memory node from the user's request.
    Extracts 'text' and optional 'topic' from the agent args.

    Returns a synthesised reply action so the daemon can speak the confirmation.
    """
    avail, err = is_memory_available()
    if not avail:
        return {
            "action": "reply",
            "arguments": {"message": err}
        }

    text = args.get("text", "").strip()
    topic = args.get("topic", "general").strip() or "general"

    if not text:
        return {
            "action": "reply",
            "arguments": {"message": "I didn't catch what you wanted me to remember. Could you repeat that?"}
        }

    try:
        store_memory(text, topic=topic, source="user")
        logger.info("Stored memory under topic '%s': %s", topic, text[:60])
        return {
            "action": "reply",
            "arguments": {
                "message": f"Got it, I'll remember that under \"{topic}\": {text}"
            }
        }
    except Exception as e:
        return {
            "action": "reply",
            "arguments": {"message": f"Error storing memory: {str(e)}"}
        }


def _handle_memory_recall(args: dict) -> dict:
    """
    Searches memory for a query and returns a conversational spoken summary.
    Handles the special query 'all' to list all stored topics instead.
    """
    avail, err = is_memory_available()
    if not avail:
        return {
            "action": "reply",
            "arguments": {"message": err}
        }

    query = args.get("query", "").strip()

    try:
        # Special case: list all topics
        if not query or query.lower() in ("all", "everything", "anything"):
            topics = list_memory_topics()
            if not topics:
                return {
                    "action": "reply",
                    "arguments": {"message": "I haven't stored any memories yet."}
                }
            lines = ["Here's what I have in memory by topic:"]
            for topic, count in sorted(topics.items(), key=lambda x: -x[1]):
                noun = "memory" if count == 1 else "memories"
                lines.append(f"  {topic}: {count} {noun}")
            return {
                "action": "reply",
                "arguments": {"message": "\n".join(lines)}
            }

        # Semantic search
        hits = search_memory(query)
        if not hits:
            return {
                "action": "reply",
                "arguments": {
                    "message": f"I don't have any memories stored about \"{query}\"."
                }
            }

        lines = [f"Here's what I remember about \"{query}\":"]
        for h in hits:
            lines.append(f"  ({h['created_at'][:10]}) {h['text']}")
        return {
            "action": "reply",
            "arguments": {"message": "\n".join(lines)}
        }
    except Exception as e:
        return {
            "action": "reply",
            "arguments": {"message": f"Error recalling memory: {str(e)}"}
        }


def _handle_memory_focus(args: dict) -> dict:
    """
    Retrieves memories relevant to a topic and packages them for the daemon
    to inject as active focus context.

    Returns a special 'memory_focus' action dict (not 'reply') so the daemon
    can intercept it and set the active_focus session variable.
    """
    avail, err = is_memory_available()
    if not avail:
        return {
            "action": "reply",
            "arguments": {"message": err}
        }

    query = args.get("query", "").strip()
    if not query:
        return {
            "action": "reply",
            "arguments": {"message": "What topic would you like me to focus on?"}
        }

    try:
        hits = search_memory(query, top_k=5, threshold=0.65)
        memory_block = format_for_llm(hits, header=f"[Memory Focus: \"{query}\"]") if hits else ""

        spoken_msg = (
            f"Focusing on \"{query}\" now."
            if hits else
            f"I don't have any memories about \"{query}\" yet, but I'll keep it as our focus."
        )

        # Return the action as memory_focus so daemon can capture memory_block
        return {
            "action": "memory_focus",
            "arguments": {
                "query":        query,
                "memory_block": memory_block,
                "message":      spoken_msg,
            }
        }
    except Exception as e:
        return {
            "action": "reply",
            "arguments": {"message": f"Error focusing memory: {str(e)}"}
        }


def _handle_memory_forget(args: dict) -> dict:
    """
    Deletes memory nodes matching the query and returns a spoken confirmation.
    Uses a slightly lower threshold than search so near-matches are also erased.
    """
    avail, err = is_memory_available()
    if not avail:
        return {
            "action": "reply",
            "arguments": {"message": err}
        }

    query = args.get("query", "").strip()
    if not query:
        return {
            "action": "reply",
            "arguments": {"message": "What would you like me to forget?"}
        }

    try:
        # Check if the user wants to wipe the entire database
        if query.lower() in ("everything", "all", "all memories", "wipe", "wipe all", "clear all", "forget everything"):
            count = clear_all_memories()
            return {
                "action": "reply",
                "arguments": {
                    "message": f"I have cleared all {count} memories from my database and started fresh."
                }
            }

        count = delete_memories(query)
        if count == 0:
            return {
                "action": "reply",
                "arguments": {
                    "message": f"I couldn't find any memories matching \"{query}\" to delete."
                }
            }
        noun = "memory" if count == 1 else "memories"
        return {
            "action": "reply",
            "arguments": {
                "message": f"Done. I've forgotten {count} {noun} related to \"{query}\"."
            }
        }
    except Exception as e:
        return {
            "action": "reply",
            "arguments": {"message": f"Error forgetting memory: {str(e)}"}
        }


