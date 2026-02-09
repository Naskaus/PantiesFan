#!/bin/bash
# ==============================================================================
# PantiesFan.com — One-Command Deployment Script
# ==============================================================================
#
# USAGE (from Windows Git Bash or WSL):
#   bash deploy.sh
#
# USAGE (if you want to skip prompts):
#   bash deploy.sh --yes
#
# WHAT IT DOES:
#   1. Packages the project (excluding dev files, .git, __pycache__, .db)
#   2. Uploads to digital-shadow server via SCP (you type SSH password)
#   3. SSHs into server and runs remote setup (you type SSH password again)
#   4. Remote setup: extracts, creates venv, installs deps, generates secret,
#      installs systemd service, restarts service, verifies it's running
#
# REQUIREMENTS:
#   - ssh and scp commands available (Git Bash on Windows has these)
#   - Server: digital-shadow at 100.119.245.18 (Tailscale) with user 'seb'
#   - Cloudflare Tunnel already configured (pantiesfan.com → localhost:8005)
#
# ==============================================================================

set -e

# --- Configuration ---
SERVER_USER="seb"
SERVER_HOST="100.119.245.18"
REMOTE_DIR="/var/www/panties-fan"
SERVICE_NAME="panties_fan"
LOCAL_ARCHIVE="deploy_pantiesfan.tar.gz"
REMOTE_ARCHIVE="/tmp/deploy_pantiesfan.tar.gz"
AUTO_YES="${1:-}"

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${CYAN}[DEPLOY]${NC} $1"; }
ok()   { echo -e "${GREEN}[  OK  ]${NC} $1"; }
warn() { echo -e "${YELLOW}[ WARN ]${NC} $1"; }
err()  { echo -e "${RED}[ERROR ]${NC} $1"; }

# --- Pre-flight ---
log "PantiesFan.com Deployment Script"
log "Target: ${SERVER_USER}@${SERVER_HOST}:${REMOTE_DIR}"
echo ""

if [ "$AUTO_YES" != "--yes" ]; then
    read -p "Deploy to production? (y/N): " confirm
    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        err "Aborted."
        exit 1
    fi
fi

# --- Step 1: Package ---
log "Step 1/4: Packaging project..."

# Get script directory (project root)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Clean up old archive
rm -f "$LOCAL_ARCHIVE"

# Create tarball excluding dev/temp files
tar czf "$LOCAL_ARCHIVE" \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='panties_fan.db' \
    --exclude='deploy.tar.gz' \
    --exclude='deploy_pantiesfan.tar.gz' \
    --exclude='cookies.txt' \
    --exclude='nul' \
    --exclude='.claude' \
    --exclude='venv' \
    --exclude='test_*.py' \
    --exclude='*.output' \
    --exclude='PLAN.md' \
    --exclude='PLAN_readable.tmp' \
    --exclude='plan_presentation.html' \
    --exclude='incubator_2026_gtm.html' \
    --exclude='PantiesFan.html' \
    --exclude='CLAUDE_PROJECT_INSTRUCTIONS.md' \
    --exclude='Static/uploads/*' \
    -C "$SCRIPT_DIR" \
    app.py \
    requirements.txt \
    .env \
    panties_fan.service \
    config.yml \
    CLAUDE.md \
    DEPLOY.md \
    deploy.sh \
    Static/css \
    Static/js \
    Static/images \
    Static/uploads \
    templates

ARCHIVE_SIZE=$(du -h "$LOCAL_ARCHIVE" | cut -f1)
ok "Archive created: ${LOCAL_ARCHIVE} (${ARCHIVE_SIZE})"

# --- Step 2: Upload ---
log "Step 2/4: Uploading to server... (enter SSH password)"
scp "$LOCAL_ARCHIVE" "${SERVER_USER}@${SERVER_HOST}:${REMOTE_ARCHIVE}"
ok "Upload complete"

# --- Step 3 & 4: Remote setup ---
log "Step 3/4: Running remote setup... (enter SSH password)"

