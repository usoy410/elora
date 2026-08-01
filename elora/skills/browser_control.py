"""
Elora Brave Browser Control Engine.
Integrates Playwright CDP (Chrome DevTools Protocol) to automate Brave.
"""

import logging
import os
import socket
import subprocess
import time

from playwright.sync_api import Page, sync_playwright

logger = logging.getLogger("elora.browser")

CDP_URL = "http://127.0.0.1:9222"
SCREENSHOT_PATH = "/tmp/elora_browser.png"


def is_brave_debugging_active() -> bool:
    """Checks if the remote debugging port is open."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", 9222))
        s.close()
        return True
    except Exception:
        return False


def launch_brave_with_debugging() -> bool:
    """Launches Brave browser with the remote debugging port enabled."""
    if is_brave_debugging_active():
        return True

    # Search common Brave installation paths
    brave_paths = ["/usr/bin/brave", "/usr/bin/brave-browser", "brave"]
    brave_path = None
    for path in brave_paths:
        if os.path.exists(path) or subprocess.run(["which", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            brave_path = path
            break

    if not brave_path:
        logger.error("Brave browser executable not found.")
        return False

    logger.info("Launching Brave browser with remote debugging...")
    try:
        # Use a separate user data directory so it doesn't conflict with existing running Brave instances
        user_data_dir = os.path.expanduser("~/.config/elora/brave_profile")
        os.makedirs(user_data_dir, exist_ok=True)

        # Launch Brave as a completely detached background process group
        subprocess.Popen(
            [brave_path, "--remote-debugging-port=9222", "--no-first-run", f"--user-data-dir={user_data_dir}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setpgrp
        )


        # Wait up to 6 seconds for port to open
        for _ in range(30):
            if is_brave_debugging_active():
                logger.info("Brave debugging port open.")
                time.sleep(1.0)  # Let it fully initialize
                return True
            time.sleep(0.2)

        return False
    except Exception as e:
        logger.error("Failed to launch Brave: %s", e)
        return False


def execute_browser_action(action_name: str, **kwargs) -> str:
    """
    Connects to Brave via CDP, performs a single action on the active page,
    captures a page screenshot, and disconnects (leaving the browser open).
    
    Why: Keeps connection stateless to prevent lockouts, guarantees a fresh
    screenshot update after every interaction, and avoids closing the browser window.
    """
    if not launch_brave_with_debugging():
        return "Error: Brave browser is not running and could not be launched with remote debugging enabled."

    try:
        with sync_playwright() as p:
            logger.info("Connecting to Brave via CDP: %s", CDP_URL)
            browser = p.chromium.connect_over_cdp(CDP_URL)
            
            # Find or create a page
            contexts = browser.contexts
            if not contexts:
                context = browser.new_context()
            else:
                context = contexts[0]

            pages = context.pages
            if not pages:
                page = context.new_page()
            else:
                # Target the last opened or active page
                page = pages[-1]

            # Bring page to front
            try:
                page.bring_to_front()
            except Exception:
                pass

            result = ""
            if action_name == "browse":
                url = kwargs.get("url", "")
                if not url.startswith("http://") and not url.startswith("https://"):
                    url = "https://" + url
                logger.info("Browsing to URL: %s", url)
                page.goto(url, wait_until="load", timeout=20000)
                result = f"Successfully navigated to {url}"

            elif action_name == "click":
                selector = kwargs.get("selector_or_text", "")
                result = _perform_click(page, selector)

            elif action_name == "type":
                selector = kwargs.get("selector_or_text", "")
                text = kwargs.get("text", "")
                result = _perform_type(page, selector, text)

            elif action_name == "get_elements":
                result = _get_interactive_elements(page)

            # Take a fresh screenshot after any action (except just extracting elements)
            if action_name != "get_elements":
                try:
                    page.screenshot(path=SCREENSHOT_PATH)
                    logger.info("Saved browser screenshot to %s", SCREENSHOT_PATH)
                except Exception as e:
                    logger.warning("Failed to take page screenshot: %s", e)

            # Do not call browser.close() here as it terminates the remote browser process.
            # The connection will close when the sync_playwright context manager exits.
            return result
    except Exception as e:
        logger.error("Browser action '%s' failed: %s", action_name, e)
        return f"Error executing browser action '{action_name}': {e!s}"


def _perform_click(page: Page, selector: str) -> str:
    """Helper to click elements by CSS, XPath, or raw text content."""
    # Attempt 1: Direct CSS selector
    try:
        if page.locator(selector).count() > 0:
            page.locator(selector).first.click(timeout=3000)
            return f"Clicked element matching selector '{selector}'"
    except Exception:
        pass

    # Attempt 2: Text matching (case insensitive) for buttons, links, etc.
    text_selectors = [
        f"text={selector}",
        f"button:has-text('{selector}')",
        f"a:has-text('{selector}')",
        f"[role='button']:has-text('{selector}')",
        f"span:has-text('{selector}')"
    ]
    for sel in text_selectors:
        try:
            loc = page.locator(sel)
            if loc.count() > 0:
                loc.first.click(timeout=2000)
                return f"Clicked element via matching text query: '{sel}'"
        except Exception:
            continue

    # Attempt 3: Scroll to view and click elements matching role
    try:
        # Fallback to fuzzy text search on all elements
        all_elements = page.query_selector_all("a, button, input, [role='button']")
        for el in all_elements:
            inner_text = el.inner_text().strip().lower()
            if selector.lower() in inner_text:
                el.scroll_into_view_if_needed()
                el.click()
                return f"Clicked element with text content matching '{selector}'"
    except Exception:
        pass

    return f"Failed to find or click any element matching '{selector}'"


def _perform_type(page: Page, selector: str, text: str) -> str:
    """Helper to find input fields and fill them with text, then press Enter."""
    # Attempt 1: Direct CSS selector matching input elements
    input_selectors = [
        selector,
        f"input[placeholder*='{selector}' i]",
        f"textarea[placeholder*='{selector}' i]",
        f"[aria-label*='{selector}' i]",
        f"input[name='{selector}']",
        "input[type='text']"
    ]
    
    for sel in input_selectors:
        try:
            loc = page.locator(sel)
            if loc.count() > 0:
                loc.first.fill(text, timeout=2000)
                # Press Enter key in case it's a search box
                loc.first.press("Enter")
                return f"Typed '{text}' into input matching '{sel}' and pressed Enter."
        except Exception:
            continue

    # Attempt 2: Search for label text associated with inputs
    try:
        labels = page.query_selector_all("label")
        for label in labels:
            label_text = label.inner_text().strip().lower()
            if selector.lower() in label_text:
                for_id = label.get_attribute("for")
                if for_id:
                    page.fill(f"#{for_id}", text, timeout=2000)
                    page.press(f"#{for_id}", "Enter")
                    return f"Filled input linked to label '{label_text}' with '{text}'."
    except Exception:
        pass

    return f"Failed to find or type in input field matching '{selector}'"


def _get_interactive_elements(page: Page) -> str:
    """Scrapes visible elements and formats them into a neat list to feed the LLM."""
    try:
        # Wait for network idle to ensure elements are rendered
        page.wait_for_load_state("networkidle", timeout=3000)
    except Exception:
        pass

    try:
        # Find visible buttons, inputs, textareas, and links
        elements = page.query_selector_all("a, button, input:not([type='hidden']), textarea, [role='button']")
        seen = set()
        formatted_list = []

        for idx, el in enumerate(elements):
            if not el.is_visible():
                continue
                
            tag = el.evaluate("el => el.tagName.toLowerCase()")
            text = el.inner_text().strip().replace("\n", " ")
            placeholder = el.get_attribute("placeholder") or ""
            role = el.get_attribute("role") or ""
            href = el.get_attribute("href") or ""
            name = el.get_attribute("name") or ""
            aria = el.get_attribute("aria-label") or ""

            # De-duplicate elements
            descriptor = f"{tag}|{text}|{placeholder}|{name}|{aria}"
            if descriptor in seen:
                continue
            seen.add(descriptor)

            # Format descriptive name
            label = text or placeholder or aria or name or href
            if not label:
                continue

            # Keep items clean and truncated
            if len(label) > 60:
                label = label[:60] + "..."

            element_type = tag
            if tag == "input":
                element_type = f"input[{el.get_attribute('type') or 'text'}]"
            elif role:
                element_type = f"{tag}[role={role}]"

            formatted_list.append(f"[{idx}] {element_type.upper()}: '{label}'")

        if not formatted_list:
            return "No visible interactive elements detected on the page."

        return "Visible Interactive Elements:\n" + "\n".join(formatted_list[:35])  # Cap to prevent context explosion
    except Exception as e:
        return f"Failed to scrape page elements: {e}"
