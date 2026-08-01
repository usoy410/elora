# Elora API & Tool Reference

Elora is equipped with a strict set of tools that allow her to interact with the OS and external services. These tools are defined in `elora/core/brain.py` and executed by `elora/core/agent.py`.

## Local System Tools
- **`system_control`**: Controls OS-level settings (volume, brightness, media playback).
- **`command_run`**: Executes arbitrary bash commands securely. Used for compiling code, reading local files, and utilizing git.
- **`desktop_input`**: Simulates raw keyboard strokes and mouse movements (xdotool).

## Workspace & Productivity
- **`workspace_query`**: Interfaces with Google Workspace (via the `gws` CLI). Supports querying Gmail, Drive, Calendar, and Google Tasks. Accepts a `gws_profile` argument to switch between personal and work accounts.
- **`classroom_query`**: Queries Google Classroom for course lists, announcements, and assignment rubrics.
- **`classroom_export_doc`**: Writes study guides, drafts, and notes directly to the `~/Documents/EloraWorkspace/Classroom/` directory in Markdown or PDF formats.

## Web & Search
- **`browser`**: Navigates headless or visible browser instances to scrape dynamic content.
- **`web_search`**: Performs DuckDuckGo searches to gather real-time data from the internet.
- **`web_scrape`**: Extracts raw text from static URLs.
- **`news_fetch`**: Fetches the latest headlines from standard news sources.

## Media
- **`spotify_control`**: Integrates with the Spotify API/DBus to play music, search for tracks, and control playback states.

## Communication
- **`reply`**: Outputs spoken and textual responses back to the user via the HUD and Text-to-Speech (TTS) engine. Automatically sanitizes markdown code blocks before speaking.
