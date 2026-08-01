# Elora Architecture

Elora is an OS-level autonomous assistant built on a ReAct (Reasoning + Acting) loop powered by the Gemini API.

## Core Components

1. **`elora/main.py` & IPC Daemon**
   - Elora runs a background daemon that handles text-to-speech, system interactions, and background tasks.
   - The CLI connects to this daemon via inter-process communication (IPC) to send user prompts and receive tool executions without blocking the UI.

2. **`elora/core/brain.py`**
   - The "Brain" contains Elora's core system prompts, cognitive guidelines, and the strict tool registry.
   - It defines exactly what tools Elora is allowed to use and how she should format her JSON tool calls.

3. **`elora/core/agent.py`**
   - The ReAct loop. This file parses the LLM's responses, executes the requested Python functions locally on the OS, and feeds the output back into the LLM context so it can continue reasoning.

4. **`elora/skills/`**
   - The modular toolset. Each file here provides specific capabilities (e.g., `workspace.py` for Google API integrations, `system.py` for OS controls).

## Memory & Workspace Protocol
Elora does not use a complex vector database for memory. Instead, she utilizes the OS File System:
- All data is routed to `~/Documents/EloraWorkspace/`.
- Projects are saved with a `.elora_meta.md` metadata tag.
- When asked to recall a project, Elora uses bash commands (`ls -lt`) to read these metadata tags instantly.
