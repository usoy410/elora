"""
Elora ReAct Agent Execution Loop.
Manages multi-turn query loops, tool execution, and history updates.
"""

import logging
import os
from collections.abc import Callable
from typing import Any

from elora.core.brain import query_elora
from elora.core.memory import (
    clear_all_memories,
    delete_memories,
    format_for_llm,
    is_memory_available,
    list_memory_topics,
    search_memory,
    store_memory,
)
from elora.skills.browser_control import execute_browser_action
from elora.skills.classroom import fetch_classroom_data, save_classroom_document
from elora.skills.os_control import (
    click_mouse_at,
    move_mouse_smoothly,
    type_keyboard_text,
)
from elora.skills.skills import run_local_command, scrape_webpage, search_duckduckgo
from elora.skills.spotify import (
    control_spotify,
    play_spotify_uri,
    search_and_play_spotify,
)
from elora.skills.system_skills import (
    launch_application,
    perform_window_action,
    set_system_brightness,
    set_system_volume,
)
from elora.skills.workspace import run_workspace_query

logger = logging.getLogger("elora.agent")


def is_visual_query(prompt: str) -> bool:
    """
    Checks if a query requires visual screen context based on specific visual/GUI keywords.
    
    Why: Prevents taking slow, unnecessary screenshots for common conversational verbs
    like 'show', 'see', 'look', or 'this' which are frequently used in pure text queries.
    """
    prompt_lower = prompt.lower()
    
    # Precise screen inspection and explanation queries
    screen_queries = {
        "screenshot", 
        "explain screen", "explain my screen", "explain the screen",
        "describe screen", "describe my screen", "describe the screen",
        "what is on my screen", "what's on my screen", "what is on the screen", "what's on the screen",
        "tell me what you see", "what do you see on", "what is visible", "explain this window", "what window is open"
    }
    
    if any(query in prompt_lower for query in screen_queries):
        return True
        
    # Graphical interaction commands that explicitly require visual coordinates or element locating immediately
    interaction_patterns = [
        "click on the", "click the", "click on my", "double-click the", "double-click on",
        "type into the", "type in the", "type text into", "move mouse to", "move cursor to",
        "click at", "click on screen", "click screen"
    ]
    
    if any(pattern in prompt_lower for pattern in interaction_patterns):
        return True
        
    return False


