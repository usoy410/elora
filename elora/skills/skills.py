"""
Elora Local Skills and Web Browsing Engines.
Provides DuckDuckGo search, BeautifulSoup text scraping, and command execution.
"""

import logging
import os
import subprocess
import requests
import re
from bs4 import BeautifulSoup

logger = logging.getLogger("elora.skills")


def search_duckduckgo(query: str) -> str:
    """
    Queries DuckDuckGo HTML search and returns the top 5 snippets.
    
    Why: Bypasses paid/rate-limited API services, keeping search free and local.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:119.0) Gecko/20100101 Firefox/119.0"
    }
    url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}"
    
    try:
        logger.info("Executing DuckDuckGo search for: '%s'", query)
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return f"DuckDuckGo search failed with HTTP status code {r.status_code}."
            
        soup = BeautifulSoup(r.text, "html.parser")
        results = []
        
        # Parse result cards in DuckDuckGo HTML layout
        for a in soup.find_all("a", class_="result__snippet")[:5]:
            parent = a.parent.parent
            title_a = parent.find("a", class_="result__url")
            title = title_a.text.strip() if title_a else "No Title"
            link = title_a["href"] if title_a and "href" in title_a.attrs else ""
            snippet = a.text.strip()
            
            # Clean up redundant redirection links in DDG urls if present
            if link.startswith("//duckduckgo.com/l/?kh=-1&uddg="):
                # Extract actual target link from parameter
                from urllib.parse import urlparse, parse_qs
                parsed = urlparse(link)
                params = parse_qs(parsed.query)
                if "uddg" in params:
                    link = params["uddg"][0]
            
            results.append(f"Title: {title}\nLink: {link}\nSnippet: {snippet}\n---")
            
        if not results:
            return "No search results returned from DuckDuckGo."
            
        return "\n".join(results)
    except Exception as e:
        logger.error("DuckDuckGo search failed: %s", e)
        return f"Error executing DuckDuckGo search: {str(e)}"


def scrape_webpage(url: str) -> str:
    """
    Fetches raw HTML from a URL, strip scripts/styling, and extracts readable text.
    Truncates response output to the first 1200 words.
    
    Why: Prevents LLM context window crashes while keeping documentation crawls light.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:119.0) Gecko/20100101 Firefox/119.0"
    }
    
    try:
        logger.info("Scraping webpage content from URL: %s", url)
        r = requests.get(url, headers=headers, timeout=12)
        if r.status_code != 200:
            return f"Failed to fetch webpage text, HTTP status code: {r.status_code}."
            
        soup = BeautifulSoup(r.text, "html.parser")
        
        # Strip script, stylesheet, and metadata tags
        for junk in soup(["script", "style", "noscript", "iframe", "header", "footer", "nav"]):
            junk.decompose()
            
        # Get formatted plain text
        raw_text = soup.get_text(separator="\n")
        lines = (line.strip() for line in raw_text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        cleaned_text = "\n".join(chunk for chunk in chunks if chunk)
        
        # Word limits to protect context buffers
        words = cleaned_text.split()
        if len(words) > 1200:
            return " ".join(words[:1200]) + "\n\n... [Webpage Text Truncated to 1200 words] ..."
            
        return cleaned_text
    except Exception as e:
        logger.error("Web scraping failed: %s", e)
        return f"Error scraping page text: {str(e)}"


def adapt_package_manager_commands(command: str) -> str:
    """
    Detects if a lockfile exists in the current directory and adapts npm/yarn/pnpm commands to match.
    
    Why: Prevents creating duplicate lockfiles and ensures package installation consistency.
    """
    if os.path.exists("pnpm-lock.yaml"):
        pref = "pnpm"
    elif os.path.exists("yarn.lock"):
        pref = "yarn"
    elif os.path.exists("bun.lockb"):
        pref = "bun"
    else:
        return command
        
    # Replace commands globally in the string to support chained commands
    if pref == "pnpm":
        command = re.sub(r'\bnpm\s+(install|i)\b', 'pnpm install', command)
        command = re.sub(r'\bnpm\s+run\b', 'pnpm run', command)
        command = re.sub(r'\bnpx\b', 'pnpm dlx', command)
    elif pref == "yarn":
        command = re.sub(r'\bnpm\s+(install|i)\b', 'yarn install', command)
        command = re.sub(r'\bnpm\s+run\b', 'yarn run', command)
        command = re.sub(r'\bnpx\b', 'yarn dlx', command)
    elif pref == "bun":
        command = re.sub(r'\bnpm\s+(install|i)\b', 'bun install', command)
        command = re.sub(r'\bnpm\s+run\b', 'bun run', command)
        command = re.sub(r'\bnpx\b', 'bunx', command)
        
    return command


def strip_ansi_codes(text: str) -> str:
    """
    Removes ANSI escape codes (colors, cursor movements, etc.) from the text.
    
    Why: Keeps output clean for LLM consumption, saving context window tokens.
    """
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)


