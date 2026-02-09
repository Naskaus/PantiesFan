# DEPLOY.md — PantiesFan.com Deployment Manual

> **For LLM agents deploying this project. Read this ENTIRELY before executing.**

---

## 🎯 TL;DR — One Command Deploy

```bash
# From the project root on Seb's Windows machine (Git Bash / WSL):
bash deploy.sh
```

Seb types his SSH password **twice** (once for SCP upload, once for SSH). That's it.

---

## 📋 Server Information

| Key | Value |
|-----|-------|
| **Host** | `digital-shadow` (Raspberry Pi, Linux ARM) |
| **User** | `seb` |
| **IP** | `100.119.245.18` (Tailscale VPN) |
| **SSH** | `ssh seb@100.119.245.18` |
| **App directory** | `/var/www/panties-fan/` |
| **Service name** | `panties_fan` (systemd) |
| **Internal port** | `8005` (Gunicorn → localhost) |
| **Public URL** | `https://pantiesfan.com` |
| **Public URL alias** | `https://www.pantiesfan.com` |
| **Tunnel** | Cloudflare Tunnel `f661a430-...` → `localhost:8005` |
| **Tunnel config** | `/etc/cloudflared/config.yml` |

## 🏗️ Architecture

```
[Browser] → pantiesfan.com → [Cloudflare Tunnel] → localhost:8005 → [Gunicorn] → [Flask app.py]
                                                                         ↑
                                                          /var/www/panties-fan/venv/bin/gunicorn
```

- **Cloudflare Tunnel** is ALREADY configured and running as a systemd service (`cloudflared`). Do NOT touch it.
- **Gunicorn** binds to `127.0.0.1:8005`, managed by systemd service `panties_fan`.
- **SQLite** database file: `/var/www/panties-fan/panties_fan.db` (auto-created on first run).

## 📁 Project Structure (What Gets Deployed)

```
/var/www/panties-fan/
├── app.py                    # Main Flask application (~1400 lines)
├── requirements.txt          # Python dependencies
├── .env                      # Secret key + mail config (generated on first deploy)
├── panties_fan.service       # systemd unit file (copied to /etc/systemd/system/)
├── panties_fan.db            # SQLite database (auto-created, PRESERVED across deploys)
├── venv/                     # Python virtual environment (created on first deploy)
├── Static/
│   ├── css/
│   │   ├── main.css          # Site-wide styles
│   │   ├── admin.css         # Admin panel styles
│   │   └── buyer.css         # Buyer dashboard + payment styles
│   ├── js/
│   │   └── app.js            # Client-side JS (countdowns, bids, GSAP)
│   ├── images/               # Seed images (girls (1-4).jpg, landing, packaging)
│   └── uploads/              # User-uploaded images (UUID filenames, PRESERVED)
├── templates/
│   ├── base.html             # Shared layout (nav, footer, GSAP, flash messages)
│   ├── index.html            # Homepage with auction grid
│   ├── dashboard.html        # Buyer dashboard (bids, wins, orders, address)
│   ├── payment.html          # Payment page (order summary, address, methods)
│   ├── muse_profile.html     # Public muse profile
│   ├── auth/
│   │   ├── login.html
│   │   └── register.html
│   └── admin/
│       ├── dashboard.html    # Admin overview + auction table
│       ├── auction_form.html # Create/edit auction
│       ├── auction_bids.html # View bids for auction
│       ├── muses.html        # Muse management
│       ├── muse_form.html    # Create/edit muse
│       └── orders.html       # Order management (pay/ship/deliver)
├── CLAUDE.md                 # Instructions for Claude Code
└── DEPLOY.md                 # This file
```

## 🔑 Credentials

| Account | Email | Password | Role |
|---------|-------|----------|------|
| Admin | `admin@pantiesfan.com` | `admin123` | admin |

⚠️ **CHANGE THE ADMIN PASSWORD IN PRODUCTION** — Register a new admin or update via SQLite.

## 🚀 Manual Deployment Steps (If Script Fails)

If `deploy.sh` fails or you need to deploy manually, here are the exact steps:

### Step 1: Package on Windows

```bash
cd C:/Users/sebab/Coding/Incubator_Projects_2026

tar czf deploy_pantiesfan.tar.gz \
  --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='panties_fan.db' --exclude='venv' --exclude='test_*.py' \
  --exclude='.claude' --exclude='cookies.txt' --exclude='nul' \
  app.py requirements.txt .env panties_fan.service config.yml \
  CLAUDE.md DEPLOY.md deploy.sh \
  Static/css Static/js Static/images Static/uploads templates
```

### Step 2: Upload to Server

```bash
scp deploy_pantiesfan.tar.gz seb@100.119.245.18:/tmp/
```

### Step 3: SSH and Setup

```bash
ssh seb@100.119.245.18
```

Then on the server:

