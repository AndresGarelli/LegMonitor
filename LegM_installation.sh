#!/bin/bash
set -e
exec > >(stdbuf -oL -eL tee install.log) 2>&1
APP_NAME="LegM"
APP_DIR="$HOME/LegM"
SCRIPT="$APP_DIR/LegM.py"
VENV_DIR="$HOME/venvs/pupa"
CONFIG_FILE="/boot/firmware/config.txt"

echo "=== Pupa installer ==="

############################
# 1-Wire detection
############################
echo "Checking 1-Wire status..."
WIRE_OK=true

if [ ! -d /sys/bus/w1/devices ]; then
    WIRE_OK=false
fi

if ! lsmod | grep -q w1_therm; then
    WIRE_OK=false
fi

if ! grep -q "^dtoverlay=w1-gpio" "$CONFIG_FILE"; then
    WIRE_OK=false
fi

if [ "$WIRE_OK" = false ]; then
    echo "Enabling 1-Wire..."
    if ! grep -q "^dtoverlay=w1-gpio" "$CONFIG_FILE"; then
        echo "dtoverlay=w1-gpio" | sudo tee -a "$CONFIG_FILE" > /dev/null
    fi
    echo "1-wire was just enabled, reboot required"
    echo
    read -r -p "Press Enter to close this window. Reboot, then re-run this installer."
    exit 0
fi

echo "1-Wire OK."

############################
# System dependencies
############################
echo "Installing system dependencies..."
sudo apt update
sudo apt install -y \
    python3-venv \
    python3-dev \
    python3-opencv \
    python3-matplotlib \
    python3-pandas \
    python3-scipy \
    python3-numpy

############################
# Virtual environment
############################
echo "Creating virtual environment..."
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv --system-site-packages "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
pip install --upgrade pip
pip install w1thermsensor



############################
# Desktop launcher
############################
echo "Creating desktop launcher..."

cat > "$HOME/Desktop/LegM.desktop" <<EOF
[Desktop Entry]
Version=1.0
Name=LegM 
Comment=Pupal leg movement monitor
Exec=$VENV_DIR/bin/python $SCRIPT
Icon=utilities-terminal
Terminal=true
Type=Application
Categories=Science;Utility;
EOF

chmod +x "$HOME/Desktop/LegM.desktop"

############################
# Done
############################
echo "================================"
echo "Installation complete"
echo
echo "Run manually:"
echo "  $VENV_DIR/bin/python $SCRIPT"
echo "Log saved to: $(pwd)/install.log"
echo "press enter to close"
read -r

