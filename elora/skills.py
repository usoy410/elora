"""
Elora Local Skills and Web Browsing Engines.
Provides DuckDuckGo search, BeautifulSoup text scraping, and command execution.
"""

import logging
import subprocess
import requests
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


def run_local_command(command: str) -> str:
    """
    Executes a shell command locally on behalf of Elora and returns the output.
    Enforces a 15 second execution timeout limit.
    """
    try:
        logger.info("Executing local shell command: '%s'", command)
        output = subprocess.check_output(
            command,
            shell=True,
            stderr=subprocess.STDOUT,
            timeout=15
        ).decode()
        
        return output if output.strip() else "[Command executed successfully, no output returned]"
    except subprocess.TimeoutExpired:
        return "Error: Command execution timed out after 15 seconds."
    except subprocess.CalledProcessError as e:
        return f"Error (exit code {e.returncode}):\n{e.output.decode()}"
    except Exception as e:
        logger.error("Command execution failed: %s", e)
        return f"Failed to execute command: {str(e)}"
