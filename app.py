from __future__ import annotations

import os
import re
import json
import csv
import io
import math
import shutil
import sqlite3
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


BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / 'instance'
DEFAULT_DATABASE_PATH = INSTANCE_DIR / 'occ_assist.db'
SUPERADMIN_EMAIL = os.environ.get('OCC_ASSIST_SUPERADMIN_EMAIL', 'michael.dodsworth@gonorthwest.co.uk')
SUPERADMIN_PASSWORD = os.environ.get('OCC_ASSIST_SUPERADMIN_PASSWORD')
PERMISSIONS = {
    'live_updates': 'Daily overview',
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
GTFS_DIR = INSTANCE_DIR / 'gtfs'
GTFS_UPLOAD_PATH = GTFS_DIR / 'latest-gtfs.zip'
GTFS_EXTRACT_DIR = GTFS_DIR / 'extracted'
GTFS_CACHE_PATH = GTFS_DIR / 'routes-cache.json'
GTFS_MAX_UPLOAD_BYTES = int(os.environ.get('OCC_ASSIST_GTFS_MAX_UPLOAD_BYTES', '60000000'))
GTFS_ALLOWED_AGENCY_ID = str(os.environ.get('OCC_ASSIST_GTFS_ALLOWED_AGENCY_ID', 'OP11122')).strip()
GTFS_MAX_FALLBACK_PATTERNS_PER_ROUTE = int(os.environ.get('OCC_ASSIST_GTFS_MAX_FALLBACK_PATTERNS_PER_ROUTE', '4'))


app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('OCC_ASSIST_SECRET_KEY', 'change-me-before-production')
app.config['MAPBOX_TOKEN'] = os.environ.get('OCC_ASSIST_MAPBOX_TOKEN', '')
app.config['BODS_FEED_ID'] = os.environ.get('OCC_ASSIST_BODS_FEED_ID', '18880')
app.config['BODS_API_KEY'] = os.environ.get('OCC_ASSIST_BODS_API_KEY', '')
app.config['BODS_STALE_SECONDS'] = int(os.environ.get('OCC_ASSIST_BODS_STALE_SECONDS', '120'))
app.config['STATIC_VERSION'] = str(int(max((BASE_DIR / 'static' / 'scripts.js').stat().st_mtime, (BASE_DIR / 'static' / 'styles.css').stat().st_mtime)))


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

        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            rotacloud_ical_url TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
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
        'tracking_stops_url': url_for('tracking_stops'),
        'service_overview_url': url_for('service_overview'),
    }


@app.before_request
def prepare_database() -> None:
    init_db()


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
    session['user_id'] = user['id']
    return jsonify({'ok': True, 'redirect': url_for('daily_overview')})


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


@app.get('/daily-overview')
@login_required('live_updates')
def daily_overview():
    return render_template('daily-overview.html')


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

    return jsonify(
        {
            'ok': True,
            'configured': True,
            'scope': scope,
            'offset': offset,
            'periodLabel': period_label,
            'shifts': [serialize_shift_event(event) for event in filtered_events],
            'weekStartsOn': 'Sunday',
        }
    )


@app.get('/tracking')
@login_required('tracking')
def tracking():
    return render_template('tracking.html')


@app.get('/service-overview')
@login_required('tracking')
def service_overview():
    return render_template('service-overview.html')


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
                'journeyRef': journey_ref,
                'vehicleJourneyRef': vehicle_journey_ref,
                'blockRef': block_ref,
                'journeyCode': journey_code,
            }
        )

    return items, response_timestamp


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
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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


def format_punctuality_delta(delta_seconds: int) -> str:
    if delta_seconds == 0:
        return '0m'

    minutes = int(round(abs(delta_seconds) / 60))
    if minutes <= 0:
        minutes = 1
    return f'{minutes}m'


def format_punctuality_label(delta_seconds: int, scheduled_at: datetime | None) -> str:
    if delta_seconds < 0:
        prefix = f'Early -{format_punctuality_delta(delta_seconds)}'
    elif delta_seconds > 0:
        prefix = f'Late +{format_punctuality_delta(delta_seconds)}'
    else:
        prefix = 'On time 0m'

    if scheduled_at is None:
        return prefix

    time_text = scheduled_at.astimezone(timezone.utc).strftime('%H:%M')
    return f'{prefix} · {time_text}'


def collect_stop_match_keys(stop: dict[str, object] | None) -> set[str]:
    keys: set[str] = set()
    if not isinstance(stop, dict):
        return keys

    for field in ('stopId', 'id', 'stopPointRef', 'stopRef', 'name', 'stopName'):
        value = str(stop.get(field) or '').strip()
        if not value:
            continue
        normalized = normalize_tracking_key(value)
        if normalized:
            keys.add(normalized)
    return keys


