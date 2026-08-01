# Workspace & Google Authentication Setup

Elora uses the external `gws` (Google Workspace CLI) tool to interface with Google APIs securely without requiring complex OAuth boilerplate inside the codebase.

## Google Cloud Setup
Because organization-managed accounts (like school domains) often block internal third-party apps, Elora must be connected to an **External** OAuth client.
1. Create a Google Cloud Project on a personal account.
2. Configure the OAuth Consent Screen as **External**.
3. Add your personal and work/school emails as **Test users**.
4. Create an OAuth 2.0 Client ID (Desktop App).
5. Download the `client_secret.json` and save it to `~/.config/elora/classroom_credentials.json`.

## Multi-Profile Login
Elora supports multiple profiles (e.g., personal email vs school classroom). The system automatically bootstraps the credentials when you authenticate.

To login to your **Personal** account:
```bash
GOOGLE_WORKSPACE_CLI_CONFIG_DIR=~/.config/elora/gws-personal gws auth login --services drive,gmail,calendar
```

To login to your **Work/School** account:
```bash
GOOGLE_WORKSPACE_CLI_CONFIG_DIR=~/.config/elora/gws-work gws auth login --services drive,gmail,calendar,classroom
```

Elora's `workspace_query` tool in `brain.py` allows her to dynamically select which profile to use based on user intent.