def run_agent_loop(
    initial_prompt: str,
    history: list[dict[str, str]],
    status_callback: Callable[[Any], None] | None = None,
    confirm_callback: Callable[[str, dict[str, Any]], bool] | None = None,
    screenshot_callback: Callable[[], bool] | None = None
) -> dict[str, Any]:
    """
    Executes the multi-turn ReAct reasoning loop.
    Runs up to 5 steps, executing intermediate skills (search, scrape, shell command)
    and feeding results back to the brain before delivering a final reply.
    
    Why: Equips Elora with autonomous problem solving and real-time research capabilities.
    """
    current_prompt = initial_prompt
    loop_history = list(history)  # Shallow copy history to modify locally
    
    # Initialize visual requirement check
    needs_screenshot = is_visual_query(initial_prompt)
    
    step_count = 0
    max_steps = 5
    spoke_already = False
    
    def _report_status(payload: Any):
        if status_callback:
            try:
                status_callback(payload)
            except TypeError:
                # Fallback for simple string receivers
                if isinstance(payload, dict):
                    if payload.get("type") == "thought":
                        status_callback(f"Thought: {payload.get('text')}")
                    elif payload.get("type") == "tool_start":
                        status_callback(f"Executing {payload.get('tool')} with {payload.get('arguments')}")
                    elif payload.get("type") == "tool_output":
                        status_callback(f"Tool {payload.get('tool')} finished.")
                    elif payload.get("type") == "confirm_request":
                        status_callback(f"Requesting confirmation for {payload.get('action')}")
                else:
                    status_callback(str(payload))
    
    while step_count < max_steps:
        step_count += 1
        logger.info("Agent Loop Turn %d/%d", step_count, max_steps)
        
        # Capture a fresh screenshot of the desktop/active window if needed, otherwise clean up stale ones
        if needs_screenshot:
            try:
                if screenshot_callback:
                    success = screenshot_callback()
                    if not success:
                        from elora.skills.os_control import capture_desktop_screenshot
                        capture_desktop_screenshot()
                else:
                    from elora.skills.os_control import capture_desktop_screenshot
                    capture_desktop_screenshot()
            except Exception as e:
                logger.debug("Failed to capture screenshot: %s", e)
        else:
            screenshot_path = "/tmp/elora_screenshot.png"
            if os.path.exists(screenshot_path):
                try:
                    os.remove(screenshot_path)
                except Exception:
                    pass

        # Query the Ollama brain
        result = query_elora(current_prompt, history=loop_history)
        thought = result.get("thought", "")
        action = result.get("action")
        args = result.get("arguments", {})
        
        # Decide if the next step in the loop requires visual feedback (e.g. raw desktop coordinate interaction)
        needs_screenshot = action in ("desktop_input",)
        
        # Report reasoning thought
        if thought:
            _report_status({"type": "thought", "text": thought})
        
        # End loop on terminal actions
        if action in ("reply", "browser", "news_fetch", "antigravity",
                      "memory_store", "memory_recall", "memory_focus", "memory_forget"):
            # Handle memory actions inline and return a synthesised reply
            if action == "memory_store":
                result_obj = _handle_memory_store(args)
            elif action == "memory_recall":
                result_obj = _handle_memory_recall(args)
            elif action == "memory_focus":
                # memory_focus returns raw hits so daemon can set active_focus;
                # wrap them in the result so daemon can consume them.
                result_obj = _handle_memory_focus(args)
            elif action == "memory_forget":
                result_obj = _handle_memory_forget(args)
            else:
                result_obj = result

            if spoke_already and isinstance(result_obj, dict):
                result_obj["spoke_already"] = True
            return result_obj
            
        # Report tool start
        if action in ("web_search", "web_scrape", "command_run", "browser_browse", "browser_click",
                      "browser_type", "browser_get_elements", "desktop_input", "system_control", "spotify_control", "classroom_query", "classroom_export_doc", "workspace_query"):
            _report_status({
                "type": "tool_start",
                "tool": action,
                "arguments": args
            })
            
        if action == "classroom_query":
            mode_val = args.get("mode", "list_pending")
            cw_id = args.get("coursework_id")
            c_id = args.get("course_id")
            
            logger.info("Executing classroom query with mode %s", mode_val)
            classroom_result = fetch_classroom_data(mode=mode_val, coursework_id=cw_id, course_id=c_id)
            
            _report_status({
                "type": "tool_output",
                "tool": "classroom_query",
                "arguments": args,
                "output": classroom_result
            })
            
            # Feed back to the LLM context
            loop_history.append({"role": "user", "content": f"System Tool Output (classroom_query):\n{classroom_result}"})
            current_prompt = (
                f"Analyze the retrieved Classroom data for mode '{mode_val}' and formulate your next response or action. "
                "Ensure your reply is highly conversational, clear, flowing, and directly answers what the user asked."
            )

        elif action == "classroom_export_doc":
            content_val = args.get("content", "")
            filename_val = args.get("filename", "classroom_document")
            format_val = args.get("format", "md")
            
            logger.info("Executing classroom export doc to %s in %s format", filename_val, format_val)
            export_result = save_classroom_document(content=content_val, filename=filename_val, file_format=format_val)
            
            _report_status({
                "type": "tool_output",
                "tool": "classroom_export_doc",
                "arguments": args,
                "output": export_result
            })
            
            loop_history.append({"role": "user", "content": f"System Tool Output (classroom_export_doc):\n{export_result}"})
            current_prompt = (
                f"Acknowledge the document export outcome ('{export_result}') conversationally back to the user. "
                "Confirm that it has been saved, and present a very brief outline or summary of what was generated, if relevant."
            )

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
                
            # Run DuckDuckGo search
            search_result = search_duckduckgo(query)
            
            _report_status({
                "type": "tool_output",
                "tool": "web_search",
                "arguments": args,
                "output": search_result
            })
            
            # Feed back to the LLM context
            loop_history.append({"role": "user", "content": f"System Tool Output (web_search for '{query}'):\n{search_result}"})
            current_prompt = (
                f"Analyze the search results for '{query}' and determine your next action. "
                "If you have enough information to reply, make sure your response is highly conversational, "
                "fluent, and avoids mechanical numbered lists."
            )
            
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
                
            # Run webpage text scraper
            scrape_result = scrape_webpage(url)
            
            _report_status({
                "type": "tool_output",
                "tool": "web_scrape",
                "arguments": args,
                "output": scrape_result
            })
            
            # Feed back to LLM context
            loop_history.append({"role": "user", "content": f"System Tool Output (web_scrape of {url}):\n{scrape_result}"})
            current_prompt = (
                f"Analyze the scraped webpage content from {url} and determine your next action. "
                "If you have enough information to reply, make sure your response is highly conversational, "
                "fluent, and avoids mechanical numbered lists."
            )
            
        elif action == "command_run":
            cmd = args.get("command", "")
            if not cmd:
                msg = "Tool execution skipped: Command parameter missing."
                logger.warning(msg)
                loop_history.append({"role": "user", "content": f"System Alert: {msg}"})
                current_prompt = "Provide your next action block."
                continue
                
            # Safe Gate checks for destructive shell commands
            from elora.core.config import load_config
            from elora.utils import is_destructive_command
            config = load_config()
            safe_gate_enabled = config.get("safe_gate_mode", True)
            
            if safe_gate_enabled and is_destructive_command(cmd):
                _report_status({
                    "type": "confirm_request",
                    "action": "command_run",
                    "arguments": args
                })
                
                approved = False
                if confirm_callback:
                    try:
                        approved = confirm_callback("command_run", args)
                    except Exception as e:
                        logger.error("Error in confirmation callback: %s", e)
                else:
                    # CLI Terminal fallback prompt
                    try:
                        user_choice = input(
                            f"\n[!] WARNING: Elora wants to run a potentially destructive command:\n"
                            f"    {cmd}\n"
                            f"Allow execution? (y/N): "
                        ).strip().lower()
                        approved = user_choice in ("y", "yes")
                    except Exception:
                        approved = False
                        
                if not approved:
                    msg = f"Tool execution blocked: Command '{cmd}' denied by user confirmation."
                    logger.warning(msg)
                    loop_history.append({"role": "user", "content": f"System Alert: {msg}"})
                    _report_status({
                        "type": "tool_output",
                        "tool": "command_run",
                        "arguments": args,
                        "output": "Error: Command execution denied by user."
                    })
                    current_prompt = "Provide your next action block."
                    continue
                
            status_msg = f"Executing shell command: '{cmd}'"
            logger.info(status_msg)
                
            # Execute local shell utility command
            command_result = run_local_command(cmd)
            
            _report_status({
                "type": "tool_output",
                "tool": "command_run",
                "arguments": args,
                "output": command_result
            })
            
            # Feed output back
            loop_history.append({"role": "user", "content": f"System Tool Output (command_run for '{cmd}'):\n{command_result}"})
            current_prompt = f"Analyze the command output of '{cmd}' and determine your next action."
            
        elif action == "browser_browse":
            url = args.get("url", "")
            status_msg = f"Browsing Brave to URL: {url}"
            res = execute_browser_action("browse", url=url)
            _report_status({
                "type": "tool_output",
                "tool": "browser_browse",
                "arguments": args,
                "output": res
            })
            loop_history.append({"role": "user", "content": f"System Tool Output (browser_browse of {url}): {res}"})
            current_prompt = f"Analyze navigation outcome for {url} and determine next action."
 
        elif action == "browser_click":
            sel = args.get("selector_or_text", "")
            status_msg = f"Clicking element matching '{sel}' on Brave page"
            res = execute_browser_action("click", selector_or_text=sel)
            _report_status({
                "type": "tool_output",
                "tool": "browser_click",
                "arguments": args,
                "output": res
            })
            loop_history.append({"role": "user", "content": f"System Tool Output (browser_click on '{sel}'): {res}"})
            current_prompt = f"Analyze click outcome for '{sel}' and determine next action."
 
        elif action == "browser_type":
            sel = args.get("selector_or_text", "")
            val = args.get("text", "")
            status_msg = f"Typing '{val}' into input '{sel}' on Brave page"
            res = execute_browser_action("type", selector_or_text=sel, text=val)
            _report_status({
                "type": "tool_output",
                "tool": "browser_type",
                "arguments": args,
                "output": res
            })
            loop_history.append({"role": "user", "content": f"System Tool Output (browser_type '{val}' into '{sel}'): {res}"})
            current_prompt = f"Analyze input typing outcome for '{sel}' and determine next action."
 
        elif action == "browser_get_elements":
            status_msg = "Extracting visible elements from Brave page"
            res = execute_browser_action("get_elements")
            _report_status({
                "type": "tool_output",
                "tool": "browser_get_elements",
                "arguments": args,
                "output": res
            })
            loop_history.append({"role": "user", "content": f"System Tool Output (browser_get_elements): {res}"})
            current_prompt = "Identify target elements from page structure and determine next action."
 
        elif action == "desktop_input":
            itype = args.get("input_type", "")
            x_coord = args.get("x", 0)
            y_coord = args.get("y", 0)
            text_val = args.get("text", "")
            
            status_msg = f"Simulating OS input: {itype}"
                
            res = ""
            if itype == "move":
                res = move_mouse_smoothly(int(x_coord), int(y_coord))
            elif itype == "click":
                res = click_mouse_at(int(x_coord), int(y_coord))
            elif itype in ("type", "shortcut"):
                res = type_keyboard_text(text_val)
            else:
                res = f"Error: Unknown input type '{itype}'"
                
            _report_status({
                "type": "tool_output",
                "tool": "desktop_input",
                "arguments": args,
                "output": res
            })
            loop_history.append({"role": "user", "content": f"System Tool Output (desktop_input: {itype}): {res}"})
            current_prompt = "Analyze desktop input outcome and determine next action."
 
        elif action == "system_control":
            ctype = args.get("control_type", "")
            lvl = args.get("level", 0)
            param_val = args.get("param", "")
            
            status_msg = f"Adjusting system control: {ctype}"
                
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
                
            _report_status({
                "type": "tool_output",
                "tool": "system_control",
                "arguments": args,
                "output": res
            })
            loop_history.append({"role": "user", "content": f"System Tool Output (system_control: {ctype}): {res}"})
            current_prompt = "Analyze system control adjustment outcome and determine next action."
            
        elif action == "spotify_control":
            spotify_action = args.get("param", "")
            
            # Bulletproof fallback: scan for any non-empty string/int argument that isn't the action param itself
            fallback_val = ""
            for k, v in args.items():
                if k != "param" and isinstance(v, (str, int)) and str(v).strip():
                    fallback_val = str(v).strip()
                    break
                    
            spotify_value = args.get("text", "") or args.get("query", "") or args.get("content", "") or fallback_val
            search_query = args.get("query", "") or args.get("text", "") or args.get("content", "") or fallback_val
            
            if isinstance(spotify_value, int):
                spotify_value = str(spotify_value)
            if isinstance(search_query, int):
                search_query = str(search_query)
                
            logger.info("Executing spotify control with action %s", spotify_action)
            
            # Speak announcement first if present and starting playback
            announcement = args.get("message", "")
            spoke_this_turn = False
            if announcement and spotify_action in ("play", "play_uri", "search_play"):
                try:
                    import time

                    from elora.skills.voice import is_speaking, speak_text
                    
                    # Print announcement so CLI users see it
                    print(f"\nElora: {announcement}\n")
                    _report_status({
                        "type": "thought",
                        "text": f"Speaking: {announcement}"
                    })
                    
                    speak_text(announcement)
                    
                    # Block until speaking is finished to prevent overlap with music
                    while is_speaking():
                        time.sleep(0.1)
                        
                    spoke_this_turn = True
                except Exception as voice_err:
                    logger.error("Failed to speak Spotify announcement: %s", voice_err)
            
            # Execute the action
            res = ""
            if spotify_action == "play_uri":
                res = play_spotify_uri(spotify_value)
            elif spotify_action == "search_play":
                res = search_and_play_spotify(search_query)
            else:
                res = control_spotify(spotify_action, spotify_value)
                
            _report_status({
                "type": "tool_output",
                "tool": "spotify_control",
                "arguments": args,
                "output": res
            })
            
            loop_history.append({"role": "user", "content": f"System Tool Output (spotify_control: {spotify_action}):\n{res}"})
            
            # If playback succeeded, set spoke_already flag so we suppress the duplicate speech at the end of the loop
            if spoke_this_turn and "error" not in res.lower() and "failed" not in res.lower():
                spoke_already = True
                current_prompt = (
                    f"Analyze the Spotify action execution outcome ('{res}'). "
                    "Since the playback announcement has already been spoken to the user before the music started, "
                    "you MUST respond with a reply action containing an empty message (arguments: {'message': ''}) "
                    "so Elora does not speak again. Simply acknowledge silently."
                )
            else:
                current_prompt = (
                    f"Analyze the Spotify action execution outcome ('{res}') and formulate your next response. "
                    "Ensure your reply is highly conversational, clear, flowing, and directly confirms the action or explains the error."
                )
            
            
        elif action == "workspace_query":
            gws_service = args.get("gws_service", "")
            gws_resource = args.get("gws_resource", "")
            gws_method = args.get("gws_method", "list")
            gws_params_str = args.get("gws_params", "{}")
            gws_body_str = args.get("gws_body", "")
            gws_profile = args.get("gws_profile", "default")
            
            logger.info("Executing workspace query [%s]: %s %s %s", gws_profile, gws_service, gws_resource, gws_method)
            
            workspace_result = run_workspace_query(
                service=gws_service,
                resource=gws_resource,
                method=gws_method,
                params_json=gws_params_str,
                body_json=gws_body_str,
                gws_profile=gws_profile
            )
            
            _report_status({
                "type": "tool_output",
                "tool": "workspace_query",
                "arguments": args,
                "output": workspace_result
            })
            
            loop_history.append({"role": "user", "content": f"System Tool Output (workspace_query [{gws_profile}]: {gws_service} {gws_resource} {gws_method}):\n{workspace_result}"})
            current_prompt = (
                f"Analyze the Google Workspace query results for '{gws_service} {gws_resource} {gws_method}' "
                "and formulate your next response or action. "
                "Ensure your reply is highly conversational, clear, flowing, and directly answers what the user asked."
            )

        else:
            logger.warning("Agent encountered unknown action: %s", action)
            # Inject spoke_already flag even in limits/errors if returning result
            if spoke_already and isinstance(result, dict):
                result["spoke_already"] = True
            return result
            
    # Fallback response if loop iteration limit is hit.
    # Instead of throwing a generic error message, query the Gemini brain one last time
    # with the full research history of the loop to generate a helpful conversational summary
    # of what was done and what remains. This ensures the user gets a useful answer and
    # the session history remembers the context so a "continue" command will work.
    try:
        logger.warning("Agent loop reached execution step limit (max_steps=%d). Summarising findings.", max_steps)
        summary_prompt = (
            "You have reached the execution step limit. Please compile a final conversational reply "
            "summarising what you have done and found so far, and explain what was left to do. "
            "Do not start a new tool action; respond with a direct reply to the user."
        )
        loop_history.append({"role": "user", "content": summary_prompt})
        summary_result = query_elora(summary_prompt, history=loop_history)
        if summary_result.get("action") == "reply":
            return summary_result
    except Exception as e:
        logger.error("Failed to generate limit-hit summary: %s", e)

    return {
        "action": "reply",
        "arguments": {
            "message": "I've conducted extensive background research but hit my execution limit before compiling the answer. Please try asking a more focused question."
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
            "arguments": {"message": f"Error storing memory: {e!s}"}
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
            "arguments": {"message": f"Error recalling memory: {e!s}"}
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
            "arguments": {"message": f"Error focusing memory: {e!s}"}
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
            "arguments": {"message": f"Error forgetting memory: {e!s}"}
        }