def stop_matches_schedule_entry(stop: dict[str, object] | None, schedule_entry: dict[str, object] | None) -> bool:
    if not isinstance(stop, dict) or not isinstance(schedule_entry, dict):
        return False

    stop_keys = collect_stop_match_keys(stop)
    schedule_keys = collect_stop_match_keys(schedule_entry)
    return bool(stop_keys and schedule_keys and stop_keys.intersection(schedule_keys))


def build_scheduled_stop_datetime(base_time: datetime | None, stop_time_value: object, first_stop_time_value: object | None = None) -> datetime | None:
    if base_time is None:
        return None

    stop_seconds = parse_gtfs_time(stop_time_value)
    if stop_seconds is None:
        return None

    first_seconds = parse_gtfs_time(first_stop_time_value) if first_stop_time_value is not None else None
    candidate_time = base_time
    if first_seconds is not None and stop_seconds < first_seconds:
        candidate_time = base_time + timedelta(days=1)

    day_offset = stop_seconds // 86400
    if day_offset:
        candidate_time = candidate_time + timedelta(days=day_offset)

    seconds_within_day = stop_seconds % 86400
    hours, remainder = divmod(seconds_within_day, 3600)
    minutes, seconds = divmod(remainder, 60)
    scheduled_date = candidate_time.date()
    return datetime(scheduled_date.year, scheduled_date.month, scheduled_date.day, hours, minutes, seconds, tzinfo=timezone.utc)


def calculate_vehicle_punctuality(
    vehicle: dict[str, object],
    last_stop: dict[str, object] | None,
    trip_schedules: dict[str, object],
    route_id: str | None = None,
    direction: str | None = None,
    reference_time: object | None = None,
) -> dict[str, object]:
    observed_time = parse_tracking_datetime(vehicle.get('recordedAt') or vehicle.get('sourceTimestamp') or vehicle.get('refreshedAt') or reference_time)
    if observed_time is None:
        observed_time = parse_tracking_datetime(reference_time) or datetime.now(timezone.utc)

    stop_keys = collect_stop_match_keys(last_stop)
    if not stop_keys:
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
        schedule_entry = None
        for stop_entry in trip_payload:
            if not isinstance(stop_entry, dict):
                continue
            if stop_matches_schedule_entry(last_stop, stop_entry):
                schedule_entry = stop_entry
                break
        if schedule_entry is None:
            continue
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
        first_stop_time = first_stop.get('departureTime') or first_stop.get('arrivalTime') if first_stop else None
        scheduled_at = build_scheduled_stop_datetime(base_time, schedule_entry.get('departureTime') or schedule_entry.get('arrivalTime'), first_stop_time)
        if scheduled_at is None:
            continue

        delta_seconds = int((observed_time - scheduled_at).total_seconds())
        candidate_key = abs(delta_seconds)
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
    for stop, stop_along in zip(stops, cumulative):
        if not isinstance(stop, dict):
            continue
        if stop_along <= projection['along'] + 30:
            selected_stop = stop

    return selected_stop