ssh "${SERVER_USER}@${SERVER_HOST}" bash -s << 'REMOTE_SCRIPT'
set -e

REMOTE_DIR="/var/www/panties-fan"
SERVICE_NAME="panties_fan"
ARCHIVE="/tmp/deploy_pantiesfan.tar.gz"

echo "[REMOTE] Starting deployment on $(hostname)..."

# --- Backup existing DB if present ---
if [ -f "${REMOTE_DIR}/panties_fan.db" ]; then
    BACKUP_NAME="panties_fan_$(date +%Y%m%d_%H%M%S).db"
    cp "${REMOTE_DIR}/panties_fan.db" "/tmp/${BACKUP_NAME}"
    echo "[REMOTE] Database backed up to /tmp/${BACKUP_NAME}"
fi

# --- Create directory structure ---
sudo mkdir -p "${REMOTE_DIR}"
sudo chown seb:seb "${REMOTE_DIR}"
mkdir -p "${REMOTE_DIR}/Static/uploads"

# --- Extract new code ---
echo "[REMOTE] Extracting new code..."
tar xzf "${ARCHIVE}" -C "${REMOTE_DIR}"
echo "[REMOTE] Code extracted"

# --- Restore DB backup if it existed ---
if [ -f "/tmp/${BACKUP_NAME:-nonexistent}" ]; then
    cp "/tmp/${BACKUP_NAME}" "${REMOTE_DIR}/panties_fan.db"
    echo "[REMOTE] Database restored from backup"
fi

# --- Restore uploaded files (don't overwrite user uploads) ---
# uploads dir is preserved because we only extract the empty folder structure

# --- Create/update Python virtual environment ---
echo "[REMOTE] Setting up Python environment..."
cd "${REMOTE_DIR}"

if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "[REMOTE] Virtual environment created"
fi

source venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
echo "[REMOTE] Dependencies installed"

# --- Generate production secret key if still default ---
if grep -q "change-me-to-a-real-secret-key" "${REMOTE_DIR}/.env" 2>/dev/null; then
    NEW_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    sed -i "s/change-me-to-a-real-secret-key-in-production/${NEW_SECRET}/" "${REMOTE_DIR}/.env"
    echo "[REMOTE] Production SECRET_KEY generated"
fi

# --- Install systemd service ---
echo "[REMOTE] Configuring systemd service..."
sudo cp "${REMOTE_DIR}/panties_fan.service" /etc/systemd/system/${SERVICE_NAME}.service
sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE_NAME}.service

# --- Restart service ---
echo "[REMOTE] Restarting service..."
sudo systemctl restart ${SERVICE_NAME}.service
sleep 2

# --- Verify ---
if sudo systemctl is-active --quiet ${SERVICE_NAME}.service; then
    echo "[REMOTE] ✅ Service is RUNNING"
else
    echo "[REMOTE] ❌ Service FAILED to start!"
    sudo journalctl -u ${SERVICE_NAME}.service --no-pager -n 20
    exit 1
fi

# --- Test HTTP ---
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8005/ 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
    echo "[REMOTE] ✅ HTTP check passed (200 OK on localhost:8005)"
else
    echo "[REMOTE] ⚠️  HTTP check returned: ${HTTP_CODE}"
fi

# --- Cleanup ---
rm -f "${ARCHIVE}"

echo ""
echo "=========================================="
echo "  DEPLOYMENT COMPLETE"
echo "  URL: https://pantiesfan.com"
echo "  Admin: /admin (admin@pantiesfan.com)"
echo "  Service: sudo systemctl status ${SERVICE_NAME}"
echo "  Logs: sudo journalctl -u ${SERVICE_NAME} -f"
echo "=========================================="

REMOTE_SCRIPT

ok "Deployment complete!"

# --- Cleanup local archive ---
rm -f "$LOCAL_ARCHIVE"

echo ""
echo -e "${GREEN}=========================================="
echo "  🎉 DEPLOYED TO PRODUCTION"
echo "  URL: https://pantiesfan.com"
echo "  Admin: https://pantiesfan.com/admin"
echo "==========================================${NC}"
