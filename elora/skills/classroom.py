"""
Elora Google Classroom Integration Skill.
Handles authentication, coursework/submission fetching, and Google Drive attachment parsing.
"""

import os
import json
import logging
import datetime
from typing import Dict, Any, List, Optional
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

logger = logging.getLogger("elora.skills.classroom")

# Standard XDG configuration directory
CONFIG_DIR = os.path.expanduser("~/.config/elora")
CREDENTIALS_PATH = os.path.join(CONFIG_DIR, "classroom_credentials.json")
TOKEN_PATH = os.path.join(CONFIG_DIR, "classroom_token.json")

# Required scopes for Google Classroom and Google Drive access
SCOPES = [
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.students.readonly",
    "https://www.googleapis.com/auth/classroom.student-submissions.me.readonly",
    "https://www.googleapis.com/auth/drive.readonly"
]


def get_classroom_credentials() -> Optional[Credentials]:
    """
    Retrieves OAuth 2.0 credentials from classroom_token.json or starts authentication
    flow using classroom_credentials.json if token is missing/expired.
    """
    os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'
    creds = None
    if os.path.exists(TOKEN_PATH):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
        except Exception as e:
            logger.warning("Failed to load existing classroom token: %s", e)

    # If credentials don't exist or are invalid
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                # Save refreshed credentials
                with open(TOKEN_PATH, "w") as token_file:
                    token_file.write(creds.to_json())
                return creds
            except Exception as e:
                logger.warning("Failed to refresh classroom credentials: %s", e)
                creds = None

        # Run complete OAuth flow
        if not os.path.exists(CREDENTIALS_PATH):
            logger.error("OAuth client credentials not found at %s", CREDENTIALS_PATH)
            return None

        try:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            # Start local server on a random available port to receive authorization code
            creds = flow.run_local_server(port=0, prompt="consent")
            # Save credentials for future use
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(TOKEN_PATH, "w") as token_file:
                token_file.write(creds.to_json())
        except Exception as e:
            logger.error("OAuth flow failed: %s", e)
            return None

    return creds


def get_service(service_name: str, version: str):
    """
    Builds and returns a Google API service object.
    """
    creds = get_classroom_credentials()
    if not creds:
        return None
    return build(service_name, version, credentials=creds)


def parse_classroom_date(due_date_dict: Dict[str, int], due_time_dict: Optional[Dict[str, int]] = None) -> Optional[datetime.datetime]:
    """
    Converts classroom API due date/time dictionaries into a datetime object.
    """
    if not due_date_dict:
        return None
    
    year = due_date_dict.get("year")
    month = due_date_dict.get("month")
    day = due_date_dict.get("day")
    
    if not all([year, month, day]):
        return None
        
    hour = 23
    minute = 59
    if due_time_dict:
        hour = due_time_dict.get("hours", hour)
        minute = due_time_dict.get("minutes", minute)
        
    try:
        return datetime.datetime(year, month, day, hour, minute)
    except ValueError:
        return None


