from __future__ import annotations

import os
import base64
import re
import json
import hashlib
import time
import csv
import io
import math
import secrets
import shutil
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
import zipfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import urlopen
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

from flask import Flask, abort, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from cryptography.fernet import Fernet, InvalidToken


BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / 'instance'
DEFAULT_DATABASE_PATH = INSTANCE_DIR / 'occ_assist.db'
SUPERADMIN_EMAIL = os.environ.get('OCC_ASSIST_SUPERADMIN_EMAIL', 'michael.dodsworth@gonorthwest.co.uk')
SUPERADMIN_PASSWORD = os.environ.get('OCC_ASSIST_SUPERADMIN_PASSWORD')
PERMISSIONS = {
    'live_updates': 'Daily overview',
    'tracking': 'Tracking',
    'service_overview': 'Service overview',
    'contacts': 'Contacts',
    'driving_hours': 'Driving hours',
    'admin_privileges': 'Admin privileges',
}
PAGE_PERMISSIONS = {
    'daily_overview': 'live_updates',
    'tracking': 'tracking',
    'service_overview': 'service_overview',
    'contacts_page': 'contacts',
    'driving_hours': 'driving_hours',
    'admin_page': 'admin_privileges',
}
SNAPSHOT_RETENTION_DAYS = 14
SNAPSHOT_RETENTION_SECONDS = SNAPSHOT_RETENTION_DAYS * 24 * 60 * 60
GTFS_DIR = INSTANCE_DIR / 'gtfs'
GTFS_UPLOAD_PATH = GTFS_DIR / 'latest-gtfs.zip'
GTFS_EXTRACT_DIR = GTFS_DIR / 'extracted'
GTFS_CACHE_PATH = GTFS_DIR / 'routes-cache.json'
GTFS_MANUAL_LOCK_PATH = GTFS_DIR / 'manual-lock.json'
GTFS_MAX_UPLOAD_BYTES = int(os.environ.get('OCC_ASSIST_GTFS_MAX_UPLOAD_BYTES', '60000000'))
GTFS_ALLOWED_AGENCY_ID = str(os.environ.get('OCC_ASSIST_GTFS_ALLOWED_AGENCY_ID', 'OP11122')).strip()
GTFS_MAX_FALLBACK_PATTERNS_PER_ROUTE = int(os.environ.get('OCC_ASSIST_GTFS_MAX_FALLBACK_PATTERNS_PER_ROUTE', '4'))
GTFS_ALLOWED_ROUTE_PREFIXES = [
    value.strip().upper()
    for value in str(os.environ.get('OCC_ASSIST_GTFS_ALLOWED_ROUTE_PREFIXES', '')).split(',')
    if value.strip()
]
DATA_HEALTH_STATUS_PATH = INSTANCE_DIR / 'data-health-status.json'
AUTO_DATA_CHECK_INTERVAL_SECONDS = int(os.environ.get('OCC_ASSIST_AUTO_DATA_CHECK_INTERVAL_SECONDS', '900'))
GTFS_AUTO_DOWNLOAD_URL = str(os.environ.get('OCC_ASSIST_GTFS_AUTO_DOWNLOAD_URL', '')).strip()
GTFS_AUTO_DOWNLOAD_TIMEOUT_SECONDS = int(os.environ.get('OCC_ASSIST_GTFS_AUTO_DOWNLOAD_TIMEOUT_SECONDS', '45'))
ROADWORKS_DIR = INSTANCE_DIR / 'roadworks'
ROADWORKS_CSV_PATH = ROADWORKS_DIR / 'latest-roadworks.csv'
ROADWORKS_CACHE_PATH = ROADWORKS_DIR / 'roadworks-cache.json'
ROADWORKS_MAX_UPLOAD_BYTES = int(os.environ.get('OCC_ASSIST_ROADWORKS_MAX_UPLOAD_BYTES', '5000000'))
ROADWORKS_FIELD_ALIASES = {
    'reference': ('reference', 'id', 'roadworks_id', 'ref', 'usrn'),
    'title': ('title', 'description', 'location', 'name', 'street', 'location_description'),
    'latitude': ('latitude', 'lat'),
    'longitude': ('longitude', 'lon', 'lng', 'long'),
    'severity': ('severity', 'rag', 'rag_rating', 'priority', 'severity_level'),
    'status': ('status', 'state', 'works_status'),
    'start_date': ('start_date', 'start', 'startdate', 'from', 'proposed_start_date'),
    'end_date': ('end_date', 'end', 'enddate', 'to', 'proposed_end_date'),
    'promoter': ('promoter', 'organisation', 'organization', 'company', 'promoter_organisation'),
    'impact': ('impact', 'details', 'notes', 'comments', 'traffic_management'),
}
BODS_TIMETABLE_NOC = str(os.environ.get('OCC_ASSIST_BODS_TIMETABLE_NOC', 'GONW')).strip().upper()
BODS_TIMETABLE_LIMIT = int(os.environ.get('OCC_ASSIST_BODS_TIMETABLE_LIMIT', '100'))
TRANSXCHANGE_MAX_FILES = int(os.environ.get('OCC_ASSIST_TRANSXCHANGE_MAX_FILES', '180'))
TRANSXCHANGE_MAX_TRIPS = int(os.environ.get('OCC_ASSIST_TRANSXCHANGE_MAX_TRIPS', '4000'))
BODS_VEHICLE_CACHE_SECONDS = int(os.environ.get('OCC_ASSIST_BODS_VEHICLE_CACHE_SECONDS', '5'))
DEPARTURE_BOARD_REFRESH_SECONDS = int(os.environ.get('OCC_ASSIST_DEPARTURE_BOARD_REFRESH_SECONDS', '30'))
_last_data_health_run_monotonic = 0.0
_bods_vehicle_cache_lock = threading.Lock()
_bods_vehicle_cache: dict[str, object] = {
    'loadedAtMonotonic': 0.0,
    'vehicles': [],
    'sourceTimestamp': '',
    'hasData': False,
}

CONTACT_ENCRYPTION_PREFIX = 'enc:v1:'
_contacts_cipher: Fernet | None = None


app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('OCC_ASSIST_SECRET_KEY', 'change-me-before-production')
app.config['MAPBOX_TOKEN'] = os.environ.get('OCC_ASSIST_MAPBOX_TOKEN', '')
app.config['BODS_FEED_ID'] = os.environ.get('OCC_ASSIST_BODS_FEED_ID', '18880')
app.config['BODS_API_KEY'] = os.environ.get('OCC_ASSIST_BODS_API_KEY', '')
app.config['BODS_STALE_SECONDS'] = int(os.environ.get('OCC_ASSIST_BODS_STALE_SECONDS', '120'))
app.config['SESSION_INACTIVITY_SECONDS'] = int(os.environ.get('OCC_ASSIST_SESSION_INACTIVITY_SECONDS', '3600'))


SIRI_NAMESPACE = {'siri': 'http://www.siri.org.uk/siri'}
LONDON_TZ = ZoneInfo('Europe/London')


def get_database_path() -> Path:
    configured_path = os.environ.get('OCC_ASSIST_DB_PATH')
    if configured_path:
        return Path(configured_path)
    return DEFAULT_DATABASE_PATH


def get_db() -> sqlite3.Connection:
    if 'db' not in g:
        database_path = get_database_path()
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        g.db = connection
    return g.db


def get_contacts_cipher() -> Fernet:
    global _contacts_cipher
    if _contacts_cipher is not None:
        return _contacts_cipher

    explicit_key = str(os.environ.get('OCC_ASSIST_CONTACTS_ENCRYPTION_KEY', '')).strip()
    if explicit_key:
        key_bytes = explicit_key.encode('utf-8')
    else:
        secret = str(os.environ.get('OCC_ASSIST_CONTACTS_ENCRYPTION_SECRET') or app.config.get('SECRET_KEY') or '').strip()
        if not secret:
            raise RuntimeError('Missing contacts encryption secret.')
        key_bytes = base64.urlsafe_b64encode(hashlib.sha256(secret.encode('utf-8')).digest())

    _contacts_cipher = Fernet(key_bytes)
    return _contacts_cipher


def is_encrypted_contact_value(value: object) -> bool:
    return isinstance(value, str) and value.startswith(CONTACT_ENCRYPTION_PREFIX)


def encrypt_contact_value(value: object) -> str:
    plain = str(value or '')
    if is_encrypted_contact_value(plain):
        return plain
    token = get_contacts_cipher().encrypt(plain.encode('utf-8')).decode('utf-8')
    return f"{CONTACT_ENCRYPTION_PREFIX}{token}"


def decrypt_contact_value(value: object) -> str:
    if value is None:
        return ''
    text = str(value)
    if not is_encrypted_contact_value(text):
        return text

    token_bytes = text[len(CONTACT_ENCRYPTION_PREFIX):].encode('utf-8')
    try:
        return get_contacts_cipher().decrypt(token_bytes).decode('utf-8')
    except InvalidToken:
        # Fallback for data encrypted before runtime env secrets were loaded.
        legacy_default_secret = 'change-me-before-production'
        current_secret = str(os.environ.get('OCC_ASSIST_CONTACTS_ENCRYPTION_SECRET') or app.config.get('SECRET_KEY') or '').strip()
        if legacy_default_secret and legacy_default_secret != current_secret:
            legacy_key = base64.urlsafe_b64encode(hashlib.sha256(legacy_default_secret.encode('utf-8')).digest())
            try:
                return Fernet(legacy_key).decrypt(token_bytes).decode('utf-8')
            except InvalidToken:
                pass
        return ''

def encrypt_existing_contacts(database: sqlite3.Connection) -> None:
    rows = database.execute(
        '''
        SELECT id, first_name, last_name, job_role, job_title, depot_location, phone_number
        FROM contacts
        '''
    ).fetchall()
    for row in rows:
        updates: dict[str, str] = {}
        for column in ('first_name', 'last_name', 'job_role', 'job_title', 'depot_location', 'phone_number'):
            current_value = row[column]
            if current_value is None:
                continue

            current_text = str(current_value)
            plain_value = decrypt_contact_value(current_text)

            is_primary_encrypted = False
            if is_encrypted_contact_value(current_text):
                token_bytes = current_text[len(CONTACT_ENCRYPTION_PREFIX):].encode('utf-8')
                try:
                    get_contacts_cipher().decrypt(token_bytes)
                    is_primary_encrypted = True
                except InvalidToken:
                    is_primary_encrypted = False

            if is_primary_encrypted:
                continue

            updates[column] = encrypt_contact_value(plain_value)

        if not updates:
            continue

        database.execute(
            '''
            UPDATE contacts
            SET first_name = COALESCE(?, first_name),
                last_name = COALESCE(?, last_name),
                job_role = COALESCE(?, job_role),
                job_title = COALESCE(?, job_title),
                depot_location = COALESCE(?, depot_location),
                phone_number = COALESCE(?, phone_number)
            WHERE id = ?
            ''',
            (
                updates.get('first_name'),
                updates.get('last_name'),
                updates.get('job_role'),
                updates.get('job_title'),
                updates.get('depot_location'),
                updates.get('phone_number'),
                int(row['id']),
            ),
        )

    database.commit()

@app.teardown_appcontext
def close_db(_: object | None) -> None:
    database = g.pop('db', None)
    if database is not None:
        database.close()


def init_db() -> None:
    database = get_db()
    database.executescript(
        '''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            is_superadmin INTEGER NOT NULL DEFAULT 0,
            must_reset_password INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS permissions (
            user_id INTEGER NOT NULL,
            permission_key TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, permission_key),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS driving_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            driver_name TEXT NOT NULL,
            employee_number TEXT NOT NULL,
            segment_summary TEXT NOT NULL,
            status TEXT NOT NULL,
            breaches_json TEXT NOT NULL DEFAULT '[]',
            total_driving_minutes INTEGER NOT NULL DEFAULT 0,
            total_break_minutes INTEGER NOT NULL DEFAULT 0,
            spreadover_minutes INTEGER NOT NULL DEFAULT 0,
            current_continuous_driving_minutes INTEGER NOT NULL DEFAULT 0,
            non_driving_first_window_minutes INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_at_epoch INTEGER NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_driving_snapshots_user_epoch
        ON driving_snapshots (user_id, created_at_epoch DESC);

        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            rotacloud_ical_url TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS user_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            session_token TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_activity_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            active INTEGER NOT NULL DEFAULT 1,
            ended_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_user_sessions_user_active
        ON user_sessions (user_id, active, last_activity_at DESC);

        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            job_role TEXT NOT NULL,
            job_title TEXT NOT NULL,
            depot_location TEXT NOT NULL,
            phone_number TEXT NOT NULL,
            is_important INTEGER NOT NULL DEFAULT 0,
            is_private INTEGER NOT NULL DEFAULT 0,
            created_by_user_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_contacts_name
        ON contacts (last_name, first_name);

        CREATE INDEX IF NOT EXISTS idx_contacts_phone
        ON contacts (phone_number);
        '''
    )
    database.commit()
    ensure_superadmin(database)
    sync_user_permissions_schema(database)
    encrypt_existing_contacts(database)


def cleanup_expired_snapshots(database: sqlite3.Connection, user_id: int | None = None) -> None:
    now_epoch = int(datetime.now(timezone.utc).timestamp())
    cutoff = now_epoch - SNAPSHOT_RETENTION_SECONDS
    if user_id is None:
        database.execute('DELETE FROM driving_snapshots WHERE created_at_epoch < ?', (cutoff,))
    else:
        database.execute(
            'DELETE FROM driving_snapshots WHERE user_id = ? AND created_at_epoch < ?',
            (user_id, cutoff),
        )
    database.commit()


def ensure_superadmin(database: sqlite3.Connection) -> None:
    user = database.execute('SELECT id, is_superadmin FROM users WHERE email = ?', (SUPERADMIN_EMAIL,)).fetchone()
    if user is None:
        if not SUPERADMIN_PASSWORD:
            return
        cursor = database.execute(
            'INSERT INTO users (email, password_hash, is_superadmin) VALUES (?, ?, 1)',
            (SUPERADMIN_EMAIL, generate_password_hash(SUPERADMIN_PASSWORD)),
        )
        user_id = cursor.lastrowid
    else:
        user_id = user['id']
        if not bool(user['is_superadmin']):
            database.execute('UPDATE users SET is_superadmin = 1 WHERE id = ?', (user_id,))

    for permission_key in PERMISSIONS:
        database.execute(
            '''
            INSERT INTO permissions (user_id, permission_key, enabled)
            VALUES (?, ?, 1)
            ON CONFLICT(user_id, permission_key) DO UPDATE SET enabled = 1
            ''',
            (user_id, permission_key),
        )
    database.commit()


def sync_user_permissions_schema(database: sqlite3.Connection) -> None:
    users = database.execute('SELECT id, is_superadmin FROM users').fetchall()
    for user in users:
        user_id = int(user['id'])
        permission_rows = database.execute(
            'SELECT permission_key, enabled FROM permissions WHERE user_id = ?',
            (user_id,),
        ).fetchall()
        existing = {str(row['permission_key']): bool(row['enabled']) for row in permission_rows}

        tracking_enabled = bool(existing.get('tracking', False))
        for permission_key in PERMISSIONS:
            if permission_key in existing:
                continue
            default_enabled = tracking_enabled if permission_key in {'service_overview', 'contacts'} else False
            database.execute(
                'INSERT INTO permissions (user_id, permission_key, enabled) VALUES (?, ?, ?)',
                (user_id, permission_key, int(default_enabled)),
            )

        if bool(user['is_superadmin']) or bool(existing.get('admin_privileges', False)):
            for permission_key in PERMISSIONS:
                database.execute(
                    '''
                    INSERT INTO permissions (user_id, permission_key, enabled)
                    VALUES (?, ?, 1)
                    ON CONFLICT(user_id, permission_key) DO UPDATE SET enabled = 1
                    ''',
                    (user_id, permission_key),
                )

    database.commit()


def fetch_user_by_email(email: str) -> sqlite3.Row | None:
    return get_db().execute('SELECT * FROM users WHERE lower(email) = lower(?)', (email,)).fetchone()


def fetch_user_permissions(user_id: int) -> dict[str, bool]:
    rows = get_db().execute(
        'SELECT permission_key, enabled FROM permissions WHERE user_id = ?',
        (user_id,),
    ).fetchall()
    permissions = {key: False for key in PERMISSIONS}
    for row in rows:
        permissions[row['permission_key']] = bool(row['enabled'])
    return permissions


def mark_user_for_password_reset(user_id: int, enabled: bool = True) -> None:
    database = get_db()
    database.execute('UPDATE users SET must_reset_password = ? WHERE id = ?', (int(enabled), user_id))
    database.commit()


def update_user_password(user_id: int, password: str) -> None:
    database = get_db()
    database.execute(
        'UPDATE users SET password_hash = ?, must_reset_password = 0 WHERE id = ?',
        (generate_password_hash(password), user_id),
    )
    database.commit()


def ensure_user_session(database: sqlite3.Connection, user_id: int, session_token: str, now: datetime | None = None) -> None:
    if not session_token:
        return
    timestamp = (now or datetime.now(timezone.utc)).isoformat()
    database.execute(
        '''
        INSERT INTO user_sessions (user_id, session_token, created_at, last_activity_at, active)
        VALUES (?, ?, ?, ?, 1)
        ON CONFLICT(session_token) DO UPDATE SET
            user_id = excluded.user_id,
            last_activity_at = excluded.last_activity_at,
            active = 1,
            created_at = COALESCE(user_sessions.created_at, excluded.created_at)
        ''',
        (user_id, session_token, timestamp, timestamp),
    )
    database.commit()


def invalidate_user_sessions(user_id: int, session_token: str | None = None) -> None:
    database = get_db()
    now = datetime.now(timezone.utc).isoformat()
    if session_token:
        database.execute(
            'UPDATE user_sessions SET active = 0, ended_at = ? WHERE user_id = ? AND session_token = ? AND active = 1',
            (now, user_id, session_token),
        )
    else:
        database.execute(
            'UPDATE user_sessions SET active = 0, ended_at = ? WHERE user_id = ? AND active = 1',
            (now, user_id),
        )
    database.commit()


def fetch_user_session_summary(user_id: int) -> dict[str, object]:
    database = get_db()
    row = database.execute(
        '''
        SELECT session_token, created_at, last_activity_at, active
        FROM user_sessions
        WHERE user_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        ''',
        (user_id,),
    ).fetchone()
    if row is None:
        return {'hasSession': False, 'isActive': False, 'sessionDurationSeconds': 0}

    now = datetime.now(timezone.utc)
    created_at = parse_session_timestamp(row['created_at']) or parse_session_timestamp(row['last_activity_at']) or now
    last_activity_at = parse_session_timestamp(row['last_activity_at']) or created_at
    if bool(row['active']):
        duration_seconds = max(0, int((now - created_at).total_seconds()))
    else:
        duration_seconds = max(0, int((last_activity_at - created_at).total_seconds()))

    return {
        'hasSession': True,
        'isActive': bool(row['active']),
        'sessionDurationSeconds': duration_seconds,
        'sessionToken': row['session_token'],
    }


def get_current_user() -> dict[str, object] | None:
    user_id = session.get('user_id')
    if not user_id:
        return None

    user = get_db().execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if user is None:
        session.clear()
        return None

    permissions = fetch_user_permissions(user['id'])
    return {
        'id': user['id'],
        'email': user['email'],
        'is_superadmin': bool(user['is_superadmin']),
        'permissions': permissions,
    }


def has_permission(user: dict[str, object] | None, permission_key: str) -> bool:
    if not user:
        return False
    if bool(user['is_superadmin']):
        return True
    permissions = user['permissions']
    return bool(permissions.get('admin_privileges')) or bool(permissions.get(permission_key))


def login_required(permission_key: str | None = None):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped_view(*args, **kwargs):
            user = get_current_user()
            if user is None:
                return redirect(url_for('index'))
            if permission_key and not has_permission(user, permission_key):
                abort(403)
            return view_func(*args, **kwargs)

        return wrapped_view

    return decorator


def login_required_any(permission_keys: tuple[str, ...]):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped_view(*args, **kwargs):
            user = get_current_user()
            if user is None:
                return redirect(url_for('index'))
            if permission_keys and not any(has_permission(user, key) for key in permission_keys):
                abort(403)
            return view_func(*args, **kwargs)

        return wrapped_view

    return decorator


@app.context_processor
def inject_user_context() -> dict[str, object]:
    user = get_current_user()
    static_version = str(
        int(
            max(
                (BASE_DIR / 'static' / 'scripts.js').stat().st_mtime,
                (BASE_DIR / 'static' / 'styles.css').stat().st_mtime,
            )
        )
    )
    return {
        'current_user': user,
        'mapbox_token': app.config['MAPBOX_TOKEN'],
        'static_version': static_version,
        'permissions_map': PERMISSIONS,
        'page_permissions': PAGE_PERMISSIONS,
        'tracking_stops_url': url_for('tracking_stops'),
        'service_overview_url': url_for('service_overview'),
        'admin_data_status_url': url_for('admin_data_status'),
        'admin_contacts_encryption_status_url': url_for('admin_contacts_encryption_status'),
        'admin_gtfs_manual_lock_url': url_for('admin_gtfs_manual_lock'),
        'tracking_roadworks_url': url_for('tracking_roadworks'),
        'roadworks_status_url': url_for('roadworks_status'),
        'roadworks_upload_url': url_for('upload_roadworks'),
    }


def parse_session_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        current = value
    elif isinstance(value, (int, float)):
        current = datetime.fromtimestamp(float(value), tz=timezone.utc)
    elif isinstance(value, str) and value:
        try:
            current = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
    else:
        return None

    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


@app.before_request
def prepare_database() -> None:
    init_db()

    session_timeout = app.config.get('SESSION_INACTIVITY_SECONDS', 3600)
    if not isinstance(session_timeout, int):
        try:
            session_timeout = int(session_timeout)
        except (TypeError, ValueError):
            session_timeout = 3600

    last_activity = parse_session_timestamp(session.get('last_activity'))
    if session.get('user_id'):
        user_id = int(session['user_id'])
        session_token = str(session.get('session_token') or '').strip()
        if not session_token:
            session_token = secrets.token_urlsafe(24)
            session['session_token'] = session_token
        if last_activity is not None and (datetime.now(timezone.utc) - last_activity).total_seconds() > session_timeout:
            invalidate_user_sessions(user_id, session_token)
            session.clear()
            return

        ensure_user_session(get_db(), user_id, session_token)
        session['last_activity'] = datetime.now(timezone.utc).isoformat()
        session.modified = True


