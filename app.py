from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen
import xml.etree.ElementTree as ET

from flask import Flask, abort, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / 'instance'
DATABASE_PATH = INSTANCE_DIR / 'occ_assist.db'
SUPERADMIN_EMAIL = os.environ.get('OCC_ASSIST_SUPERADMIN_EMAIL', 'michael.dodsworth@gonorthwest.co.uk')
SUPERADMIN_PASSWORD = os.environ.get('OCC_ASSIST_SUPERADMIN_PASSWORD')
PERMISSIONS = {
    'live_updates': 'Live updates',
    'tracking': 'Tracking',
    'driving_hours': 'Driving hours',
    'user_management': 'User management',
    'admin_privileges': 'Admin privileges',
}
PAGE_PERMISSIONS = {
    'live_updates': 'live_updates',
    'tracking': 'tracking',
    'driving_hours': 'driving_hours',
    'users': 'user_management',
}


app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('OCC_ASSIST_SECRET_KEY', 'change-me-before-production')
app.config['MAPBOX_TOKEN'] = os.environ.get('OCC_ASSIST_MAPBOX_TOKEN', '')
app.config['BODS_FEED_ID'] = os.environ.get('OCC_ASSIST_BODS_FEED_ID', '18880')
app.config['BODS_API_KEY'] = os.environ.get('OCC_ASSIST_BODS_API_KEY', '')
app.config['BODS_STALE_SECONDS'] = int(os.environ.get('OCC_ASSIST_BODS_STALE_SECONDS', '120'))
app.config['STATIC_VERSION'] = str(int(max((BASE_DIR / 'static' / 'scripts.js').stat().st_mtime, (BASE_DIR / 'static' / 'styles.css').stat().st_mtime)))


SIRI_NAMESPACE = {'siri': 'http://www.siri.org.uk/siri'}


def get_db() -> sqlite3.Connection:
    if 'db' not in g:
        INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(DATABASE_PATH)
        connection.row_factory = sqlite3.Row
        g.db = connection
    return g.db


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
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS permissions (
            user_id INTEGER NOT NULL,
            permission_key TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, permission_key),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        '''
    )
    database.commit()
    ensure_superadmin(database)


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


@app.context_processor
def inject_user_context() -> dict[str, object]:
    user = get_current_user()
    return {
        'current_user': user,
        'mapbox_token': app.config['MAPBOX_TOKEN'],
        'static_version': app.config['STATIC_VERSION'],
        'permissions_map': PERMISSIONS,
        'page_permissions': PAGE_PERMISSIONS,
    }


@app.before_request
def prepare_database() -> None:
    init_db()


@app.get('/')
def index():
    if get_current_user() is not None:
        return redirect(url_for('live_updates'))
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
    session['user_id'] = user['id']
    return jsonify({'ok': True, 'redirect': url_for('live_updates')})


@app.post('/api/logout')
def logout():
    session.clear()
    return jsonify({'ok': True, 'redirect': url_for('index')})


@app.get('/api/session')
def session_info():
    user = get_current_user()
    if user is None:
        return jsonify({'authenticated': False})
    return jsonify({'authenticated': True, 'user': user})


@app.get('/live-updates')
@login_required('live_updates')
def live_updates():
    return render_template('live-updates.html')


@app.get('/tracking')
@login_required('tracking')
def tracking():
    return render_template('tracking.html')


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


def parse_bods_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def fetch_bods_vehicles() -> tuple[list[dict[str, object]], str]:
    feed_url = get_bods_feed_url()
    if not feed_url:
        raise RuntimeError('BODS feed credentials are not configured.')

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
        journey_ref = get_xml_text(journey, 'siri:FramedVehicleJourneyRef/siri:DatedVehicleJourneyRef')

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
            }
        )

    return items, response_timestamp


@app.get('/api/tracking/vehicles')
@login_required('tracking')
def tracking_vehicles():
    try:
        vehicles, source_timestamp = fetch_bods_vehicles()
    except RuntimeError as error:
        return jsonify({'ok': False, 'message': str(error)}), 503

    return jsonify(
        {
            'ok': True,
            'vehicles': vehicles,
            'sourceTimestamp': source_timestamp,
            'refreshedAt': datetime.now(timezone.utc).isoformat(),
        }
    )


@app.get('/driving-hours')
@login_required('driving_hours')
def driving_hours():
    return render_template('driving-hours.html')


@app.get('/users')
@login_required('user_management')
def users_page():
    return render_template('users.html')


@app.get('/api/users')
@login_required('user_management')
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
            }
        )
    return jsonify({'users': items, 'permissionLabels': PERMISSIONS})


@app.post('/api/users')
@login_required('user_management')
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
    for permission_key in PERMISSIONS:
        enabled = bool(requested_permissions.get(permission_key, False))
        if permission_key == 'admin_privileges' and not actor_can_grant_admin:
            enabled = False
        database.execute(
            'INSERT INTO permissions (user_id, permission_key, enabled) VALUES (?, ?, ?)',
            (user_id, permission_key, int(enabled)),
        )
    database.commit()
    return jsonify({'ok': True})


@app.patch('/api/users/<int:user_id>/permissions')
@login_required('user_management')
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