def fetch_classroom_data(mode: str = "list_pending", coursework_id: Optional[str] = None, course_id: Optional[str] = None) -> str:
    """
    Main entry point for retrieving Google Classroom assignment details.
    
    Modes:
      - 'list_pending': Retrieves all active coursework across courses that are not turned in.
      - 'due_soon': Retrieves pending coursework due in the next 7 days.
      - 'analyze_materials': Downloads/reads files attached to coursework_id in course_id.
    """
    if not os.path.exists(CREDENTIALS_PATH) and not os.path.exists(TOKEN_PATH):
        return (
            "Error: Google Classroom credentials are missing. Please complete the following steps:\n"
            "1. Go to Google Cloud Console (https://console.cloud.google.com/).\n"
            "2. Create a desktop application credential, and download the JSON file.\n"
            f"3. Save the file exactly at: {CREDENTIALS_PATH}\n"
            "4. Ask me again to authenticate."
        )

    # 1. Initialize Classroom Service
    classroom = get_service("classroom", "v1")
    if not classroom:
        return "Error: Failed to authenticate with Google Classroom API. Please check your classroom_credentials.json."

    try:
        # Fetch active courses
        courses_res = classroom.courses().list(studentId="me", courseStates="ACTIVE").execute()
        courses = courses_res.get("courses", [])
        if not courses:
            return "You are not enrolled in any active Google Classroom courses."

        now = datetime.datetime.now()

        if mode in ("list_pending", "due_soon"):
            pending_list = []
            
            for course in courses:
                cid = course["id"]
                cname = course["name"]
                
                # Fetch coursework for the course
                work_res = classroom.courses().courseWork().list(courseId=cid).execute()
                coursework_list = work_res.get("courseWork", [])
                if not coursework_list:
                    continue
                
                # Fetch student submissions for the course (wildcard courseWorkId fetches all)
                sub_res = classroom.courses().courseWork().studentSubmissions().list(
                    courseId=cid, courseWorkId="-", userId="me"
                ).execute()
                submissions = {sub["courseWorkId"]: sub for sub in sub_res.get("studentSubmissions", [])}
                
                for work in coursework_list:
                    wid = work["id"]
                    title = work["title"]
                    desc = work.get("description", "")
                    
                    # Match with student submission
                    sub = submissions.get(wid)
                    sub_state = sub.get("state", "ASSIGNED") if sub else "ASSIGNED"
                    
                    # We are only interested in pending tasks (not turned in or returned)
                    if sub_state in ("TURNED_IN", "RETURNED"):
                        continue
                        
                    due_date_raw = work.get("dueDate")
                    due_time_raw = work.get("dueTime")
                    due_dt = parse_classroom_date(due_date_raw, due_time_raw)
                    
                    # Filter for 'due_soon' mode (within next 7 days)
                    if mode == "due_soon":
                        if not due_dt:
                            continue
                        delta = due_dt - now
                        if not (datetime.timedelta(days=0) <= delta <= datetime.timedelta(days=7)):
                            continue
                    
                    due_str = due_dt.strftime("%Y-%m-%d %H:%M") if due_dt else "No due date"
                    pending_list.append({
                        "course_name": cname,
                        "course_id": cid,
                        "id": wid,
                        "title": title,
                        "description": desc,
                        "due_date": due_str,
                        "state": sub_state
                    })
            
            if not pending_list:
                if mode == "due_soon":
                    return "Great news! You have no assignments due in the next 7 days."
                return "Excellent work! You have no pending or missing assignments in Google Classroom."

            # Format the output
            lines = [f"## Google Classroom Assignments ({'Due Soon' if mode == 'due_soon' else 'Pending'}):"]
            for i, p in enumerate(pending_list, 1):
                desc_snippet = p["description"][:120].replace("\n", " ") + "..." if p["description"] else "No instructions provided."
                lines.append(
                    f"### {i}. {p['title']}\n"
                    f"- **Class**: {p['course_name']}\n"
                    f"- **Due Date**: {p['due_date']}\n"
                    f"- **Status**: {p['state']}\n"
                    f"- **Description**: {desc_snippet}\n"
                    f"- **IDs**: Course ID `{p['course_id']}`, Assignment ID `{p['id']}`\n"
                    f"---"
                )
            return "\n".join(lines)

        elif mode == "analyze_materials":
            if not course_id or not coursework_id:
                return "Error: Missing course_id or coursework_id for analyzing materials."
                
            # Fetch specific coursework details
            work = classroom.courses().courseWork().get(courseId=course_id, id=coursework_id).execute()
            title = work["title"]
            desc = work.get("description", "No description provided.")
            materials = work.get("materials", [])
            
            drive = get_service("drive", "v3")
            doc_contents = []
            
            for mat in materials:
                # Resolve attached Drive Files
                drive_file = mat.get("driveFile")
                if drive_file:
                    fid = drive_file["driveFile"]["id"]
                    fname = drive_file["driveFile"]["title"]
                    
                    if not drive:
                        doc_contents.append(f"*(Drive File attached: '{fname}' - Could not access Drive API)*")
                        continue
                        
                    try:
                        # Fetch file metadata to determine MIME type
                        file_meta = drive.files().get(fileId=fid, fields="name, mimeType").execute()
                        mimetype = file_meta.get("mimeType", "")
                        
                        if mimetype == "application/vnd.google-apps.document":
                            # Export Google Doc as plain text
                            text_bytes = drive.files().export(fileId=fid, mimeType="text/plain").execute()
                            text_content = text_bytes.decode("utf-8", errors="replace")
                            doc_contents.append(f"### Attached Document: {fname}\n{text_content}")
                        elif mimetype.startswith("text/"):
                            # Download plain text files directly
                            text_bytes = drive.files().get_media(fileId=fid).execute()
                            text_content = text_bytes.decode("utf-8", errors="replace")
                            doc_contents.append(f"### Attached File: {fname}\n{text_content}")
                        else:
                            doc_contents.append(f"*(Attached File: '{fname}' of type {mimetype} is currently not exportable as plain text)*")
                    except Exception as e:
                        logger.warning("Failed to fetch attachment %s: %s", fname, e)
                        doc_contents.append(f"*(Failed to fetch attachment '{fname}' due to error: {e})*")
            
            output = [
                f"# Assignment: {title}",
                f"## Classroom Instructions:\n{desc}",
            ]
            if doc_contents:
                output.append("## Attachment Contents:\n" + "\n\n".join(doc_contents))
            else:
                output.append("*(No text-readable attachments found)*")
                
            return "\n".join(output)
            
        else:
            return f"Error: Unknown classroom mode '{mode}'"

    except Exception as e:
        logger.error("Failed to fetch Classroom data: %s", e)
        return f"Error connecting to Google Classroom: {str(e)}"
