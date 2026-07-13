"""
Elora Local Email Reporting Skill.
Provides IMAP connection capabilities to retrieve unread or recent emails.

Why: Keeps the desktop assistant lightweight and fast by leveraging Python's built-in imaplib/email.
"""

import os
import logging
import email
import email.message
from email.header import decode_header
import imaplib
import socket
from typing import Dict, Any, List

logger = logging.getLogger("elora.skills.email")


def load_env_credential(name: str) -> str:
    """
    Manually parses the ~/.env file to load a credential if not present in the environment.
    
    Why: Bypasses the need for external python-dotenv dependency while conforming to
    the Safe Credentials Protocol by checking ~/.env.
    """
    val = os.environ.get(name)
    if val:
        return val
        
    env_path = os.path.expanduser("~/.env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        if k.strip() == name:
                            # Strip any surrounding quotes
                            val_cleaned = v.strip()
                            if (val_cleaned.startswith('"') and val_cleaned.endswith('"')) or \
                               (val_cleaned.startswith("'") and val_cleaned.endswith("'")):
                                val_cleaned = val_cleaned[1:-1]
                            return val_cleaned
        except Exception as e:
            logger.warning("Failed to parse ~/.env: %s", e)
    return ""


def decode_mime_header(header_value: str) -> str:
    """
    Decodes MIME-encoded email headers (like Subject or From) to plain unicode text.
    
    Why: Email headers frequently contain special encodings (e.g. UTF-8 base64) that
    appear as raw garbage text unless decoded.
    """
    if not header_value:
        return ""
    try:
        decoded_parts = decode_header(header_value)
        header_text = []
        for part, encoding in decoded_parts:
            if isinstance(part, bytes):
                charset = encoding or "utf-8"
                try:
                    header_text.append(part.decode(charset, errors="replace"))
                except Exception:
                    header_text.append(part.decode("utf-8", errors="replace"))
            else:
                header_text.append(str(part))
        return "".join(header_text).strip()
    except Exception as e:
        logger.warning("Failed to decode header: %s", e)
        return str(header_value)


def extract_email_body(msg: email.message.Message, max_chars: int = 500) -> str:
    """
    Traverses message parts to locate plain text body content and truncates it.
    
    Why: Prevents inflating context sizes of the LLM by sending entire email contents
    including HTML formatting and signature footers.
    """
    body = ""
    try:
        if msg.is_multipart():
            # Walk the multipart parts
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disp = str(part.get("Content-Disposition"))
                if content_type == "text/plain" and "attachment" not in content_disp:
                    try:
                        payload = part.get_payload(decode=True)
                        charset = part.get_content_charset() or "utf-8"
                        body = payload.decode(charset, errors="replace")
                        break
                    except Exception:
                        pass
            if not body:
                # Fallback to HTML if no plain text is found
                for part in msg.walk():
                    content_type = part.get_content_type()
                    if content_type == "text/html":
                        try:
                            payload = part.get_payload(decode=True)
                            charset = part.get_content_charset() or "utf-8"
                            html_content = payload.decode(charset, errors="replace")
                            # Basic HTML tag strip (BeautifulSoup is already configured in dependencies)
                            from bs4 import BeautifulSoup
                            soup = BeautifulSoup(html_content, "html.parser")
                            body = soup.get_text(separator=" ")
                            break
                        except Exception:
                            pass
        else:
            payload = msg.get_payload(decode=True)
            charset = msg.get_content_charset() or "utf-8"
            body = payload.decode(charset, errors="replace")
    except Exception as e:
        logger.warning("Failed to extract body content: %s", e)
        body = ""

    # Normalize whitespace and truncate
    body_cleaned = " ".join(body.split())
    if len(body_cleaned) > max_chars:
        body_cleaned = body_cleaned[:max_chars] + "..."
    return body_cleaned or "[No readable text content]"


def fetch_recent_emails() -> str:
    """
    Connects to the configured IMAP server, authenticates, searches for
    recent/unread emails, extracts metadata + body snippets, and returns
    a formatted markdown list.
    
    Why: Provides the Gemini agent with real-time email details to prioritize,
    summarize, and report back to the user.
    """
    from elora.core.config import load_config
    config = load_config()
    email_cfg = config.get("email", {})
    
    if not email_cfg.get("enabled", False):
        return "Error: Email reporting is disabled in the configuration. Use dynamic updates to enable it."
        
    imap_server = email_cfg.get("imap_server", "imap.gmail.com")
    imap_port = email_cfg.get("imap_port", 993)
    email_address = email_cfg.get("email_address", "")
    password_env = email_cfg.get("password_env_var", "ELORA_EMAIL_PASSWORD")
    max_emails = email_cfg.get("max_emails_to_check", 10)
    
    if not email_address:
        return "Error: Email address is not configured. Please add 'email_address' under 'email' in config.json."
        
    password = load_env_credential(password_env) or load_env_credential("ELORA_EMAIL_PASSWORD")
    if not password:
        return f"Error: Email password not found. Please set it in ~/.env as {password_env}=your_password or as an environment variable."
        
    mail = None
    try:
        # Establish connection with a 10s default socket timeout to prevent hangs
        socket.setdefaulttimeout(10.0)
        mail = imaplib.IMAP4_SSL(imap_server, imap_port)
        mail.login(email_address, password)
        
        # Select Inbox
        status, _ = mail.select("INBOX", readonly=True)
        if status != "OK":
            return "Error: Failed to select INBOX."
            
        # Search unread (UNSEEN) emails first
        status, data = mail.search(None, "UNSEEN")
        is_unread_only = True
        
        if status != "OK" or not data or not data[0]:
            # No unread emails, check all recent ones instead
            logger.info("No unread emails found. Fetching recent emails instead.")
            status, data = mail.search(None, "ALL")
            is_unread_only = False
            
        if status != "OK" or not data or not data[0]:
            return "No emails found in the inbox."
            
        msg_ids = data[0].split()
        # Retrieve the latest max_emails (descending order)
        msg_ids = msg_ids[-max_emails:]
        msg_ids.reverse()  # Show newest first
        
        results = []
        for i, msg_id in enumerate(msg_ids, 1):
            status, msg_data = mail.fetch(msg_id, "(RFC822)")
            if status != "OK" or not msg_data:
                continue
                
            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)
            
            # Decode headers
            subject = decode_mime_header(msg.get("Subject", "No Subject"))
            sender = decode_mime_header(msg.get("From", "Unknown Sender"))
            date_str = decode_mime_header(msg.get("Date", "Unknown Date"))
            
            # Extract content snippet
            body_snippet = extract_email_body(msg)
            
            # Read status flag
            read_status = "Unread" if is_unread_only else "Read"
            
            results.append(
                f"### Email {i}\n"
                f"**From**: {sender}\n"
                f"**Subject**: {subject}\n"
                f"**Date**: {date_str}\n"
                f"**Status**: {read_status}\n"
                f"**Snippet**: {body_snippet}\n"
                f"---"
            )
            
        header_msg = "## Unread Emails found:\n" if is_unread_only else "## Recent Emails (no unread found):\n"
        return header_msg + "\n".join(results)
        
    except socket.timeout:
        return "Error: Connection to IMAP server timed out."
    except imaplib.IMAP4.error as e:
        return f"Error: IMAP authentication or protocol error: {e}"
    except Exception as e:
        logger.error("Failed to fetch emails: %s", e)
        return f"Error: Failed to fetch emails: {e}"
    finally:
        if mail:
            try:
                mail.logout()
            except Exception:
                pass