def clean_carriage_returns(text: str) -> str:
    """
    Resolves carriage returns (\r) by keeping only the overwritten line content,
    similar to how a real terminal displays progress and status lines.
    
    Why: Drastically reduces repetitive progress output log size.
    """
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        if '\r' in line:
            parts = line.split('\r')
            last_part = parts[-1] if parts else ""
            cleaned_lines.append(last_part)
        else:
            cleaned_lines.append(line)
    return '\n'.join(cleaned_lines)


def preprocess_command(command: str) -> str:
    """
    Rewrites common interactive shell commands to run non-interactively.
    
    Why: Prevents blocking execution loops when packages prompt for options/confirmations.
    """
    # 0. Adapt command to match project package manager lockfile
    cmd_stripped = adapt_package_manager_commands(command)
    cmd_stripped = cmd_stripped.strip()
    
    # 1. Inject -y into npx command if a creator is called without the non-interactive flag
    if re.search(r'\bnpx\b', cmd_stripped) and not re.search(r'\bnpx\s+(-y|--yes)\b', cmd_stripped):
        cmd_stripped = re.sub(r'\bnpx\b', 'npx -y', cmd_stripped, count=1)
        logger.info("Auto-inserted '-y' flag to npx command.")

    # 2. Append default arguments to create-next-app to select modern choices non-interactively
    if "create-next-app" in cmd_stripped:
        has_config_flags = any(
            flag in cmd_stripped 
            for flag in ["--ts", "--typescript", "--js", "--javascript", "--tailwind", "--eslint", "--app", "--src-dir", "--import-alias"]
        )
        import shutil
        pref_pkg = "npm"
        if shutil.which("pnpm"):
            pref_pkg = "pnpm"
        elif shutil.which("yarn"):
            pref_pkg = "yarn"
        elif shutil.which("bun"):
            pref_pkg = "bun"
            
        if not has_config_flags:
            cmd_stripped += f" --typescript --tailwind --eslint --app --no-src-dir --import-alias '@/*' --use-{pref_pkg} --yes"
            logger.info("Auto-appended non-interactive defaults with --use-%s to create-next-app command.", pref_pkg)
        else:
            if "--yes" not in cmd_stripped:
                cmd_stripped += " --yes"
            if not any(f in cmd_stripped for f in ["--use-npm", "--use-pnpm", "--use-yarn", "--use-bun"]):
                cmd_stripped += f" --use-{pref_pkg}"
                logger.info("Auto-appended --use-%s to custom create-next-app command.", pref_pkg)

    # 3. Rewrite npm init and yarn init to bypass question-and-answer prompts
    if re.match(r'^npm\s+init\b', cmd_stripped) and not re.search(r'\b(-y|--yes)\b', cmd_stripped):
        cmd_stripped = cmd_stripped.replace("npm init", "npm init -y", 1)
        logger.info("Auto-appended '-y' to npm init command.")
    elif re.match(r'^yarn\s+init\b', cmd_stripped) and not re.search(r'\b(-y|--yes)\b', cmd_stripped):
        cmd_stripped = cmd_stripped.replace("yarn init", "yarn init -y", 1)
        logger.info("Auto-appended '-y' to yarn init command.")
        
    return cmd_stripped


def run_local_command(command: str) -> str:
    """
    Executes a shell command locally on behalf of Elora and returns the output.
    Enforces a 15 second execution timeout limit and passes DEVNULL to stdin to prevent hangs.
    Automatically cleans ANSI codes and filters carriage returns from the output.
    """
    try:
        # Preprocess the command to run non-interactively where possible
        processed_command = preprocess_command(command)
        
        # Detect active user shell to use as subprocess executable (e.g. bash, zsh)
        shell_path = os.environ.get("SHELL", "/bin/bash")
        
        logger.info("Executing local command using shell '%s': '%s' (processed: '%s')", shell_path, command, processed_command)
        raw_output = subprocess.check_output(
            processed_command,
            shell=True,
            executable=shell_path,
            stdin=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            timeout=15
        ).decode(errors="replace")
        
        # Clean output to be brief and readable
        cleaned_output = strip_ansi_codes(raw_output)
        cleaned_output = clean_carriage_returns(cleaned_output)
        
        return cleaned_output if cleaned_output.strip() else "[Command executed successfully, no output returned]"
    except subprocess.TimeoutExpired:
        return "Error: Command execution timed out after 15 seconds."
    except subprocess.CalledProcessError as e:
        raw_err = e.output.decode(errors="replace")
        cleaned_err = strip_ansi_codes(raw_err)
        cleaned_err = clean_carriage_returns(cleaned_err)
        return f"Error (exit code {e.returncode}):\n{cleaned_err}"
    except Exception as e:
        logger.error("Command execution failed: %s", e)
        return f"Failed to execute command: {str(e)}"
