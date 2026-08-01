"""
Elora Google Workspace CLI Adapter.
Wraps the `gws` CLI tool to provide Python-friendly access to Google Workspace
services (Classroom, Gmail, Calendar, Drive) without managing OAuth credentials directly.

Why: Replaces fragile hand-rolled OAuth flows with a battle-tested CLI tool that handles
token management, API versioning, and credential rotation automatically.
"""

import json
import logging
import os
import shutil
import subprocess
from typing import Optional, Tuple, List, Dict, Any, Union

logger = logging.getLogger('elora.skills.workspace')

def is_gws_available() -> bool:
    """Checks if gws binary is on PATH."""
    return shutil.which("gws") is not None

def _ensure_gws_profile_creds(profile: str):
    """
    Bootstraps a gws profile directory by copying Elora's existing
    classroom_credentials.json if the profile doesn't have a client_secret.json yet.
    """
    config_dir = os.path.expanduser(f"~/.config/elora/gws-{profile}") if profile and profile != "default" else os.path.expanduser("~/.config/gws")
    os.makedirs(config_dir, exist_ok=True)
    
    secret_path = os.path.join(config_dir, "client_secret.json")
    elora_creds = os.path.expanduser("~/.config/elora/classroom_credentials.json")
    
    if not os.path.exists(secret_path) and os.path.exists(elora_creds):
        try:
            shutil.copy2(elora_creds, secret_path)
            logger.info(f"Auto-populated OAuth credentials for gws profile: {profile}")
        except Exception as e:
            logger.warning(f"Failed to copy credentials for gws profile {profile}: {e}")


def is_gws_authenticated(profile: str = "default") -> bool:
    """Runs `gws auth status` and checks if auth_method is not 'none'."""
    if not is_gws_available():
        return False
        
    _ensure_gws_profile_creds(profile)
    
    cmd = ["gws", "auth", "status", "--format", "json"]
    env = os.environ.copy()
    if profile and profile != "default":
        env["GOOGLE_WORKSPACE_CLI_CONFIG_DIR"] = os.path.expanduser(f"~/.config/elora/gws-{profile}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return data.get("auth_method") != "none"
        return False
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, json.JSONDecodeError) as e:
        logger.warning(f"Failed to check gws auth status: {e}")
        return False