```bash
# Backup existing DB
cp /var/www/panties-fan/panties_fan.db /tmp/panties_fan_backup.db 2>/dev/null

# Extract
sudo mkdir -p /var/www/panties-fan
sudo chown seb:seb /var/www/panties-fan
tar xzf /tmp/deploy_pantiesfan.tar.gz -C /var/www/panties-fan/
mkdir -p /var/www/panties-fan/Static/uploads

# Restore DB
cp /tmp/panties_fan_backup.db /var/www/panties-fan/panties_fan.db 2>/dev/null

# Python venv
cd /var/www/panties-fan
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Generate secret key (first time only)
python3 -c "import secrets; print(secrets.token_hex(32))"
# Edit .env and replace the SECRET_KEY value

# Install service
sudo cp panties_fan.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable panties_fan
sudo systemctl restart panties_fan

# Verify
sudo systemctl status panties_fan
curl -s -o /dev/null -w "%{http_code}" http://localhost:8005/
# Should print: 200
```

### Step 4: Verify Public Access

```bash
curl -s -o /dev/null -w "%{http_code}" https://pantiesfan.com/
# Should print: 200
```

## 🔧 Common Operations

### View Logs
```bash
sudo journalctl -u panties_fan -f          # Live logs
sudo journalctl -u panties_fan --no-pager -n 50  # Last 50 lines
```

### Restart Service
```bash
sudo systemctl restart panties_fan
```

### Reset Database (Wipe All Data)
```bash
sudo systemctl stop panties_fan
rm /var/www/panties-fan/panties_fan.db
sudo systemctl start panties_fan
# DB auto-recreated with seed data on next request
```

### Check Cloudflare Tunnel
```bash
sudo systemctl status cloudflared
# Tunnel config: /etc/cloudflared/config.yml
# pantiesfan.com → localhost:8005 (already configured)
```

### Update Only Code (No Dependency Changes)
```bash
# From Windows:
scp app.py seb@100.119.245.18:/var/www/panties-fan/
scp -r templates seb@100.119.245.18:/var/www/panties-fan/
scp -r Static/css seb@100.119.245.18:/var/www/panties-fan/Static/
scp -r Static/js seb@100.119.245.18:/var/www/panties-fan/Static/
ssh seb@100.119.245.18 "sudo systemctl restart panties_fan"
```

## ⚠️ Critical Notes

1. **Database is PRESERVED across deploys** — `deploy.sh` backs up and restores `panties_fan.db`. Never include the `.db` file in the archive.

2. **User uploads are PRESERVED** — `Static/uploads/` is not overwritten. Uploaded images have UUID filenames.

3. **Cloudflare Tunnel is SHARED** — The tunnel config at `/etc/cloudflared/config.yml` serves MULTIPLE domains. **NEVER overwrite it** with the local `config.yml` (which is kept for reference only). If you need to modify the tunnel config, **ADD entries, don't replace**.

4. **Raspberry Pi = ARM architecture** — If you ever add native Python packages (like Pillow), they must compile on ARM. Stick to pure-Python packages when possible.

5. **The `.env` secret key is auto-generated on first deploy** — If you redeploy and the `.env` still has the placeholder, it gets a new secret. This invalidates all existing sessions (users must re-login). This is fine.

6. **Port 8005 is reserved** — Other services on this server use ports 8001-8003, 8006-8007, 3006, 5678. Don't conflict.

7. **No test suite in production** — `test_admin.py` and `test_batch3.py` are excluded from the deploy archive. They're dev-only.

## 🗺️ Server Context (Other Services)

This app runs alongside other services on digital-shadow. See the server manifesto for full details:

- `naskaus.com` → port 8080 (Docker)
- `staff.naskaus.com` → port 8001
- `tasks.naskaus.com` → port 8002
- `aperipommes.naskaus.com` → port 8003
- **`pantiesfan.com` → port 8005** ← THIS APP
- `agency.naskaus.com` → port 3006/8006
- `meetbeyond.naskaus.com` → port 8007

## 📊 Database Schema (9 Tables)

```sql
users           -- Buyers + admin accounts (email, password_hash, role, age_verified)
muse_profiles   -- Seller profiles (display_name, bio, avatar, verification status)
auctions        -- Auction listings (title, image, bids, status, start/end times)
bids            -- Individual bid records (amount, user, timestamp, is_winning)
payments        -- Payment records per won auction (token, status, processor)
shipments       -- Shipping records (tracking, carrier, status, cost)
shipping_addresses -- Buyer addresses (full_name, lines, city, country, phone)
notifications   -- In-app notifications (type, title, message, read status)
```

### Auction Status Flow
```
draft → live → ended → [awaiting_payment → pending → paid → shipped → completed]
```

### Payment Status Flow
```
awaiting_payment → pending → paid → shipped → completed
```

## 🔄 What deploy.sh Does (Step by Step)

1. `tar czf` — Creates archive of app code (excludes .git, .db, tests, cache)
2. `scp` — Uploads archive to `/tmp/` on server (Seb enters password)
3. `ssh` — Connects to server (Seb enters password again), then:
   a. Backs up existing `.db` file
   b. Extracts new code to `/var/www/panties-fan/`
   c. Restores `.db` backup
   d. Creates Python venv if needed
   e. `pip install -r requirements.txt`
   f. Generates production `SECRET_KEY` if still placeholder
   g. Copies `.service` file to systemd
   h. `systemctl daemon-reload && enable && restart`
   i. Verifies service is running
   j. HTTP health check on `localhost:8005`
   k. Cleans up temp archive
