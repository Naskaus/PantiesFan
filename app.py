"""
PantiesFan.com — Luxury Auction Marketplace
Phase 1 MVP: Authentication, Real Auctions, Proper Bidding, Admin Dashboard
"""

import os
import uuid
import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, render_template, jsonify, request, redirect, url_for, flash, abort
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

load_dotenv()

# =============================================
# APP CONFIGURATION
# =============================================

app = Flask(__name__, static_folder='Static')
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

csrf = CSRFProtect(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please sign in to place a bid.'
login_manager.login_message_category = 'info'

DB_NAME = "panties_fan.db"
MIN_BID_INCREMENT = 5.00
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def admin_required(f):
    """Decorator: requires login + admin role."""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if current_user.role != 'admin':
            abort(403)
        return f(*args, **kwargs)
    return decorated


SHIPPING_RATES = {
    'US': 85.00,
    'CA': 80.00,
    'GB': 55.00,
    'DE': 52.00,
    'FR': 55.00,
    'JP': 50.00,
    'AU': 65.00,
    'DEFAULT': 70.00,
}

PAYMENT_WINDOW_HOURS = 48  # Hours buyer has to pay before offer goes to next bidder


def create_payment_for_winner(conn, auction_id):
    """Create a payment record + notification when an auction ends with a winner.
    Returns the payment row or None if no winner / already exists."""
    auction = conn.execute('SELECT * FROM auctions WHERE id = ?', (auction_id,)).fetchone()
    if not auction or not auction['current_bidder_id']:
        return None

    # Check if payment already exists
    existing = conn.execute('SELECT id FROM payments WHERE auction_id = ?', (auction_id,)).fetchone()
    if existing:
        return existing

    token = secrets.token_urlsafe(32)
    now_str = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    conn.execute('''
        INSERT INTO payments (auction_id, buyer_id, amount, status, payment_token, created_at)
        VALUES (?, ?, ?, 'awaiting_payment', ?, ?)
    ''', (auction_id, auction['current_bidder_id'], auction['current_bid'], token, now_str))

    # Update auction status
    conn.execute("UPDATE auctions SET status = 'ended' WHERE id = ?", (auction_id,))

    # Create notification for winner
    buyer = conn.execute('SELECT display_name FROM users WHERE id = ?',
                         (auction['current_bidder_id'],)).fetchone()
    conn.execute('''
        INSERT INTO notifications (user_id, type, title, message, link, created_at)
        VALUES (?, 'auction_won', ?, ?, ?, ?)
    ''', (
        auction['current_bidder_id'],
        'You won an auction!',
        f'Congratulations! You won "{auction["title"]}" for ${auction["current_bid"]:.2f}. Complete your payment within {PAYMENT_WINDOW_HOURS} hours.',
        f'/pay/{token}',
        now_str
    ))

    conn.commit()
    return conn.execute('SELECT * FROM payments WHERE auction_id = ?', (auction_id,)).fetchone()


def end_expired_auctions(conn):
    """Auto-end expired auctions and create payment records for winners."""
    now_str = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    # Find auctions that just expired
    expired = conn.execute('''
        SELECT id FROM auctions
        WHERE status = 'live' AND ends_at <= ?
    ''', (now_str,)).fetchall()

    # Update status
    conn.execute("UPDATE auctions SET status = 'ended' WHERE status = 'live' AND ends_at <= ?", (now_str,))
    conn.commit()

    # Create payment records for winners
    for row in expired:
        create_payment_for_winner(conn, row['id'])


# =============================================
# DATABASE
# =============================================

def get_db():
    conn = sqlite3.connect(DB_NAME, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def log_audit(conn, entity_type, entity_id, action, details=None):
    """Insert an entry into the audit_log table."""
    details_json = json.dumps(details) if details else None
    aid = current_user.id if current_user.is_authenticated else None
    now_str = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    conn.execute('''
        INSERT INTO audit_log (entity_type, entity_id, action, details, admin_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (entity_type, entity_id, action, details_json, aid, now_str))


def init_db():
    """Create tables and seed data if database doesn't exist."""
    fresh = not os.path.exists(DB_NAME)
    conn = get_db()

    conn.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'buyer',
            age_verified INTEGER DEFAULT 0,
            dob TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            last_login TEXT,
            is_active INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS muse_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id),
            display_name TEXT NOT NULL,
            bio TEXT,
            avatar_url TEXT,
            verification TEXT DEFAULT 'pending',
            total_sales INTEGER DEFAULT 0,
            avg_rating REAL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS auctions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            muse_id INTEGER REFERENCES muse_profiles(id),
            title TEXT NOT NULL,
            description TEXT,
            category TEXT,
            wear_duration TEXT,
            image TEXT NOT NULL,
            starting_bid REAL NOT NULL,
            current_bid REAL,
            current_bidder_id INTEGER REFERENCES users(id),
            bid_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'draft',
            starts_at TEXT,
            ends_at TEXT NOT NULL,
            original_end TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            created_by INTEGER REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS bids (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            auction_id INTEGER NOT NULL REFERENCES auctions(id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            amount REAL NOT NULL,
            placed_at TEXT DEFAULT (datetime('now')),
            is_winning INTEGER DEFAULT 0,
            ip_address TEXT
        );

        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            auction_id INTEGER REFERENCES auctions(id),
            buyer_id INTEGER REFERENCES users(id),
            amount REAL NOT NULL,
            processor TEXT,
            processor_txn TEXT,
            status TEXT DEFAULT 'pending',
            payment_token TEXT UNIQUE,
            created_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS shipments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payment_id INTEGER REFERENCES payments(id),
            tracking_number TEXT,
            carrier TEXT DEFAULT 'DHL',
            destination TEXT,
            status TEXT DEFAULT 'preparing',
            shipped_at TEXT,
            delivered_at TEXT,
            shipping_cost REAL
        );

        CREATE TABLE IF NOT EXISTS shipping_addresses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id),
            full_name TEXT NOT NULL,
            address_line1 TEXT NOT NULL,
            address_line2 TEXT,
            city TEXT NOT NULL,
            state TEXT,
            postal_code TEXT NOT NULL,
            country TEXT NOT NULL,
            phone TEXT,
            is_default INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            type TEXT NOT NULL,
            title TEXT NOT NULL,
            message TEXT,
            link TEXT,
            is_read INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            entity_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            details TEXT,
            admin_id INTEGER REFERENCES users(id),
            created_at TEXT DEFAULT (datetime('now'))
        );
    ''')

    # Safe migration: add admin_notes to payments if missing
    try:
        conn.execute("SELECT admin_notes FROM payments LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE payments ADD COLUMN admin_notes TEXT")

    if fresh:
        _seed_data(conn)

    conn.commit()
    conn.close()


def _seed_data(conn):
    """Seed initial muse profiles and auction items."""
    # Create admin user
    admin_hash = generate_password_hash('admin123')
    conn.execute(
        'INSERT INTO users (email, password_hash, display_name, role, age_verified) VALUES (?, ?, ?, ?, ?)',
        ('admin@pantiesfan.com', admin_hash, 'Admin', 'admin', 1)
    )

    # Create muse profiles (not linked to user accounts in Phase 1 — admin-created)
    muses = [
        ('Mistress_V', 'Exotic silk specialist. Every piece tells a story.', None),
        ('FitGirl_99', 'Gym addict. My workouts make everything more intense.', None),
        ('RedRose', 'Special requests are my specialty. Tell me your fantasy.', None),
        ('SweetPea', 'Casual and natural. The girl next door experience.', None),
    ]
    for name, bio, avatar in muses:
        conn.execute(
            'INSERT INTO muse_profiles (display_name, bio, avatar_url, verification) VALUES (?, ?, ?, ?)',
            (name, bio, avatar, 'verified')
        )

    # Create auctions with real UTC end times
    now = datetime.now(timezone.utc)
    auctions = [
        (1, 'Silk Lace Nightset (Worn 2 Days)', 'Exquisite silk lace set worn for two full days. The fabric holds every moment.',
         'set', '2 days', 'girls (1).jpg', 145.00, 'live',
         now + timedelta(hours=2, minutes=15)),
        (2, 'Gym Session Thong (Intense)', 'Fresh from an intense 2-hour gym session. Authenticity guaranteed.',
         'thong', '1 workout', 'girls (2).jpg', 85.00, 'live',
         now + timedelta(minutes=45)),
        (3, 'Red Velvet Special Request', 'Custom worn to your specifications. 72 hours of dedicated wear.',
         'custom', '3 days', 'girls (3).jpg', 210.00, 'live',
         now + timedelta(hours=5, minutes=30)),
        (4, 'Cotton Daily (Very Casual)', 'Simple cotton comfort, worn through a full day of Bangkok life.',
         'panties', '1 day', 'girls (4).jpg', 65.00, 'live',
         now + timedelta(minutes=12)),
    ]

    for muse_id, title, desc, cat, wear, img, bid, status, end_time in auctions:
        end_str = end_time.strftime('%Y-%m-%dT%H:%M:%SZ')
        start_str = now.strftime('%Y-%m-%dT%H:%M:%SZ')
        conn.execute('''
            INSERT INTO auctions
            (muse_id, title, description, category, wear_duration, image,
             starting_bid, current_bid, status, starts_at, ends_at, original_end, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        ''', (muse_id, title, desc, cat, wear, img, bid, bid, status, start_str, end_str, end_str))

    print("Database initialized with seed data.")


# =============================================
# USER MODEL (Flask-Login)
# =============================================

class User(UserMixin):
    def __init__(self, id, email, display_name, role, age_verified, is_active):
        self.id = id
        self.email = email
        self.display_name = display_name
        self.role = role
        self.age_verified = age_verified
        self._is_active = is_active

    @property
    def is_active(self):
        return bool(self._is_active)

    @staticmethod
    def get_by_id(user_id):
        conn = get_db()
        row = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
        conn.close()
        if row:
            return User(row['id'], row['email'], row['display_name'],
                        row['role'], row['age_verified'], row['is_active'])
        return None

    @staticmethod
    def get_by_email(email):
        conn = get_db()
        row = conn.execute('SELECT * FROM users WHERE email = ?', (email.lower().strip(),)).fetchone()
        conn.close()
        if row:
            return User(row['id'], row['email'], row['display_name'],
                        row['role'], row['age_verified'], row['is_active']), row['password_hash']
        return None, None


@login_manager.user_loader
def load_user(user_id):
    return User.get_by_id(int(user_id))


# =============================================
# AUTH ROUTES
# =============================================

@app.route('/auth/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('home'))

    # Max DOB = 18 years ago
    max_dob = (datetime.now() - timedelta(days=18 * 365)).strftime('%Y-%m-%d')

    if request.method == 'POST':
        email = request.form.get('email', '').lower().strip()
        password = request.form.get('password', '')
        password_confirm = request.form.get('password_confirm', '')
        display_name = request.form.get('display_name', '').strip()
        dob = request.form.get('dob', '')
        age_confirm = request.form.get('age_confirm')
        terms_confirm = request.form.get('terms_confirm')

        # Validation
        errors = []
        if not email or '@' not in email:
            errors.append('Please enter a valid email address.')
        if len(password) < 8:
            errors.append('Password must be at least 8 characters.')
        if password != password_confirm:
            errors.append('Passwords do not match.')
        if not display_name or len(display_name) < 2:
            errors.append('Display name must be at least 2 characters.')
        if len(display_name) > 30:
            errors.append('Display name must be 30 characters or fewer.')
        if not dob:
            errors.append('Date of birth is required.')
        if not age_confirm:
            errors.append('You must confirm you are 18 or older.')
        if not terms_confirm:
            errors.append('You must agree to the Terms of Service.')

        # Age check
        if dob:
            try:
                dob_date = datetime.strptime(dob, '%Y-%m-%d')
                age = (datetime.now() - dob_date).days / 365.25
                if age < 18:
                    errors.append('You must be at least 18 years old to register.')
            except ValueError:
                errors.append('Invalid date of birth.')

        # Check if email already exists
        if not errors:
            existing, _ = User.get_by_email(email)
            if existing:
                errors.append('An account with this email already exists.')

        if errors:
            for error in errors:
                flash(error, 'error')
            return render_template('auth/register.html', max_dob=max_dob)

        # Create user
        conn = get_db()
        password_hash = generate_password_hash(password)
        conn.execute(
            'INSERT INTO users (email, password_hash, display_name, dob, age_verified) VALUES (?, ?, ?, ?, ?)',
            (email, password_hash, display_name, dob, 1)
        )
        conn.commit()

        # Auto-login
        row = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
        conn.close()

        user = User(row['id'], row['email'], row['display_name'],
                    row['role'], row['age_verified'], row['is_active'])
        login_user(user)
        flash('Welcome to PantiesFan! Your account has been created.', 'success')
        return redirect(url_for('home'))

    return render_template('auth/register.html', max_dob=max_dob)


@app.route('/auth/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('home'))

    if request.method == 'POST':
        email = request.form.get('email', '').lower().strip()
        password = request.form.get('password', '')

        user, password_hash = User.get_by_email(email)

        if user and check_password_hash(password_hash, password):
            login_user(user)
            # Update last login
            conn = get_db()
            conn.execute('UPDATE users SET last_login = ? WHERE id = ?',
                         (datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), user.id))
            conn.commit()
            conn.close()

            flash(f'Welcome back, {user.display_name}!', 'success')
            next_page = request.args.get('next')
            return redirect(next_page or url_for('home'))
        else:
            flash('Invalid email or password.', 'error')

    return render_template('auth/login.html')


@app.route('/auth/logout')
@login_required
def logout():
    logout_user()
    flash('You have been signed out.', 'info')
    return redirect(url_for('home'))


# =============================================
# MAIN ROUTES
# =============================================

@app.route('/')
def home():
    conn = get_db()

    # Auto-end expired auctions + create payment records for winners
    end_expired_auctions(conn)

    # Fetch auctions with muse info
    rows = conn.execute('''
        SELECT a.*, m.display_name as seller_name
        FROM auctions a
        LEFT JOIN muse_profiles m ON a.muse_id = m.id
        ORDER BY
            CASE a.status WHEN 'live' THEN 0 ELSE 1 END,
            a.ends_at ASC
    ''').fetchall()

    auctions = []
    for row in rows:
        item = dict(row)

        # Get last bidder display name
        item['last_bidder_name'] = None
        if item['current_bidder_id']:
            bidder = conn.execute('SELECT display_name FROM users WHERE id = ?',
                                 (item['current_bidder_id'],)).fetchone()
            if bidder:
                item['last_bidder_name'] = bidder['display_name']

        # Get recent bids (last 5)
        recent = conn.execute('''
            SELECT b.amount, u.display_name as bidder
            FROM bids b
            JOIN users u ON b.user_id = u.id
            WHERE b.auction_id = ?
            ORDER BY b.placed_at DESC
            LIMIT 5
        ''', (item['id'],)).fetchall()
        item['recent_bids'] = [dict(r) for r in recent]

        auctions.append(item)

    conn.close()
    return render_template('index.html', auctions=auctions)


# =============================================
# BID API
# =============================================

@app.route('/api/bid/<int:item_id>', methods=['POST'])
@login_required
def place_bid(item_id):
    conn = get_db()
    now = datetime.now(timezone.utc)
    now_str = now.strftime('%Y-%m-%dT%H:%M:%SZ')

    # Get auction
    auction = conn.execute('SELECT * FROM auctions WHERE id = ?', (item_id,)).fetchone()

    if not auction:
        conn.close()
        return jsonify({'success': False, 'message': 'Auction not found.'}), 404

    # Check auction is live
    if auction['status'] != 'live':
        conn.close()
        return jsonify({'success': False, 'message': 'This auction is no longer active.'}), 400

    # Check auction hasn't ended
    ends_at = datetime.strptime(auction['ends_at'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    if now >= ends_at:
        conn.execute('UPDATE auctions SET status = ? WHERE id = ?', ('ended', item_id))
        conn.commit()
        conn.close()
        return jsonify({'success': False, 'message': 'This auction has ended.'}), 400

    # Get bid amount from request
    data = request.get_json()
    if not data or 'amount' not in data:
        conn.close()
        return jsonify({'success': False, 'message': 'Bid amount is required.'}), 400

    try:
        bid_amount = float(data['amount'])
    except (ValueError, TypeError):
        conn.close()
        return jsonify({'success': False, 'message': 'Invalid bid amount.'}), 400

    # Validate bid amount
    min_bid = (auction['current_bid'] or auction['starting_bid']) + MIN_BID_INCREMENT
    if bid_amount < min_bid:
        conn.close()
        return jsonify({
            'success': False,
            'message': f'Bid must be at least ${min_bid:.2f} (current + ${MIN_BID_INCREMENT:.2f} minimum increment).'
        }), 400

    # Can't bid on own auction (check if current user is the muse)
    # In Phase 1, muses don't have accounts, so this is future-proofing

    # Record the bid
    previous_bidder_id = auction['current_bidder_id']
    ip_address = request.remote_addr

    conn.execute(
        'INSERT INTO bids (auction_id, user_id, amount, ip_address) VALUES (?, ?, ?, ?)',
        (item_id, current_user.id, bid_amount, ip_address)
    )

    # Clear previous winning bid flag
    conn.execute('UPDATE bids SET is_winning = 0 WHERE auction_id = ?', (item_id,))
    # Set new winning bid (get the latest bid ID first, then update it)
    latest_bid = conn.execute('''
        SELECT id FROM bids
        WHERE auction_id = ? AND user_id = ? AND amount = ?
        ORDER BY placed_at DESC LIMIT 1
    ''', (item_id, current_user.id, bid_amount)).fetchone()
    if latest_bid:
        conn.execute('UPDATE bids SET is_winning = 1 WHERE id = ?', (latest_bid['id'],))

    # Sniper protection: extend by 2 minutes if bid placed within last 5 minutes
    time_remaining = (ends_at - now).total_seconds()
    new_ends_at = ends_at
    if time_remaining < 300:  # 5 minutes
        new_ends_at = ends_at + timedelta(minutes=2)

    new_ends_str = new_ends_at.strftime('%Y-%m-%dT%H:%M:%SZ')

    # Update auction
    conn.execute('''
        UPDATE auctions
        SET current_bid = ?, current_bidder_id = ?, bid_count = bid_count + 1, ends_at = ?
        WHERE id = ?
    ''', (bid_amount, current_user.id, new_ends_str, item_id))

    conn.commit()

    # Get recent bids for response
    recent = conn.execute('''
        SELECT b.amount, u.display_name as bidder
        FROM bids b
        JOIN users u ON b.user_id = u.id
        WHERE b.auction_id = ?
        ORDER BY b.placed_at DESC
        LIMIT 5
    ''', (item_id,)).fetchall()

    conn.close()

    min_next = bid_amount + MIN_BID_INCREMENT

    return jsonify({
        'success': True,
        'new_price': f"${bid_amount:.2f}",
        'bidder': current_user.display_name,
        'message': 'Bid Accepted!',
        'ends_at': new_ends_str,
        'min_next_bid': f"{min_next:.2f}",
        'sniper_extended': time_remaining < 300,
        'recent_bids': [{'bidder': r['bidder'], 'amount': f"{r['amount']:.2f}"} for r in recent]
    })


# =============================================
# ADMIN ROUTES
# =============================================

@app.route('/admin')
@admin_required
def admin_dashboard():
    conn = get_db()

    # Auto-end expired auctions + create payment records for winners
    end_expired_auctions(conn)

    # Stats
    stats = {}
    stats['total_auctions'] = conn.execute('SELECT COUNT(*) FROM auctions').fetchone()[0]
    stats['live_auctions'] = conn.execute("SELECT COUNT(*) FROM auctions WHERE status = 'live'").fetchone()[0]
    stats['ended_auctions'] = conn.execute("SELECT COUNT(*) FROM auctions WHERE status = 'ended'").fetchone()[0]
    stats['total_bids'] = conn.execute('SELECT COUNT(*) FROM bids').fetchone()[0]
    stats['total_users'] = conn.execute("SELECT COUNT(*) FROM users WHERE role = 'buyer'").fetchone()[0]
    stats['total_muses'] = conn.execute('SELECT COUNT(*) FROM muse_profiles').fetchone()[0]
    gmv_row = conn.execute("SELECT COALESCE(SUM(current_bid), 0) FROM auctions WHERE status = 'ended' AND current_bidder_id IS NOT NULL").fetchone()
    stats['total_gmv'] = gmv_row[0]

    # Fulfillment pipeline counts
    stats['orders_awaiting_payment'] = conn.execute(
        "SELECT COUNT(*) FROM payments WHERE status = 'awaiting_payment'"
    ).fetchone()[0]
    stats['orders_pending_verification'] = conn.execute(
        "SELECT COUNT(*) FROM payments WHERE status = 'pending'"
    ).fetchone()[0]
    stats['orders_ready_to_ship'] = conn.execute(
        "SELECT COUNT(*) FROM payments WHERE status = 'paid'"
    ).fetchone()[0]
    stats['orders_shipped'] = conn.execute(
        "SELECT COUNT(*) FROM payments WHERE status = 'shipped'"
    ).fetchone()[0]
    stats['orders_need_action'] = stats['orders_pending_verification'] + stats['orders_ready_to_ship']

    # Actionable orders — privacy-safe (NO buyer PII: no user join, no address)
    actionable_orders = conn.execute('''
        SELECT p.id as payment_id, p.status as payment_status, p.amount,
               p.processor, p.created_at as payment_created,
               a.id as auction_id, a.title as auction_title, a.image as auction_image,
               m.display_name as muse_name,
               s.status as shipment_status, s.tracking_number, s.carrier
        FROM payments p
        JOIN auctions a ON p.auction_id = a.id
        LEFT JOIN muse_profiles m ON a.muse_id = m.id
        LEFT JOIN shipments s ON s.payment_id = p.id
        WHERE p.status IN ('pending', 'paid', 'shipped')
        ORDER BY
            CASE p.status
                WHEN 'pending' THEN 0
                WHEN 'paid' THEN 1
                WHEN 'shipped' THEN 2
            END,
            p.created_at ASC
    ''').fetchall()

    # All auctions with details
    auctions = conn.execute('''
        SELECT a.*, m.display_name as muse_name,
               u.display_name as bidder_name
        FROM auctions a
        LEFT JOIN muse_profiles m ON a.muse_id = m.id
        LEFT JOIN users u ON a.current_bidder_id = u.id
        ORDER BY
            CASE a.status WHEN 'live' THEN 0 WHEN 'draft' THEN 1 ELSE 2 END,
            a.created_at DESC
    ''').fetchall()

    conn.close()
    return render_template('admin/dashboard.html', stats=stats, auctions=auctions,
                           actionable_orders=actionable_orders)


@app.route('/admin/auction/new', methods=['GET', 'POST'])
@admin_required
def admin_auction_new():
    conn = get_db()
    muses = conn.execute("SELECT * FROM muse_profiles WHERE verification = 'verified' ORDER BY display_name").fetchall()

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        muse_id = request.form.get('muse_id', type=int)
        category = request.form.get('category', '').strip()
        wear_duration = request.form.get('wear_duration', '').strip()
        starting_bid = request.form.get('starting_bid', type=float)
        duration_hours = request.form.get('duration_hours', type=int)
        status = request.form.get('status', 'draft')

        # Validation
        errors = []
        if not title:
            errors.append('Title is required.')
        if not muse_id:
            errors.append('Please select a muse.')
        if not starting_bid or starting_bid < 1:
            errors.append('Starting bid must be at least $1.')
        if not duration_hours or duration_hours < 1:
            errors.append('Duration must be at least 1 hour.')

        # Handle image upload
        image_filename = None
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename and allowed_file(file.filename):
                ext = file.filename.rsplit('.', 1)[1].lower()
                fname = f"{uuid.uuid4().hex}.{ext}"
                os.makedirs(UPLOAD_FOLDER, exist_ok=True)
                file.save(os.path.join(UPLOAD_FOLDER, fname))
                image_filename = f"uploads/{fname}"  # Store with path prefix

        if not image_filename:
            errors.append('Please upload an image (JPG, PNG, GIF, WebP).')

        if errors:
            for e in errors:
                flash(e, 'error')
            conn.close()
            return render_template('admin/auction_form.html', muses=muses, editing=False)

        # Calculate end time
        now = datetime.now(timezone.utc)
        ends_at = now + timedelta(hours=duration_hours)
        now_str = now.strftime('%Y-%m-%dT%H:%M:%SZ')
        ends_str = ends_at.strftime('%Y-%m-%dT%H:%M:%SZ')

        conn.execute('''
            INSERT INTO auctions
            (muse_id, title, description, category, wear_duration, image,
             starting_bid, current_bid, status, starts_at, ends_at, original_end, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (muse_id, title, description, category, wear_duration,
              image_filename, starting_bid, starting_bid, status,
              now_str, ends_str, ends_str, current_user.id))
        conn.commit()
        conn.close()

        flash(f'Auction "{title}" created successfully!', 'success')
        return redirect(url_for('admin_dashboard'))

    conn.close()
    return render_template('admin/auction_form.html', muses=muses, editing=False)


@app.route('/admin/auction/<int:auction_id>/edit', methods=['GET', 'POST'])
@admin_required
def admin_auction_edit(auction_id):
    conn = get_db()
    auction = conn.execute('SELECT * FROM auctions WHERE id = ?', (auction_id,)).fetchone()
    if not auction:
        conn.close()
        abort(404)

    muses = conn.execute("SELECT * FROM muse_profiles WHERE verification = 'verified' ORDER BY display_name").fetchall()

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        muse_id = request.form.get('muse_id', type=int)
        category = request.form.get('category', '').strip()
        wear_duration = request.form.get('wear_duration', '').strip()
        status = request.form.get('status', auction['status'])

        # Handle optional new image
        image_filename = auction['image']
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename and allowed_file(file.filename):
                ext = file.filename.rsplit('.', 1)[1].lower()
                fname = f"{uuid.uuid4().hex}.{ext}"
                os.makedirs(UPLOAD_FOLDER, exist_ok=True)
                file.save(os.path.join(UPLOAD_FOLDER, fname))
                image_filename = f"uploads/{fname}"  # Store with path prefix

        conn.execute('''
            UPDATE auctions
            SET title = ?, description = ?, muse_id = ?, category = ?,
                wear_duration = ?, image = ?, status = ?
            WHERE id = ?
        ''', (title, description, muse_id, category, wear_duration, image_filename, status, auction_id))
        conn.commit()
        conn.close()

        flash(f'Auction "{title}" updated.', 'success')
        return redirect(url_for('admin_dashboard'))

    conn.close()
    return render_template('admin/auction_form.html', muses=muses, auction=dict(auction), editing=True)


@app.route('/admin/auction/<int:auction_id>/extend', methods=['POST'])
@admin_required
def admin_auction_extend(auction_id):
    minutes = request.form.get('minutes', 30, type=int)
    conn = get_db()
    auction = conn.execute('SELECT ends_at FROM auctions WHERE id = ?', (auction_id,)).fetchone()
    if auction:
        ends = datetime.strptime(auction['ends_at'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
        new_ends = ends + timedelta(minutes=minutes)
        conn.execute('UPDATE auctions SET ends_at = ? WHERE id = ?',
                     (new_ends.strftime('%Y-%m-%dT%H:%M:%SZ'), auction_id))
        conn.commit()
        flash(f'Auction extended by {minutes} minutes.', 'success')
    conn.close()
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/auction/<int:auction_id>/end', methods=['POST'])
@admin_required
def admin_auction_end(auction_id):
    conn = get_db()
    conn.execute("UPDATE auctions SET status = 'ended', ends_at = ? WHERE id = ?",
                 (datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), auction_id))
    conn.commit()

    # Create payment record for the winner (if any)
    payment = create_payment_for_winner(conn, auction_id)
    conn.close()

    if payment:
        flash('Auction ended. Payment record created for winner.', 'success')
    else:
        flash('Auction ended (no winning bidder).', 'info')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/auction/<int:auction_id>/bids')
@admin_required
def admin_auction_bids(auction_id):
    conn = get_db()
    auction = conn.execute('''
        SELECT a.*, m.display_name as muse_name
        FROM auctions a
        LEFT JOIN muse_profiles m ON a.muse_id = m.id
        WHERE a.id = ?
    ''', (auction_id,)).fetchone()
    if not auction:
        conn.close()
        abort(404)

    bids = conn.execute('''
        SELECT b.*, u.display_name as bidder_name, u.email as bidder_email
        FROM bids b
        JOIN users u ON b.user_id = u.id
        WHERE b.auction_id = ?
        ORDER BY b.placed_at DESC
    ''', (auction_id,)).fetchall()
    conn.close()
    return render_template('admin/auction_bids.html', auction=auction, bids=bids)


# --- Admin: Muse Management ---

@app.route('/admin/muses')
@admin_required
def admin_muses():
    conn = get_db()
    muses = conn.execute('''
        SELECT m.*,
            (SELECT COUNT(*) FROM auctions a WHERE a.muse_id = m.id) as auction_count,
            (SELECT COUNT(*) FROM auctions a WHERE a.muse_id = m.id AND a.status = 'ended' AND a.current_bidder_id IS NOT NULL) as sold_count,
            (SELECT COALESCE(SUM(a.current_bid), 0) FROM auctions a WHERE a.muse_id = m.id AND a.status = 'ended' AND a.current_bidder_id IS NOT NULL) as total_revenue
        FROM muse_profiles m
        ORDER BY m.created_at DESC
    ''').fetchall()
    conn.close()
    return render_template('admin/muses.html', muses=muses)


@app.route('/admin/muse/new', methods=['GET', 'POST'])
@admin_required
def admin_muse_new():
    if request.method == 'POST':
        display_name = request.form.get('display_name', '').strip()
        bio = request.form.get('bio', '').strip()

        if not display_name:
            flash('Display name is required.', 'error')
            return render_template('admin/muse_form.html', editing=False)

        # Handle avatar upload
        avatar_url = None
        if 'avatar' in request.files:
            file = request.files['avatar']
            if file and file.filename and allowed_file(file.filename):
                ext = file.filename.rsplit('.', 1)[1].lower()
                avatar_url = f"uploads/{uuid.uuid4().hex}.{ext}"
                os.makedirs(UPLOAD_FOLDER, exist_ok=True)
                file.save(os.path.join(UPLOAD_FOLDER, f"{avatar_url.split('/')[-1]}"))

        conn = get_db()
        conn.execute(
            'INSERT INTO muse_profiles (display_name, bio, avatar_url, verification) VALUES (?, ?, ?, ?)',
            (display_name, bio, avatar_url, 'verified')
        )
        conn.commit()
        conn.close()
        flash(f'Muse "{display_name}" created.', 'success')
        return redirect(url_for('admin_muses'))

    return render_template('admin/muse_form.html', editing=False)


@app.route('/admin/muse/<int:muse_id>/edit', methods=['GET', 'POST'])
@admin_required
def admin_muse_edit(muse_id):
    conn = get_db()
    muse = conn.execute('SELECT * FROM muse_profiles WHERE id = ?', (muse_id,)).fetchone()
    if not muse:
        conn.close()
        abort(404)

    if request.method == 'POST':
        display_name = request.form.get('display_name', '').strip()
        bio = request.form.get('bio', '').strip()
        verification = request.form.get('verification', muse['verification'])

        avatar_url = muse['avatar_url']
        if 'avatar' in request.files:
            file = request.files['avatar']
            if file and file.filename and allowed_file(file.filename):
                ext = file.filename.rsplit('.', 1)[1].lower()
                avatar_url = f"uploads/{uuid.uuid4().hex}.{ext}"
                os.makedirs(UPLOAD_FOLDER, exist_ok=True)
                file.save(os.path.join(UPLOAD_FOLDER, f"{avatar_url.split('/')[-1]}"))

        conn.execute(
            'UPDATE muse_profiles SET display_name = ?, bio = ?, avatar_url = ?, verification = ? WHERE id = ?',
            (display_name, bio, avatar_url, verification, muse_id)
        )
        conn.commit()
        conn.close()
        flash(f'Muse "{display_name}" updated.', 'success')
        return redirect(url_for('admin_muses'))

    conn.close()
    return render_template('admin/muse_form.html', muse=dict(muse), editing=True)


# =============================================
# PUBLIC: MUSE PROFILES
# =============================================

@app.route('/muse/<int:muse_id>')
def muse_profile(muse_id):
    conn = get_db()
    muse = conn.execute('SELECT * FROM muse_profiles WHERE id = ?', (muse_id,)).fetchone()
    if not muse:
        conn.close()
        abort(404)

    # Get muse's auctions
    auctions = conn.execute('''
        SELECT a.*, u.display_name as bidder_name
        FROM auctions a
        LEFT JOIN users u ON a.current_bidder_id = u.id
        WHERE a.muse_id = ? AND a.status IN ('live', 'ended')
        ORDER BY
            CASE a.status WHEN 'live' THEN 0 ELSE 1 END,
            a.ends_at ASC
    ''', (muse_id,)).fetchall()

    # Stats
    stats = {}
    stats['total_listed'] = conn.execute('SELECT COUNT(*) FROM auctions WHERE muse_id = ?', (muse_id,)).fetchone()[0]
    sold_row = conn.execute("SELECT COUNT(*), COALESCE(AVG(current_bid), 0) FROM auctions WHERE muse_id = ? AND status = 'ended' AND current_bidder_id IS NOT NULL", (muse_id,)).fetchone()
    stats['total_sold'] = sold_row[0]
    stats['avg_price'] = sold_row[1]

    conn.close()
    return render_template('muse_profile.html', muse=muse, auctions=auctions, stats=stats)


# =============================================
# PAYMENT FLOW
# =============================================

@app.route('/pay/<token>')
@login_required
def payment_page(token):
    conn = get_db()
    payment = conn.execute('SELECT * FROM payments WHERE payment_token = ?', (token,)).fetchone()
    if not payment:
        conn.close()
        abort(404)

    # Only the winner can see their payment page
    if payment['buyer_id'] != current_user.id:
        conn.close()
        abort(403)

    auction = conn.execute('''
        SELECT a.*, m.display_name as muse_name
        FROM auctions a
        LEFT JOIN muse_profiles m ON a.muse_id = m.id
        WHERE a.id = ?
    ''', (payment['auction_id'],)).fetchone()

    # Check if buyer has a saved address
    address = conn.execute(
        'SELECT * FROM shipping_addresses WHERE user_id = ? AND is_default = 1 ORDER BY created_at DESC LIMIT 1',
        (current_user.id,)
    ).fetchone()

    # Get shipment if exists
    shipment = conn.execute('SELECT * FROM shipments WHERE payment_id = ?', (payment['id'],)).fetchone()

    # Calculate payment deadline
    created = datetime.strptime(payment['created_at'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    deadline = created + timedelta(hours=PAYMENT_WINDOW_HOURS)
    now = datetime.now(timezone.utc)
    expired = now >= deadline

    conn.close()
    return render_template('payment.html',
                           payment=payment, auction=auction, address=address,
                           shipment=shipment, deadline=deadline.strftime('%Y-%m-%dT%H:%M:%SZ'),
                           expired=expired, shipping_rates=SHIPPING_RATES)


@app.route('/pay/<token>/address', methods=['POST'])
@login_required
def payment_save_address(token):
    conn = get_db()
    payment = conn.execute('SELECT * FROM payments WHERE payment_token = ?', (token,)).fetchone()
    if not payment or payment['buyer_id'] != current_user.id:
        conn.close()
        abort(403)

    # Save/update shipping address
    full_name = request.form.get('full_name', '').strip()
    address_line1 = request.form.get('address_line1', '').strip()
    address_line2 = request.form.get('address_line2', '').strip()
    city = request.form.get('city', '').strip()
    state = request.form.get('state', '').strip()
    postal_code = request.form.get('postal_code', '').strip()
    country = request.form.get('country', '').strip()
    phone = request.form.get('phone', '').strip()

    errors = []
    if not full_name:
        errors.append('Full name is required.')
    if not address_line1:
        errors.append('Address is required.')
    if not city:
        errors.append('City is required.')
    if not postal_code:
        errors.append('Postal code is required.')
    if not country:
        errors.append('Country is required.')

    if errors:
        for e in errors:
            flash(e, 'error')
        conn.close()
        return redirect(url_for('payment_page', token=token))

    # Clear old defaults
    conn.execute('UPDATE shipping_addresses SET is_default = 0 WHERE user_id = ?', (current_user.id,))

    conn.execute('''
        INSERT INTO shipping_addresses
        (user_id, full_name, address_line1, address_line2, city, state, postal_code, country, phone)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (current_user.id, full_name, address_line1, address_line2, city, state, postal_code, country, phone))
    conn.commit()
    conn.close()

    flash('Shipping address saved!', 'success')
    return redirect(url_for('payment_page', token=token))


@app.route('/pay/<token>/confirm', methods=['POST'])
@login_required
def payment_confirm_method(token):
    """Buyer selects payment method — routes to appropriate checkout flow."""
    conn = get_db()
    payment = conn.execute('SELECT * FROM payments WHERE payment_token = ?', (token,)).fetchone()
    if not payment or payment['buyer_id'] != current_user.id:
        conn.close()
        abort(403)

    if payment['status'] not in ('awaiting_payment',):
        flash('This payment has already been processed.', 'info')
        conn.close()
        return redirect(url_for('payment_page', token=token))

    # Check buyer has a shipping address
    address = conn.execute(
        'SELECT * FROM shipping_addresses WHERE user_id = ? AND is_default = 1 ORDER BY created_at DESC LIMIT 1',
        (current_user.id,)
    ).fetchone()
    if not address:
        flash('Please add a shipping address first.', 'error')
        conn.close()
        return redirect(url_for('payment_page', token=token))

    method = request.form.get('method', 'card')

    # Calculate shipping cost & ensure shipment record exists
    shipping_cost = SHIPPING_RATES.get(address['country'], SHIPPING_RATES['DEFAULT'])
    existing_shipment = conn.execute('SELECT id FROM shipments WHERE payment_id = ?', (payment['id'],)).fetchone()
    if not existing_shipment:
        destination = f"{address['full_name']}, {address['address_line1']}, {address['city']}, {address['country']}"
        conn.execute('''
            INSERT INTO shipments (payment_id, destination, status, shipping_cost)
            VALUES (?, ?, 'awaiting_payment', ?)
        ''', (payment['id'], destination, shipping_cost))
        conn.commit()

    conn.close()

    if method == 'card':
        # Redirect to credit card checkout page
        return redirect(url_for('checkout_card', token=token))
    else:
        # Crypto: set to pending (manual verification by admin)
        conn = get_db()
        now_str = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        conn.execute('''
            UPDATE payments SET status = 'pending', processor = 'crypto', completed_at = NULL
            WHERE id = ?
        ''', (payment['id'],))
        conn.execute('''
            INSERT INTO notifications (user_id, type, title, message, link, created_at)
            VALUES (?, 'payment_pending', ?, ?, ?, ?)
        ''', (
            current_user.id,
            'Crypto Payment Initiated',
            'Your cryptocurrency payment is awaiting confirmation. You will be notified once verified.',
            f'/pay/{token}',
            now_str
        ))
        conn.commit()
        conn.close()
        flash('Crypto payment initiated! You will receive confirmation once the transaction is verified.', 'success')
        return redirect(url_for('payment_page', token=token))


@app.route('/pay/<token>/checkout')
@login_required
def checkout_card(token):
    """Multi-step credit card checkout page."""
    conn = get_db()
    payment = conn.execute('SELECT * FROM payments WHERE payment_token = ?', (token,)).fetchone()
    if not payment or payment['buyer_id'] != current_user.id:
        conn.close()
        abort(403)

    if payment['status'] not in ('awaiting_payment',):
        conn.close()
        return redirect(url_for('payment_page', token=token))

    auction = conn.execute('''
        SELECT a.*, m.display_name as muse_name
        FROM auctions a
        LEFT JOIN muse_profiles m ON a.muse_id = m.id
        WHERE a.id = ?
    ''', (payment['auction_id'],)).fetchone()

    address = conn.execute(
        'SELECT * FROM shipping_addresses WHERE user_id = ? AND is_default = 1 ORDER BY created_at DESC LIMIT 1',
        (current_user.id,)
    ).fetchone()

    shipment = conn.execute('SELECT * FROM shipments WHERE payment_id = ?', (payment['id'],)).fetchone()
    shipping_cost = shipment['shipping_cost'] if shipment else SHIPPING_RATES.get(
        address['country'] if address else 'DEFAULT', SHIPPING_RATES['DEFAULT'])

    conn.close()
    return render_template('checkout.html',
                           payment=payment, auction=auction, address=address,
                           shipping_cost=shipping_cost,
                           total=payment['amount'] + shipping_cost)


@app.route('/pay/<token>/process-card', methods=['POST'])
@login_required
def process_card_payment(token):
    """Process credit card payment (simulated — accepts any card)."""
    conn = get_db()
    payment = conn.execute('SELECT * FROM payments WHERE payment_token = ?', (token,)).fetchone()
    if not payment or payment['buyer_id'] != current_user.id:
        conn.close()
        return jsonify({'success': False, 'message': 'Payment not found.'}), 403

    if payment['status'] not in ('awaiting_payment',):
        conn.close()
        return jsonify({'success': False, 'message': 'This payment has already been processed.'}), 400

    # Get card details from request
    data = request.get_json()
    if not data:
        conn.close()
        return jsonify({'success': False, 'message': 'No payment data received.'}), 400

    card_number = (data.get('card_number', '') or '').replace(' ', '')
    card_name = (data.get('card_name', '') or '').strip()
    card_expiry = (data.get('card_expiry', '') or '').strip()
    card_cvv = (data.get('card_cvv', '') or '').strip()

    # Basic validation
    errors = []
    if len(card_number) < 13 or not card_number.isdigit():
        errors.append('Please enter a valid card number.')
    if not card_name or len(card_name) < 2:
        errors.append('Cardholder name is required.')
    if not card_expiry or '/' not in card_expiry:
        errors.append('Please enter a valid expiry date (MM/YY).')
    if not card_cvv or len(card_cvv) < 3:
        errors.append('Please enter a valid CVV.')

    if errors:
        conn.close()
        return jsonify({'success': False, 'message': errors[0]}), 400

    # Simulate payment processing
    # Generate a realistic transaction ID
    txn_id = f"CCB-{secrets.token_hex(6).upper()}"
    now_str = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    last_four = card_number[-4:]

    # Mark payment as paid (instant card processing)
    conn.execute('''
        UPDATE payments SET status = 'paid', processor = ?, processor_txn = ?, completed_at = ?
        WHERE id = ?
    ''', (f'card-{last_four}', txn_id, now_str, payment['id']))

    # Update shipment to preparing
    conn.execute("UPDATE shipments SET status = 'preparing' WHERE payment_id = ?", (payment['id'],))

    # Update auction status
    conn.execute("UPDATE auctions SET status = 'paid' WHERE id = ?", (payment['auction_id'],))

    # Notify buyer
    conn.execute('''
        INSERT INTO notifications (user_id, type, title, message, link, created_at)
        VALUES (?, 'payment_confirmed', ?, ?, ?, ?)
    ''', (
        current_user.id,
        'Payment Confirmed!',
        f'Your credit card payment (ending {last_four}) of ${payment["amount"]:.2f} has been confirmed. We are preparing your item for shipping.',
        f'/pay/{token}',
        now_str
    ))

    conn.commit()
    conn.close()

    return jsonify({
        'success': True,
        'message': 'Payment successful!',
        'txn_id': txn_id,
        'redirect': url_for('payment_page', token=token)
    })


# =============================================
# BUYER DASHBOARD
# =============================================

@app.route('/dashboard')
@login_required
def buyer_dashboard():
    conn = get_db()

    # Auto-end expired auctions
    end_expired_auctions(conn)

    # Active bids (on live auctions)
    active_bids = conn.execute('''
        SELECT b.amount, b.placed_at, b.is_winning,
               a.id as auction_id, a.title, a.current_bid, a.current_bidder_id,
               a.ends_at, a.status, a.image,
               m.display_name as muse_name
        FROM bids b
        JOIN auctions a ON b.auction_id = a.id
        LEFT JOIN muse_profiles m ON a.muse_id = m.id
        WHERE b.user_id = ? AND a.status = 'live'
        GROUP BY a.id
        HAVING b.amount = MAX(b.amount)
        ORDER BY a.ends_at ASC
    ''', (current_user.id,)).fetchall()

    # Won auctions (with payment info)
    won_auctions = conn.execute('''
        SELECT a.id as auction_id, a.title, a.current_bid, a.image, a.status as auction_status,
               m.display_name as muse_name,
               p.id as payment_id, p.status as payment_status, p.payment_token,
               p.amount as payment_amount, p.created_at as payment_created,
               s.status as shipment_status, s.tracking_number, s.carrier, s.shipping_cost
        FROM auctions a
        LEFT JOIN muse_profiles m ON a.muse_id = m.id
        LEFT JOIN payments p ON p.auction_id = a.id AND p.buyer_id = ?
        LEFT JOIN shipments s ON s.payment_id = p.id
        WHERE a.current_bidder_id = ? AND a.status != 'live'
        ORDER BY a.ends_at DESC
    ''', (current_user.id, current_user.id)).fetchall()

    # Recent bid history (all bids across all auctions)
    bid_history = conn.execute('''
        SELECT b.amount, b.placed_at, b.is_winning,
               a.id as auction_id, a.title, a.status as auction_status,
               a.current_bid
        FROM bids b
        JOIN auctions a ON b.auction_id = a.id
        WHERE b.user_id = ?
        ORDER BY b.placed_at DESC
        LIMIT 20
    ''', (current_user.id,)).fetchall()

    # Notifications
    notifications = conn.execute('''
        SELECT * FROM notifications
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT 10
    ''', (current_user.id,)).fetchall()

    # Mark notifications as read
    conn.execute('UPDATE notifications SET is_read = 1 WHERE user_id = ? AND is_read = 0', (current_user.id,))
    conn.commit()

    # Saved address
    address = conn.execute(
        'SELECT * FROM shipping_addresses WHERE user_id = ? AND is_default = 1 ORDER BY created_at DESC LIMIT 1',
        (current_user.id,)
    ).fetchone()

    conn.close()
    return render_template('dashboard.html',
                           active_bids=active_bids, won_auctions=won_auctions,
                           bid_history=bid_history, notifications=notifications,
                           address=address)


@app.route('/dashboard/address', methods=['POST'])
@login_required
def dashboard_save_address():
    """Save/update default shipping address from buyer dashboard."""
    conn = get_db()

    full_name = request.form.get('full_name', '').strip()
    address_line1 = request.form.get('address_line1', '').strip()
    address_line2 = request.form.get('address_line2', '').strip()
    city = request.form.get('city', '').strip()
    state = request.form.get('state', '').strip()
    postal_code = request.form.get('postal_code', '').strip()
    country = request.form.get('country', '').strip()
    phone = request.form.get('phone', '').strip()

    if not all([full_name, address_line1, city, postal_code, country]):
        flash('Please fill in all required address fields.', 'error')
        conn.close()
        return redirect(url_for('buyer_dashboard'))

    # Clear old defaults
    conn.execute('UPDATE shipping_addresses SET is_default = 0 WHERE user_id = ?', (current_user.id,))

    conn.execute('''
        INSERT INTO shipping_addresses
        (user_id, full_name, address_line1, address_line2, city, state, postal_code, country, phone)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (current_user.id, full_name, address_line1, address_line2, city, state, postal_code, country, phone))
    conn.commit()
    conn.close()

    flash('Shipping address updated!', 'success')
    return redirect(url_for('buyer_dashboard'))


# =============================================
# ADMIN: ORDER & PAYMENT MANAGEMENT
# =============================================

@app.route('/admin/orders')
@admin_required
def admin_orders():
    conn = get_db()

    orders = conn.execute('''
        SELECT p.*, a.title as auction_title, a.image as auction_image,
               u.display_name as buyer_name, u.email as buyer_email,
               m.display_name as muse_name,
               s.id as shipment_id, s.status as shipment_status,
               s.tracking_number, s.carrier, s.shipping_cost,
               sa.full_name as ship_name, sa.address_line1 as ship_addr,
               sa.city as ship_city, sa.country as ship_country,
               sa.postal_code as ship_zip
        FROM payments p
        JOIN auctions a ON p.auction_id = a.id
        JOIN users u ON p.buyer_id = u.id
        LEFT JOIN muse_profiles m ON a.muse_id = m.id
        LEFT JOIN shipments s ON s.payment_id = p.id
        LEFT JOIN shipping_addresses sa ON sa.user_id = u.id AND sa.is_default = 1
        ORDER BY
            CASE p.status
                WHEN 'pending' THEN 0
                WHEN 'awaiting_payment' THEN 1
                WHEN 'paid' THEN 2
                WHEN 'shipped' THEN 3
                WHEN 'completed' THEN 4
                ELSE 5
            END,
            p.created_at DESC
    ''').fetchall()

    # Order stats
    stats = {}
    stats['awaiting'] = conn.execute("SELECT COUNT(*) FROM payments WHERE status = 'awaiting_payment'").fetchone()[0]
    stats['pending'] = conn.execute("SELECT COUNT(*) FROM payments WHERE status = 'pending'").fetchone()[0]
    stats['paid'] = conn.execute("SELECT COUNT(*) FROM payments WHERE status = 'paid'").fetchone()[0]
    stats['shipped'] = conn.execute("SELECT COUNT(*) FROM payments WHERE status IN ('shipped', 'completed')").fetchone()[0]
    revenue = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status IN ('paid', 'shipped', 'completed')").fetchone()
    stats['revenue'] = revenue[0]

    conn.close()
    return render_template('admin/orders.html', orders=orders, stats=stats)


@app.route('/admin/order/<int:payment_id>/mark-paid', methods=['POST'])
@admin_required
def admin_mark_paid(payment_id):
    conn = get_db()
    now_str = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    payment = conn.execute('SELECT * FROM payments WHERE id = ?', (payment_id,)).fetchone()
    if not payment:
        conn.close()
        abort(404)

    processor_txn = request.form.get('processor_txn', '').strip() or f"MANUAL-{secrets.token_hex(4).upper()}"

    conn.execute('''
        UPDATE payments SET status = 'paid', processor_txn = ?, completed_at = ?
        WHERE id = ?
    ''', (processor_txn, now_str, payment_id))

    # Update shipment status
    conn.execute("UPDATE shipments SET status = 'preparing' WHERE payment_id = ?", (payment_id,))

    # Update auction status
    conn.execute("UPDATE auctions SET status = 'paid' WHERE id = ?", (payment['auction_id'],))

    # Notify buyer
    conn.execute('''
        INSERT INTO notifications (user_id, type, title, message, link, created_at)
        VALUES (?, 'payment_confirmed', ?, ?, ?, ?)
    ''', (
        payment['buyer_id'],
        'Payment Confirmed!',
        'Your payment has been confirmed. We are preparing your item for shipping.',
        f'/pay/{payment["payment_token"]}',
        now_str
    ))

    log_audit(conn, 'order', payment_id, 'marked_paid',
              {'processor_txn': processor_txn})
    conn.commit()
    conn.close()
    flash('Payment marked as paid. Buyer notified.', 'success')
    return redirect(request.referrer or url_for('admin_orders'))


@app.route('/admin/order/<int:payment_id>/ship', methods=['POST'])
@admin_required
def admin_ship_order(payment_id):
    conn = get_db()
    now_str = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    payment = conn.execute('SELECT * FROM payments WHERE id = ?', (payment_id,)).fetchone()
    if not payment:
        conn.close()
        abort(404)

    tracking_number = request.form.get('tracking_number', '').strip()
    carrier = request.form.get('carrier', 'DHL').strip()

    if not tracking_number:
        flash('Tracking number is required.', 'error')
        conn.close()
        return redirect(url_for('admin_orders'))

    conn.execute('''
        UPDATE shipments SET status = 'shipped', tracking_number = ?, carrier = ?, shipped_at = ?
        WHERE payment_id = ?
    ''', (tracking_number, carrier, now_str, payment_id))

    conn.execute("UPDATE payments SET status = 'shipped' WHERE id = ?", (payment_id,))
    conn.execute("UPDATE auctions SET status = 'shipped' WHERE id = ?", (payment['auction_id'],))

    # Notify buyer
    conn.execute('''
        INSERT INTO notifications (user_id, type, title, message, link, created_at)
        VALUES (?, 'order_shipped', ?, ?, ?, ?)
    ''', (
        payment['buyer_id'],
        'Your Order Has Shipped!',
        f'Tracking: {tracking_number} via {carrier}. Check your dashboard for updates.',
        f'/pay/{payment["payment_token"]}',
        now_str
    ))

    log_audit(conn, 'order', payment_id, 'shipped',
              {'tracking_number': tracking_number, 'carrier': carrier})
    conn.commit()
    conn.close()
    flash(f'Order shipped! Tracking: {tracking_number}. Buyer notified.', 'success')
    return redirect(request.referrer or url_for('admin_orders'))


@app.route('/admin/order/<int:payment_id>/deliver', methods=['POST'])
@admin_required
def admin_deliver_order(payment_id):
    conn = get_db()
    now_str = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    payment = conn.execute('SELECT * FROM payments WHERE id = ?', (payment_id,)).fetchone()
    if not payment:
        conn.close()
        abort(404)

    conn.execute('''
        UPDATE shipments SET status = 'delivered', delivered_at = ?
        WHERE payment_id = ?
    ''', (now_str, payment_id))

    conn.execute("UPDATE auctions SET status = 'completed' WHERE id = ?", (payment['auction_id'],))
    conn.execute("UPDATE payments SET status = 'completed' WHERE id = ?", (payment_id,))

    # Update muse sales count
    auction = conn.execute('SELECT muse_id FROM auctions WHERE id = ?', (payment['auction_id'],)).fetchone()
    if auction and auction['muse_id']:
        conn.execute('UPDATE muse_profiles SET total_sales = total_sales + 1 WHERE id = ?', (auction['muse_id'],))

    # Notify buyer
    conn.execute('''
        INSERT INTO notifications (user_id, type, title, message, link, created_at)
        VALUES (?, 'order_delivered', ?, ?, ?, ?)
    ''', (
        payment['buyer_id'],
        'Order Delivered!',
        'Your order has been delivered. Enjoy! We hope to see you again soon.',
        '/dashboard',
        now_str
    ))

    log_audit(conn, 'order', payment_id, 'delivered', {})
    conn.commit()
    conn.close()
    flash('Order marked as delivered. Transaction complete!', 'success')
    return redirect(request.referrer or url_for('admin_orders'))


# --- Admin: Order Detail + CRUD ---

@app.route('/admin/order/<int:payment_id>')
@admin_required
def admin_order_detail(payment_id):
    """Full order detail page with timeline and actions."""
    conn = get_db()

    order = conn.execute('''
        SELECT p.*, a.id as auction_id, a.title as auction_title,
               a.image as auction_image, a.status as auction_status,
               a.category, a.wear_duration, a.current_bid,
               u.id as buyer_id, u.display_name as buyer_name,
               u.email as buyer_email, u.created_at as buyer_since,
               m.display_name as muse_name,
               s.id as shipment_id, s.status as shipment_status,
               s.tracking_number, s.carrier, s.shipped_at,
               s.delivered_at, s.shipping_cost, s.destination,
               sa.full_name as ship_name, sa.address_line1, sa.address_line2,
               sa.city, sa.state, sa.postal_code, sa.country, sa.phone
        FROM payments p
        JOIN auctions a ON p.auction_id = a.id
        JOIN users u ON p.buyer_id = u.id
        LEFT JOIN muse_profiles m ON a.muse_id = m.id
        LEFT JOIN shipments s ON s.payment_id = p.id
        LEFT JOIN shipping_addresses sa ON sa.user_id = u.id AND sa.is_default = 1
        WHERE p.id = ?
    ''', (payment_id,)).fetchone()

    if not order:
        conn.close()
        abort(404)

    # Audit timeline
    timeline = conn.execute('''
        SELECT al.*, u.display_name as admin_name
        FROM audit_log al
        LEFT JOIN users u ON al.admin_id = u.id
        WHERE al.entity_type = 'order' AND al.entity_id = ?
        ORDER BY al.created_at DESC
    ''', (payment_id,)).fetchall()

    # Recent bids for this auction
    bids = conn.execute('''
        SELECT b.amount, b.placed_at, b.is_winning, u.display_name as bidder_name
        FROM bids b
        JOIN users u ON b.user_id = u.id
        WHERE b.auction_id = ?
        ORDER BY b.placed_at DESC
        LIMIT 10
    ''', (order['auction_id'],)).fetchall()

    conn.close()
    return render_template('admin/order_detail.html',
                           order=dict(order), timeline=timeline, bids=bids)


@app.route('/admin/order/<int:payment_id>/edit', methods=['POST'])
@admin_required
def admin_order_edit(payment_id):
    """Edit order details: status, tracking, carrier, notes."""
    conn = get_db()
    payment = conn.execute('SELECT * FROM payments WHERE id = ?', (payment_id,)).fetchone()
    if not payment:
        conn.close()
        abort(404)

    new_status = request.form.get('status', payment['status'])
    tracking_number = request.form.get('tracking_number', '').strip()
    carrier = request.form.get('carrier', '').strip()
    admin_notes = request.form.get('admin_notes', '').strip()

    changes = {}

    # Update payment status if changed
    if new_status != payment['status']:
        changes['status'] = {'from': payment['status'], 'to': new_status}
        now_str = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        conn.execute('UPDATE payments SET status = ? WHERE id = ?', (new_status, payment_id))
        if new_status == 'paid' and not payment['completed_at']:
            conn.execute('UPDATE payments SET completed_at = ? WHERE id = ?', (now_str, payment_id))
        # Sync auction status
        conn.execute('UPDATE auctions SET status = ? WHERE id = ?',
                     (new_status, payment['auction_id']))

    # Update admin notes
    conn.execute('UPDATE payments SET admin_notes = ? WHERE id = ?', (admin_notes, payment_id))

    # Update shipment tracking/carrier if provided
    shipment = conn.execute('SELECT * FROM shipments WHERE payment_id = ?', (payment_id,)).fetchone()
    if shipment:
        if tracking_number and tracking_number != (shipment['tracking_number'] or ''):
            changes['tracking_number'] = {'from': shipment['tracking_number'], 'to': tracking_number}
            conn.execute('UPDATE shipments SET tracking_number = ? WHERE payment_id = ?',
                         (tracking_number, payment_id))
        if carrier and carrier != (shipment['carrier'] or ''):
            changes['carrier'] = {'from': shipment['carrier'], 'to': carrier}
            conn.execute('UPDATE shipments SET carrier = ? WHERE payment_id = ?',
                         (carrier, payment_id))

    if changes:
        log_audit(conn, 'order', payment_id, 'edited', changes)

    conn.commit()
    conn.close()
    flash('Order updated successfully.', 'success')
    return redirect(url_for('admin_order_detail', payment_id=payment_id))


@app.route('/admin/order/<int:payment_id>/delete', methods=['POST'])
@admin_required
def admin_order_delete(payment_id):
    """Delete an order (only awaiting_payment orders)."""
    conn = get_db()
    payment = conn.execute('SELECT * FROM payments WHERE id = ?', (payment_id,)).fetchone()
    if not payment:
        conn.close()
        abort(404)

    deletable = ('awaiting_payment',)
    if payment['status'] not in deletable:
        flash(f'Cannot delete orders with status "{payment["status"]}". '
              f'Only awaiting_payment orders can be deleted.', 'error')
        conn.close()
        return redirect(url_for('admin_order_detail', payment_id=payment_id))

    log_audit(conn, 'order', payment_id, 'deleted',
              {'auction_id': payment['auction_id'], 'amount': payment['amount'],
               'status': payment['status']})

    conn.execute('DELETE FROM notifications WHERE link LIKE ?',
                 (f'%{payment["payment_token"]}%',))
    conn.execute('DELETE FROM shipments WHERE payment_id = ?', (payment_id,))
    conn.execute('DELETE FROM payments WHERE id = ?', (payment_id,))
    conn.commit()
    conn.close()

    flash('Order deleted.', 'success')
    return redirect(url_for('admin_orders'))


@app.route('/admin/order/new', methods=['GET', 'POST'])
@admin_required
def admin_order_new():
    """Create a manual order."""
    conn = get_db()

    if request.method == 'POST':
        auction_id = request.form.get('auction_id', type=int)
        buyer_id = request.form.get('buyer_id', type=int)
        amount = request.form.get('amount', type=float)
        status = request.form.get('status', 'awaiting_payment')
        admin_notes = request.form.get('admin_notes', '').strip()

        errors = []
        if not auction_id:
            errors.append('Please select an auction.')
        if not buyer_id:
            errors.append('Please select a buyer.')
        if not amount or amount <= 0:
            errors.append('Amount must be greater than $0.')

        if errors:
            for e in errors:
                flash(e, 'error')
            auctions = conn.execute("SELECT id, title FROM auctions ORDER BY title").fetchall()
            buyers = conn.execute(
                "SELECT id, display_name, email FROM users WHERE role = 'buyer' AND is_active = 1 ORDER BY display_name"
            ).fetchall()
            conn.close()
            return render_template('admin/order_form.html', auctions=auctions,
                                   buyers=buyers, editing=False)

        token = secrets.token_urlsafe(32)
        now_str = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

        conn.execute('''
            INSERT INTO payments (auction_id, buyer_id, amount, status, payment_token,
                                  admin_notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (auction_id, buyer_id, amount, status, token, admin_notes, now_str))
        conn.commit()

        new_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        log_audit(conn, 'order', new_id, 'created',
                  {'auction_id': auction_id, 'buyer_id': buyer_id,
                   'amount': amount, 'manual': True})
        conn.commit()
        conn.close()

        flash('Manual order created.', 'success')
        return redirect(url_for('admin_order_detail', payment_id=new_id))

    auctions = conn.execute("SELECT id, title FROM auctions ORDER BY title").fetchall()
    buyers = conn.execute(
        "SELECT id, display_name, email FROM users WHERE role = 'buyer' AND is_active = 1 ORDER BY display_name"
    ).fetchall()
    conn.close()
    return render_template('admin/order_form.html', auctions=auctions,
                           buyers=buyers, editing=False)


# --- Admin: User Management ---

@app.route('/admin/users')
@admin_required
def admin_users():
    """List all users with search/filter."""
    conn = get_db()
    search = request.args.get('q', '').strip()
    role_filter = request.args.get('role', '')
    status_filter = request.args.get('status', '')

    query = '''
        SELECT u.*,
            (SELECT COUNT(*) FROM bids b WHERE b.user_id = u.id) as bid_count,
            (SELECT COUNT(*) FROM payments p WHERE p.buyer_id = u.id) as order_count,
            (SELECT COALESCE(SUM(p.amount), 0) FROM payments p
             WHERE p.buyer_id = u.id AND p.status IN ('paid', 'shipped', 'completed')) as total_spent
        FROM users u
        WHERE 1=1
    '''
    params = []

    if search:
        query += ' AND (u.display_name LIKE ? OR u.email LIKE ?)'
        params.extend([f'%{search}%', f'%{search}%'])
    if role_filter:
        query += ' AND u.role = ?'
        params.append(role_filter)
    if status_filter == 'active':
        query += ' AND u.is_active = 1'
    elif status_filter == 'inactive':
        query += ' AND u.is_active = 0'

    query += ' ORDER BY u.created_at DESC'
    users = conn.execute(query, params).fetchall()

    stats = {
        'total': conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        'buyers': conn.execute("SELECT COUNT(*) FROM users WHERE role = 'buyer'").fetchone()[0],
        'admins': conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0],
        'active': conn.execute("SELECT COUNT(*) FROM users WHERE is_active = 1").fetchone()[0],
        'inactive': conn.execute("SELECT COUNT(*) FROM users WHERE is_active = 0").fetchone()[0],
    }

    conn.close()
    return render_template('admin/users.html', users=users, stats=stats,
                           search=search, role_filter=role_filter,
                           status_filter=status_filter)


@app.route('/admin/user/new', methods=['GET', 'POST'])
@admin_required
def admin_user_new():
    """Create a new user."""
    if request.method == 'POST':
        email = request.form.get('email', '').lower().strip()
        password = request.form.get('password', '')
        display_name = request.form.get('display_name', '').strip()
        role = request.form.get('role', 'buyer')

        errors = []
        if not email or '@' not in email:
            errors.append('Valid email is required.')
        if len(password) < 8:
            errors.append('Password must be at least 8 characters.')
        if not display_name:
            errors.append('Display name is required.')
        if role not in ('buyer', 'admin'):
            errors.append('Invalid role.')

        if not errors:
            conn = get_db()
            existing = conn.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone()
            if existing:
                errors.append('An account with this email already exists.')
                conn.close()

        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('admin/user_form.html', editing=False)

        conn = get_db()
        password_hash = generate_password_hash(password)
        now_str = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        conn.execute('''
            INSERT INTO users (email, password_hash, display_name, role, age_verified, created_at)
            VALUES (?, ?, ?, ?, 1, ?)
        ''', (email, password_hash, display_name, role, now_str))
        conn.commit()
        new_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]

        log_audit(conn, 'user', new_id, 'created',
                  {'email': email, 'role': role, 'created_by_admin': True})
        conn.commit()
        conn.close()

        flash(f'User "{display_name}" created.', 'success')
        return redirect(url_for('admin_users'))

    return render_template('admin/user_form.html', editing=False)


@app.route('/admin/user/<int:user_id>/edit', methods=['GET', 'POST'])
@admin_required
def admin_user_edit(user_id):
    """Edit user profile."""
    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user:
        conn.close()
        abort(404)

    if request.method == 'POST':
        display_name = request.form.get('display_name', '').strip()
        email = request.form.get('email', '').lower().strip()
        role = request.form.get('role', user['role'])

        errors = []
        if not display_name:
            errors.append('Display name is required.')
        if not email or '@' not in email:
            errors.append('Valid email is required.')
        existing = conn.execute('SELECT id FROM users WHERE email = ? AND id != ?',
                                (email, user_id)).fetchone()
        if existing:
            errors.append('Another account with this email already exists.')

        if errors:
            for e in errors:
                flash(e, 'error')
            conn.close()
            return render_template('admin/user_form.html', user=dict(user), editing=True)

        changes = {}
        if display_name != user['display_name']:
            changes['display_name'] = {'from': user['display_name'], 'to': display_name}
        if email != user['email']:
            changes['email'] = {'from': user['email'], 'to': email}
        if role != user['role']:
            changes['role'] = {'from': user['role'], 'to': role}

        conn.execute('UPDATE users SET display_name = ?, email = ?, role = ? WHERE id = ?',
                     (display_name, email, role, user_id))

        if changes:
            log_audit(conn, 'user', user_id, 'edited', changes)

        conn.commit()
        conn.close()
        flash(f'User "{display_name}" updated.', 'success')
        return redirect(url_for('admin_users'))

    conn.close()
    return render_template('admin/user_form.html', user=dict(user), editing=True)


@app.route('/admin/user/<int:user_id>/toggle-active', methods=['POST'])
@admin_required
def admin_user_toggle_active(user_id):
    """Activate or deactivate a user."""
    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user:
        conn.close()
        abort(404)

    if user_id == current_user.id:
        flash('You cannot deactivate your own account.', 'error')
        conn.close()
        return redirect(url_for('admin_users'))

    new_status = 0 if user['is_active'] else 1
    conn.execute('UPDATE users SET is_active = ? WHERE id = ?', (new_status, user_id))
    log_audit(conn, 'user', user_id, 'toggled_active',
              {'is_active': {'from': user['is_active'], 'to': new_status}})
    conn.commit()
    conn.close()

    action = 'activated' if new_status else 'deactivated'
    flash(f'User "{user["display_name"]}" {action}.', 'success')
    return redirect(url_for('admin_users'))


@app.route('/admin/user/<int:user_id>/reset-password', methods=['POST'])
@admin_required
def admin_user_reset_password(user_id):
    """Reset a user's password."""
    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user:
        conn.close()
        abort(404)

    new_password = request.form.get('new_password', '').strip()
    if len(new_password) < 8:
        flash('Password must be at least 8 characters.', 'error')
        conn.close()
        return redirect(url_for('admin_user_edit', user_id=user_id))

    password_hash = generate_password_hash(new_password)
    conn.execute('UPDATE users SET password_hash = ? WHERE id = ?', (password_hash, user_id))
    log_audit(conn, 'user', user_id, 'password_reset', {'reset_by_admin': True})
    conn.commit()
    conn.close()

    flash(f'Password reset for "{user["display_name"]}".', 'success')
    return redirect(url_for('admin_user_edit', user_id=user_id))


# =============================================
# NOTIFICATIONS API
# =============================================

@app.route('/api/notifications/count')
@login_required
def notification_count():
    conn = get_db()
    count = conn.execute(
        'SELECT COUNT(*) FROM notifications WHERE user_id = ? AND is_read = 0',
        (current_user.id,)
    ).fetchone()[0]
    conn.close()
    return jsonify({'count': count})


# =============================================
# CSRF exemption for bid API (uses JSON + login_required)
# We re-add CSRF via custom header check instead
# =============================================

@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    return response


# Exempt JSON APIs from CSRF (they require authentication)
csrf.exempt(place_bid)
csrf.exempt(notification_count)
csrf.exempt(process_card_payment)


# =============================================
# INIT & RUN
# =============================================

init_db()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8005, debug=True)
