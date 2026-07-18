#!/usr/bin/env bash

# Elora Installer Script
# Installs Elora to standard hidden directories (~/.local/share/elora)
# and registers a desktop application entry and a systemd daemon service.

set -euo pipefail

# Style formatting helper
info() {
    echo -e "\e[34m[INFO]\e[0m $*"
}
success() {
    echo -e "\e[32m[SUCCESS]\e[0m $*"
}
warning() {
    echo -e "\e[33m[WARNING]\e[0m $*"
}
error() {
    echo -e "\e[31m[ERROR]\e[0m $*" >&2
}

info "Starting Elora installation..."

# 1. Check dependencies
info "Checking system requirements..."
MISSING_DEPS=()

for cmd in tmux notify-send mpv aplay uv; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        MISSING_DEPS+=("$cmd")
    fi
done

if [ ${#MISSING_DEPS[@]} -ne 0 ]; then
    warning "The following dependencies are missing: ${MISSING_DEPS[*]}"
    if [[ " ${MISSING_DEPS[*]} " =~ " uv " ]]; then
        error "Elora requires 'uv' (Python package manager) to be installed."
        echo "Please install uv: https://github.com/astral-sh/uv"
        exit 1
    fi
    echo "We highly recommend installing the missing system utilities using your package manager."
    echo "For Ubuntu/Debian: sudo apt install tmux libnotify-bin mpv alsa-utils"
    echo ""
fi

# 2. Setup target directories
INSTALL_DIR="$HOME/.local/share/elora"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/.local/share/applications"
SYSTEMD_DIR="$HOME/.config/systemd/user"

info "Creating target directories..."
mkdir -p "$INSTALL_DIR"
mkdir -p "$BIN_DIR"
mkdir -p "$DESKTOP_DIR"
mkdir -p "$SYSTEMD_DIR"

# 3. Copy project files
info "Copying files to $INSTALL_DIR..."
if command -v rsync >/dev/null 2>&1; then
    rsync -av --delete \
        --exclude='.git' \
        --exclude='.venv' \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        --exclude='install.sh' \
        ./ "$INSTALL_DIR/"
else
    # Fallback to standard copy if rsync isn't available
    cp -r .gitignore .python-version pyproject.toml README.md assets elora main.py "$INSTALL_DIR/"
fi

# 4. Synchronize python environment
info "Setting up Python virtual environment with uv..."
(
    cd "$INSTALL_DIR"
    uv sync
)

# 5. Create wrapper script in ~/.local/bin/elora
info "Creating wrapper executable at $BIN_DIR/elora..."
cat << 'EOF' > "$BIN_DIR/elora"
#!/usr/bin/env bash
# Elora CLI Wrapper
# Runs Elora dynamically inside its installation folder

set -e
INSTALL_DIR="$HOME/.local/share/elora"

# Ensure we run from the install dir so paths resolve correctly
cd "$INSTALL_DIR"
exec uv run python main.py "$@"
EOF

chmod +x "$BIN_DIR/elora"

# 6. Setup desktop entry (.desktop)
info "Creating desktop entry..."
cat << EOF > "$DESKTOP_DIR/elora.desktop"
[Desktop Entry]
Name=Elora
Comment=Linux Desktop OS Orchestrator
Exec=$BIN_DIR/elora --hud
Icon=$INSTALL_DIR/assets/Elora_logo_no_bg.png
Terminal=false
Type=Application
Categories=Utility;Development;
StartupNotify=true
EOF

chmod +x "$DESKTOP_DIR/elora.desktop"

# 7. Setup systemd daemon service
info "Creating background systemd user service..."
cat << EOF > "$SYSTEMD_DIR/elora-daemon.service"
[Unit]
Description=Elora Background Daemon
After=network.target

[Service]
Type=simple
ExecStart=$INSTALL_DIR/.venv/bin/python -m elora.ipc.daemon
WorkingDirectory=$INSTALL_DIR
Restart=on-failure
Environment="PATH=$INSTALL_DIR/.venv/bin:/usr/local/bin:/usr/bin:/bin"

[Install]
WantedBy=default.target
EOF

info "Reloading systemd user configuration and enabling elora-daemon..."
systemctl --user daemon-reload
systemctl --user enable elora-daemon.service
systemctl --user restart elora-daemon.service

success "Elora has been installed successfully!"
echo "--------------------------------------------------------"
echo "🖥️  Launch: Search for 'Elora' in your application menu or run 'elora --hud'"
echo "🎙️  Instant Voice: Run 'elora --hud --voice' to start the HUD in listening mode instantly"
echo "💬  CLI Mode: run 'elora' in your terminal"
echo "⚙️  Background daemon is managed via systemd user services:"
echo "    - Stop daemon: systemctl --user stop elora-daemon"
echo "    - Start daemon: systemctl --user start elora-daemon"
echo "    - Check status: systemctl --user status elora-daemon"
echo "--------------------------------------------------------"

# 8. Interactive Configuration Wizard Option
if [ -t 0 ]; then
    echo ""
    read -rp "Would you like to run the configuration wizard now to set up your APIs (Gemini, Spotify, etc.)? [Y/n]: " run_wizard
    if [[ -z "$run_wizard" || "$run_wizard" =~ ^[Yy](es)?$ ]]; then
        (
            cd "$INSTALL_DIR"
            uv run python main.py --setup
        )
    fi
fi

# PATH warning if bin is not set
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    warning "$BIN_DIR is not in your current PATH."
    echo "You may need to add it to your shell configuration (e.g. .bashrc or .zshrc):"
    echo "export PATH=\"\$HOME/.local/bin:\$PATH\""
fi
