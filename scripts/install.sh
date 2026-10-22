#!/bin/bash
# DaTaK Gateway Installation Script

set -e

INSTALL_DIR="/opt/datak"
USER="datak"
GROUP="datak"

echo "🚀 Installing DaTaK Gateway..."

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "❌ Please run as root"
    exit 1
fi

# Create user and group
if ! id "$USER" &>/dev/null; then
    echo "📦 Creating user $USER..."
    useradd -r -s /bin/false -d $INSTALL_DIR $USER
fi

# Add user to dialout and plugdev groups for hardware access
usermod -aG dialout,plugdev $USER

# Create directory structure
echo "📁 Creating directories..."
mkdir -p $INSTALL_DIR/{backend,frontend,configs,data/{exports,buffer},logs}

# Copy files
echo "📋 Copying files..."
cp -r backend/* $INSTALL_DIR/backend/
cp -r configs/* $INSTALL_DIR/configs/
cp -r systemd/* /etc/systemd/system/

# Create Python virtual environment
echo "🐍 Setting up Python environment..."
cd $INSTALL_DIR/backend
python3.12 -m venv $INSTALL_DIR/.venv
source $INSTALL_DIR/.venv/bin/activate
pip install --upgrade pip
pip install -e .

# Run database migrations
echo "🗄️ Running database migrations..."
alembic upgrade head

# Create default config if not exists
if [ ! -f $INSTALL_DIR/configs/gateway.yaml ]; then
    cp $INSTALL_DIR/configs/gateway.example.yaml $INSTALL_DIR/configs/gateway.yaml
    echo "⚠️  Please edit $INSTALL_DIR/configs/gateway.yaml"
fi

# Set permissions
echo "🔒 Setting permissions..."
chown -R $USER:$GROUP $INSTALL_DIR
chmod 750 $INSTALL_DIR
chmod 640 $INSTALL_DIR/configs/*

# Reload systemd
echo "🔄 Reloading systemd..."
systemctl daemon-reload

# Enable service
echo "✅ Enabling service..."
systemctl enable datak-gateway

echo ""
echo "✅ Installation complete!"
echo ""
echo "Next steps:"
echo "  1. Edit configuration: nano $INSTALL_DIR/configs/gateway.yaml"
echo "  2. Start service: systemctl start datak-gateway"
echo "  3. Check status: systemctl status datak-gateway"
echo "  4. View logs: journalctl -u datak-gateway -f"