def _run_gws(
    service: str, 
    resource: str, 
    method: str, 
    params: Optional[Dict[str, Any]] = None, 
    body: Optional[Dict[str, Any]] = None, 
    sub_resource: Optional[str] = None, 
    page_all: bool = False,
    profile: str = "default"
) -> Tuple[Union[Dict[str, Any], List[Any], None], Optional[str]]:
    """Core wrapper. Returns (data, error_string). Runs subprocess with timeout=30s."""
    if not is_gws_available():
        return None, "Google Workspace CLI (gws) is not installed. Install it with: npm install -g @anthropic/workspace-cli"
        
    _ensure_gws_profile_creds(profile)
    
    auth_cmd = "gws auth login"
    if profile and profile != "default":
        auth_cmd = f"GOOGLE_WORKSPACE_CLI_CONFIG_DIR=~/.config/elora/gws-{profile} gws auth login"

    if not is_gws_authenticated(profile=profile) and service != "auth":
        return None, f"Google Workspace CLI is not authenticated. Run '{auth_cmd}' in your terminal to authenticate, or run 'elora --setup' to configure workspace credentials."

    # Build the command path: gws <service> <resource> [sub_resource...] <method>
    # The gws CLI uses space-separated tokens for nested resources
    # e.g. "gws classroom courses courseWork list" not "gws classroom courses.courseWork list"
    cmd = ["gws", service]
    # Split resource on spaces (e.g. "courses courseWork" -> ["courses", "courseWork"])
    cmd.extend(resource.split())
    if sub_resource:
        # sub_resource can also contain spaces or dots — normalize to space-separated tokens
        cmd.extend(sub_resource.replace(".", " ").split())
    cmd.append(method)
    cmd.extend(["--format", "json"])

    if params:
        cmd.extend(["--params", json.dumps(params)])
    
    if body:
        cmd.extend(["--json", json.dumps(body)])
        
    if page_all:
        cmd.append("--page-all")

    env = os.environ.copy()
    if profile and profile != "default":
        env["GOOGLE_WORKSPACE_CLI_CONFIG_DIR"] = os.path.expanduser(f"~/.config/elora/gws-{profile}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)
        
        if result.returncode == 2:
            return None, f"Google Workspace CLI is not authenticated. Run '{auth_cmd}' in your terminal to authenticate, or run 'elora --setup' to configure workspace credentials."
        
        if result.returncode != 0:
            return None, result.stderr.strip()
            
        if not result.stdout.strip():
            return None, None

        if page_all:
            # Parse NDJSON
            all_data = []
            for line in result.stdout.strip().splitlines():
                if line.strip():
                    try:
                        all_data.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            
            # Since page_all results are usually lists wrapped in some pagination object or just arrays
            # We will merge list values if they exist, otherwise just return the list of responses
            merged = []
            for item in all_data:
                if isinstance(item, dict):
                    # Find the first list value and extend merged with it
                    for val in item.values():
                        if isinstance(val, list):
                            merged.extend(val)
                            break
                    else:
                        merged.append(item)
                elif isinstance(item, list):
                    merged.extend(item)
                else:
                    merged.append(item)
            return merged, None
        else:
            try:
                return json.loads(result.stdout), None
            except json.JSONDecodeError:
                return None, f"Failed to parse JSON output: {result.stdout}"
                
    except subprocess.TimeoutExpired:
        return None, "Command timed out after 30 seconds"
    except Exception as e:
        return None, str(e)

def list_active_courses() -> Tuple[Optional[List[Any]], Optional[str]]:
    """Lists active courses (studentId=me)."""
    return _run_gws("classroom", "courses", "list", params={"studentId": "me", "courseStates": "ACTIVE"}, page_all=True)

def list_coursework(course_id: str) -> Tuple[Optional[List[Any]], Optional[str]]:
    """Lists coursework for a course."""
    return _run_gws("classroom", "courses", "list", sub_resource="courseWork", params={"courseId": course_id}, page_all=True)

def list_student_submissions(course_id: str, coursework_id: str = "-") -> Tuple[Optional[List[Any]], Optional[str]]:
    """Lists student submissions."""
    return _run_gws(
        "classroom", "courses", "list", 
        sub_resource="courseWork.studentSubmissions", 
        params={"courseId": course_id, "courseWorkId": coursework_id}, 
        page_all=True
    )

def get_coursework(course_id: str, coursework_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Gets specific coursework."""
    return _run_gws(
        "classroom", "courses", "get", 
        sub_resource="courseWork", 
        params={"courseId": course_id, "id": coursework_id}
    )

def export_drive_file(file_id: str, output_path: str, mime_type: str = "text/plain", profile: str = "default") -> Tuple[Optional[str], Optional[str]]:
    """Exports/downloads a Drive file."""
    if not is_gws_available():
        return None, "Google Workspace CLI (gws) is not installed. Install it with: npm install -g @anthropic/workspace-cli"
        
    _ensure_gws_profile_creds(profile)
    
    auth_cmd = "gws auth login"
    if profile and profile != "default":
        auth_cmd = f"GOOGLE_WORKSPACE_CLI_CONFIG_DIR=~/.config/elora/gws-{profile} gws auth login"

    if not is_gws_authenticated(profile=profile):
        return None, f"Google Workspace CLI is not authenticated. Run '{auth_cmd}' in your terminal to authenticate, or run 'elora --setup' to configure workspace credentials."

    cmd = ["gws", "drive", "files", "export", "--params", json.dumps({"fileId": file_id, "mimeType": mime_type})]
    env = os.environ.copy()
    if profile and profile != "default":
        env["GOOGLE_WORKSPACE_CLI_CONFIG_DIR"] = os.path.expanduser(f"~/.config/elora/gws-{profile}")
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30, env=env)
        
        if result.returncode == 2:
            return None, f"Google Workspace CLI is not authenticated. Run '{auth_cmd}' in your terminal to authenticate, or run 'elora --setup' to configure workspace credentials."
            
        if result.returncode != 0:
            return None, result.stderr.decode('utf-8', errors='replace').strip()
            
        with open(output_path, "wb") as f:
            f.write(result.stdout)
            
        return output_path, None
    except subprocess.TimeoutExpired:
        return None, "Command timed out after 30 seconds"
    except Exception as e:
        return None, str(e)

def get_drive_file_metadata(file_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Gets Drive file metadata."""
    return _run_gws("drive", "files", "get", params={"fileId": file_id, "fields": "*"})

def list_gmail_messages(query: str = "is:unread", max_results: int = 10) -> Tuple[Optional[List[Any]], Optional[str]]:
    """Lists Gmail messages."""
    return _run_gws("gmail", "users", "list", sub_resource="messages", params={"userId": "me", "q": query, "maxResults": max_results})

def get_gmail_message(message_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Gets a single Gmail message with full content."""
    return _run_gws("gmail", "users", "get", sub_resource="messages", params={"userId": "me", "id": message_id, "format": "full"})

def insert_calendar_event(event_body: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Creates a Calendar event."""
    return _run_gws("calendar", "events", "insert", params={"calendarId": "primary"}, body=event_body)

def get_calendar_event(event_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Gets a Calendar event."""
    return _run_gws("calendar", "events", "get", params={"calendarId": "primary", "eventId": event_id})

def update_calendar_event(event_id: str, event_body: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Updates a Calendar event."""
    return _run_gws("calendar", "events", "update", params={"calendarId": "primary", "eventId": event_id}, body=event_body)


def run_workspace_query(
    service: str,
    resource: str,
    method: str = "list",
    params_json: str = "{}",
    body_json: str = "",
    gws_profile: str = "default"
) -> str:
    """
    Generic entry point for the agent's workspace_query action.
    Accepts raw JSON strings for params/body (as the LLM produces them),
    executes the gws command, and returns a human-readable result string.

    Why: Lets the Gemini brain query any Google Workspace service (Gmail,
    Calendar, Drive, Sheets, Tasks, etc.) without needing a dedicated skill
    function for each endpoint.
    """
    if not is_gws_available():
        return "Error: Google Workspace CLI (gws) is not installed. Install it with your package manager."

    if not is_gws_authenticated(profile=gws_profile):
        auth_cmd = "gws auth login"
        if gws_profile and gws_profile != "default":
            auth_cmd = f"GOOGLE_WORKSPACE_CLI_CONFIG_DIR=~/.config/elora/gws-{gws_profile} gws auth login"
        return (f"Error: Google Workspace CLI is not authenticated. "
                f"Run '{auth_cmd}' in your terminal to authenticate.")

    # Parse params and body from JSON strings
    params = None
    if params_json and params_json.strip() not in ("", "{}"):
        try:
            params = json.loads(params_json)
        except json.JSONDecodeError as e:
            return f"Error: Invalid gws_params JSON: {e}"

    body = None
    if body_json and body_json.strip() not in ("", "{}"):
        try:
            body = json.loads(body_json)
        except json.JSONDecodeError as e:
            return f"Error: Invalid gws_body JSON: {e}"

    data, err = _run_gws(service, resource, method, params=params, body=body, profile=gws_profile)

    if err:
        return f"Error from Google Workspace: {err}"

    if data is None:
        return "No results returned from Google Workspace."

    # Format result as readable JSON (truncated to avoid flooding LLM context)
    try:
        formatted = json.dumps(data, indent=2, default=str)
        if len(formatted) > 8000:
            formatted = formatted[:8000] + "\n... (truncated, result too large)"
        return formatted
    except (TypeError, ValueError):
        return str(data)