def enrich_tracking_vehicles(vehicles: list[dict[str, object]], cache: dict[str, object] | None) -> list[dict[str, object]]:
    route_lookup = build_tracking_route_lookup(cache)
    route_sequences = build_tracking_route_sequences(cache)
    all_stops = [stop for stop in (cache or {}).get('stops', []) if isinstance(stop, dict)]
    enriched: list[dict[str, object]] = []
    trip_schedules = cache.get('tripSchedules', {}) if isinstance(cache, dict) else {}

    for vehicle in vehicles:
        service = str(vehicle.get('service') or '').strip()
        normalized_service = normalize_tracking_key(service)
        route = route_lookup.get(normalized_service)
        route_id = str(route.get('id') or '').strip() if route else ''
        route_label = str(route.get('label') or route.get('lineName') or service or route_id).strip()
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
        )

        enriched.append(
            {
                **vehicle,
                'routeId': route_id or None,
                'routeLabel': route_label,
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


def serialize_tracking_stop(stop: dict[str, object]) -> dict[str, object]:
    return {
        'id': str(stop.get('stopId') or stop.get('id') or '').strip(),
        'name': str(stop.get('name') or stop.get('stopName') or 'Unknown stop').strip(),
        'latitude': float(stop.get('latitude', 0.0)),
        'longitude': float(stop.get('longitude', 0.0)),
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
        stops_lookup[stop_id] = {
            'stopId': stop_id,
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

    return GTFS_EXTRACT_DIR


def parse_gtfs_routes_from_directory(extracted_dir: Path) -> dict[str, object]:
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

    route_meta: dict[str, dict[str, str]] = {}
    for row in route_rows:
        route_id = str(row.get('route_id') or '').strip()
        agency_id = str(row.get('agency_id') or '').strip()
        if not route_id:
            continue
        if GTFS_ALLOWED_AGENCY_ID and agency_id != GTFS_ALLOWED_AGENCY_ID:
            continue
        route_meta[route_id] = {
            'shortName': str(row.get('route_short_name') or '').strip(),
            'longName': str(row.get('route_long_name') or '').strip(),
        }

    if GTFS_ALLOWED_AGENCY_ID and not route_meta:
        raise ValueError(f'No routes found for agency ID {GTFS_ALLOWED_AGENCY_ID} in this GTFS ZIP.')

    allowed_route_ids = set(route_meta.keys())

    route_shapes: dict[str, set[str]] = {}
    route_shape_directions: dict[str, dict[str, set[str]]] = {}
    route_trips: dict[str, set[str]] = {}
    trip_routes: dict[str, str] = {}
    trip_directions: dict[str, str] = {}
    for row in trip_rows:
        route_id = str(row.get('route_id') or '').strip()
        trip_id = str(row.get('trip_id') or '').strip()
        shape_id = str(row.get('shape_id') or '').strip()
        direction = normalize_gtfs_direction(str(row.get('direction_id') or ''))
        if not route_id:
            continue
        if allowed_route_ids and route_id not in allowed_route_ids:
            continue
        if trip_id:
            route_trips.setdefault(route_id, set()).add(trip_id)
            trip_routes[trip_id] = route_id
            trip_directions[trip_id] = direction
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
    if stops_path is not None and stop_times_path is not None:
        relevant_trip_ids = set().union(*route_trips.values()) if route_trips else set()
        if relevant_trip_ids:
            stop_rows = read_gtfs_rows(stops_path)
            for row in stop_rows:
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
                stops_lookup[stop_id] = {
                    'stopId': stop_id,
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
        'featureCollection': {
            'type': 'FeatureCollection',
            'features': features,
        },
    }


def load_gtfs_cache(allow_rebuild: bool = False) -> dict[str, object] | None:
    if not GTFS_CACHE_PATH.exists():
        return None
    try:
        data = json.loads(GTFS_CACHE_PATH.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if allow_rebuild and not data.get('tripSchedules') and GTFS_UPLOAD_PATH.exists():
        try:
            raw = GTFS_UPLOAD_PATH.read_bytes()
            extracted_dir = unzip_gtfs_archive(raw)
            parsed = parse_gtfs_routes_from_directory(extracted_dir)
        except (OSError, ValueError):
            return data
        data['routeStopSequences'] = parsed.get('routeStopSequences', {})
        data['tripSchedules'] = parsed.get('tripSchedules', {})
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
        'featureCollection': parsed['featureCollection'],
    }
    GTFS_CACHE_PATH.write_text(json.dumps(payload), encoding='utf-8')
    return payload


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
@login_required('user_management')
def gtfs_status():
    cache = load_gtfs_cache(allow_rebuild=False)
    if cache is None:
        return jsonify(
            {
                'ok': True,
                'configured': False,
                'message': 'No GTFS ZIP uploaded yet.',
                'routeCount': 0,
            }
        )

    return jsonify(
        {
            'ok': True,
            'configured': True,
            'uploadedAt': cache.get('uploadedAt', ''),
            'originalFilename': cache.get('originalFilename', ''),
            'routeCount': int(cache.get('routeCount', 0)),
        }
    )


@app.post('/api/gtfs/upload')
@login_required('user_management')
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
@login_required('tracking')
def tracking_vehicles():
    cache = ensure_gtfs_cache_stops(load_gtfs_cache(allow_rebuild=False))

    try:
        vehicles, source_timestamp = fetch_bods_vehicles()
    except Exception:
        vehicles = []
        source_timestamp = ''

    if not vehicles:
        return jsonify(
            {
                'ok': True,
                'message': 'No live vehicle feed is available right now. Upload a timetable ZIP in Users to enable timetable-based tracking.',
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

    stops = [serialize_tracking_stop(stop) for stop in cache.get('stops', []) if isinstance(stop, dict)]
    return jsonify(
        {
            'ok': True,
            'configured': True,
            'stops': stops,
            'stopCount': len(stops),
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


@app.delete('/api/users/<int:user_id>')
@login_required('user_management')
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