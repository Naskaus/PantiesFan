# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PantiesFan.com — a Flask-based luxury auction marketplace for intimate apparel. Sellers list items, buyers place bids via a REST API, and auctions have sniper-protection logic (extends time by 2 minutes if bid placed with <5 minutes remaining).

## Tech Stack

- **Backend:** Python 3 / Flask / SQLite3
- **Frontend:** Vanilla JS, GSAP (ScrollTrigger), Font Awesome
- **Production:** Gunicorn behind Cloudflare Tunnel, systemd service
- **Fonts:** Cinzel, Playfair Display, Montserrat (loaded from Google Fonts)

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run dev server (port 8005, debug mode)
python app.py

# Run production server
gunicorn --bind 127.0.0.1:8005 app:app
```

No test suite exists. No linter is configured.

## Architecture

### Backend (`app.py`)

Single-file Flask app. The static folder is configured as `Static` (capital S):
```python
app = Flask(__name__, static_folder='Static')
```

**Database:** SQLite file `panties_fan.db` in project root. Auto-created and seeded with 4 auction items on first run if the file doesn't exist. Delete the `.db` file to reset.

**Routes:**
- `GET /` — renders `templates/index.html` with all auctions from DB
- `POST /api/bid/<int:item_id>` — places a bid (increments by random $5/$10/$15/$20), returns JSON

### Frontend (`templates/index.html`)

Single-page template (~29KB) with all CSS and JS inline. Sections: hero with parallax, manifesto, live auctions grid, how-it-works, packaging showcase, CTA, footer. Bids are placed via `fetch()` to `/api/bid/<id>` with UI feedback (button state change, price flash animation).

### Static Assets

Images live in `Static/images/`. Filenames contain spaces (e.g., `girls (1).jpg`) — use `url_for('static', ...)` in templates.

### Deployment

- `config.yml` — Cloudflare Tunnel config routing `pantiesfan.com` → `localhost:8005`
- `panties_fan.service` — systemd unit file, expects app at `/var/www/panties-fan/`
- `deploy.tar.gz` — pre-built deployment archive
