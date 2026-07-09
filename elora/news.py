"""
Elora's news engine backend.
Fetches tech RSS feeds, formats markdown summaries, and opens articles for deep-diving.
"""

import subprocess
import logging
from typing import List, Dict
import feedparser

logger = logging.getLogger("elora.news")

# Default technical feeds seed list
DEFAULT_FEEDS = [
    "https://news.ycombinator.com/rss",
    "https://www.phoronix.com/rss.php",
    "https://techcrunch.com/feed/",
    "https://news.google.com/rss"
]


# Simple in-memory cache of the last fetched skim articles to support indexing for deep dive
_article_cache: List[Dict[str, str]] = []


def scrape_custom_blog(url: str, title_selector: str, link_selector: str, limit: int = 3) -> List[Dict[str, str]]:
    """
    Spawns a headless Playwright Chromium instance to load the page, render JS,
    and extract article titles and links based on CSS selectors.
    
    Why: Handles modern technical blogs that lack standard RSS XML feeds.
    """
    articles = []
    try:
        from playwright.sync_api import sync_playwright
        logger.info("Scraping RSS-less blog with Playwright: %s", url)
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            # Set a standard timeout
            page.goto(url, timeout=15000)
            
            title_locators = page.locator(title_selector)
            link_locators = page.locator(link_selector)
            
            count = min(title_locators.count(), link_locators.count())
            limit = min(count, limit)
            
            for i in range(limit):
                title = title_locators.nth(i).inner_text().strip()
                link = link_locators.nth(i).get_attribute("href")
                if link:
                    if not link.startswith("http"):
                        from urllib.parse import urljoin
                        link = urljoin(url, link)
                        
                    articles.append({
                        "title": title or "No Title",
                        "link": link,
                        "source": url.split("//")[-1].split("/")[0]
                    })
                    
            browser.close()
    except Exception as e:
        logger.error("Playwright scraping failed for %s: %s", url, e)
    return articles


def fetch_tech_news(feed_urls: List[str] = None, limit_per_feed: int = None) -> List[Dict[str, str]]:
    """
    Fetches articles from RSS feeds and normalizes them into standard items.
    
    Why: feedparser is an optimized local parser that fetches and parses XML feeds
    without launching heavy headless browser overhead.
    """
    from elora.config import load_config
    config = load_config()
    news_config = config.get("news", {})
    
    if feed_urls is None:
        feed_urls = news_config.get("feeds", DEFAULT_FEEDS)
    if limit_per_feed is None:
        limit_per_feed = news_config.get("limit_per_feed", 3)

    global _article_cache
    articles = []
    
    # Fetch standard RSS XML feeds
    for url in feed_urls:
        try:
            logger.info("Fetching RSS feed: %s", url)
            feed = feedparser.parse(url)
            
            # Extract standard fields
            count = 0
            for entry in feed.entries:
                if count >= limit_per_feed:
                    break
                articles.append({
                    "title": entry.get("title", "No Title"),
                    "link": entry.get("link", ""),
                    "source": feed.feed.get("title", "Unknown Source")
                })
                count += 1
        except Exception as e:
            logger.error("Error parsing feed %s: %s", url, str(e))
            
    # Fetch custom RSS-less blogs using Playwright
    custom_blogs = news_config.get("custom_blogs", [])
    for blog in custom_blogs:
        url = blog.get("url")
        t_sel = blog.get("title_selector")
        l_sel = blog.get("link_selector")
        if url and t_sel and l_sel:
            blog_articles = scrape_custom_blog(url, t_sel, l_sel, limit=limit_per_feed)
            articles.extend(blog_articles)
            
    # Cache the list for subsequent deep dive reference
    _article_cache = articles
    return articles



def get_news_summary() -> str:
    """
    Summarizes the top fetched articles into a clean Markdown format.
    
    Why: Adheres to the 'Skim' option in the Elora blueprint.
    """
    articles = fetch_tech_news()
    if not articles:
        return "No tech news articles could be fetched at this time."
        
    markdown_lines = [
        "## 📰 Elora Tech News Skim",
        "Here are today's top technical articles:\n"
    ]
    
    # Take up to the top 5 articles overall
    for i, art in enumerate(articles[:5], start=1):
        markdown_lines.append(f"{i}. **[{art['source']}]** {art['title']}")
        markdown_lines.append(f"   *Link: {art['link']}*")
        
    markdown_lines.append("\n*Tip: Say 'Open the full article for number <index>' to deep dive.*")
    return "\n".join(markdown_lines)


def open_article(index: int) -> bool:
    """
    Launches default system web browser to open the chosen cached article.
    
    Why: Implements 'Deep Dive' option by using xdg-open to launch browser instantly.
    """
    global _article_cache
    
    # Normalize index (1-based index to 0-based index)
    cached_idx = index - 1
    if not _article_cache:
        # Try fetching again to populate cache if empty
        fetch_tech_news()
        
    if cached_idx < 0 or cached_idx >= len(_article_cache):
        logger.warning("Invalid article index requested: %d (cached size: %d)", index, len(_article_cache))
        return False
        
    target_url = _article_cache[cached_idx]["link"]
    if not target_url:
        logger.warning("No URL found for cached article index %d", index)
        return False
        
    try:
        # Launch default browser in detached subprocess
        subprocess.Popen(
            ["xdg-open", target_url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        logger.info("Successfully opened article index %d in browser: %s", index, target_url)
        return True
    except Exception as e:
        logger.error("Failed to open article %s: %s", target_url, str(e))
        return False