@app.get('/')
def index():
    if get_current_user() is not None:
        return redirect(url_for('daily_overview'))
    return render_template('index.html')


@app.post('/api/login')
def login():
    payload = request.get_json(silent=True) or {}
    email = str(payload.get('email', '')).strip()
    password = str(payload.get('password', ''))

    if not email or not password:
        return jsonify({'ok': False, 'message': 'Enter a valid email address and password.'}), 400

    user = fetch_user_by_email(email)
    if user is None or not check_password_hash(user['password_hash'], password):
        return jsonify({'ok': False, 'message': 'Incorrect email or password.'}), 401

    session.clear()
    session_token = secrets.token_urlsafe(24)
    session['user_id'] = user['id']
    session['session_token'] = session_token
    session['last_activity'] = datetime.now(timezone.utc).isoformat()
    session.modified = True
    ensure_user_session(get_db(), int(user['id']), session_token)

    if bool(user['must_reset_password']):
        return jsonify({'ok': True, 'redirect': url_for('settings_page') + '?must_reset_password=1', 'mustResetPassword': True})

    return jsonify({'ok': True, 'redirect': url_for('daily_overview')})


@app.post('/api/logout')
def logout():
    session_token = str(session.get('session_token') or '').strip()
    if session.get('user_id'):
        invalidate_user_sessions(int(session['user_id']), session_token or None)
    session.clear()
    return jsonify({'ok': True, 'redirect': url_for('index')})


@app.get('/api/session')
def session_info():
    user = get_current_user()
    if user is None:
        return jsonify({'authenticated': False})
    return jsonify({'authenticated': True, 'user': user})


@app.get('/daily-overview')
@login_required('live_updates')
def daily_overview():
    return render_template('daily-overview.html')


@app.get('/google-calendar')
def google_calendar_page():
    return render_template('google-calendar.html')


@app.get('/live-updates')
def live_updates_legacy_redirect():
    return redirect(url_for('daily_overview'))


@app.get('/settings')
@login_required()
def settings_page():
    return render_template('settings.html')


@app.get('/api/settings/rotacloud')
@login_required()
def get_rotacloud_setting():
    user = get_current_user()
    if user is None:
        abort(401)
    url = get_user_rotacloud_ical_url(int(user['id']))
    return jsonify({'ok': True, 'rotacloudIcalUrl': url})


@app.post('/api/settings/password')
@login_required()
def update_password_setting():
    user = get_current_user()
    if user is None:
        abort(401)

    payload = request.get_json(silent=True) or {}
    current_password = str(payload.get('currentPassword', '')).strip()
    new_password = str(payload.get('newPassword', '')).strip()
    confirm_password = str(payload.get('confirmPassword', '')).strip()

    if len(new_password) < 8:
        return jsonify({'ok': False, 'message': 'Choose a password with at least 8 characters.'}), 400
    if new_password != confirm_password:
        return jsonify({'ok': False, 'message': 'New passwords do not match.'}), 400

    database = get_db()
    current_user_row = database.execute('SELECT password_hash, must_reset_password FROM users WHERE id = ?', (user['id'],)).fetchone()
    if current_user_row is None:
        abort(404)

    if bool(current_user_row['must_reset_password']):
        if current_password and not check_password_hash(current_user_row['password_hash'], current_password):
            return jsonify({'ok': False, 'message': 'Current password is incorrect.'}), 401
    elif not check_password_hash(current_user_row['password_hash'], current_password):
        return jsonify({'ok': False, 'message': 'Current password is incorrect.'}), 401

    update_user_password(int(user['id']), new_password)
    return jsonify({'ok': True, 'message': 'Password changed successfully.'})


@app.put('/api/settings/rotacloud')
@login_required()
def update_rotacloud_setting():
    user = get_current_user()
    if user is None:
        abort(401)

    payload = request.get_json(silent=True) or {}
    raw_url = str(payload.get('rotacloudIcalUrl', ''))
    try:
        normalized_url = validate_rotacloud_ical_url(raw_url)
    except ValueError as error:
        return jsonify({'ok': False, 'message': str(error)}), 400

    save_user_rotacloud_ical_url(int(user['id']), normalized_url)
    return jsonify({'ok': True, 'rotacloudIcalUrl': normalized_url})


@app.get('/api/overview/shifts')
@login_required('live_updates')
def daily_overview_shifts():
    user = get_current_user()
    if user is None:
        abort(401)

    ical_url = get_user_rotacloud_ical_url(int(user['id']))
    if not ical_url:
        return jsonify(
            {
                'ok': True,
                'configured': False,
                'message': 'No RotaCloud iCal link configured yet.',
                'currentShift': None,
                'nextShift': None,
            }
        )

    try:
        shifts = fetch_rotacloud_shift_overview(ical_url)
    except RuntimeError as error:
        return jsonify({'ok': False, 'configured': True, 'message': str(error)}), 503

    return jsonify(
        {
            'ok': True,
            'configured': True,
            'currentShift': shifts['currentShift'],
            'nextShift': shifts['nextShift'],
        }
    )


@app.get('/api/overview/upcoming-shifts')
@login_required('live_updates')
def daily_overview_upcoming_shifts():
    user = get_current_user()
    if user is None:
        abort(401)

    ical_url = get_user_rotacloud_ical_url(int(user['id']))
    if not ical_url:
        return jsonify(
            {
                'ok': True,
                'configured': False,
                'scope': 'week',
                'offset': 0,
                'periodLabel': '',
                'shifts': [],
                'message': 'No RotaCloud iCal link configured yet.',
            }
        )

    scope = str(request.args.get('scope', 'week')).strip().lower()
    if scope not in {'week', 'month'}:
        scope = 'week'

    try:
        offset = int(request.args.get('offset', '0'))
    except ValueError:
        offset = 0
    offset = max(-12, min(24, offset))

    period_start_local, period_end_local, period_label = get_period_bounds(scope, offset)
    period_start_utc = period_start_local.astimezone(timezone.utc)
    period_end_utc = period_end_local.astimezone(timezone.utc)
    now_utc = datetime.now(timezone.utc)

    try:
        all_events = fetch_rotacloud_events(ical_url)
    except RuntimeError as error:
        return jsonify({'ok': False, 'configured': True, 'message': str(error)}), 503

    filtered_events = [
        event for event in all_events
        if event['end'] >= now_utc and event['start'] < period_end_utc and event['end'] > period_start_utc
    ]
    include_rest_days = str(request.args.get('includeRestDays', '1')).strip().lower() not in {
        '0',
        'false',
        'no',
        'off',
    }
    if not include_rest_days:
        filtered_events = [event for event in filtered_events if not is_rest_day_or_holiday_event(event)]

    serialized_shifts = [serialize_shift_event(event) for event in filtered_events]
    week_days: list[dict[str, object]] = []
    if scope == 'week':
        for day_index in range(7):
            day_start_local = period_start_local + timedelta(days=day_index)
            day_events = [
                event for event in filtered_events
                if event['start'].astimezone(LONDON_TZ).date() == day_start_local.date()
            ]
            week_days.append(
                {
                    'dateIso': day_start_local.date().isoformat(),
                    'dayLabel': day_start_local.strftime('%A %d %b'),
                    'shifts': [serialize_shift_event(event) for event in day_events],
                }
            )

    return jsonify(
        {
            'ok': True,
            'configured': True,
            'scope': scope,
            'offset': offset,
            'periodLabel': period_label,
            'shifts': serialized_shifts,
            'weekDays': week_days,
            'weekStartsOn': 'Sunday',
        }
    )


@app.get('/tracking')
@login_required('tracking')
def tracking():
    return render_template('tracking.html')


@app.get('/service-overview')
@login_required('service_overview')
def service_overview():
    return render_template('service-overview.html')


@app.get('/contacts')
@login_required('contacts')
def contacts_page():
    return render_template('contacts.html')


def get_xml_text(node: ET.Element | None, path: str) -> str:
    if node is None:
        return ''
    element = node.find(path, SIRI_NAMESPACE)
    if element is None or element.text is None:
        return ''
    return element.text.strip()


def get_bods_feed_url() -> str | None:
    api_key = app.config['BODS_API_KEY']
    feed_id = app.config['BODS_FEED_ID']
    if not api_key or not feed_id:
        return None
    query = urlencode({'api_key': api_key})
    return f'https://data.bus-data.dft.gov.uk/api/v1/datafeed/{feed_id}/?{query}'


def get_latest_bods_timetable_dataset_download_url() -> tuple[str | None, dict[str, object] | None]:
    api_key = app.config['BODS_API_KEY']
    noc = BODS_TIMETABLE_NOC
    if not api_key or not noc:
        return None, None

    query = urlencode({'api_key': api_key, 'noc': noc, 'limit': BODS_TIMETABLE_LIMIT})
    url = f'https://data.bus-data.dft.gov.uk/api/v1/dataset/?{query}'
    try:
        with urlopen(url, timeout=GTFS_AUTO_DOWNLOAD_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode('utf-8', errors='replace'))
    except Exception:
        return None, None

    results = payload.get('results', []) if isinstance(payload, dict) else []
    if not isinstance(results, list) or not results:
        return None, None

    published = [
        row for row in results
        if isinstance(row, dict)
        and str(row.get('status') or '').lower() == 'published'
        and str(row.get('extension') or '').lower() == 'zip'
        and isinstance(row.get('url'), str)
    ]
    if not published:
        return None, None

    published.sort(key=lambda row: str(row.get('modified') or ''), reverse=True)
    selected = published[0]
    return str(selected.get('url') or ''), selected


def parse_bods_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def get_user_rotacloud_ical_url(user_id: int) -> str:
    row = get_db().execute(
        'SELECT rotacloud_ical_url FROM user_settings WHERE user_id = ?',
        (user_id,),
    ).fetchone()
    if row is None:
        return ''
    return str(row['rotacloud_ical_url'] or '').strip()


