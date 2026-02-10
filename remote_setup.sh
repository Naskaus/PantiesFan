#!/bin/bash
set -e
REMOTE_DIR="/var/www/panties-fan"
SERVICE_NAME="panties_fan"
ARCHIVE="/tmp/deploy_pantiesfan.tar.gz"

echo "[REMOTE] Starting deployment..."

# Backup DB
if [ -f "${REMOTE_DIR}/panties_fan.db" ]; then
    BACKUP_NAME="panties_fan_$(date +%Y%m%d_%H%M%S).db"
    cp "${REMOTE_DIR}/panties_fan.db" "/tmp/${BACKUP_NAME}"
    echo "[REMOTE] DB backed up to /tmp/${BACKUP_NAME}"
fi

# Extract
sudo mkdir -p "${REMOTE_DIR}"
sudo chown seb:seb "${REMOTE_DIR}"
mkdir -p "${REMOTE_DIR}/Static/uploads"

echo "[REMOTE] Extracting..."
tar xzf "${ARCHIVE}" -C "${REMOTE_DIR}"

# Restore DB
if [ -f "/tmp/${BACKUP_NAME:-nonexistent}" ]; then
    cp "/tmp/${BACKUP_NAME}" "${REMOTE_DIR}/panties_fan.db"
    echo "[REMOTE] DB restored"
fi

# Setup Python
cd "${REMOTE_DIR}"
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "[REMOTE] venv created"
fi
source venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
echo "[REMOTE] Deps installed"

# Secret Key
if grep -q "change-me-to-a-real-secret-key" "${REMOTE_DIR}/.env" 2>/dev/null; then
    NEW_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    sed -i "s/change-me-to-a-real-secret-key-in-production/${NEW_SECRET}/" "${REMOTE_DIR}/.env"
    echo "[REMOTE] Secret key generated"
fi

# Service
echo "[REMOTE] Configuring Service..."
sudo cp "${REMOTE_DIR}/panties_fan.service" /etc/systemd/system/${SERVICE_NAME}.service
sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE_NAME}.service
sudo systemctl restart ${SERVICE_NAME}.service

# Verify
sleep 2
if sudo systemctl is-active --quiet ${SERVICE_NAME}.service; then
    echo "[REMOTE] Service RUNNING"
else
    echo "[REMOTE] Service FAILED"
    sudo journalctl -u ${SERVICE_NAME}.service --no-pager -n 20
    exit 1
fi

rm -f "${ARCHIVE}"
rm -f "/tmp/remote_setup.sh"
echo "[REMOTE] Setup Complete"
