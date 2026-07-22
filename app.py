from __future__ import annotations

import os
import json
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
SNAPSHOT_RETENTION_DAYS = 14
SNAPSHOT_RETENTION_SECONDS = SNAPSHOT_RETENTION_DAYS * 24 * 60 * 60


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
        '''
    )
    database.commit()
    ensure_superadmin(database)


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