def validate_rotacloud_ical_url(value: str) -> str:
    url = value.strip()
    if not url:
        return ''
    if len(url) > 2048:
        raise ValueError('The iCal link is too long.')

    parsed = urlparse(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        raise ValueError('Enter a valid http or https iCal link.')
    return url


def save_user_rotacloud_ical_url(user_id: int, url: str) -> None:
    database = get_db()
    database.execute(
        '''
        INSERT INTO user_settings (user_id, rotacloud_ical_url)
        VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            rotacloud_ical_url = excluded.rotacloud_ical_url,
            updated_at = CURRENT_TIMESTAMP
        ''',
        (user_id, url),
    )
    database.commit()


def unfold_ical_lines(content: str) -> list[str]:
    normalized = content.replace('\r\n', '\n').replace('\r', '\n')
    lines: list[str] = []
    for raw_line in normalized.split('\n'):
        if (raw_line.startswith(' ') or raw_line.startswith('\t')) and lines:
            lines[-1] = lines[-1] + raw_line[1:]
        else:
            lines.append(raw_line)
    return lines


def parse_ical_property(line: str) -> tuple[str, dict[str, str], str] | None:
    if ':' not in line:
        return None
    property_part, value = line.split(':', 1)
    pieces = property_part.split(';')
    name = pieces[0].strip().upper()
    params: dict[str, str] = {}
    for piece in pieces[1:]:
        if '=' not in piece:
            continue
        param_key, param_value = piece.split('=', 1)
        params[param_key.strip().upper()] = param_value.strip()
    return name, params, value.strip()


def parse_ical_datetime(value: str, params: dict[str, str]) -> datetime | None:
    value_type = params.get('VALUE', '').upper()
    tz_name = params.get('TZID', 'Europe/London')

    if value_type == 'DATE':
        try:
            parsed = datetime.strptime(value, '%Y%m%d')
            return parsed.replace(tzinfo=LONDON_TZ)
        except ValueError:
            return None

    if value.endswith('Z'):
        for fmt in ('%Y%m%dT%H%M%SZ', '%Y%m%dT%H%MZ'):
            try:
                parsed = datetime.strptime(value, fmt)
                return parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None

    timezone_info = LONDON_TZ
    try:
        timezone_info = ZoneInfo(tz_name)
    except Exception:
        timezone_info = LONDON_TZ

    for fmt in ('%Y%m%dT%H%M%S', '%Y%m%dT%H%M'):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.replace(tzinfo=timezone_info)
        except ValueError:
            continue
    return None


def parse_ical_events(content: str) -> list[dict[str, object]]:
    lines = unfold_ical_lines(content)
    events: list[dict[str, object]] = []
    in_event = False
    event_values: dict[str, tuple[dict[str, str], str]] = {}

    for line in lines:
        stripped = line.strip()
        if stripped == 'BEGIN:VEVENT':
            in_event = True
            event_values = {}
            continue
        if stripped == 'END:VEVENT':
            if not in_event:
                continue

            dtstart_data = event_values.get('DTSTART')
            dtend_data = event_values.get('DTEND')
            summary_data = event_values.get('SUMMARY')
            location_data = event_values.get('LOCATION')

            if dtstart_data and dtend_data:
                start = parse_ical_datetime(dtstart_data[1], dtstart_data[0])
                end = parse_ical_datetime(dtend_data[1], dtend_data[0])
                if start and end and end > start:
                    events.append(
                        {
                            'start': start.astimezone(timezone.utc),
                            'end': end.astimezone(timezone.utc),
                            'summary': summary_data[1] if summary_data else 'Shift',
                            'location': location_data[1] if location_data else '',
                        }
                    )

            in_event = False
            event_values = {}
            continue

        if not in_event:
            continue

        parsed_property = parse_ical_property(line)
        if parsed_property is None:
            continue
        name, params, value = parsed_property
        if name in {'DTSTART', 'DTEND', 'SUMMARY', 'LOCATION'}:
            event_values[name] = (params, value)

    return events


def fetch_rotacloud_shift_overview(ical_url: str) -> dict[str, object]:
    events = fetch_rotacloud_events(ical_url)
    now_utc = datetime.now(timezone.utc)

    current_shift = None
    next_shift = None
    for event in events:
        start = event['start']
        end = event['end']
        if start <= now_utc < end:
            current_shift = event
        elif start >= now_utc and next_shift is None:
            next_shift = event
        if current_shift and next_shift:
            break

    def serialize_shift(shift: dict[str, object] | None) -> dict[str, object] | None:
        if shift is None:
            return None
        start_local = shift['start'].astimezone(LONDON_TZ)
        end_local = shift['end'].astimezone(LONDON_TZ)
        return {
            'summary': str(shift.get('summary') or 'Shift'),
            'location': str(shift.get('location') or ''),
            'startIso': shift['start'].isoformat(),
            'endIso': shift['end'].isoformat(),
            'windowLabel': f"{start_local.strftime('%a %d %b %H:%M')} - {end_local.strftime('%H:%M')}",
        }

    return {
        'currentShift': serialize_shift(current_shift),
        'nextShift': serialize_shift(next_shift),
    }

def fetch_rotacloud_events(ical_url: str) -> list[dict[str, object]]:
    try:
        with urlopen(ical_url, timeout=20) as response:
            payload = response.read().decode('utf-8', errors='replace')
    except HTTPError as error:
        raise RuntimeError(f'RotaCloud iCal link returned HTTP {error.code}.') from error
    except URLError as error:
        raise RuntimeError('Unable to reach the RotaCloud iCal link right now.') from error

    return sorted(parse_ical_events(payload), key=lambda event: event['start'])


def add_months(base: datetime, months: int) -> datetime:
    year = base.year + ((base.month - 1 + months) // 12)
    month = ((base.month - 1 + months) % 12) + 1
    return base.replace(year=year, month=month, day=1)


def get_period_bounds(scope: str, offset: int) -> tuple[datetime, datetime, str]:
    now_local = datetime.now(LONDON_TZ)
    if scope == 'month':
        month_start = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        period_start = add_months(month_start, offset)
        period_end = add_months(period_start, 1)
        label = period_start.strftime('%B %Y')
        return period_start, period_end, label

    # Week starts Sunday. Python weekday: Monday=0 ... Sunday=6.
    days_since_sunday = (now_local.weekday() + 1) % 7
    week_start = (now_local.replace(hour=0, minute=0, second=0, microsecond=0) -
                  timedelta(days=days_since_sunday))
    period_start = week_start + timedelta(weeks=offset)
    period_end = period_start + timedelta(days=7)
    label = f"{period_start.strftime('%d %b %Y')} - {(period_end - timedelta(days=1)).strftime('%d %b %Y')}"
    return period_start, period_end, label


def serialize_shift_event(event: dict[str, object]) -> dict[str, object]:
    start_local = event['start'].astimezone(LONDON_TZ)
    end_local = event['end'].astimezone(LONDON_TZ)
    return {
        'summary': str(event.get('summary') or 'Shift'),
        'location': str(event.get('location') or ''),
        'startIso': event['start'].isoformat(),
        'endIso': event['end'].isoformat(),
        'windowLabel': f"{start_local.strftime('%a %d %b %H:%M')} - {end_local.strftime('%H:%M')}",
    }
    
def is_rest_day_or_holiday_event(event: dict[str, object]) -> bool:
    summary = str(event.get('summary') or '').lower()
    location = str(event.get('location') or '').lower()
    text = f'{summary} {location}'
    keywords = [
        'rest day',
        'restday',
        'holiday',
        'annual leave',
        'day off',
        'dayoff',
    ]
    return any(keyword in text for keyword in keywords)


def parse_clock_to_minutes(value: str) -> int | None:
    parts = value.split(':')
    if len(parts) != 2:
        return None
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
    except ValueError:
        return None
    if hours < 0 or hours > 23 or minutes < 0 or minutes > 59:
        return None
    return hours * 60 + minutes


def format_duration(minutes: int) -> str:
    safe = max(0, int(minutes))
    hours = safe // 60
    remainder = safe % 60
    return f'{hours}h {remainder:02d}m'


def format_duration_compact(minutes: int) -> str:
    safe = max(0, int(minutes))
    hours = safe // 60
    remainder = safe % 60
    if hours == 0:
        return f'{remainder}m'
    return f'{hours}h{remainder:02d}'


def validate_segments(payload_segments: object) -> list[dict[str, object]]:
    if not isinstance(payload_segments, list) or not payload_segments:
        raise ValueError('Add at least one valid segment before saving.')

    validated: list[dict[str, object]] = []
    for item in payload_segments:
        if not isinstance(item, dict):
            raise ValueError('One or more segments are invalid.')
        segment_type = str(item.get('type', '')).strip().lower()
        if segment_type not in {'driving', 'break'}:
            raise ValueError('Segment type must be driving or break.')

        start = parse_clock_to_minutes(str(item.get('start', '')).strip())
        end = parse_clock_to_minutes(str(item.get('end', '')).strip())
        if start is None or end is None or end <= start:
            raise ValueError('Each segment must have valid start/end times on the same day.')

        validated.append(
            {
                'type': segment_type,
                'startMinutes': start,
                'endMinutes': end,
            }
        )

    ordered = sorted(validated, key=lambda seg: int(seg['startMinutes']))
    for index in range(1, len(ordered)):
        previous = ordered[index - 1]
        current = ordered[index]
        if int(current['startMinutes']) < int(previous['endMinutes']):
            raise ValueError('Segments overlap. Adjust segment times before saving.')

    return ordered


def calculate_domestic_compliance(segments: list[dict[str, object]]) -> dict[str, object]:
    minutes_per_hour = 60
    daily_limit = 10 * minutes_per_hour
    spreadover_limit = 16 * minutes_per_hour
    break_trigger = int(5.5 * minutes_per_hour)
    short_break = 30
    long_day_threshold = int(8.5 * minutes_per_hour)
    long_day_non_driving = 45

    if not segments:
        return {
            'totalDrivingMinutes': 0,
            'totalBreakMinutes': 0,
            'spreadoverMinutes': 0,
            'currentContinuousDrivingMinutes': 0,
            'nonDrivingInFirstWindowMinutes': 0,
            'breaches': [],
            'status': 'compliant',
        }

    day_start = int(segments[0]['startMinutes'])
    day_end = int(segments[-1]['endMinutes'])
    spreadover_minutes = day_end - day_start

    total_driving = 0
    total_break = 0
    current_spell_driving = 0
    break_rule_a_exceeded = False
    non_driving_first_window = 0
    has_break_30_after_window = False
    long_day_window_end = day_start + long_day_threshold

    for segment in segments:
        start_minutes = int(segment['startMinutes'])
        end_minutes = int(segment['endMinutes'])
        duration = end_minutes - start_minutes
        if segment['type'] == 'driving':
            total_driving += duration
            current_spell_driving += duration
            if current_spell_driving > break_trigger:
                break_rule_a_exceeded = True
            continue

        total_break += duration
        overlap_start = max(start_minutes, day_start)
        overlap_end = min(end_minutes, long_day_window_end)
        non_driving_first_window += max(0, overlap_end - overlap_start)

        if duration >= short_break and start_minutes >= long_day_window_end:
            has_break_30_after_window = True

        if duration >= short_break:
            current_spell_driving = 0

    continuous_at_end = 0
    for segment in reversed(segments):
        duration = int(segment['endMinutes']) - int(segment['startMinutes'])
        if segment['type'] == 'driving':
            continuous_at_end += duration
            continue
        if duration >= short_break:
            break

    breaches: list[str] = []
    if total_driving > daily_limit:
        breaches.append(
            f'Daily driving limit exceeded: {format_duration(total_driving)} (limit {format_duration(daily_limit)}).'
        )

    if spreadover_minutes > spreadover_limit:
        breaches.append(
            f'Spreadover limit exceeded: {format_duration(spreadover_minutes)} (limit {format_duration(spreadover_limit)}).'
        )

    if spreadover_minutes < long_day_threshold:
        if break_rule_a_exceeded:
            breaches.append('Break breach: a 30-minute break is required before driving exceeds 5h 30m.')
    else:
        option_a = not break_rule_a_exceeded
        option_b = non_driving_first_window >= long_day_non_driving and has_break_30_after_window
        if not option_a and not option_b:
            breaches.append(
                'Break breach: for days of 8h 30m or more, either take a 30-minute break before 5h 30m driving, or complete 45 minutes non-driving in first 8h 30m and then take a 30-minute break.'
            )

    return {
        'totalDrivingMinutes': total_driving,
        'totalBreakMinutes': total_break,
        'spreadoverMinutes': spreadover_minutes,
        'currentContinuousDrivingMinutes': continuous_at_end,
        'nonDrivingInFirstWindowMinutes': non_driving_first_window,
        'breaches': breaches,
        'status': 'breached' if breaches else 'compliant',
    }


def fetch_bods_vehicles() -> tuple[list[dict[str, object]], str]:
    feed_url = get_bods_feed_url()
    if not feed_url:
        return [], ''

    try:
        with urlopen(feed_url, timeout=20) as response:
            payload = response.read()
    except HTTPError as error:
        raise RuntimeError(f'BODS feed returned HTTP {error.code}.') from error
    except URLError as error:
        raise RuntimeError('BODS feed is not reachable right now.') from error

    root = ET.fromstring(payload)
    response_timestamp = get_xml_text(root, './/siri:VehicleMonitoringDelivery/siri:ResponseTimestamp')
    source_time = parse_bods_timestamp(response_timestamp)
    stale_seconds = app.config['BODS_STALE_SECONDS']
    items: list[dict[str, object]] = []

    for activity in root.findall('.//siri:VehicleActivity', SIRI_NAMESPACE):
        journey = activity.find('siri:MonitoredVehicleJourney', SIRI_NAMESPACE)
        if journey is None:
            continue

        latitude = get_xml_text(journey, 'siri:VehicleLocation/siri:Latitude')
        longitude = get_xml_text(journey, 'siri:VehicleLocation/siri:Longitude')
        if not latitude or not longitude:
            continue

        service = get_xml_text(journey, 'siri:PublishedLineName') or get_xml_text(journey, 'siri:LineRef')
        destination = get_xml_text(journey, 'siri:DestinationName') or 'Destination unavailable'
        direction = (get_xml_text(journey, 'siri:DirectionRef') or 'unknown').lower()
        fleet_number = (
            get_xml_text(activity, 'siri:Extensions/siri:VehicleJourney/siri:VehicleUniqueId')
            or get_xml_text(journey, 'siri:VehicleRef')
            or 'Unknown'
        )
        operator_ref = get_xml_text(journey, 'siri:OperatorRef')
        recorded_at = get_xml_text(activity, 'siri:RecordedAtTime')
        origin_departure = get_xml_text(journey, 'siri:OriginAimedDepartureTime')
        destination_arrival = get_xml_text(journey, 'siri:DestinationAimedArrivalTime')
        monitored_call = journey.find('siri:MonitoredCall', SIRI_NAMESPACE)
        stop_point_ref = get_xml_text(monitored_call, 'siri:StopPointRef') if monitored_call is not None else ''
        expected_arrival = get_xml_text(monitored_call, 'siri:ExpectedArrivalTime') if monitored_call is not None else ''
        aimed_arrival = get_xml_text(monitored_call, 'siri:AimedArrivalTime') if monitored_call is not None else ''
        actual_arrival = get_xml_text(monitored_call, 'siri:ActualArrivalTime') if monitored_call is not None else ''
        journey_ref = get_xml_text(journey, 'siri:FramedVehicleJourneyRef/siri:DatedVehicleJourneyRef')
        vehicle_journey_ref = get_xml_text(journey, 'siri:FramedVehicleJourneyRef/siri:VehicleJourneyRef')
        block_ref = get_xml_text(journey, 'siri:BlockRef')
        journey_code = get_xml_text(journey, 'siri:JourneyCode')

        recorded_at_time = parse_bods_timestamp(recorded_at)
        origin_departure_time = parse_bods_timestamp(origin_departure)
        destination_arrival_time = parse_bods_timestamp(destination_arrival)

        if source_time is not None:
            if recorded_at_time is None or (source_time - recorded_at_time).total_seconds() >= stale_seconds:
                continue

            if origin_departure_time is not None and destination_arrival_time is not None:
                if not (origin_departure_time <= source_time <= destination_arrival_time):
                    continue

        items.append(
            {
                'id': get_xml_text(activity, 'siri:ItemIdentifier') or f'{fleet_number}-{journey_ref or service}',
                'latitude': float(latitude),
                'longitude': float(longitude),
                'service': service,
                'destination': destination.replace('_', ' '),
                'direction': direction,
                'fleetNumber': fleet_number,
                'operator': operator_ref,
                'recordedAt': recorded_at,
                'originAimedDepartureTime': origin_departure,
                'destinationAimedArrivalTime': destination_arrival,
                'stopPointRef': stop_point_ref,
                'naptan': stop_point_ref,
                'expectedArrivalTime': expected_arrival,
                'aimedArrivalTime': aimed_arrival,
                'actualArrivalTime': actual_arrival,
                'journeyRef': journey_ref,
                'vehicleJourneyRef': vehicle_journey_ref,
                'blockRef': block_ref,
                'journeyCode': journey_code,
            }
        )

    return items, response_timestamp


def fetch_bods_vehicles_cached(force: bool = False) -> tuple[list[dict[str, object]], str]:
    cache_ttl = max(1, int(BODS_VEHICLE_CACHE_SECONDS))
    now_monotonic = time.monotonic()

    with _bods_vehicle_cache_lock:
        loaded_at = float(_bods_vehicle_cache.get('loadedAtMonotonic') or 0.0)
        if not force and loaded_at > 0 and (now_monotonic - loaded_at) < cache_ttl:
            return list(_bods_vehicle_cache.get('vehicles') or []), str(_bods_vehicle_cache.get('sourceTimestamp') or '')

    try:
        vehicles, source_timestamp = fetch_bods_vehicles()
    except Exception:
        with _bods_vehicle_cache_lock:
            loaded_at = float(_bods_vehicle_cache.get('loadedAtMonotonic') or 0.0)
            if loaded_at > 0 and (now_monotonic - loaded_at) < max(cache_ttl * 3, 20):
                return list(_bods_vehicle_cache.get('vehicles') or []), str(_bods_vehicle_cache.get('sourceTimestamp') or '')
            # Prevent repeated slow failures by briefly caching an empty result.
            _bods_vehicle_cache['loadedAtMonotonic'] = now_monotonic
            _bods_vehicle_cache['vehicles'] = []
            _bods_vehicle_cache['sourceTimestamp'] = ''
            _bods_vehicle_cache['hasData'] = False
            return [], ''

    with _bods_vehicle_cache_lock:
        _bods_vehicle_cache['loadedAtMonotonic'] = now_monotonic
        _bods_vehicle_cache['vehicles'] = vehicles
        _bods_vehicle_cache['sourceTimestamp'] = source_timestamp
        _bods_vehicle_cache['hasData'] = True

    return list(vehicles), str(source_timestamp)



def normalize_tracking_key(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', '', str(value or '').strip().lower())


def parse_tracking_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=LONDON_TZ).astimezone(timezone.utc)
    return parsed.astimezone(LONDON_TZ).astimezone(timezone.utc)


def parse_gtfs_time(value: object) -> int | None:
    text = str(value or '').strip()
    if not text:
        return None
    parts = text.split(':')
    if len(parts) < 2:
        return None
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        return None
    return hours * 3600 + minutes * 60 + seconds


GTFS_WEEKDAY_COLUMNS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
TRANSXCHANGE_DAY_GROUPS = {
    'mondaytofriday': {'monday', 'tuesday', 'wednesday', 'thursday', 'friday'},
    'mondaytosaturday': {'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'},
    'mondaytosunday': set(GTFS_WEEKDAY_COLUMNS),
    'notsaturday': {'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'sunday'},
    'weekend': {'saturday', 'sunday'},
    'everyday': set(GTFS_WEEKDAY_COLUMNS),
}
# Timetables can span years; only keep a usable window so the cache stays small.
SERVICE_CALENDAR_PAST_DAYS = 7
SERVICE_CALENDAR_FUTURE_DAYS = 400


def build_gtfs_service_calendar(extracted_dir: Path) -> dict[str, list[str]]:
    calendar_path = find_gtfs_file(extracted_dir, 'calendar.txt')
    calendar_dates_path = find_gtfs_file(extracted_dir, 'calendar_dates.txt')
    active_dates: dict[str, set[str]] = {}

    today = datetime.now(LONDON_TZ).date()
    window_start = today - timedelta(days=SERVICE_CALENDAR_PAST_DAYS)
    window_end = today + timedelta(days=SERVICE_CALENDAR_FUTURE_DAYS)

    if calendar_path is not None:
        for row in read_gtfs_rows(calendar_path):
            service_id = str(row.get('service_id') or '').strip()
            if not service_id:
                continue
            start_text = str(row.get('start_date') or '').strip()
            end_text = str(row.get('end_date') or '').strip()
            if not start_text or not end_text:
                continue
            try:
                start_date = datetime.strptime(start_text, '%Y%m%d').date()
                end_date = datetime.strptime(end_text, '%Y%m%d').date()
            except ValueError:
                continue

            weekday_columns = GTFS_WEEKDAY_COLUMNS
            weekday_flags = [str(row.get(column) or '0').strip() for column in weekday_columns]

            current_date = max(start_date, window_start)
            final_date = min(end_date, window_end)
            while current_date <= final_date:
                weekday_index = current_date.weekday()
                if weekday_flags[weekday_index] == '1':
                    active_dates.setdefault(service_id, set()).add(current_date.strftime('%Y%m%d'))
                current_date += timedelta(days=1)

    if calendar_dates_path is not None:
        for row in read_gtfs_rows(calendar_dates_path):
            service_id = str(row.get('service_id') or '').strip()
            date_text = str(row.get('date') or '').strip()
            exception_type = str(row.get('exception_type') or '0').strip()
            if not service_id or not date_text:
                continue
            if exception_type == '1':
                active_dates.setdefault(service_id, set()).add(date_text)
            elif exception_type == '2':
                active_dates.setdefault(service_id, set()).discard(date_text)

    return {service_id: sorted(dates) for service_id, dates in active_dates.items()}


def service_is_active(service_id: str, service_calendar: dict[str, list[str]] | None, target_date: datetime | None) -> bool:
    if not service_id:
        return True
    if not service_calendar:
        return True
    if target_date is None:
        return True

    active_dates = service_calendar.get(service_id, [])
    return target_date.strftime('%Y%m%d') in {str(value) for value in active_dates}


def format_punctuality_delta(delta_seconds: int) -> str:
    if delta_seconds == 0:
        return '0m'

    minutes = int(round(abs(delta_seconds) / 60))
    if minutes <= 0:
        minutes = 1
    return f'{minutes}m'


def format_punctuality_label(delta_seconds: int, scheduled_at: datetime | None) -> str:
    if delta_seconds < 0:
        return f'Early -{format_punctuality_delta(delta_seconds)}'
    if delta_seconds > 0:
        return f'Late +{format_punctuality_delta(delta_seconds)}'
    return 'On time'


def collect_stop_match_keys(stop: dict[str, object] | None) -> set[str]:
    keys: set[str] = set()
    if not isinstance(stop, dict):
        return keys

    for field in ('naptan', 'atcoCode', 'stopPointRef', 'stopRef', 'stopId', 'id', 'stop_id', 'stop_code', 'atco_code', 'name', 'stopName'):
        value = str(stop.get(field) or '').strip()
        if not value:
            continue
        normalized = normalize_tracking_key(value)
        if normalized:
            keys.add(normalized)
    return keys


_stop_trip_index_lock = threading.Lock()
_stop_trip_index_memo: dict[str, object] = {'source': None, 'index': None}


def build_stop_trip_index(trip_schedules: dict[str, object]) -> dict[str, list[str]]:
    """Map each stop key to the trips calling there, so a board build need not scan every trip."""
    with _stop_trip_index_lock:
        # Identity check; the reference is retained so the id cannot be recycled.
        if _stop_trip_index_memo['source'] is trip_schedules:
            cached = _stop_trip_index_memo['index']
            if isinstance(cached, dict):
                return cached

    index: dict[str, list[str]] = {}
    for trip_id, payload in trip_schedules.items():
        if not isinstance(payload, dict):
            continue
        schedule_stops = payload.get('stops')
        if not isinstance(schedule_stops, list):
            continue
        for entry in schedule_stops:
            if not isinstance(entry, dict):
                continue
            for key in collect_stop_match_keys(entry):
                index.setdefault(key, []).append(str(trip_id))

    with _stop_trip_index_lock:
        _stop_trip_index_memo['source'] = trip_schedules
        _stop_trip_index_memo['index'] = index
    return index


def find_stop_index(stop: dict[str, object] | None, stop_sequence: list[dict[str, object]] | None) -> int | None:
    if not isinstance(stop, dict) or not isinstance(stop_sequence, list):
        return None

    stop_keys = collect_stop_match_keys(stop)
    if not stop_keys:
        return None

    for index, candidate in enumerate(stop_sequence):
        if not isinstance(candidate, dict):
            continue
        candidate_keys = collect_stop_match_keys(candidate)
        if stop_keys.intersection(candidate_keys):
            return index
    return None


def stop_matches_schedule_entry(stop: dict[str, object] | None, schedule_entry: dict[str, object] | None) -> bool:
    if not isinstance(stop, dict) or not isinstance(schedule_entry, dict):
        return False

    stop_keys = collect_stop_match_keys(stop)
    schedule_keys = collect_stop_match_keys(schedule_entry)
    return bool(stop_keys and schedule_keys and stop_keys.intersection(schedule_keys))


def get_schedule_time_value(schedule_entry: dict[str, object] | None) -> str:
    if not isinstance(schedule_entry, dict):
        return ''
    arrival_time = str(schedule_entry.get('arrivalTime') or '').strip()
    if arrival_time:
        return arrival_time
    return str(schedule_entry.get('departureTime') or '').strip()


def build_scheduled_stop_datetime(base_time: datetime | None, stop_time_value: object, first_stop_time_value: object | None = None) -> datetime | None:
    if base_time is None:
        return None

    stop_seconds = parse_gtfs_time(stop_time_value)
    if stop_seconds is None:
        return None

    first_seconds = parse_gtfs_time(first_stop_time_value) if first_stop_time_value is not None else None
    reference_time = base_time.astimezone(LONDON_TZ)
    best_scheduled: datetime | None = None
    best_distance: int | None = None

    for day_offset in (-1, 0, 1):
        candidate_date = reference_time.date() + timedelta(days=day_offset)
        candidate_time = datetime(candidate_date.year, candidate_date.month, candidate_date.day, tzinfo=LONDON_TZ)

        if first_seconds is not None and stop_seconds < first_seconds and day_offset == 0:
            candidate_time = candidate_time + timedelta(days=1)

        day_offset_value = stop_seconds // 86400
        if day_offset_value:
            candidate_time = candidate_time + timedelta(days=day_offset_value)

        seconds_within_day = stop_seconds % 86400
        hours, remainder = divmod(seconds_within_day, 3600)
        minutes, seconds = divmod(remainder, 60)
        scheduled_time = datetime(candidate_time.year, candidate_time.month, candidate_time.day, hours, minutes, seconds, tzinfo=LONDON_TZ).astimezone(timezone.utc)

        distance = abs(int((base_time.astimezone(timezone.utc) - scheduled_time).total_seconds()))
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_scheduled = scheduled_time

    return best_scheduled


def calculate_vehicle_punctuality(
    vehicle: dict[str, object],
    last_stop: dict[str, object] | None,
    trip_schedules: dict[str, object],
    route_id: str | None = None,
    direction: str | None = None,
    reference_time: object | None = None,
    route_sequence: dict[str, object] | None = None,
    service_calendar: dict[str, list[str]] | None = None,
) -> dict[str, object]:
    observed_time = parse_tracking_datetime(vehicle.get('recordedAt') or vehicle.get('sourceTimestamp') or vehicle.get('refreshedAt') or reference_time)
    if observed_time is None:
        observed_time = parse_tracking_datetime(reference_time) or datetime.now(timezone.utc)

    stop_keys = collect_stop_match_keys(last_stop)
    if not stop_keys and not (isinstance(route_sequence, dict) and isinstance(route_sequence.get('stops'), list)):
        return {
            'status': 'unknown',
            'tone': 'neutral',
            'deltaSeconds': 0,
            'label': 'Unknown',
            'detail': 'No matching stop found',
            'scheduledAt': None,
        }

    normalized_route = normalize_tracking_key(route_id or '')
    normalized_direction = normalize_gtfs_direction(str(direction or ''))
    vehicle_identifiers = [normalize_tracking_key(str(value or '')) for value in [
        vehicle.get('journeyRef'),
        vehicle.get('vehicleJourneyRef'),
        vehicle.get('blockRef'),
        vehicle.get('journeyCode'),
    ] if value]

    schedule_matches: list[tuple[str, dict[str, object], dict[str, object]]] = []
    route_stop_index = find_stop_index(last_stop, route_sequence.get('stops', []) if isinstance(route_sequence, dict) else None)
    for trip_id, payload in trip_schedules.items():
        if not isinstance(payload, dict):
            continue
        trip_payload = payload.get('stops', []) if isinstance(payload.get('stops'), list) else []
        if not trip_payload:
            continue
        payload_route = normalize_tracking_key(str(payload.get('routeId') or ''))
        payload_direction = normalize_gtfs_direction(str(payload.get('direction') or ''))
        if normalized_route and payload_route and normalized_route != payload_route:
            continue
        if normalized_direction and payload_direction and normalized_direction != 'unknown' and payload_direction != 'unknown' and normalized_direction != payload_direction:
            continue
        if not service_is_active(str(payload.get('serviceId') or ''), service_calendar, observed_time.astimezone(LONDON_TZ)):
            continue

        matching_entries: list[tuple[int, dict[str, object], int]] = []
        for index, stop_entry in enumerate(trip_payload):
            if not isinstance(stop_entry, dict):
                continue
            exact_match = stop_matches_schedule_entry(last_stop, stop_entry)
            if exact_match:
                penalty = 0
            elif route_stop_index is not None:
                penalty = abs(route_stop_index - index) * 15 + 100
            else:
                penalty = 1000
            matching_entries.append((index, stop_entry, penalty))

        if not matching_entries:
            continue

        best_index = None
        best_entry = None
        best_penalty = None
        for stop_index, stop_entry, penalty in matching_entries:
            if best_entry is None or penalty < best_penalty or (penalty == best_penalty and stop_index > (best_index or -1)):
                best_index = stop_index
                best_entry = stop_entry
                best_penalty = penalty

        if best_entry is None:
            continue

        schedule_entry = best_entry
        if normalize_tracking_key(str(trip_id)) in vehicle_identifiers:
            schedule_matches.insert(0, (str(trip_id), payload, schedule_entry))
        else:
            schedule_matches.append((str(trip_id), payload, schedule_entry))

    if not schedule_matches:
        return {
            'status': 'unknown',
            'tone': 'neutral',
            'deltaSeconds': 0,
            'label': 'Unknown',
            'detail': 'No matching timetable found',
            'scheduledAt': None,
        }

    base_time = parse_tracking_datetime(vehicle.get('originAimedDepartureTime')) or observed_time
    best_choice: tuple[dict[str, object], dict[str, object], datetime, int] | None = None
    for _, payload, schedule_entry in schedule_matches:
        first_stop = None
        if isinstance(payload.get('stops'), list) and payload['stops']:
            first_stop = next((entry for entry in payload['stops'] if isinstance(entry, dict)), None)
        first_stop_time = get_schedule_time_value(first_stop) if first_stop else ''
        scheduled_at = build_scheduled_stop_datetime(observed_time, get_schedule_time_value(schedule_entry), first_stop_time)
        if scheduled_at is None:
            continue

        delta_seconds = int((observed_time - scheduled_at).total_seconds())
        candidate_key = abs(delta_seconds)
        if route_stop_index is not None:
            stop_index = find_stop_index(schedule_entry, payload.get('stops', []) if isinstance(payload.get('stops'), list) else None)
            if stop_index is not None:
                candidate_key += abs(route_stop_index - stop_index) * 15
        if best_choice is None or candidate_key < best_choice[3]:
            best_choice = (payload, schedule_entry, scheduled_at, candidate_key)

    if best_choice is None:
        return {
            'status': 'unknown',
            'tone': 'neutral',
            'deltaSeconds': 0,
            'label': 'Unknown',
            'detail': 'No scheduled time available',
            'scheduledAt': None,
        }

    payload, schedule_entry, scheduled_at, _ = best_choice
    delta_seconds = int((observed_time - scheduled_at).total_seconds())
    if delta_seconds < 0:
        tone = 'red'
        status = 'early'
    elif delta_seconds <= 299:
        tone = 'green'
        status = 'on-time'
    else:
        tone = 'yellow'
        status = 'late'

    return {
        'status': status,
        'tone': tone,
        'deltaSeconds': delta_seconds,
        'label': format_punctuality_label(delta_seconds, scheduled_at),
        'detail': str(schedule_entry.get('name') or 'Scheduled stop'),
        'scheduledAt': scheduled_at,
    }


def route_points_from_stop_sequence(stops: list[dict[str, object]]) -> list[list[float]]:
    points: list[list[float]] = []
    for stop in stops:
        longitude = stop.get('longitude')
        latitude = stop.get('latitude')
        if longitude is None or latitude is None:
            continue
        point = [float(longitude), float(latitude)]
        if not points or points[-1] != point:
            points.append(point)
    return points


def cumulative_path_distances(path: list[list[float]]) -> list[float]:
    distances = [0.0]
    total = 0.0
    for index in range(1, len(path)):
        start_longitude, start_latitude = path[index - 1]
        end_longitude, end_latitude = path[index]
        longitude_scale = 111412.84 * max(0.01, math.cos(math.radians((start_latitude + end_latitude) / 2.0)))
        latitude_scale = 111132.92
        delta_x = (end_longitude - start_longitude) * longitude_scale
        delta_y = (end_latitude - start_latitude) * latitude_scale
        total += math.hypot(delta_x, delta_y)
        distances.append(total)
    return distances


def project_point_onto_path(longitude: float, latitude: float, path: list[list[float]]) -> dict[str, float] | None:
    if len(path) < 2:
        return None

    reference_latitude = latitude
    longitude_scale = 111412.84 * max(0.01, math.cos(math.radians(reference_latitude)))
    latitude_scale = 111132.92

    point_x = longitude * longitude_scale
    point_y = latitude * latitude_scale

    best_distance = float('inf')
    best_along = 0.0
    accumulated = 0.0

    for index in range(len(path) - 1):
        start_longitude, start_latitude = path[index]
        end_longitude, end_latitude = path[index + 1]
        start_x = start_longitude * longitude_scale
        start_y = start_latitude * latitude_scale
        end_x = end_longitude * longitude_scale
        end_y = end_latitude * latitude_scale

        segment_x = end_x - start_x
        segment_y = end_y - start_y
        segment_length = math.hypot(segment_x, segment_y)
        if segment_length == 0:
            continue

        t = ((point_x - start_x) * segment_x + (point_y - start_y) * segment_y) / (segment_length * segment_length)
        t = max(0.0, min(1.0, t))
        projected_x = start_x + (segment_x * t)
        projected_y = start_y + (segment_y * t)
        distance = math.hypot(point_x - projected_x, point_y - projected_y)
        along = accumulated + (segment_length * t)

        if distance < best_distance:
            best_distance = distance
            best_along = along

        accumulated += segment_length

    return {'along': best_along, 'distance': best_distance}


def select_nearest_stop(
    vehicle: dict[str, object],
    stops: list[dict[str, object]],
    max_distance_meters: float = 250.0,
) -> dict[str, object] | None:
    if not stops:
        return None

    latitude = float(vehicle.get('latitude') or 0.0)
    longitude = float(vehicle.get('longitude') or 0.0)
    longitude_scale = 111412.84 * max(0.01, math.cos(math.radians(latitude)))
    latitude_scale = 111132.92

    best_stop: dict[str, object] | None = None
    best_distance = float('inf')
    for stop in stops:
        if not isinstance(stop, dict):
            continue
        stop_lat = stop.get('latitude')
        stop_lon = stop.get('longitude')
        if stop_lat is None or stop_lon is None:
            continue

        delta_x = (float(stop_lon) - longitude) * longitude_scale
        delta_y = (float(stop_lat) - latitude) * latitude_scale
        distance = math.hypot(delta_x, delta_y)
        if distance < best_distance:
            best_distance = distance
            best_stop = stop

    if best_stop is None or best_distance > max_distance_meters:
        return None
    return best_stop


def select_nearest_route_stop(
    vehicle: dict[str, object],
    route_sequence: dict[str, object] | None,
    max_distance_meters: float = 250.0,
) -> dict[str, object] | None:
    if not isinstance(route_sequence, dict):
        return None

    stops = route_sequence.get('stops', [])
    if not isinstance(stops, list):
        return None
    return select_nearest_stop(vehicle, [stop for stop in stops if isinstance(stop, dict)], max_distance_meters=max_distance_meters)


def build_tracking_route_lookup(cache: dict[str, object] | None) -> dict[str, dict[str, object]]:
    if not cache:
        return {}

    lookup: dict[str, dict[str, object]] = {}
    for route in cache.get('routes', []):
        if not isinstance(route, dict):
            continue
        route_id = str(route.get('id') or '').strip()
        route_label = str(route.get('label') or route.get('lineName') or route_id).strip()
        for candidate in {route_id, route_label, str(route.get('lineName') or '').strip()}:
            key = normalize_tracking_key(candidate)
            if key:
                lookup[key] = route
    return lookup


def build_tracking_route_sequences(cache: dict[str, object] | None) -> dict[str, dict[str, object]]:
    if not cache:
        return {}

    route_sequences = cache.get('routeStopSequences', {})
    return route_sequences if isinstance(route_sequences, dict) else {}


def build_stop_next_arrivals(
    stop: dict[str, object] | None,
    trip_schedules: dict[str, object],
    reference_time: object | None = None,
    max_results: int = 5,
    service_calendar: dict[str, list[str]] | None = None,
) -> list[dict[str, object]]:
    if not isinstance(stop, dict) or not isinstance(trip_schedules, dict):
        return []

    reference_now = parse_tracking_datetime(reference_time) or datetime.now(timezone.utc)
    stop_match_keys = collect_stop_match_keys(stop)
    if not stop_match_keys:
        return []

    arrivals: list[dict[str, object]] = []
    stop_trip_index = build_stop_trip_index(trip_schedules)
    candidate_trip_ids: set[str] = set()
    for key in stop_match_keys:
        candidate_trip_ids.update(stop_trip_index.get(key, ()))

    for trip_id in candidate_trip_ids:
        payload = trip_schedules.get(trip_id)
        if not isinstance(payload, dict):
            continue
        schedule_stops = payload.get('stops', []) if isinstance(payload.get('stops'), list) else []
        if not schedule_stops:
            continue
        if service_calendar and not service_is_active(
            str(payload.get('serviceId') or ''), service_calendar, reference_now.astimezone(LONDON_TZ)
        ):
            continue

        first_stop = next((entry for entry in schedule_stops if isinstance(entry, dict)), None)
        first_stop_time = get_schedule_time_value(first_stop) if first_stop else ''
        for schedule_entry in schedule_stops:
            if not isinstance(schedule_entry, dict):
                continue
            if not stop_matches_schedule_entry(stop, schedule_entry):
                continue
            arrival_time = get_schedule_time_value(schedule_entry)
            if not arrival_time:
                continue
            scheduled_at = build_scheduled_stop_datetime(reference_now, arrival_time, first_stop_time)
            if scheduled_at is None:
                continue
            if scheduled_at < reference_now - timedelta(minutes=5):
                continue

            countdown_seconds = max(0, int((scheduled_at - reference_now).total_seconds()))
            arrivals.append(
                {
                    'tripId': str(trip_id),
                    'routeId': str(payload.get('routeId') or '').strip() or None,
                    'serviceId': str(payload.get('serviceId') or '').strip() or None,
                    'direction': normalize_gtfs_direction(str(payload.get('direction') or '')),
                    'scheduledAt': scheduled_at.isoformat(),
                    'scheduledTime': format_local_clock_time(scheduled_at),
                    'countdownSeconds': countdown_seconds,
                    'countdownLabel': format_countdown_label(countdown_seconds),
                    'stopName': str(schedule_entry.get('name') or stop.get('name') or '').strip() or None,
                    'destination': schedule_destination_name(payload),
                    'blockId': str(payload.get('blockId') or '').strip() or None,
                    'originDepartureTime': first_stop_time or None,
                }
            )
            break

    arrivals.sort(key=lambda item: item.get('scheduledAt') or '')
    return arrivals[:max_results]


def format_countdown_label(total_seconds: int) -> str:
    total_seconds = max(0, int(total_seconds))
    if total_seconds <= 0:
        return 'Due now'
    if total_seconds < 60:
        return f'{total_seconds}s'
    hours, remainder = divmod(total_seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    if hours:
        return f'{hours}h {minutes}m'
    return f'{minutes}m'


def format_departure_countdown_label(total_seconds: int) -> str:
    """Departure-board style countdown: anything inside a minute reads 'Due now'."""
    total_seconds = max(0, int(total_seconds))
    if total_seconds <= 60:
        return 'Due now'
    hours, remainder = divmod(total_seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    if hours:
        return f'{hours}h {minutes} min'
    return f'{minutes} min'


def format_local_clock_time(value: datetime | None) -> str | None:
    if not isinstance(value, datetime):
        return None
    try:
        return value.astimezone(LONDON_TZ).strftime('%H:%M')
    except Exception:
        return value.strftime('%H:%M')


def schedule_destination_name(schedule: dict[str, object] | None) -> str | None:
    if not isinstance(schedule, dict):
        return None
    headsign = str(schedule.get('headsign') or '').strip()
    if headsign:
        return headsign
    schedule_stops = schedule.get('stops')
    if isinstance(schedule_stops, list):
        for entry in reversed(schedule_stops):
            if isinstance(entry, dict):
                name = str(entry.get('name') or '').strip()
                if name:
                    return name
    return None


def build_live_stop_arrivals(
    stop: dict[str, object],
    vehicles: list[dict[str, object]],
    trip_schedules: dict[str, object],
    max_results: int = 5,
) -> list[dict[str, object]]:
    if not isinstance(stop, dict) or not isinstance(trip_schedules, dict):
        return []

    stop_latitude = stop.get('latitude')
    stop_longitude = stop.get('longitude')
    if stop_latitude is None or stop_longitude is None:
        return []

    arrivals: list[dict[str, object]] = []
    seen_fleets: set[str] = set()
    for vehicle in vehicles:
        if not isinstance(vehicle, dict):
            continue
        latitude = vehicle.get('latitude')
        longitude = vehicle.get('longitude')
        if latitude is None or longitude is None:
            continue

        route_key = normalize_tracking_key(str(vehicle.get('routeId') or vehicle.get('service') or ''))
        direction = normalize_gtfs_direction(str(vehicle.get('direction') or ''))
        serves_stop = False
        for schedule in trip_schedules.values():
            if not isinstance(schedule, dict):
                continue
            schedule_route_key = normalize_tracking_key(str(schedule.get('routeId') or ''))
            schedule_direction = normalize_gtfs_direction(str(schedule.get('direction') or ''))
            if route_key and schedule_route_key and route_key != schedule_route_key:
                continue
            if direction != 'unknown' and schedule_direction != 'unknown' and direction != schedule_direction:
                continue
            schedule_stops = schedule.get('stops', [])
            if isinstance(schedule_stops, list) and any(
                isinstance(schedule_stop, dict) and stop_matches_schedule_entry(stop, schedule_stop)
                for schedule_stop in schedule_stops
            ):
                serves_stop = True
                break
        if not serves_stop:
            continue

        latitude_scale = 111132.92
        longitude_scale = 111412.84 * max(0.01, math.cos(math.radians(float(stop_latitude))))
        direct_distance_meters = math.hypot(
            (float(longitude) - float(stop_longitude)) * longitude_scale,
            (float(latitude) - float(stop_latitude)) * latitude_scale,
        )
        estimated_seconds = max(30, int((direct_distance_meters * 1.35) / 5.0))
        fleet_number = str(vehicle.get('fleetNumber') or vehicle.get('vehicleRef') or 'Unknown').strip() or 'Unknown'
        fleet_key = normalize_tracking_key(fleet_number)
        if fleet_key in seen_fleets:
            continue
        seen_fleets.add(fleet_key)
        arrivals.append(
            {
                'service': str(vehicle.get('routeLabel') or vehicle.get('routeId') or vehicle.get('service') or 'Unknown').strip() or 'Unknown',
                'fleetNumber': fleet_number,
                'direction': direction,
                'countdownSeconds': estimated_seconds,
                'countdownLabel': format_countdown_label(estimated_seconds),
                'source': 'live',
            }
        )

    arrivals.sort(key=lambda item: int(item.get('countdownSeconds') or 0))
    return arrivals[:max_results]

def build_board_matched_stop_arrivals(
    stop: dict[str, object],
    scheduled_arrivals: list[dict[str, object]],
    trip_schedules: dict[str, object],
    live_vehicles: list[dict[str, object]],
    route_labels: dict[str, str],
    max_results: int = 5,
) -> list[dict[str, object]]:
    if not isinstance(stop, dict):
        return []

    stop_latitude = stop.get('latitude')
    stop_longitude = stop.get('longitude')
    if stop_latitude is None or stop_longitude is None:
        return []

    vehicles_by_board: dict[str, dict[str, object]] = {}
    for vehicle in live_vehicles:
        if not isinstance(vehicle, dict):
            continue
        for value in (
            vehicle.get('boardNumber'),
            vehicle.get('blockRef'),
            vehicle.get('journeyCode'),
            vehicle.get('vehicleJourneyRef'),
            vehicle.get('journeyRef'),
        ):
            board_key = normalize_tracking_key(str(value or ''))
            if board_key:
                vehicles_by_board[board_key] = vehicle

    arrivals: list[dict[str, object]] = []
    seen_fleets: set[str] = set()
    for scheduled_arrival in scheduled_arrivals:
        trip_id = str(scheduled_arrival.get('tripId') or '').strip()
        schedule = trip_schedules.get(trip_id)
        if not isinstance(schedule, dict):
            continue
        board_key = normalize_tracking_key(str(schedule.get('blockId') or ''))
        vehicle = vehicles_by_board.get(board_key) if board_key else None
        if not isinstance(vehicle, dict):
            continue

        latitude = vehicle.get('latitude')
        longitude = vehicle.get('longitude')
        if latitude is None or longitude is None:
            continue
        latitude_scale = 111132.92
        longitude_scale = 111412.84 * max(0.01, math.cos(math.radians(float(stop_latitude))))
        direct_distance_meters = math.hypot(
            (float(longitude) - float(stop_longitude)) * longitude_scale,
            (float(latitude) - float(stop_latitude)) * latitude_scale,
        )
        estimated_seconds = max(30, int((direct_distance_meters * 1.35) / 5.0))
        fleet_number = str(vehicle.get('fleetNumber') or 'Unknown').strip() or 'Unknown'
        fleet_key = normalize_tracking_key(fleet_number)
        if fleet_key in seen_fleets:
            continue
        seen_fleets.add(fleet_key)

        route_id = str(schedule.get('routeId') or scheduled_arrival.get('routeId') or '').strip()
        service = str(vehicle.get('routeLabel') or '').strip() or route_labels.get(route_id) or route_id or 'Unknown'
        arrivals.append(
            {
                'service': service,
                'fleetNumber': fleet_number,
                'boardNumber': str(vehicle.get('boardNumber') or schedule.get('blockId') or '').strip() or None,
                'direction': vehicle.get('direction') or scheduled_arrival.get('direction'),
                'countdownSeconds': estimated_seconds,
                'countdownLabel': format_countdown_label(estimated_seconds),
                'source': 'live-board',
            }
        )

    arrivals.sort(key=lambda item: int(item.get('countdownSeconds') or 0))
    return arrivals[:max_results]


def index_live_vehicles_for_stop(live_vehicles: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    index: dict[str, dict[str, object]] = {}
    for vehicle in live_vehicles or []:
        if not isinstance(vehicle, dict):
            continue
        for value in (
            vehicle.get('boardNumber'),
            vehicle.get('blockRef'),
            vehicle.get('journeyCode'),
            vehicle.get('vehicleJourneyRef'),
            vehicle.get('journeyRef'),
        ):
            board_key = normalize_tracking_key(str(value or ''))
            if board_key:
                index.setdefault(board_key, vehicle)
    return index


def vehicle_matches_trip_service(
    vehicle: dict[str, object],
    schedule: dict[str, object],
    scheduled_arrival: dict[str, object],
    route_labels: dict[str, str],
) -> bool:
    """Journey codes repeat across services, so line and direction must agree as well."""
    if not isinstance(vehicle, dict):
        return False

    route_id = str(schedule.get('routeId') or scheduled_arrival.get('routeId') or '').strip()
    expected_line = normalize_tracking_key(str(route_labels.get(route_id) or '').strip())
    vehicle_line = normalize_tracking_key(str(vehicle.get('service') or vehicle.get('routeLabel') or '').strip())
    if expected_line and vehicle_line and expected_line != vehicle_line:
        return False

    schedule_direction = normalize_gtfs_direction(str(schedule.get('direction') or scheduled_arrival.get('direction') or ''))
    vehicle_direction = normalize_gtfs_direction(str(vehicle.get('direction') or ''))
    if schedule_direction != 'unknown' and vehicle_direction != 'unknown' and schedule_direction != vehicle_direction:
        return False

    return True


def match_live_vehicle_to_trip(
    scheduled_arrival: dict[str, object],
    schedule: dict[str, object],
    vehicles_by_board: dict[str, dict[str, object]],
    live_vehicles: list[dict[str, object]],
    reference_now: datetime,
    route_labels: dict[str, str] | None = None,
    vehicle_origins: list[tuple[dict[str, object], datetime]] | None = None,
) -> dict[str, object] | None:
    labels = route_labels or {}

    for key in (
        normalize_tracking_key(str(schedule.get('blockId') or '')),
        normalize_tracking_key(str(scheduled_arrival.get('tripId') or '')),
    ):
        candidate = vehicles_by_board.get(key) if key else None
        if isinstance(candidate, dict) and vehicle_matches_trip_service(candidate, schedule, scheduled_arrival, labels):
            return candidate

    origin_time = str(scheduled_arrival.get('originDepartureTime') or '').strip()
    if not origin_time:
        return None
    scheduled_origin = build_scheduled_stop_datetime(reference_now, origin_time, origin_time)
    if scheduled_origin is None:
        return None

    if vehicle_origins is None:
        vehicle_origins = build_vehicle_origin_index(live_vehicles)

    best_vehicle: dict[str, object] | None = None
    best_delta: float | None = None
    for vehicle, vehicle_origin in vehicle_origins:
        delta = abs((vehicle_origin - scheduled_origin).total_seconds())
        if delta > 300:
            continue
        if not vehicle_matches_trip_service(vehicle, schedule, scheduled_arrival, labels):
            continue
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best_vehicle = vehicle

    return best_vehicle


def build_vehicle_origin_index(
    live_vehicles: list[dict[str, object]] | None,
) -> list[tuple[dict[str, object], datetime]]:
    """Parse each vehicle's aimed origin departure once per board rather than per candidate trip."""
    origins: list[tuple[dict[str, object], datetime]] = []
    for vehicle in live_vehicles or []:
        if not isinstance(vehicle, dict):
            continue
        vehicle_origin = parse_tracking_datetime(vehicle.get('originAimedDepartureTime'))
        if vehicle_origin is not None:
            origins.append((vehicle, vehicle_origin))
    return origins


def build_stop_departure_board(
    stop: dict[str, object],
    trip_schedules: dict[str, object],
    live_vehicles: list[dict[str, object]],
    route_labels: dict[str, str],
    reference_time: object | None = None,
    max_results: int = 6,
    service_calendar: dict[str, list[str]] | None = None,
) -> list[dict[str, object]]:
    """Departure-board style arrivals: live predictions where matched, timetable otherwise."""
    if not isinstance(stop, dict) or not isinstance(trip_schedules, dict):
        return []

    reference_now = parse_tracking_datetime(reference_time) or datetime.now(timezone.utc)
    scheduled_arrivals = build_stop_next_arrivals(
        stop,
        trip_schedules,
        reference_now,
        max_results=max_results * 8,
        service_calendar=service_calendar,
    )
    if not scheduled_arrivals:
        return []

    vehicles_by_board = index_live_vehicles_for_stop(live_vehicles)
    vehicle_origins = build_vehicle_origin_index(live_vehicles)
    claimed_vehicle_ids: set[str] = set()
    board: list[dict[str, object]] = []

    for scheduled_arrival in scheduled_arrivals:
        trip_id = str(scheduled_arrival.get('tripId') or '').strip()
        schedule = trip_schedules.get(trip_id)
        if not isinstance(schedule, dict):
            schedule = {}

        route_id = str(schedule.get('routeId') or scheduled_arrival.get('routeId') or '').strip()
        direction = normalize_gtfs_direction(str(schedule.get('direction') or scheduled_arrival.get('direction') or ''))
        scheduled_seconds = int(scheduled_arrival.get('countdownSeconds') or 0)
        scheduled_at = parse_tracking_datetime(scheduled_arrival.get('scheduledAt'))

        vehicle = match_live_vehicle_to_trip(
            scheduled_arrival,
            schedule,
            vehicles_by_board,
            live_vehicles,
            reference_now,
            route_labels,
            vehicle_origins,
        )
        vehicle_id = str(vehicle.get('id') or '').strip() if isinstance(vehicle, dict) else ''
        if vehicle_id and vehicle_id in claimed_vehicle_ids:
            vehicle = None
        if isinstance(vehicle, dict) and vehicle_id:
            claimed_vehicle_ids.add(vehicle_id)

        is_live = isinstance(vehicle, dict)
        if is_live:
            punctuality = vehicle.get('punctuality') if isinstance(vehicle.get('punctuality'), dict) else {}
            delay_seconds = int(punctuality.get('deltaSeconds') or 0)
            expected_seconds = max(0, scheduled_seconds + delay_seconds)
            expected_at = reference_now + timedelta(seconds=expected_seconds)
            service = (
                route_labels.get(route_id)
                or str(vehicle.get('service') or '').strip()
                or route_id
                or 'Unknown'
            )
            # The timetable is authoritative for where this journey actually goes.
            destination = (
                schedule_destination_name(schedule)
                or scheduled_arrival.get('destination')
                or str(vehicle.get('destination') or '').strip()
                or 'Unknown'
            )
            fleet_number = str(vehicle.get('fleetNumber') or '').strip() or None
            board_number = str(vehicle.get('boardNumber') or schedule.get('blockId') or '').strip() or None
        else:
            expected_seconds = scheduled_seconds
            expected_at = scheduled_at or (reference_now + timedelta(seconds=expected_seconds))
            service = route_labels.get(route_id) or route_id or 'Unknown'
            destination = schedule_destination_name(schedule) or scheduled_arrival.get('destination') or 'Unknown'
            fleet_number = None
            board_number = str(schedule.get('blockId') or '').strip() or None

        board.append(
            {
                'tripId': trip_id or None,
                'service': service,
                'routeId': route_id or None,
                'direction': direction,
                'destination': str(destination or 'Unknown').strip() or 'Unknown',
                'fleetNumber': fleet_number,
                'boardNumber': board_number,
                'isLive': is_live,
                'source': 'live' if is_live else 'scheduled',
                'countdownSeconds': expected_seconds,
                'countdownLabel': format_departure_countdown_label(expected_seconds),
                'expectedAt': expected_at.isoformat() if isinstance(expected_at, datetime) else None,
                'scheduledAt': scheduled_arrival.get('scheduledAt'),
                'scheduledTime': scheduled_arrival.get('scheduledTime')
                or format_local_clock_time(scheduled_at),
            }
        )

    board.sort(key=lambda item: (int(item.get('countdownSeconds') or 0), str(item.get('service') or '')))
    return board[:max_results]


def select_last_stop_passed(vehicle: dict[str, object], route_sequence: dict[str, object] | None) -> dict[str, object] | None:
    if not route_sequence:
        return None

    stops = route_sequence.get('stops', [])
    if not isinstance(stops, list) or len(stops) < 2:
        return None

    path = route_points_from_stop_sequence(stops)
    projection = project_point_onto_path(float(vehicle['longitude']), float(vehicle['latitude']), path)
    if projection is None:
        return None

    cumulative = cumulative_path_distances(path)
    selected_stop: dict[str, object] | None = None
    best_distance: float | None = None
    for stop, stop_along in zip(stops, cumulative):
        if not isinstance(stop, dict):
            continue
        distance = abs(stop_along - projection['along'])
        if best_distance is None or distance < best_distance:
            best_distance = distance
            selected_stop = stop

    return selected_stop


def enrich_tracking_vehicles(vehicles: list[dict[str, object]], cache: dict[str, object] | None) -> list[dict[str, object]]:
    route_lookup = build_tracking_route_lookup(cache)
    route_sequences = build_tracking_route_sequences(cache)
    all_stops = [stop for stop in (cache or {}).get('stops', []) if isinstance(stop, dict)]
    enriched: list[dict[str, object]] = []
    trip_schedules = cache.get('tripSchedules', {}) if isinstance(cache, dict) else {}
    service_calendar = cache.get('serviceCalendar', {}) if isinstance(cache, dict) else {}

    for vehicle in vehicles:
        service = str(vehicle.get('service') or '').strip()
        normalized_service = normalize_tracking_key(service)
        route = route_lookup.get(normalized_service)
        route_id = str(route.get('id') or '').strip() if route else ''
        route_label = str((route.get('label') if route else '') or (route.get('lineName') if route else '') or service or route_id).strip()
        direction = normalize_gtfs_direction(str(vehicle.get('direction') or ''))
        route_direction_sequences = route_sequences.get(route_id, {}) if route_id else {}
        if not isinstance(route_direction_sequences, dict):
            route_direction_sequences = {}

        selected_sequence = None
        if direction in route_direction_sequences:
            selected_sequence = route_direction_sequences.get(direction)
        elif 'unknown' in route_direction_sequences:
            selected_sequence = route_direction_sequences.get('unknown')
        elif route_direction_sequences:
            selected_sequence = next(iter(route_direction_sequences.values()))

        last_stop = select_last_stop_passed(vehicle, selected_sequence if isinstance(selected_sequence, dict) else None)
        if last_stop is None:
            last_stop = select_nearest_route_stop(vehicle, selected_sequence if isinstance(selected_sequence, dict) else None)
        if last_stop is None and all_stops:
            last_stop = select_nearest_stop(vehicle, all_stops)

        board_number = (
            str(
                vehicle.get('blockRef')
                or vehicle.get('journeyCode')
                or vehicle.get('vehicleJourneyRef')
                or vehicle.get('journeyRef')
                or vehicle.get('boardNumber')
                or ''
            ).strip()
            or None
        )
        punctuality = calculate_vehicle_punctuality(
            vehicle,
            last_stop,
            trip_schedules,
            route_id=route_id or service,
            direction=direction,
            reference_time=vehicle.get('recordedAt') or vehicle.get('sourceTimestamp') or vehicle.get('refreshedAt'),
            route_sequence=selected_sequence if isinstance(selected_sequence, dict) else None,
            service_calendar=service_calendar,
        )
        enriched.append(
            {
                'id': vehicle.get('id') or vehicle.get('journeyRef') or vehicle.get('vehicleJourneyRef') or service,
                'latitude': float(vehicle.get('latitude', 0.0)),
                'longitude': float(vehicle.get('longitude', 0.0)),
                'service': service,
                'destination': str(vehicle.get('destination') or '').strip(),
                'direction': direction,
                'fleetNumber': str(vehicle.get('fleetNumber') or vehicle.get('vehicleRef') or '').strip() or None,
                'operator': str(vehicle.get('operator') or '').strip() or None,
                'recordedAt': str(vehicle.get('recordedAt') or vehicle.get('sourceTimestamp') or '').strip() or None,
                'originAimedDepartureTime': str(vehicle.get('originAimedDepartureTime') or '').strip() or None,
                'destinationAimedArrivalTime': str(vehicle.get('destinationAimedArrivalTime') or '').strip() or None,
                'journeyRef': str(vehicle.get('journeyRef') or '').strip() or None,
                'vehicleJourneyRef': str(vehicle.get('vehicleJourneyRef') or '').strip() or None,
                'blockRef': str(vehicle.get('blockRef') or '').strip() or None,
                'journeyCode': str(vehicle.get('journeyCode') or '').strip() or None,
                'boardNumber': board_number,
                'punctuality': punctuality,
                'lastStopPassed': (
                    {
                        'id': str(last_stop.get('stopId') or last_stop.get('id') or '').strip(),
                        'name': str(last_stop.get('name') or last_stop.get('stopName') or 'Unknown stop').strip(),
                        'latitude': float(last_stop.get('latitude', 0.0)),
                        'longitude': float(last_stop.get('longitude', 0.0)),
                    }
                    if last_stop
                    else None
                ),
            }
        )

    return enriched


def group_active_services(vehicles: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, dict[str, object]] = {}

    for vehicle in vehicles:
        route_id = str(vehicle.get('routeId') or vehicle.get('service') or 'unknown').strip()
        route_label = str(vehicle.get('routeLabel') or vehicle.get('service') or route_id).strip()
        key = normalize_tracking_key(route_id or route_label)
        group = grouped.setdefault(
            key,
            {
                'routeId': route_id,
                'routeLabel': route_label,
                'activeCount': 0,
                'vehicles': [],
            },
        )
        group['activeCount'] = int(group['activeCount']) + 1
        group['vehicles'].append(vehicle)

    ordered_groups = sorted(
        grouped.values(),
        key=lambda item: route_sort_key(str(item.get('routeLabel') or item.get('routeId') or '')),
    )

    for group in ordered_groups:
        group['vehicles'] = sorted(
            group['vehicles'],
            key=lambda item: (
                str(item.get('direction') or '').lower(),
                str(item.get('destination') or '').lower(),
                str(item.get('fleetNumber') or '').lower(),
            ),
        )

    return ordered_groups


def serialize_tracking_stop(
    stop: dict[str, object],
    trip_schedules: dict[str, object] | None = None,
    reference_time: object | None = None,
) -> dict[str, object]:
    naptan = str(
        stop.get('naptan')
        or stop.get('stopCode')
        or stop.get('atcoCode')
        or stop.get('stopPointRef')
        or stop.get('stopRef')
        or stop.get('stopId')
        or stop.get('id')
        or ''
    ).strip()
    next_arrivals = list(stop.get('nextArrivals') or [])
    if not next_arrivals and isinstance(trip_schedules, dict):
        next_arrivals = build_stop_next_arrivals(stop, trip_schedules, reference_time, max_results=5)

    return {
        'id': str(stop.get('stopId') or stop.get('id') or '').strip(),
        'naptan': naptan or None,
        'stopCode': naptan or None,
        'name': str(stop.get('name') or stop.get('stopName') or 'Unknown stop').strip(),
        'latitude': float(stop.get('latitude', 0.0)),
        'longitude': float(stop.get('longitude', 0.0)),
        'nextArrivals': next_arrivals,
    }


def route_sort_key(route: str) -> tuple[int, int, str]:
    value = str(route or '').strip()
    match = re.match(r'^(\d+)', value)
    if match:
        return (0, int(match.group(1)), value.lower())
    return (1, 9999, value.lower())


def normalize_gtfs_direction(value: str) -> str:
    normalized = str(value or '').strip().lower()
    if normalized in {'0', 'inbound', 'in'}:
        return 'inbound'
    if normalized in {'1', 'outbound', 'out'}:
        return 'outbound'
    return 'unknown'


def read_gtfs_rows(file_path: Path) -> list[dict[str, str]]:
    with file_path.open('r', encoding='utf-8-sig', errors='replace', newline='') as handle:
        reader = csv.DictReader(handle)
        return [
            {str(key or '').strip(): str(value or '').strip() for key, value in row.items()}
            for row in reader
        ]


def find_gtfs_file(extracted_dir: Path, filename: str) -> Path | None:
    target = filename.lower()
    for path in extracted_dir.rglob('*'):
        if not path.is_file():
            continue
        if path.name.lower() == target:
            return path
    return None


def load_gtfs_stops_from_directory(extracted_dir: Path) -> list[dict[str, object]]:
    stops_path = find_gtfs_file(extracted_dir, 'stops.txt')
    if stops_path is None:
        return []

    stops_lookup: dict[str, dict[str, object]] = {}
    for row in read_gtfs_rows(stops_path):
        stop_id = str(row.get('stop_id') or '').strip()
        stop_name = str(row.get('stop_name') or '').strip()
        lon_text = str(row.get('stop_lon') or '').strip()
        lat_text = str(row.get('stop_lat') or '').strip()
        if not stop_id or not lon_text or not lat_text:
            continue
        try:
            longitude = float(lon_text)
            latitude = float(lat_text)
        except ValueError:
            continue
        naptan_code = str(row.get('stop_code') or row.get('atco_code') or stop_id).strip()
        stops_lookup[stop_id] = {
            'stopId': stop_id,
            'naptan': naptan_code,
            'atcoCode': naptan_code,
            'name': stop_name or stop_id,
            'longitude': longitude,
            'latitude': latitude,
        }

    return sorted(
        stops_lookup.values(),
        key=lambda stop: (
            str(stop.get('name') or '').lower(),
            str(stop.get('stopId') or '').lower(),
        ),
    )


def ensure_gtfs_cache_stops(cache: dict[str, object] | None) -> dict[str, object] | None:
    if cache is not None and isinstance(cache.get('stops'), list) and cache.get('stops'):
        return cache

    fallback_stops = load_gtfs_stops_from_directory(GTFS_EXTRACT_DIR)
    if not fallback_stops:
        return cache

    updated_cache = dict(cache or {})
    updated_cache['stops'] = fallback_stops
    updated_cache.setdefault('routeStopSequences', {})
    try:
        GTFS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        GTFS_CACHE_PATH.write_text(json.dumps(updated_cache), encoding='utf-8')
    except OSError:
        pass
    return updated_cache


def _lookup_roadworks_field(row: dict[str, str], field: str) -> str:
    for alias in ROADWORKS_FIELD_ALIASES.get(field, ()):
        for key, value in row.items():
            if str(key or '').strip().lower() == alias and str(value or '').strip():
                return str(value).strip()
    return ''


def normalize_roadworks_rag(severity: str, status: str) -> str:
    """Derive a red/amber/green rating from free-text severity or status values."""
    severity_text = str(severity or '').strip().lower()
    status_text = str(status or '').strip().lower()
    if any(token in severity_text for token in ('red', 'severe', 'high', 'major', 'critical')):
        return 'red'
    if any(token in severity_text for token in ('amber', 'medium', 'moderate')):
        return 'amber'
    if any(token in severity_text for token in ('green', 'low', 'minor')):
        return 'green'
    if any(token in status_text for token in ('complete', 'closed', 'cancelled', 'canceled', 'finished')):
        return 'green'
    return 'amber'


def parse_roadworks_csv(raw_bytes: bytes) -> list[dict[str, object]]:
    try:
        text = raw_bytes.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = raw_bytes.decode('latin-1')

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError('The CSV file has no header row.')

    entries: list[dict[str, object]] = []
    for index, row in enumerate(reader, start=1):
        latitude_raw = _lookup_roadworks_field(row, 'latitude')
        longitude_raw = _lookup_roadworks_field(row, 'longitude')
        try:
            latitude = float(latitude_raw)
            longitude = float(longitude_raw)
        except (TypeError, ValueError):
            continue
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            continue

        severity = _lookup_roadworks_field(row, 'severity')
        status = _lookup_roadworks_field(row, 'status')
        reference = _lookup_roadworks_field(row, 'reference') or f'ROW-{index}'
        title = _lookup_roadworks_field(row, 'title') or 'Roadworks'

        entries.append({
            'id': reference,
            'reference': reference,
            'title': title,
            'latitude': latitude,
            'longitude': longitude,
            'severity': severity or 'Unknown',
            'status': status or 'Unknown',
            'rag': normalize_roadworks_rag(severity, status),
            'startDate': _lookup_roadworks_field(row, 'start_date'),
            'endDate': _lookup_roadworks_field(row, 'end_date'),
            'promoter': _lookup_roadworks_field(row, 'promoter'),
            'impact': _lookup_roadworks_field(row, 'impact'),
        })

    if not entries:
        raise ValueError('No valid roadworks rows were found. Ensure the CSV includes latitude and longitude columns.')

    return entries


def save_roadworks_data(csv_bytes: bytes, entries: list[dict[str, object]], original_filename: str) -> dict[str, object]:
    ROADWORKS_DIR.mkdir(parents=True, exist_ok=True)
    # Overwriting both files means the previous upload's roadworks never persist.
    ROADWORKS_CSV_PATH.write_bytes(csv_bytes)
    payload = {
        'uploadedAt': datetime.now(timezone.utc).isoformat(),
        'originalFilename': original_filename,
        'roadworksCount': len(entries),
        'roadworks': entries,
    }
    ROADWORKS_CACHE_PATH.write_text(json.dumps(payload), encoding='utf-8')
    return payload


def load_roadworks_cache() -> dict[str, object]:
    if not ROADWORKS_CACHE_PATH.exists():
        return {'roadworks': [], 'roadworksCount': 0, 'uploadedAt': '', 'originalFilename': ''}
    try:
        return json.loads(ROADWORKS_CACHE_PATH.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {'roadworks': [], 'roadworksCount': 0, 'uploadedAt': '', 'originalFilename': ''}


def unzip_gtfs_archive(zip_bytes: bytes) -> Path:
    GTFS_DIR.mkdir(parents=True, exist_ok=True)
    if GTFS_EXTRACT_DIR.exists():
        shutil.rmtree(GTFS_EXTRACT_DIR)
    GTFS_EXTRACT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as error:
        raise ValueError('The uploaded file is not a valid ZIP archive.') from error

    with archive:
        for member in archive.infolist():
            if member.is_dir():
                continue

            member_name = member.filename.replace('\\', '/')
            if member_name.startswith('/'):
                continue

            destination = (GTFS_EXTRACT_DIR / member_name).resolve()
            root = GTFS_EXTRACT_DIR.resolve()
            if not str(destination).startswith(str(root)):
                continue

            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source_handle:
                destination.write_bytes(source_handle.read())

    # If this is a TransXChange ZIP, synthesize GTFS text files for downstream parsing.
    if find_gtfs_file(GTFS_EXTRACT_DIR, 'routes.txt') is None:
        convert_transxchange_directory_to_gtfs(GTFS_EXTRACT_DIR)

    return GTFS_EXTRACT_DIR



def extract_route_prefix(route_id: object) -> str:
    value = str(route_id or '').strip().upper()
    if not value:
        return ''
    if ':' in value:
        return value.split(':', 1)[0].strip()
    return value


def extract_route_prefixes_from_cache(cache: dict[str, object] | None) -> list[str]:
    if not isinstance(cache, dict):
        return []
    routes = cache.get('routes')
    if not isinstance(routes, list):
        return []

    seen: set[str] = set()
    ordered: list[str] = []
    for route in routes:
        if not isinstance(route, dict):
            continue
        prefix = extract_route_prefix(route.get('id'))
        if not prefix or prefix in seen:
            continue
        seen.add(prefix)
        ordered.append(prefix)
    return ordered



def detect_best_pc_route_prefix(extracted_dir: Path) -> str:
    routes_path = find_gtfs_file(extracted_dir, 'routes.txt')
    if routes_path is None:
        return ''

    counts: dict[str, int] = {}
    for row in read_gtfs_rows(routes_path):
        prefix = extract_route_prefix(row.get('route_id'))
        if not prefix or not re.match(r'^PC\d+$', prefix):
            continue
        counts[prefix] = counts.get(prefix, 0) + 1

    if not counts:
        return ''

    def sort_key(item: tuple[str, int]) -> tuple[int, int, str]:
        prefix, count = item
        try:
            numeric = int(prefix[2:])
        except ValueError:
            numeric = -1
        return (count, numeric, prefix)

    return max(counts.items(), key=sort_key)[0]

def parse_iso8601_duration_seconds(value: str) -> int:
    text = str(value or '').strip().upper()
    if not text:
        return 0
    match = re.match(r'^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$', text)
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return max(0, (hours * 3600) + (minutes * 60) + seconds)


def format_gtfs_hhmmss(total_seconds: int) -> str:
    safe = max(0, int(total_seconds))
    hours = safe // 3600
    minutes = (safe % 3600) // 60
    seconds = safe % 60
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}'


def extract_transxchange_section_sequences(root: ET.Element, namespace: dict[str, str]) -> dict[str, list[dict[str, object]]]:
    sequences: dict[str, list[dict[str, object]]] = {}
    for section in root.findall('.//tx:JourneyPatternSection', namespace):
        section_id = str(section.attrib.get('id') or '').strip()
        if not section_id:
            continue
        links = []
        for link in section.findall('tx:JourneyPatternTimingLink', namespace):
            link_id = str(link.attrib.get('id') or '').strip()
            from_node = link.find('tx:From', namespace)
            to_node = link.find('tx:To', namespace)
            from_stop = str(from_node.findtext('tx:StopPointRef', default='', namespaces=namespace) if from_node is not None else '').strip()
            to_stop = str(to_node.findtext('tx:StopPointRef', default='', namespaces=namespace) if to_node is not None else '').strip()
            try:
                sequence = int(str((from_node.attrib.get('SequenceNumber') if from_node is not None else '') or '0').strip() or '0')
            except ValueError:
                sequence = 0
            runtime_text = str(link.findtext('tx:RunTime', default='', namespaces=namespace) or '').strip()
            links.append(
                {
                    'linkId': link_id,
                    'sequence': sequence,
                    'fromStop': from_stop,
                    'toStop': to_stop,
                    'runtimeSeconds': parse_iso8601_duration_seconds(runtime_text),
                }
            )
        links.sort(key=lambda item: int(item.get('sequence', 0)))
        if links:
            sequences[section_id] = links
    return sequences


def convert_transxchange_directory_to_gtfs(extracted_dir: Path) -> bool:
    xml_files = [path for path in extracted_dir.rglob('*.xml') if path.is_file()]
    if not xml_files:
        return False

    def tx_file_sort_key(path: Path) -> tuple[str, str]:
        match = re.search(r'_(\d{8})_', path.name)
        date_key = str(match.group(1)) if match else '00000000'
        return (date_key, path.name)

    xml_files.sort(key=tx_file_sort_key, reverse=True)
    if TRANSXCHANGE_MAX_FILES > 0:
        xml_files = xml_files[:TRANSXCHANGE_MAX_FILES]

    tx_ns = {'tx': 'http://www.transxchange.org.uk/'}
    stops_lookup: dict[str, dict[str, object]] = {}
    routes: dict[str, dict[str, str]] = {}
    journey_patterns: dict[str, dict[str, object]] = {}
    section_links: dict[str, list[dict[str, object]]] = {}
    trips: list[dict[str, object]] = []
    service_days: dict[str, set[str]] = {}
    service_periods: dict[str, tuple[str, str]] = {}

    for xml_path in xml_files:
        try:
            root = ET.fromstring(xml_path.read_text(encoding='utf-8', errors='replace'))
        except Exception:
            continue

        file_scope = re.sub(r'[^A-Za-z0-9]+', '_', xml_path.stem).strip('_').lower() or 'tx'

        def scoped(value: object) -> str:
            raw = str(value or '').strip()
            return f'{file_scope}:{raw}' if raw else ''

        for stop_node in root.findall('.//tx:AnnotatedStopPointRef', tx_ns):
            stop_ref = str(stop_node.findtext('tx:StopPointRef', default='', namespaces=tx_ns) or '').strip()
            if not stop_ref:
                continue
            name = str(stop_node.findtext('tx:CommonName', default='', namespaces=tx_ns) or stop_ref).strip()
            lon_text = str(stop_node.findtext('tx:Location/tx:Longitude', default='', namespaces=tx_ns) or '').strip()
            lat_text = str(stop_node.findtext('tx:Location/tx:Latitude', default='', namespaces=tx_ns) or '').strip()
            try:
                lon = float(lon_text)
                lat = float(lat_text)
            except ValueError:
                continue
            stops_lookup[stop_ref] = {
                'stop_id': stop_ref,
                'stop_code': stop_ref,
                'stop_name': name,
                'stop_lon': lon,
                'stop_lat': lat,
            }

        line_name = str(root.findtext('.//tx:Lines/tx:Line/tx:LineName', default='', namespaces=tx_ns) or '').strip()
        service_code = str(root.findtext('.//tx:ServiceCode', default='', namespaces=tx_ns) or '').strip()
        route_id = service_code or line_name
        if route_id:
            routes.setdefault(
                route_id,
                {
                    'route_id': route_id,
                    'agency_id': GTFS_ALLOWED_AGENCY_ID or '',
                    'route_short_name': line_name or route_id,
                    'route_long_name': str(root.findtext('.//tx:OutboundDescription/tx:Description', default='', namespaces=tx_ns) or '').strip(),
                },
            )

        service_ref_for_file = str(root.findtext('.//tx:Service/tx:ServiceCode', default='', namespaces=tx_ns) or '').strip()
        # Mon-Fri/Sat/Sun variants of a service share one ServiceCode across files, so scope per file.
        scoped_service_id = scoped(service_ref_for_file)
        if scoped_service_id:
            operating_profile = root.find('.//tx:Service/tx:OperatingProfile', tx_ns)
            if operating_profile is not None:
                days_node = operating_profile.find('tx:RegularDayType/tx:DaysOfWeek', tx_ns)
                if days_node is not None:
                    for day_element in days_node:
                        day_name = day_element.tag.split('}')[-1].strip().lower()
                        if day_name in GTFS_WEEKDAY_COLUMNS:
                            service_days.setdefault(scoped_service_id, set()).add(day_name)
                        elif day_name in TRANSXCHANGE_DAY_GROUPS:
                            service_days.setdefault(scoped_service_id, set()).update(TRANSXCHANGE_DAY_GROUPS[day_name])
            period = root.find('.//tx:Service/tx:OperatingPeriod', tx_ns)
            if period is not None:
                start_text = str(period.findtext('tx:StartDate', default='', namespaces=tx_ns) or '').strip()
                end_text = str(period.findtext('tx:EndDate', default='', namespaces=tx_ns) or '').strip()
                if start_text:
                    service_periods[scoped_service_id] = (start_text, end_text)

        current_sections = extract_transxchange_section_sequences(root, tx_ns)
        for section_id, links in current_sections.items():
            scoped_section_id = scoped(section_id)
            scoped_links: list[dict[str, object]] = []
            for link in links:
                scoped_link = dict(link)
                scoped_link['linkId'] = scoped(link.get('linkId') or '')
                scoped_links.append(scoped_link)
            section_links[scoped_section_id] = scoped_links

        for jp in root.findall('.//tx:JourneyPattern', tx_ns):
            raw_jp_id = str(jp.attrib.get('id') or '').strip()
            jp_id = scoped(raw_jp_id)
            if not jp_id:
                continue
            direction = str(jp.findtext('tx:Direction', default='', namespaces=tx_ns) or '').strip().lower()
            section_refs_text = str(jp.findtext('tx:JourneyPatternSectionRefs', default='', namespaces=tx_ns) or '').strip()
            section_refs = [scoped(value) for value in re.split(r'\s+', section_refs_text) if value]
            jp_route_ref = str(jp.findtext('tx:RouteRef', default='', namespaces=tx_ns) or '').strip()
            journey_patterns[jp_id] = {
                'routeId': route_id or jp_route_ref or raw_jp_id,
                'directionId': '1' if direction == 'outbound' else ('0' if direction == 'inbound' else ''),
                'sectionRefs': section_refs,
                'destinationDisplay': str(jp.findtext('tx:DestinationDisplay', default='', namespaces=tx_ns) or '').strip(),
            }

        for trip in root.findall('.//tx:VehicleJourney', tx_ns):
            raw_trip_id = str(trip.findtext('tx:VehicleJourneyCode', default='', namespaces=tx_ns) or '').strip()
            raw_jp_ref = str(trip.findtext('tx:JourneyPatternRef', default='', namespaces=tx_ns) or '').strip()
            trip_id = scoped(raw_trip_id)
            jp_ref = scoped(raw_jp_ref)
            if not trip_id or not jp_ref:
                continue
            departure_time = str(trip.findtext('tx:DepartureTime', default='00:00:00', namespaces=tx_ns) or '00:00:00').strip()
            service_ref = str(trip.findtext('tx:ServiceRef', default='', namespaces=tx_ns) or '').strip()
            # This feed carries the board/duty identifier as a ticket machine journey code.
            journey_code = str(
                trip.findtext('tx:Operational/tx:TicketMachine/tx:JourneyCode', default='', namespaces=tx_ns)
                or trip.findtext('tx:Operational/tx:Block/tx:BlockNumber', default='', namespaces=tx_ns)
                or ''
            ).strip()
            trip_destination = str(trip.findtext('tx:DestinationDisplay', default='', namespaces=tx_ns) or '').strip()
            timing_overrides: dict[str, int] = {}
            for link in trip.findall('tx:VehicleJourneyTimingLink', tx_ns):
                jp_link_ref = str(link.findtext('tx:JourneyPatternTimingLinkRef', default='', namespaces=tx_ns) or '').strip()
                run_time = str(link.findtext('tx:RunTime', default='', namespaces=tx_ns) or '').strip()
                if jp_link_ref:
                    timing_overrides[scoped(jp_link_ref)] = parse_iso8601_duration_seconds(run_time)
            trips.append(
                {
                    'tripId': trip_id,
                    'journeyPatternRef': jp_ref,
                    'departureTime': departure_time,
                    'serviceRef': scoped(service_ref or route_id or raw_jp_ref),
                    'timingOverrides': timing_overrides,
                    'journeyCode': journey_code,
                    'destinationDisplay': trip_destination,
                }
            )

    if not routes or not trips or not stops_lookup:
        return False

    route_rows: list[dict[str, str]] = []
    for route in routes.values():
        route_rows.append(
            {
                'route_id': str(route.get('route_id') or ''),
                'agency_id': str(route.get('agency_id') or ''),
                'route_short_name': str(route.get('route_short_name') or ''),
                'route_long_name': str(route.get('route_long_name') or ''),
            }
        )

    trip_rows: list[dict[str, str]] = []
    shape_rows: list[dict[str, str]] = []
    stop_time_rows: list[dict[str, str]] = []
    emitted_shape_ids: set[str] = set()

    for trip in trips:
        trip_id = str(trip.get('tripId') or '').strip()
        jp_ref = str(trip.get('journeyPatternRef') or '').strip()
        jp = journey_patterns.get(jp_ref)
        if not trip_id or jp is None:
            continue

        route_id = str(jp.get('routeId') or trip.get('serviceRef') or '').strip()
        if route_id not in routes:
            routes[route_id] = {
                'route_id': route_id,
                'agency_id': GTFS_ALLOWED_AGENCY_ID or '',
                'route_short_name': route_id,
                'route_long_name': '',
            }
            route_rows.append(routes[route_id])

        section_refs = list(jp.get('sectionRefs') or [])
        links: list[dict[str, object]] = []
        for section_ref in section_refs:
            links.extend(section_links.get(section_ref, []))
        if not links:
            continue

        stop_sequence_ids: list[str] = []
        for link in links:
            from_stop = str(link.get('fromStop') or '').strip()
            to_stop = str(link.get('toStop') or '').strip()
            if from_stop and (not stop_sequence_ids or stop_sequence_ids[-1] != from_stop):
                stop_sequence_ids.append(from_stop)
            if to_stop and (not stop_sequence_ids or stop_sequence_ids[-1] != to_stop):
                stop_sequence_ids.append(to_stop)

        coordinate_stops = [stop_id for stop_id in stop_sequence_ids if stop_id in stops_lookup]
        if len(coordinate_stops) < 2:
            continue

        shape_id = f'shape:{jp_ref}'
        for index, stop_id in enumerate(coordinate_stops, start=1):
            stop = stops_lookup[stop_id]
            shape_rows.append(
                {
                    'shape_id': shape_id,
                    'shape_pt_lat': str(stop.get('stop_lat') or ''),
                    'shape_pt_lon': str(stop.get('stop_lon') or ''),
                    'shape_pt_sequence': str(index),
                }
            )

        try:
            departure_seconds = parse_gtfs_time(trip.get('departureTime')) or 0
        except Exception:
            departure_seconds = 0

        cumulative = departure_seconds
        stop_arrivals: list[tuple[str, int]] = []
        if coordinate_stops:
            stop_arrivals.append((coordinate_stops[0], cumulative))

        overrides = dict(trip.get('timingOverrides') or {})
        for link in links:
            to_stop = str(link.get('toStop') or '').strip()
            if not to_stop or to_stop not in stops_lookup:
                continue
            runtime = int(overrides.get(str(link.get('linkId') or ''), int(link.get('runtimeSeconds') or 0)))
            cumulative += max(0, runtime)
            if stop_arrivals and stop_arrivals[-1][0] == to_stop:
                continue
            stop_arrivals.append((to_stop, cumulative))

        for sequence, (stop_id, seconds_value) in enumerate(stop_arrivals, start=1):
            hhmmss = format_gtfs_hhmmss(seconds_value)
            stop_time_rows.append(
                {
                    'trip_id': trip_id,
                    'arrival_time': hhmmss,
                    'departure_time': hhmmss,
                    'stop_id': stop_id,
                    'stop_sequence': str(sequence),
                }
            )

        trip_rows.append(
            {
                'route_id': route_id,
                'service_id': str(trip.get('serviceRef') or route_id),
                'trip_id': trip_id,
                'shape_id': shape_id,
                'direction_id': str(jp.get('directionId') or ''),
                'trip_headsign': str(trip.get('destinationDisplay') or jp.get('destinationDisplay') or ''),
                'block_id': str(trip.get('journeyCode') or ''),
            }
        )

    if not trip_rows or not shape_rows:
        return False

    stops_rows = sorted(stops_lookup.values(), key=lambda row: str(row.get('stop_name') or '').lower())

    def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
        with path.open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({name: row.get(name, '') for name in fieldnames})

    write_csv(extracted_dir / 'routes.txt', ['route_id', 'agency_id', 'route_short_name', 'route_long_name'], route_rows)
    write_csv(
        extracted_dir / 'trips.txt',
        ['route_id', 'service_id', 'trip_id', 'shape_id', 'direction_id', 'trip_headsign', 'block_id'],
        trip_rows,
    )

    calendar_rows: list[dict[str, object]] = []
    for service_id, days in service_days.items():
        start_text, end_text = service_periods.get(service_id, ('', ''))
        start_date = str(start_text or '').replace('-', '').strip()
        end_date = str(end_text or '').replace('-', '').strip()
        if len(start_date) != 8:
            start_date = (datetime.now(LONDON_TZ).date() - timedelta(days=SERVICE_CALENDAR_PAST_DAYS)).strftime('%Y%m%d')
        if len(end_date) != 8:
            end_date = (datetime.now(LONDON_TZ).date() + timedelta(days=SERVICE_CALENDAR_FUTURE_DAYS)).strftime('%Y%m%d')
        row: dict[str, object] = {'service_id': service_id, 'start_date': start_date, 'end_date': end_date}
        for column in GTFS_WEEKDAY_COLUMNS:
            row[column] = '1' if column in days else '0'
        calendar_rows.append(row)

    if calendar_rows:
        write_csv(
            extracted_dir / 'calendar.txt',
            ['service_id', *GTFS_WEEKDAY_COLUMNS, 'start_date', 'end_date'],
            calendar_rows,
        )
    write_csv(extracted_dir / 'shapes.txt', ['shape_id', 'shape_pt_lat', 'shape_pt_lon', 'shape_pt_sequence'], shape_rows)
    write_csv(extracted_dir / 'stops.txt', ['stop_id', 'stop_code', 'stop_name', 'stop_lat', 'stop_lon'], stops_rows)
    write_csv(extracted_dir / 'stop_times.txt', ['trip_id', 'arrival_time', 'departure_time', 'stop_id', 'stop_sequence'], stop_time_rows)

    return True


def parse_gtfs_routes_from_directory(extracted_dir: Path, allowed_route_prefixes: list[str] | None = None) -> dict[str, object]:
    routes_path = find_gtfs_file(extracted_dir, 'routes.txt')
    trips_path = find_gtfs_file(extracted_dir, 'trips.txt')
    shapes_path = find_gtfs_file(extracted_dir, 'shapes.txt')
    stops_path = find_gtfs_file(extracted_dir, 'stops.txt')
    stop_times_path = find_gtfs_file(extracted_dir, 'stop_times.txt')

    if routes_path is None or trips_path is None or shapes_path is None:
        raise ValueError('GTFS ZIP must include routes.txt, trips.txt, and shapes.txt.')

    route_rows = read_gtfs_rows(routes_path)
    trip_rows = read_gtfs_rows(trips_path)
    shape_rows = read_gtfs_rows(shapes_path)
    source_xml_file_count = len([path for path in extracted_dir.rglob('*.xml') if path.is_file()])

    normalized_allowed_prefixes = {extract_route_prefix(value) for value in (allowed_route_prefixes or []) if extract_route_prefix(value)}

    route_meta: dict[str, dict[str, str]] = {}
    for row in route_rows:
        route_id = str(row.get('route_id') or '').strip()
        agency_id = str(row.get('agency_id') or '').strip()
        if not route_id:
            continue
        if GTFS_ALLOWED_AGENCY_ID and agency_id != GTFS_ALLOWED_AGENCY_ID:
            continue
        route_prefix = extract_route_prefix(route_id)
        if normalized_allowed_prefixes and route_prefix not in normalized_allowed_prefixes:
            continue
        route_meta[route_id] = {
            'shortName': str(row.get('route_short_name') or '').strip(),
            'longName': str(row.get('route_long_name') or '').strip(),
        }

    if GTFS_ALLOWED_AGENCY_ID and not route_meta:
        raise ValueError(f'No routes found for agency ID {GTFS_ALLOWED_AGENCY_ID} in this GTFS ZIP.')
    if normalized_allowed_prefixes and not route_meta:
        labels = ', '.join(sorted(normalized_allowed_prefixes))
        raise ValueError(f'No routes found for configured route prefixes: {labels}.')

    allowed_route_ids = set(route_meta.keys())

    route_shapes: dict[str, set[str]] = {}
    route_shape_directions: dict[str, dict[str, set[str]]] = {}
    route_trips: dict[str, set[str]] = {}
    trip_routes: dict[str, str] = {}
    trip_directions: dict[str, str] = {}
    trip_service_ids: dict[str, str] = {}
    trip_headsigns: dict[str, str] = {}
    trip_block_ids: dict[str, str] = {}
    for row in trip_rows:
        route_id = str(row.get('route_id') or '').strip()
        trip_id = str(row.get('trip_id') or '').strip()
        shape_id = str(row.get('shape_id') or '').strip()
        service_id = str(row.get('service_id') or '').strip()
        direction = normalize_gtfs_direction(str(row.get('direction_id') or ''))
        if not route_id:
            continue
        if allowed_route_ids and route_id not in allowed_route_ids:
            continue
        if trip_id:
            route_trips.setdefault(route_id, set()).add(trip_id)
            trip_routes[trip_id] = route_id
            trip_directions[trip_id] = direction
            trip_service_ids[trip_id] = service_id
            trip_headsigns[trip_id] = str(row.get('trip_headsign') or '').strip()
            trip_block_ids[trip_id] = str(row.get('block_id') or '').strip()
        if shape_id:
            route_shapes.setdefault(route_id, set()).add(shape_id)
            route_shape_directions.setdefault(route_id, {}).setdefault(shape_id, set()).add(direction)

    shapes: dict[str, list[tuple[float, float, int]]] = {}
    for row in shape_rows:
        shape_id = str(row.get('shape_id') or '').strip()
        if not shape_id:
            continue

        lon_text = str(row.get('shape_pt_lon') or '').strip()
        lat_text = str(row.get('shape_pt_lat') or '').strip()
        sequence_text = str(row.get('shape_pt_sequence') or '').strip()
        if not lon_text or not lat_text:
            continue
        try:
            longitude = float(lon_text)
            latitude = float(lat_text)
            sequence = int(float(sequence_text or '0'))
        except ValueError:
            continue

        shapes.setdefault(shape_id, []).append((longitude, latitude, sequence))

    trip_points: dict[str, list[list[float]]] = {}
    trip_stop_sequences: dict[str, list[dict[str, object]]] = {}
    trip_schedules: dict[str, dict[str, object]] = {}
    stops_lookup: dict[str, dict[str, object]] = {}
    service_calendar = build_gtfs_service_calendar(extracted_dir)
    if stops_path is not None and stop_times_path is not None:
        relevant_trip_ids = set().union(*route_trips.values()) if route_trips else set()
        if relevant_trip_ids:
            stop_rows = read_gtfs_rows(stops_path)
            for row in stop_rows:
                stop_id = str(row.get('stop_id') or '').strip()
                stop_name = str(row.get('stop_name') or '').strip()
                stop_code = str(row.get('stop_code') or row.get('atco_code') or '').strip()
                lon_text = str(row.get('stop_lon') or '').strip()
                lat_text = str(row.get('stop_lat') or '').strip()
                if not stop_id or not lon_text or not lat_text:
                    continue
                try:
                    longitude = float(lon_text)
                    latitude = float(lat_text)
                except ValueError:
                    continue
                naptan_code = stop_code or stop_id
                stops_lookup[stop_id] = {
                    'stopId': stop_id,
                    'naptan': naptan_code,
                    'atcoCode': stop_code or naptan_code,
                    'name': stop_name or stop_id,
                    'longitude': longitude,
                    'latitude': latitude,
                }

            raw_trip_points: dict[str, list[tuple[int, str]]] = {}
            raw_trip_schedule_entries: dict[str, list[tuple[int, str, str, str]]] = {}
            with stop_times_path.open('r', encoding='utf-8-sig', newline='') as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    trip_id = str(row.get('trip_id') or '').strip()
                    if trip_id not in relevant_trip_ids:
                        continue
                    stop_id = str(row.get('stop_id') or '').strip()
                    if stop_id not in stops_lookup:
                        continue
                    sequence_text = str(row.get('stop_sequence') or '').strip()
                    try:
                        sequence = int(float(sequence_text or '0'))
                    except ValueError:
                        sequence = 0
                    arrival_time = str(row.get('arrival_time') or '').strip()
                    departure_time = str(row.get('departure_time') or '').strip()
                    raw_trip_points.setdefault(trip_id, []).append((sequence, stop_id))
                    raw_trip_schedule_entries.setdefault(trip_id, []).append((sequence, stop_id, arrival_time, departure_time))

            for trip_id, entries in raw_trip_points.items():
                coordinates: list[list[float]] = []
                stop_sequence: list[dict[str, object]] = []
                for _, stop_id in sorted(entries, key=lambda entry: entry[0]):
                    stop_data = stops_lookup[stop_id]
                    longitude = float(stop_data['longitude'])
                    latitude = float(stop_data['latitude'])
                    point = [longitude, latitude]
                    if not coordinates or coordinates[-1] != point:
                        coordinates.append(point)
                    if not stop_sequence or stop_sequence[-1].get('stopId') != stop_id:
                        stop_sequence.append(
                            {
                                'stopId': stop_id,
                                'naptan': str(stop_data.get('naptan') or stop_data.get('atcoCode') or stop_id),
                                'name': stop_data['name'],
                                'longitude': longitude,
                                'latitude': latitude,
                            }
                        )
                if len(coordinates) >= 2:
                    trip_points[trip_id] = coordinates
                if len(stop_sequence) >= 2:
                    trip_stop_sequences[trip_id] = stop_sequence

            for trip_id, entries in raw_trip_schedule_entries.items():
                schedule_stops = []
                for _, stop_id, arrival_time, departure_time in sorted(entries, key=lambda entry: entry[0]):
                    stop_data = stops_lookup[stop_id]
                    schedule_stops.append(
                        {
                            'stopId': stop_id,
                            'naptan': str(stop_data.get('naptan') or stop_data.get('atcoCode') or stop_id),
                            'name': stop_data['name'],
                            'arrivalTime': arrival_time,
                            'departureTime': departure_time,
                        }
                    )
                if schedule_stops:
                    trip_schedules[trip_id] = {
                        'tripId': trip_id,
                        'routeId': trip_routes.get(trip_id, ''),
                        'direction': trip_directions.get(trip_id, 'unknown'),
                        'serviceId': trip_service_ids.get(trip_id, ''),
                        'headsign': trip_headsigns.get(trip_id, ''),
                        'blockId': trip_block_ids.get(trip_id, ''),
                        'stops': schedule_stops,
                    }

    route_items: list[dict[str, object]] = []
    features: list[dict[str, object]] = []
    route_stop_sequences: dict[str, dict[str, dict[str, object]]] = {}

    for route_id, shape_ids in route_shapes.items():
        meta = route_meta.get(route_id, {})
        short_name = str(meta.get('shortName') or '').strip() or route_id
        long_name = str(meta.get('longName') or '').strip()
        label = short_name if not long_name or long_name.lower() == short_name.lower() else f'{short_name} - {long_name}'

        route_feature_count = 0
        for shape_id in sorted(shape_ids):
            points = sorted(shapes.get(shape_id, []), key=lambda entry: entry[2])
            coordinates: list[list[float]] = []
            for longitude, latitude, _ in points:
                point = [longitude, latitude]
                if not coordinates or coordinates[-1] != point:
                    coordinates.append(point)

            if len(coordinates) < 2:
                continue

            shape_directions = route_shape_directions.get(route_id, {}).get(shape_id, {'unknown'})
            for direction in sorted(shape_directions):
                features.append(
                    {
                        'type': 'Feature',
                        'geometry': {
                            'type': 'LineString',
                            'coordinates': coordinates,
                        },
                        'properties': {
                            'routeId': route_id,
                            'shapeId': shape_id,
                            'lineName': short_name,
                            'label': label,
                            'direction': direction,
                        },
                    }
                )
                route_feature_count += 1

        if route_feature_count:
            route_items.append(
                {
                    'id': route_id,
                    'lineName': short_name,
                    'label': label,
                    'shapeCount': route_feature_count,
                }
            )

        best_trip_by_direction: dict[str, tuple[int, str]] = {}
        for trip_id in sorted(route_trips.get(route_id, set())):
            stop_sequence = trip_stop_sequences.get(trip_id, [])
            if len(stop_sequence) < 2:
                continue
            direction = trip_directions.get(trip_id, 'unknown')
            current_best = best_trip_by_direction.get(direction)
            if current_best is None or len(stop_sequence) > current_best[0]:
                best_trip_by_direction[direction] = (len(stop_sequence), trip_id)

        if best_trip_by_direction:
            route_stop_sequences[route_id] = {}
            for direction, (_, trip_id) in best_trip_by_direction.items():
                route_stop_sequences[route_id][direction] = {
                    'tripId': trip_id,
                    'stops': trip_stop_sequences[trip_id],
                }

    for route_id, trip_ids in route_trips.items():
        if any(item.get('id') == route_id for item in route_items):
            continue

        meta = route_meta.get(route_id, {})
        short_name = str(meta.get('shortName') or '').strip() or route_id
        long_name = str(meta.get('longName') or '').strip()
        label = short_name if not long_name or long_name.lower() == short_name.lower() else f'{short_name} - {long_name}'

        route_feature_count = 0
        signature_set: set[tuple[tuple[float, float], ...]] = set()
        for trip_id in sorted(trip_ids):
            coordinates = trip_points.get(trip_id, [])
            if len(coordinates) < 2:
                continue
            signature = tuple((point[0], point[1]) for point in coordinates)
            if signature in signature_set:
                continue
            signature_set.add(signature)

            features.append(
                {
                    'type': 'Feature',
                    'geometry': {
                        'type': 'LineString',
                        'coordinates': coordinates,
                    },
                    'properties': {
                        'routeId': route_id,
                        'shapeId': f'stops:{trip_id}',
                        'lineName': short_name,
                        'label': label,
                        'direction': trip_directions.get(trip_id, 'unknown'),
                    },
                }
            )
            route_feature_count += 1
            if route_feature_count >= GTFS_MAX_FALLBACK_PATTERNS_PER_ROUTE:
                break

        if route_feature_count:
            route_items.append(
                {
                    'id': route_id,
                    'lineName': short_name,
                    'label': label,
                    'shapeCount': route_feature_count,
                }
            )

    all_stops = sorted(
        stops_lookup.values(),
        key=lambda stop: (
            str(stop.get('name') or '').lower(),
            str(stop.get('stopId') or '').lower(),
        ),
    )

    route_items.sort(key=lambda item: route_sort_key(str(item['lineName'])))
    if not route_items:
        raise ValueError('No plottable route paths were found in this GTFS ZIP for the selected agency.')

    return {
        'routeCount': len(route_items),
        'routes': route_items,
        'stops': all_stops,
        'routeStopSequences': route_stop_sequences,
        'tripSchedules': trip_schedules,
        'serviceCalendar': service_calendar,
        'featureCollection': {
            'type': 'FeatureCollection',
            'features': features,
        },
        'xmlSourceFileCount': source_xml_file_count,
        'sourceRouteRowCount': len(route_rows),
    }


_gtfs_cache_lock = threading.Lock()
_gtfs_cache_memo: dict[str, object] = {'signature': None, 'data': None}


def read_gtfs_cache_file() -> dict[str, object] | None:
    """Parsing the multi-megabyte cache per request is far too slow, so memoise on file identity."""
    try:
        stat_result = GTFS_CACHE_PATH.stat()
    except OSError:
        return None
    signature = (stat_result.st_mtime_ns, stat_result.st_size)

    with _gtfs_cache_lock:
        if _gtfs_cache_memo['signature'] == signature:
            cached = _gtfs_cache_memo['data']
            return cached if isinstance(cached, dict) else None

    try:
        data = json.loads(GTFS_CACHE_PATH.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None

    with _gtfs_cache_lock:
        _gtfs_cache_memo['signature'] = signature
        _gtfs_cache_memo['data'] = data
    return data


def load_gtfs_cache(allow_rebuild: bool = False) -> dict[str, object] | None:
    if not GTFS_CACHE_PATH.exists():
        return None
    data = read_gtfs_cache_file()
    if data is None:
        return None
    if allow_rebuild and ((not data.get('tripSchedules') and GTFS_UPLOAD_PATH.exists()) or not data.get('serviceCalendar')) and GTFS_UPLOAD_PATH.exists():
        try:
            raw = GTFS_UPLOAD_PATH.read_bytes()
            extracted_dir = unzip_gtfs_archive(raw)
            parsed = parse_gtfs_routes_from_directory(extracted_dir)
        except (OSError, ValueError):
            return data
        data['routeStopSequences'] = parsed.get('routeStopSequences', {})
        data['tripSchedules'] = parsed.get('tripSchedules', {})
        data['serviceCalendar'] = parsed.get('serviceCalendar', {})
        data['stops'] = parsed.get('stops', [])
        data['routes'] = parsed.get('routes', [])
        data['featureCollection'] = parsed.get('featureCollection', {})
        data['routeCount'] = parsed.get('routeCount', 0)
        save_gtfs_data(raw, parsed, str(data.get('originalFilename') or GTFS_UPLOAD_PATH.name))
    return data


def save_gtfs_data(zip_bytes: bytes, parsed: dict[str, object], original_filename: str) -> dict[str, object]:
    GTFS_DIR.mkdir(parents=True, exist_ok=True)
    GTFS_UPLOAD_PATH.write_bytes(zip_bytes)

    payload = {
        'uploadedAt': datetime.now(timezone.utc).isoformat(),
        'originalFilename': original_filename,
        'routeCount': int(parsed['routeCount']),
        'routes': parsed['routes'],
        'stops': parsed.get('stops', []),
        'routeStopSequences': parsed.get('routeStopSequences', {}),
        'tripSchedules': parsed.get('tripSchedules', {}),
        'serviceCalendar': parsed.get('serviceCalendar', {}),
        'featureCollection': parsed['featureCollection'],
        'xmlSourceFileCount': int(parsed.get('xmlSourceFileCount', 0) or 0),
        'sourceRouteRowCount': int(parsed.get('sourceRouteRowCount', 0) or 0),
    }
    GTFS_CACHE_PATH.write_text(json.dumps(payload), encoding='utf-8')
    return payload


def load_data_health_status() -> dict[str, object]:
    if not DATA_HEALTH_STATUS_PATH.exists():
        return {}
    try:
        payload = json.loads(DATA_HEALTH_STATUS_PATH.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def save_data_health_status(payload: dict[str, object]) -> None:
    DATA_HEALTH_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_HEALTH_STATUS_PATH.write_text(json.dumps(payload), encoding='utf-8')


def load_gtfs_manual_lock_state() -> bool:
    if not GTFS_MANUAL_LOCK_PATH.exists():
        return True
    try:
        payload = json.loads(GTFS_MANUAL_LOCK_PATH.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return True
    if not isinstance(payload, dict):
        return True
    return bool(payload.get('enabled', True))


def save_gtfs_manual_lock_state(enabled: bool) -> bool:
    GTFS_MANUAL_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {'enabled': bool(enabled), 'updatedAt': datetime.now(timezone.utc).isoformat()}
    GTFS_MANUAL_LOCK_PATH.write_text(json.dumps(payload), encoding='utf-8')
    return bool(payload['enabled'])


def maybe_auto_refresh_data_health_status() -> None:
    global _last_data_health_run_monotonic
    now = time.monotonic()
    if (now - _last_data_health_run_monotonic) < 30:
        return
    _last_data_health_run_monotonic = now
    try:
        get_data_health_status(force=False)
    except Exception:
        return



def get_contacts_encryption_status() -> dict[str, object]:
    rows = get_db().execute(
        '''
        SELECT id, first_name, last_name, job_role, job_title, depot_location, phone_number
        FROM contacts
        '''
    ).fetchall()

    total_contacts = len(rows)
    fully_encrypted = 0
    partially_encrypted = 0
    plaintext_rows = 0

    for row in rows:
        values = [
            row['first_name'],
            row['last_name'],
            row['job_role'],
            row['job_title'],
            row['depot_location'],
            row['phone_number'],
        ]
        encrypted_flags = [is_encrypted_contact_value(value) for value in values]
        encrypted_count = sum(1 for flag in encrypted_flags if flag)

        if encrypted_count == len(values):
            fully_encrypted += 1
        elif encrypted_count == 0:
            plaintext_rows += 1
        else:
            partially_encrypted += 1

    encrypted_percentage = (fully_encrypted / total_contacts * 100.0) if total_contacts else 100.0
    return {
        'totalContacts': total_contacts,
        'fullyEncryptedContacts': fully_encrypted,
        'partiallyEncryptedContacts': partially_encrypted,
        'plaintextContacts': plaintext_rows,
        'encryptedPercentage': round(encrypted_percentage, 1),
        'allEncrypted': total_contacts == fully_encrypted,
        'checkedAt': datetime.now(timezone.utc).isoformat(),
    }


def get_data_health_status(force: bool = False) -> dict[str, object]:
    previous_status = load_data_health_status()
    now = datetime.now(timezone.utc)
    last_check = parse_session_timestamp(previous_status.get('lastCheckAt'))
    if not force and last_check is not None:
        if (now - last_check).total_seconds() < max(60, AUTO_DATA_CHECK_INTERVAL_SECONDS):
            return previous_status

    status = {
        'lastCheckAt': now.isoformat(),
        'gtfs': {
            'configured': False,
            'active': False,
            'ok': False,
            'routeCount': 0,
            'message': 'No GTFS ZIP uploaded yet.',
            'lastCheckedAt': now.isoformat(),
            'manualLockEnabled': load_gtfs_manual_lock_state(),
        },
        'bods': {
            'configured': False,
            'active': False,
            'ok': False,
            'vehicleCount': 0,
            'message': 'BODS feed is not configured.',
            'lastCheckedAt': now.isoformat(),
        },
    }

    gtfs_block = status['gtfs']
    cache = load_gtfs_cache(allow_rebuild=False)
    route_prefix_filter = GTFS_ALLOWED_ROUTE_PREFIXES or extract_route_prefixes_from_cache(cache)
    if cache is not None:
        route_count = int(cache.get('routeCount', 0))
        gtfs_block.update(
            {
                'configured': True,
                'active': bool(route_count > 0 and GTFS_UPLOAD_PATH.exists()),
                'ok': bool(route_count > 0),
                'routeCount': route_count,
                'originalFilename': str(cache.get('originalFilename') or ''),
                'uploadedAt': str(cache.get('uploadedAt') or ''),
                'message': 'GTFS cache is active.' if route_count > 0 else 'GTFS cache exists but no routes were detected.',
                'routePrefixFilter': route_prefix_filter,
            }
        )

    manual_lock_enabled = load_gtfs_manual_lock_state()
    gtfs_block['manualLockEnabled'] = manual_lock_enabled

    auto_download_url = GTFS_AUTO_DOWNLOAD_URL
    source_label = 'configured URL'
    dataset_meta: dict[str, object] | None = None
    if not auto_download_url:
        auto_download_url, dataset_meta = get_latest_bods_timetable_dataset_download_url()
        source_label = f'BODS dataset (NOC {BODS_TIMETABLE_NOC})'

    if manual_lock_enabled:
        gtfs_block['autoUpdateMessage'] = 'Manual upload lock is enabled. Auto-download updates are paused.'
    elif auto_download_url:
        try:
            with urlopen(auto_download_url, timeout=GTFS_AUTO_DOWNLOAD_TIMEOUT_SECONDS) as response:
                auto_bytes = response.read()
            latest_hash = hashlib.sha256(auto_bytes).hexdigest()
            current_hash = ''
            if GTFS_UPLOAD_PATH.exists():
                current_hash = hashlib.sha256(GTFS_UPLOAD_PATH.read_bytes()).hexdigest()
            if latest_hash != current_hash:
                extracted_dir = unzip_gtfs_archive(auto_bytes)
                effective_route_prefix_filter = list(route_prefix_filter)
                if BODS_TIMETABLE_NOC == 'BNGN' and not GTFS_AUTO_DOWNLOAD_URL:
                    detected_prefix = detect_best_pc_route_prefix(extracted_dir)
                    if detected_prefix:
                        effective_route_prefix_filter = [detected_prefix]

                parsed = parse_gtfs_routes_from_directory(
                    extracted_dir,
                    allowed_route_prefixes=effective_route_prefix_filter,
                )
                source_name = 'auto-download-gtfs.zip'
                if dataset_meta and dataset_meta.get('id'):
                    source_name = f"bods-dataset-{dataset_meta.get('id')}.zip"
                saved = save_gtfs_data(auto_bytes, parsed, source_name)
                gtfs_block.update(
                    {
                        'configured': True,
                        'active': True,
                        'ok': True,
                        'routeCount': int(saved.get('routeCount', 0)),
                        'originalFilename': str(saved.get('originalFilename') or ''),
                        'uploadedAt': str(saved.get('uploadedAt') or ''),
                        'lastDownloadAt': now.isoformat(),
                        'message': f'GTFS auto-download updated successfully from {source_label}.',
                        'convertedFileLimit': TRANSXCHANGE_MAX_FILES,
                        'convertedTripLimit': TRANSXCHANGE_MAX_TRIPS,
                        'routePrefixFilter': effective_route_prefix_filter,
                    }
                )
            else:
                gtfs_block['lastDownloadAt'] = str(status.get('gtfs', {}).get('lastDownloadAt') or '')
                gtfs_block['autoUpdateMessage'] = f'GTFS auto-download checked with no changes from {source_label}.'
                gtfs_block['convertedFileLimit'] = TRANSXCHANGE_MAX_FILES
                gtfs_block['convertedTripLimit'] = TRANSXCHANGE_MAX_TRIPS
                gtfs_block['routePrefixFilter'] = route_prefix_filter
        except Exception as error:
            gtfs_block['autoUpdateMessage'] = f'GTFS auto-download check failed from {source_label}: {error}'
            gtfs_block['convertedFileLimit'] = TRANSXCHANGE_MAX_FILES
            gtfs_block['convertedTripLimit'] = TRANSXCHANGE_MAX_TRIPS
            gtfs_block['routePrefixFilter'] = route_prefix_filter
    elif not GTFS_AUTO_DOWNLOAD_URL:
        gtfs_block['autoUpdateMessage'] = f'No published BODS timetable ZIP found for NOC {BODS_TIMETABLE_NOC}.'

    feed_url = get_bods_feed_url()
    bods_block = status['bods']
    if feed_url:
        bods_block['configured'] = True
        try:
            vehicles, source_timestamp = fetch_bods_vehicles_cached()
            vehicle_count = len(vehicles)
            bods_block.update(
                {
                    'ok': True,
                    'active': vehicle_count > 0,
                    'vehicleCount': vehicle_count,
                    'sourceTimestamp': source_timestamp,
                    'message': 'BODS feed reachable.' if vehicle_count > 0 else 'BODS feed reachable but returned no active vehicles.',
                }
            )
            if vehicle_count > 0:
                bods_block['lastSuccessfulAt'] = now.isoformat()
            elif previous_status.get('bods', {}).get('lastSuccessfulAt'):
                bods_block['lastSuccessfulAt'] = str(previous_status.get('bods', {}).get('lastSuccessfulAt'))
        except Exception as error:
            bods_block.update(
                {
                    'ok': False,
                    'active': False,
                    'message': f'BODS feed check failed: {error}',
                }
            )

    if previous_status.get('bods', {}).get('lastSuccessfulAt') and not status['bods'].get('lastSuccessfulAt'):
        status['bods']['lastSuccessfulAt'] = str(previous_status.get('bods', {}).get('lastSuccessfulAt'))
    save_data_health_status(status)
    return status


def filter_route_features(cache: dict[str, object], selected_route: str, selected_direction: str) -> dict[str, object]:
    selected = str(selected_route or 'all').strip()
    direction = str(selected_direction or 'all').strip().lower()
    all_features = cache.get('featureCollection', {}).get('features', [])
    filtered: list[dict[str, object]] = []
    for feature in all_features:
        properties = feature.get('properties', {})
        route_id = str(properties.get('routeId') or '')
        feature_direction = normalize_gtfs_direction(str(properties.get('direction') or ''))
        if selected.lower() != 'all' and route_id != selected:
            continue
        if direction != 'all' and feature_direction != direction:
            continue
        filtered.append(feature)

    return {
        'type': 'FeatureCollection',
        'features': filtered,
    }


@app.get('/api/gtfs/status')
@login_required('admin_privileges')
def gtfs_status():
    force = str(request.args.get('force', '0')).strip().lower() in {'1', 'true', 'yes', 'on'}
    status = get_data_health_status(force=force)
    cache = load_gtfs_cache(allow_rebuild=False)
    if cache is None:
        return jsonify(
            {
                'ok': True,
                'configured': False,
                'message': 'No GTFS ZIP uploaded yet.',
                'routeCount': 0,
                'healthStatus': status,
            }
        )

    return jsonify(
        {
            'ok': True,
            'configured': True,
            'uploadedAt': cache.get('uploadedAt', ''),
            'originalFilename': cache.get('originalFilename', ''),
            'routeCount': int(cache.get('routeCount', 0)),
            'xmlSourceFileCount': int(cache.get('xmlSourceFileCount', 0) or 0),
            'sourceRouteRowCount': int(cache.get('sourceRouteRowCount', 0) or 0),
            'manualLockEnabled': load_gtfs_manual_lock_state(),
            'healthStatus': status,
        }
    )



@app.get('/api/admin/contacts-encryption-status')
@login_required('admin_privileges')
def admin_contacts_encryption_status():
    status = get_contacts_encryption_status()
    return jsonify({'ok': True, 'status': status})


@app.get('/api/admin/data-status')
@login_required('admin_privileges')
def admin_data_status():
    force = str(request.args.get('force', '0')).strip().lower() in {'1', 'true', 'yes', 'on'}
    return jsonify({'ok': True, 'status': get_data_health_status(force=force)})




@app.route('/api/admin/gtfs-manual-lock', methods=['GET', 'POST'])
@login_required('admin_privileges')
def admin_gtfs_manual_lock():
    if request.method == 'GET':
        return jsonify({'ok': True, 'enabled': load_gtfs_manual_lock_state()})

    payload = request.get_json(silent=True)
    enabled = bool((payload or {}).get('enabled', True))
    saved = save_gtfs_manual_lock_state(enabled)
    return jsonify({
        'ok': True,
        'enabled': saved,
        'message': 'Manual GTFS lock enabled.' if saved else 'Manual GTFS lock disabled.',
    })


@app.post('/api/gtfs/upload')
@login_required('admin_privileges')
def upload_gtfs():
    file = request.files.get('gtfsZipFile')
    if file is None or not file.filename:
        return jsonify({'ok': False, 'message': 'Select a GTFS ZIP file to upload.'}), 400

    raw = file.stream.read(GTFS_MAX_UPLOAD_BYTES + 1)
    if len(raw) > GTFS_MAX_UPLOAD_BYTES:
        return jsonify({'ok': False, 'message': 'The file is too large.'}), 413

    try:
        extracted_dir = unzip_gtfs_archive(raw)
        parsed = parse_gtfs_routes_from_directory(extracted_dir)
    except ValueError as error:
        return jsonify({'ok': False, 'message': str(error)}), 400

    cache_payload = save_gtfs_data(raw, parsed, file.filename)
    return jsonify(
        {
            'ok': True,
            'routeCount': int(cache_payload.get('routeCount', 0)),
            'uploadedAt': cache_payload.get('uploadedAt', ''),
            'originalFilename': cache_payload.get('originalFilename', ''),
        }
    )


@app.post('/api/roadworks/upload')
@login_required('admin_privileges')
def upload_roadworks():
    file = request.files.get('roadworksCsvFile')
    if file is None or not file.filename:
        return jsonify({'ok': False, 'message': 'Select a roadworks CSV file to upload.'}), 400

    raw = file.stream.read(ROADWORKS_MAX_UPLOAD_BYTES + 1)
    if len(raw) > ROADWORKS_MAX_UPLOAD_BYTES:
        return jsonify({'ok': False, 'message': 'The file is too large.'}), 413

    try:
        entries = parse_roadworks_csv(raw)
    except ValueError as error:
        return jsonify({'ok': False, 'message': str(error)}), 400

    cache_payload = save_roadworks_data(raw, entries, file.filename)
    rag_counts = {'red': 0, 'amber': 0, 'green': 0}
    for entry in entries:
        rag = str(entry.get('rag') or 'amber')
        rag_counts[rag] = rag_counts.get(rag, 0) + 1

    return jsonify(
        {
            'ok': True,
            'roadworksCount': int(cache_payload.get('roadworksCount', 0)),
            'uploadedAt': cache_payload.get('uploadedAt', ''),
            'originalFilename': cache_payload.get('originalFilename', ''),
            'ragCounts': rag_counts,
        }
    )


@app.get('/api/roadworks/status')
@login_required('admin_privileges')
def roadworks_status():
    cache = load_roadworks_cache()
    entries = cache.get('roadworks') or []
    rag_counts = {'red': 0, 'amber': 0, 'green': 0}
    for entry in entries:
        rag = str(entry.get('rag') or 'amber')
        rag_counts[rag] = rag_counts.get(rag, 0) + 1

    return jsonify(
        {
            'ok': True,
            'configured': bool(entries),
            'roadworksCount': len(entries),
            'uploadedAt': cache.get('uploadedAt', ''),
            'originalFilename': cache.get('originalFilename', ''),
            'ragCounts': rag_counts,
        }
    )


@app.get('/api/tracking/roadworks')
@login_required('tracking')
def tracking_roadworks():
    cache = load_roadworks_cache()
    entries = cache.get('roadworks') or []
    return jsonify(
        {
            'ok': True,
            'configured': bool(entries),
            'roadworks': entries,
            'roadworksCount': len(entries),
            'uploadedAt': cache.get('uploadedAt', ''),
        }
    )


@app.get('/api/tracking/static-routes')
@login_required('tracking')
def tracking_static_routes():
    cache = load_gtfs_cache(allow_rebuild=False)
    selected_route = str(request.args.get('route', 'all') or 'all').strip()
    selected_direction = str(request.args.get('direction', 'all') or 'all').strip().lower()
    if selected_direction not in {'all', 'inbound', 'outbound'}:
        selected_direction = 'all'

    if cache is None:
        return jsonify(
            {
                'ok': True,
                'configured': False,
                'message': 'No GTFS ZIP has been uploaded yet.',
                'selectedRoute': 'all',
                'selectedDirection': 'all',
                'routes': [],
                'featureCollection': {'type': 'FeatureCollection', 'features': []},
            }
        )

    routes = cache.get('routes', [])
    valid_route_ids = {str(route.get('id') or '') for route in routes}
    if selected_route.lower() != 'all' and selected_route not in valid_route_ids:
        selected_route = 'all'

    return jsonify(
        {
            'ok': True,
            'configured': True,
            'selectedRoute': selected_route,
            'selectedDirection': selected_direction,
            'routeCount': int(cache.get('routeCount', 0)),
            'routes': routes,
            'featureCollection': filter_route_features(cache, selected_route, selected_direction),
            'uploadedAt': cache.get('uploadedAt', ''),
            'originalFilename': cache.get('originalFilename', ''),
        }
    )


@app.get('/api/tracking/vehicles')
@login_required_any(('tracking', 'service_overview'))
def tracking_vehicles():
    cache = ensure_gtfs_cache_stops(load_gtfs_cache(allow_rebuild=False))

    try:
        vehicles, source_timestamp = fetch_bods_vehicles_cached()
    except Exception:
        vehicles = []
        source_timestamp = ''

    if not vehicles:
        return jsonify(
            {
                'ok': True,
                'message': 'No live vehicle feed is available right now. Upload a timetable ZIP in Admin to enable timetable-based tracking.',
                'vehicles': [],
                'sourceTimestamp': source_timestamp or None,
                'refreshedAt': datetime.now(timezone.utc).isoformat(),
                'configured': bool(cache and cache.get('stops')),
            }
        )

    enriched_vehicles = enrich_tracking_vehicles(vehicles, cache)

    return jsonify(
        {
            'ok': True,
            'vehicles': enriched_vehicles,
            'sourceTimestamp': source_timestamp,
            'refreshedAt': datetime.now(timezone.utc).isoformat(),
        }
    )


@app.get('/api/tracking/stops')
@login_required('tracking')
def tracking_stops():
    cache = ensure_gtfs_cache_stops(load_gtfs_cache(allow_rebuild=False))
    if cache is None or not cache.get('stops'):
        return jsonify(
            {
                'ok': True,
                'configured': False,
                'message': 'No GTFS stops are available yet.',
                'stops': [],
            }
        )

    stops = [
        serialize_tracking_stop(stop)
        for stop in cache.get('stops', [])
        if isinstance(stop, dict)
    ]
    return jsonify(
        {
            'ok': True,
            'configured': True,
            'stops': stops,
            'stopCount': len(stops),
        }
    )


_departure_board_vehicles_lock = threading.Lock()
_departure_board_vehicles: dict[str, object] = {'loadedAtMonotonic': 0.0, 'vehicles': []}


def get_departure_board_vehicles(cache: dict[str, object] | None) -> list[dict[str, object]]:
    """Enriching every vehicle is costly, so refresh the board's view on a fixed interval."""
    ttl = max(5, int(DEPARTURE_BOARD_REFRESH_SECONDS))
    now_monotonic = time.monotonic()

    with _departure_board_vehicles_lock:
        loaded_at = float(_departure_board_vehicles.get('loadedAtMonotonic') or 0.0)
        if loaded_at > 0 and (now_monotonic - loaded_at) < ttl:
            return list(_departure_board_vehicles.get('vehicles') or [])

    try:
        raw_vehicles, _ = fetch_bods_vehicles_cached()
    except Exception:
        raw_vehicles = []
    enriched = enrich_tracking_vehicles(raw_vehicles, cache) if raw_vehicles else []

    with _departure_board_vehicles_lock:
        _departure_board_vehicles['loadedAtMonotonic'] = now_monotonic
        _departure_board_vehicles['vehicles'] = enriched
    return list(enriched)


@app.get('/api/tracking/stops/<stop_id>')
@login_required('tracking')
def tracking_stop_details(stop_id: str):
    cache = ensure_gtfs_cache_stops(load_gtfs_cache(allow_rebuild=False))
    if cache is None:
        return jsonify({'ok': False, 'message': 'No GTFS stops are available yet.'}), 404

    stop = next(
        (
            item
            for item in cache.get('stops', [])
            if isinstance(item, dict) and str(item.get('stopId') or item.get('id') or '').strip() == stop_id
        ),
        None,
    )
    if stop is None:
        return jsonify({'ok': False, 'message': 'Stop not found.'}), 404

    live_vehicles = get_departure_board_vehicles(cache)
    trip_schedules = cache.get('tripSchedules', {}) if isinstance(cache, dict) else {}
    now = datetime.now(timezone.utc)
    route_labels = {
        str(route.get('id') or '').strip(): str(route.get('lineName') or route.get('label') or route.get('id') or '').strip()
        for route in cache.get('routes', [])
        if isinstance(route, dict)
    }
    stop_payload = serialize_tracking_stop(stop)
    stop_payload['nextArrivals'] = build_stop_departure_board(
        stop,
        trip_schedules,
        live_vehicles,
        route_labels,
        now,
        max_results=6,
        service_calendar=cache.get('serviceCalendar', {}) if isinstance(cache, dict) else {},
    )
    if not stop_payload['nextArrivals']:
        stop_payload['nextArrivals'] = build_live_stop_arrivals(stop, live_vehicles, trip_schedules, max_results=6)
    return jsonify(
        {
            'ok': True,
            'stop': stop_payload,
            'refreshedAt': now.isoformat(),
        }
    )


@app.get('/driving-hours')
@login_required('driving_hours')
def driving_hours():
    return render_template('driving-hours.html')


@app.get('/api/driving-hours/snapshots')
@login_required('driving_hours')
def list_driving_snapshots():
    user = get_current_user()
    if user is None:
        abort(401)

    database = get_db()
    cleanup_expired_snapshots(database, int(user['id']))
    rows = database.execute(
        '''
        SELECT
            id,
            driver_name,
            employee_number,
            segment_summary,
            status,
            breaches_json,
            total_driving_minutes,
            total_break_minutes,
            spreadover_minutes,
            current_continuous_driving_minutes,
            non_driving_first_window_minutes,
            created_at,
            created_at_epoch
        FROM driving_snapshots
        WHERE user_id = ?
        ORDER BY created_at_epoch DESC
        ''',
        (int(user['id']),),
    ).fetchall()

    snapshots = [
        {
            'id': row['id'],
            'driverName': row['driver_name'],
            'employeeNumber': row['employee_number'],
            'segmentSummary': row['segment_summary'],
            'status': row['status'],
            'breaches': json.loads(row['breaches_json']),
            'metrics': {
                'totalDrivingMinutes': row['total_driving_minutes'],
                'totalBreakMinutes': row['total_break_minutes'],
                'spreadoverMinutes': row['spreadover_minutes'],
                'currentContinuousDrivingMinutes': row['current_continuous_driving_minutes'],
                'nonDrivingInFirstWindowMinutes': row['non_driving_first_window_minutes'],
            },
            'createdAt': row['created_at'],
            'createdAtEpoch': row['created_at_epoch'],
        }
        for row in rows
    ]
    return jsonify({'ok': True, 'snapshots': snapshots, 'retentionDays': SNAPSHOT_RETENTION_DAYS})


@app.post('/api/driving-hours/snapshots')
@login_required('driving_hours')
def create_driving_snapshot():
    user = get_current_user()
    if user is None:
        abort(401)

    payload = request.get_json(silent=True) or {}
    driver_name = str(payload.get('driverName', '')).strip()
    employee_number = str(payload.get('employeeNumber', '')).strip()
    if not driver_name or not employee_number:
        return jsonify({'ok': False, 'message': 'Driver name and employee number are required.'}), 400

    try:
        segments = validate_segments(payload.get('segments'))
    except ValueError as error:
        return jsonify({'ok': False, 'message': str(error)}), 400

    compliance = calculate_domestic_compliance(segments)
    segment_summary = ' '.join(
        f"{format_duration_compact(int(segment['endMinutes']) - int(segment['startMinutes']))} [{'D' if segment['type'] == 'driving' else 'B'}]"
        for segment in segments
    )

    now = datetime.now(timezone.utc)
    now_epoch = int(now.timestamp())
    database = get_db()
    cleanup_expired_snapshots(database, int(user['id']))
    cursor = database.execute(
        '''
        INSERT INTO driving_snapshots (
            user_id,
            driver_name,
            employee_number,
            segment_summary,
            status,
            breaches_json,
            total_driving_minutes,
            total_break_minutes,
            spreadover_minutes,
            current_continuous_driving_minutes,
            non_driving_first_window_minutes,
            created_at_epoch
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            int(user['id']),
            driver_name,
            employee_number,
            segment_summary,
            compliance['status'],
            json.dumps(compliance['breaches']),
            int(compliance['totalDrivingMinutes']),
            int(compliance['totalBreakMinutes']),
            int(compliance['spreadoverMinutes']),
            int(compliance['currentContinuousDrivingMinutes']),
            int(compliance['nonDrivingInFirstWindowMinutes']),
            now_epoch,
        ),
    )
    database.commit()

    return jsonify(
        {
            'ok': True,
            'snapshot': {
                'id': cursor.lastrowid,
                'driverName': driver_name,
                'employeeNumber': employee_number,
                'segmentSummary': segment_summary,
                'status': compliance['status'],
                'breaches': compliance['breaches'],
                'metrics': {
                    'totalDrivingMinutes': compliance['totalDrivingMinutes'],
                    'totalBreakMinutes': compliance['totalBreakMinutes'],
                    'spreadoverMinutes': compliance['spreadoverMinutes'],
                    'currentContinuousDrivingMinutes': compliance['currentContinuousDrivingMinutes'],
                    'nonDrivingInFirstWindowMinutes': compliance['nonDrivingInFirstWindowMinutes'],
                },
                'createdAt': now.isoformat(),
                'createdAtEpoch': now_epoch,
            },
            'retentionDays': SNAPSHOT_RETENTION_DAYS,
        }
    )


@app.get('/admin')
@login_required('admin_privileges')
def admin_page():
    return render_template('users.html')


@app.get('/users')
@login_required('admin_privileges')
def users_page():
    return redirect(url_for('admin_page'))



@app.get('/api/contacts')
@login_required('contacts')
def list_contacts():
    query = str(request.args.get('q', '')).strip().lower()
    normalized_query = re.sub(r'[^0-9]', '', query)
    rows = get_db().execute(
        '''
        SELECT id, first_name, last_name, job_role, job_title, depot_location, phone_number,
               is_important, is_private, created_at
        FROM contacts
        ORDER BY id ASC
        '''
    ).fetchall()

    contacts: list[dict[str, object]] = []
    for row in rows:
        first_name_plain = decrypt_contact_value(row['first_name']).strip()
        last_name_plain = decrypt_contact_value(row['last_name']).strip()
        job_role_plain = decrypt_contact_value(row['job_role']).strip()
        job_title_plain = decrypt_contact_value(row['job_title']).strip()
        depot_location_plain = decrypt_contact_value(row['depot_location']).strip()
        phone_number_plain = decrypt_contact_value(row['phone_number']).strip()

        item = {
            'id': int(row['id']),
            'firstName': first_name_plain,
            'lastName': last_name_plain,
            'fullName': f"{first_name_plain} {last_name_plain}".strip(),
            'jobRole': job_role_plain,
            'jobTitle': job_title_plain,
            'depotLocation': depot_location_plain,
            'phoneNumber': phone_number_plain,
            'isImportant': bool(row['is_important']),
            'isPrivate': bool(row['is_private']),
            'createdAt': row['created_at'],
        }

        if query:
            text_match = (
                query in item['fullName'].lower()
                or query in item['jobRole'].lower()
                or query in item['jobTitle'].lower()
                or query in item['depotLocation'].lower()
            )
            normalized_phone = re.sub(r'[^0-9]', '', item['phoneNumber'])
            phone_match = bool(normalized_query) and normalized_query in normalized_phone
            if not (text_match or phone_match):
                continue

        contacts.append(item)

    return jsonify({'ok': True, 'contacts': contacts, 'count': len(contacts)})


@app.post('/api/contacts')
@login_required('admin_privileges')
def create_contact():
    actor = get_current_user()
    payload = request.get_json(silent=True) or {}

    first_name = str(payload.get('firstName', '')).strip()
    last_name = str(payload.get('lastName', '')).strip()
    job_role = str(payload.get('jobRole', '')).strip()
    job_title = str(payload.get('jobTitle', '')).strip()
    depot_location = str(payload.get('depotLocation', '')).strip()
    phone_number = str(payload.get('phoneNumber', '')).strip()
    is_important = bool(payload.get('isImportant', False))
    is_private = bool(payload.get('isPrivate', False))
    force_save_duplicate = bool(payload.get('forceSaveDuplicate', False))

    if not first_name or not last_name or not job_role or not depot_location or not phone_number:
        return jsonify({'ok': False, 'message': 'First name, last name, job, depot/location, and phone number are required.'}), 400

    if not job_title:
        job_title = job_role

    phone_digits = re.sub(r'[^0-9+]', '', phone_number)
    normalized_phone = re.sub(r'[^0-9]', '', phone_digits)
    if len(normalized_phone) < 7:
        return jsonify({'ok': False, 'message': 'Provide a valid phone number.'}), 400

    normalized_name = f"{first_name} {last_name}".strip().lower()
    database = get_db()
    duplicate_rows = database.execute(
        '''
        SELECT id, first_name, last_name, phone_number
        FROM contacts
        '''
    ).fetchall()

    duplicates: list[dict[str, object]] = []
    for row in duplicate_rows:
        existing_first_name = decrypt_contact_value(row['first_name']).strip()
        existing_last_name = decrypt_contact_value(row['last_name']).strip()
        existing_name = f"{existing_first_name} {existing_last_name}".strip().lower()
        existing_phone = re.sub(r'[^0-9]', '', decrypt_contact_value(row['phone_number']).strip())
        name_match = bool(normalized_name) and normalized_name == existing_name
        phone_match = bool(normalized_phone) and normalized_phone == existing_phone
        if not (name_match or phone_match):
            continue
        duplicates.append(
            {
                'id': int(row['id']),
                'fullName': f"{existing_first_name} {existing_last_name}".strip(),
                'phoneNumber': decrypt_contact_value(row['phone_number']).strip(),
                'matchedBy': 'name and phone' if name_match and phone_match else ('name' if name_match else 'phone'),
            }
        )

    if duplicates and not force_save_duplicate:
        preview = ', '.join(
            f"{item['fullName'] or 'Unknown'} ({item['matchedBy']})"
            for item in duplicates[:3]
        )
        more_count = max(0, len(duplicates) - 3)
        suffix = f" plus {more_count} more" if more_count else ''
        return jsonify(
            {
                'ok': False,
                'duplicate': True,
                'message': f"Possible duplicate contact found: {preview}{suffix}. Save anyway?",
                'duplicates': duplicates,
            }
        ), 409

    cursor = database.execute(
        '''
        INSERT INTO contacts (
            first_name, last_name, job_role, job_title, depot_location, phone_number,
            is_important, is_private, created_by_user_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            encrypt_contact_value(first_name),
            encrypt_contact_value(last_name),
            encrypt_contact_value(job_role),
            encrypt_contact_value(job_title),
            encrypt_contact_value(depot_location),
            encrypt_contact_value(phone_number),
            int(is_important),
            int(is_private),
            int(actor['id']) if actor else None,
        ),
    )
    database.commit()

    return jsonify({'ok': True, 'contactId': cursor.lastrowid})



@app.patch('/api/contacts/<int:contact_id>')
@login_required('admin_privileges')
def update_contact(contact_id: int):
    payload = request.get_json(silent=True) or {}

    first_name = str(payload.get('firstName', '')).strip()
    last_name = str(payload.get('lastName', '')).strip()
    job_role = str(payload.get('jobRole', '')).strip()
    job_title = str(payload.get('jobTitle', '')).strip()
    depot_location = str(payload.get('depotLocation', '')).strip()
    phone_number = str(payload.get('phoneNumber', '')).strip()
    is_important = bool(payload.get('isImportant', False))
    is_private = bool(payload.get('isPrivate', False))

    if not first_name or not last_name or not job_role or not depot_location or not phone_number:
        return jsonify({'ok': False, 'message': 'First name, last name, job, depot/location, and phone number are required.'}), 400

    if not job_title:
        job_title = job_role

    phone_digits = re.sub(r'[^0-9+]', '', phone_number)
    if len(re.sub(r'[^0-9]', '', phone_digits)) < 7:
        return jsonify({'ok': False, 'message': 'Provide a valid phone number.'}), 400

    database = get_db()
    existing = database.execute('SELECT id FROM contacts WHERE id = ?', (contact_id,)).fetchone()
    if existing is None:
        return jsonify({'ok': False, 'message': 'Contact not found.'}), 404

    database.execute(
        '''
        UPDATE contacts
        SET first_name = ?,
            last_name = ?,
            job_role = ?,
            job_title = ?,
            depot_location = ?,
            phone_number = ?,
            is_important = ?,
            is_private = ?
        WHERE id = ?
        ''',
        (
            encrypt_contact_value(first_name),
            encrypt_contact_value(last_name),
            encrypt_contact_value(job_role),
            encrypt_contact_value(job_title),
            encrypt_contact_value(depot_location),
            encrypt_contact_value(phone_number),
            int(is_important),
            int(is_private),
            contact_id,
        ),
    )
    database.commit()
    return jsonify({'ok': True, 'contactId': contact_id})



@app.delete('/api/contacts/<int:contact_id>')
@login_required('admin_privileges')
def delete_contact(contact_id: int):
    database = get_db()
    existing = database.execute('SELECT id FROM contacts WHERE id = ?', (contact_id,)).fetchone()
    if existing is None:
        return jsonify({'ok': False, 'message': 'Contact not found.'}), 404

    database.execute('DELETE FROM contacts WHERE id = ?', (contact_id,))
    database.commit()
    return jsonify({'ok': True, 'contactId': contact_id})


@app.get('/api/users')
@login_required('admin_privileges')
def list_users():
    database = get_db()
    rows = database.execute(
        'SELECT id, email, is_superadmin, created_at FROM users ORDER BY is_superadmin DESC, email ASC'
    ).fetchall()
    items = []
    for row in rows:
        items.append(
            {
                'id': row['id'],
                'email': row['email'],
                'isSuperadmin': bool(row['is_superadmin']),
                'createdAt': row['created_at'],
                'permissions': fetch_user_permissions(row['id']),
                'session': fetch_user_session_summary(row['id']),
            }
        )
    return jsonify({'users': items, 'permissionLabels': PERMISSIONS})


@app.post('/api/users')
@login_required('admin_privileges')
def create_user():
    actor = get_current_user()
    if actor is None:
        abort(401)

    payload = request.get_json(silent=True) or {}
    email = str(payload.get('email', '')).strip().lower()
    password = str(payload.get('password', ''))
    requested_permissions = payload.get('permissions', {})

    if not email or not password or len(password) < 8:
        return jsonify({'ok': False, 'message': 'Provide an email and a password with at least 8 characters.'}), 400

    if fetch_user_by_email(email) is not None:
        return jsonify({'ok': False, 'message': 'A user with that email already exists.'}), 409

    database = get_db()
    cursor = database.execute(
        'INSERT INTO users (email, password_hash, is_superadmin) VALUES (?, ?, 0)',
        (email, generate_password_hash(password)),
    )
    user_id = cursor.lastrowid

    actor_can_grant_admin = bool(actor['is_superadmin']) or actor['permissions'].get('admin_privileges')
    requested_admin = bool(requested_permissions.get('admin_privileges', False))
    grant_all_permissions = requested_admin and actor_can_grant_admin

    for permission_key in PERMISSIONS:
        enabled = bool(requested_permissions.get(permission_key, False))
        if permission_key == 'admin_privileges' and not actor_can_grant_admin:
            enabled = False
        elif grant_all_permissions:
            enabled = True
        database.execute(
            'INSERT INTO permissions (user_id, permission_key, enabled) VALUES (?, ?, ?)',
            (user_id, permission_key, int(enabled)),
        )
    database.commit()
    return jsonify({'ok': True})


@app.delete('/api/users/<int:user_id>')
@login_required('admin_privileges')
def delete_user(user_id: int):
    actor = get_current_user()
    if actor is None:
        abort(401)

    target_user = get_db().execute('SELECT id, email, is_superadmin FROM users WHERE id = ?', (user_id,)).fetchone()
    if target_user is None:
        abort(404)
    if int(target_user['id']) == int(actor['id']):
        return jsonify({'ok': False, 'message': 'You cannot delete your own account.'}), 400
    if bool(target_user['is_superadmin']) and not bool(actor['is_superadmin']):
        return jsonify({'ok': False, 'message': 'Only superadmins can delete superadmin accounts.'}), 403

    database = get_db()
    database.execute('DELETE FROM users WHERE id = ?', (user_id,))
    database.commit()
    return jsonify({'ok': True, 'deletedUserId': user_id})


@app.post('/api/users/<int:user_id>/sessions/force-logout')
@login_required('admin_privileges')
def force_logout_user_sessions(user_id: int):
    actor = get_current_user()
    if actor is None:
        abort(401)

    target_user = get_db().execute('SELECT id, email, is_superadmin FROM users WHERE id = ?', (user_id,)).fetchone()
    if target_user is None:
        abort(404)
    if int(target_user['id']) == int(actor['id']):
        return jsonify({'ok': False, 'message': 'You cannot sign yourself out from this page.'}), 400
    if bool(target_user['is_superadmin']) and not bool(actor['is_superadmin']):
        return jsonify({'ok': False, 'message': 'Only superadmins can force logout superadmin accounts.'}), 403

    invalidate_user_sessions(user_id)
    return jsonify({'ok': True, 'userId': user_id})


@app.post('/api/users/<int:user_id>/password-reset')
@login_required('admin_privileges')
def force_password_reset(user_id: int):
    actor = get_current_user()
    if actor is None:
        abort(401)

    target_user = get_db().execute('SELECT id, email, is_superadmin FROM users WHERE id = ?', (user_id,)).fetchone()
    if target_user is None:
        abort(404)
    if int(target_user['id']) == int(actor['id']):
        return jsonify({'ok': False, 'message': 'You cannot reset your own password from this page.'}), 400
    if bool(target_user['is_superadmin']) and not bool(actor['is_superadmin']):
        return jsonify({'ok': False, 'message': 'Only superadmins can reset superadmin passwords.'}), 403

    mark_user_for_password_reset(user_id, True)
    invalidate_user_sessions(user_id)
    return jsonify({'ok': True, 'userId': user_id})


@app.patch('/api/users/<int:user_id>/permissions')
@login_required('admin_privileges')
def update_permissions(user_id: int):
    actor = get_current_user()
    if actor is None:
        abort(401)

    target_user = get_db().execute('SELECT id, is_superadmin FROM users WHERE id = ?', (user_id,)).fetchone()
    if target_user is None:
        abort(404)
    if bool(target_user['is_superadmin']):
        return jsonify({'ok': False, 'message': 'Superadmin permissions cannot be changed.'}), 403

    payload = request.get_json(silent=True) or {}
    permission_key = str(payload.get('permissionKey', ''))
    enabled = bool(payload.get('enabled', False))

    if permission_key not in PERMISSIONS:
        return jsonify({'ok': False, 'message': 'Unknown permission requested.'}), 400
    if permission_key == 'admin_privileges' and not (bool(actor['is_superadmin']) or actor['permissions'].get('admin_privileges')):
        return jsonify({'ok': False, 'message': 'Only admins can change admin privileges.'}), 403

    database = get_db()
    if permission_key == 'admin_privileges' and enabled:
        for key in PERMISSIONS:
            database.execute(
                '''
                INSERT INTO permissions (user_id, permission_key, enabled)
                VALUES (?, ?, 1)
                ON CONFLICT(user_id, permission_key) DO UPDATE SET enabled = 1
                ''',
                (user_id, key),
            )
    else:
        database.execute(
            '''
            INSERT INTO permissions (user_id, permission_key, enabled)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, permission_key) DO UPDATE SET enabled = excluded.enabled
            ''',
            (user_id, permission_key, int(enabled)),
        )
    database.commit()
    return jsonify({'ok': True})


@app.errorhandler(403)
def forbidden(_: Exception):
    return render_template('forbidden.html'), 403


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8000)), debug=False)
