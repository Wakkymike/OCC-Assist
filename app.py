from __future__ import annotations

import os
import re
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import urlopen
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

from flask import Flask, abort, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / 'instance'
DATABASE_PATH = INSTANCE_DIR / 'occ_assist.db'
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
ROUTE_TRAIL_RETENTION_SECONDS = int(os.environ.get('OCC_ASSIST_ROUTE_TRAIL_RETENTION_SECONDS', '2700'))
ROUTE_TRAIL_MAX_POINTS = int(os.environ.get('OCC_ASSIST_ROUTE_TRAIL_MAX_POINTS', '120'))
GO_NORTH_WEST_OPERATOR_MARKERS = ('go north west', 'go-north-west', 'go_north_west', 'gonorthwest', 'gnw', 'bngn')


VEHICLE_ROUTE_TRAILS: dict[str, list[dict[str, object]]] = {}
VEHICLE_ROUTE_TRAILS_LOCK = Lock()


app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('OCC_ASSIST_SECRET_KEY', 'change-me-before-production')
app.config['MAPBOX_TOKEN'] = os.environ.get('OCC_ASSIST_MAPBOX_TOKEN', '')
app.config['BODS_FEED_ID'] = os.environ.get('OCC_ASSIST_BODS_FEED_ID', '18880')
app.config['BODS_API_KEY'] = os.environ.get('OCC_ASSIST_BODS_API_KEY', '')
app.config['BODS_STALE_SECONDS'] = int(os.environ.get('OCC_ASSIST_BODS_STALE_SECONDS', '120'))
app.config['STATIC_VERSION'] = str(int(max((BASE_DIR / 'static' / 'scripts.js').stat().st_mtime, (BASE_DIR / 'static' / 'styles.css').stat().st_mtime)))


SIRI_NAMESPACE = {'siri': 'http://www.siri.org.uk/siri'}
LONDON_TZ = ZoneInfo('Europe/London')


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

    update_vehicle_route_trails(items)

    return items, response_timestamp


def parse_bool_query(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    lowered = str(value).strip().lower()
    if lowered in {'1', 'true', 'yes', 'on'}:
        return True
    if lowered in {'0', 'false', 'no', 'off'}:
        return False
    return default


def is_go_north_west_bee_vehicle(vehicle: dict[str, object]) -> bool:
    service = str(vehicle.get('service') or '').strip()
    if not service:
        return False

    operator = str(vehicle.get('operator') or '').strip().lower()
    normalized = operator.replace(' ', '').replace('-', '').replace('_', '')
    if normalized in {'gnw', 'bngn'}:
        return True
    return any(marker in operator for marker in GO_NORTH_WEST_OPERATOR_MARKERS)


def route_sort_key(route: str) -> tuple[int, int, str]:
    value = str(route or '').strip()
    match = re.match(r'^(\d+)', value)
    if match:
        return (0, int(match.group(1)), value.lower())
    return (1, 9999, value.lower())


def get_recorded_at_epoch(vehicle: dict[str, object]) -> int:
    recorded_at = str(vehicle.get('recordedAt') or '').strip()
    recorded_time = parse_bods_timestamp(recorded_at)
    if recorded_time is None:
        return int(datetime.now(timezone.utc).timestamp())
    if recorded_time.tzinfo is None:
        recorded_time = recorded_time.replace(tzinfo=timezone.utc)
    return int(recorded_time.timestamp())


def update_vehicle_route_trails(vehicles: list[dict[str, object]]) -> None:
    now_epoch = int(datetime.now(timezone.utc).timestamp())
    cutoff = now_epoch - ROUTE_TRAIL_RETENTION_SECONDS

    with VEHICLE_ROUTE_TRAILS_LOCK:
        for vehicle in vehicles:
            if not is_go_north_west_bee_vehicle(vehicle):
                continue

            vehicle_id = str(vehicle.get('id') or '').strip()
            if not vehicle_id:
                continue

            service = str(vehicle.get('service') or '').strip()
            operator = str(vehicle.get('operator') or '').strip()
            point = {
                'service': service,
                'operator': operator,
                'latitude': float(vehicle.get('latitude') or 0.0),
                'longitude': float(vehicle.get('longitude') or 0.0),
                'recordedAtEpoch': get_recorded_at_epoch(vehicle),
            }

            trail = VEHICLE_ROUTE_TRAILS.setdefault(vehicle_id, [])
            if trail:
                previous = trail[-1]
                if (
                    previous.get('service') == service
                    and abs(float(previous.get('latitude', 0.0)) - point['latitude']) < 0.00001
                    and abs(float(previous.get('longitude', 0.0)) - point['longitude']) < 0.00001
                ):
                    previous['recordedAtEpoch'] = point['recordedAtEpoch']
                else:
                    trail.append(point)
            else:
                trail.append(point)

            if len(trail) > ROUTE_TRAIL_MAX_POINTS:
                del trail[:len(trail) - ROUTE_TRAIL_MAX_POINTS]

        stale_vehicle_ids = []
        for vehicle_id, trail in VEHICLE_ROUTE_TRAILS.items():
            filtered = [entry for entry in trail if int(entry.get('recordedAtEpoch', 0)) >= cutoff]
            if filtered:
                VEHICLE_ROUTE_TRAILS[vehicle_id] = filtered
            else:
                stale_vehicle_ids.append(vehicle_id)

        for vehicle_id in stale_vehicle_ids:
            VEHICLE_ROUTE_TRAILS.pop(vehicle_id, None)


def list_go_north_west_routes(vehicles: list[dict[str, object]]) -> list[str]:
    routes = {
        str(vehicle.get('service') or '').strip()
        for vehicle in vehicles
        if is_go_north_west_bee_vehicle(vehicle)
    }
    return sorted((route for route in routes if route), key=route_sort_key)


def build_go_north_west_route_overlay(selected_route: str) -> dict[str, object]:
    normalized_route = str(selected_route or '').strip().lower()
    cutoff = int(datetime.now(timezone.utc).timestamp()) - ROUTE_TRAIL_RETENTION_SECONDS
    features: list[dict[str, object]] = []

    with VEHICLE_ROUTE_TRAILS_LOCK:
        for vehicle_id, trail in VEHICLE_ROUTE_TRAILS.items():
            if len(trail) < 2:
                continue

            latest = trail[-1]
            service = str(latest.get('service') or '').strip()
            if not service:
                continue
            if normalized_route not in {'', 'all'} and service.lower() != normalized_route:
                continue
            if not is_go_north_west_bee_vehicle(latest):
                continue

            line_points = [
                [float(entry.get('longitude', 0.0)), float(entry.get('latitude', 0.0))]
                for entry in trail
                if int(entry.get('recordedAtEpoch', 0)) >= cutoff and str(entry.get('service') or '').strip() == service
            ]
            if len(line_points) < 2:
                continue

            features.append(
                {
                    'type': 'Feature',
                    'geometry': {
                        'type': 'LineString',
                        'coordinates': line_points,
                    },
                    'properties': {
                        'vehicleId': vehicle_id,
                        'service': service,
                    },
                }
            )

    return {
        'type': 'FeatureCollection',
        'features': features,
    }


@app.get('/api/tracking/vehicles')
@login_required('tracking')
def tracking_vehicles():
    selected_route = str(request.args.get('route', 'all') or 'all').strip()
    show_route_overlay = parse_bool_query(request.args.get('showRouteOverlay'), default=False)
    only_selected_route_vehicles = parse_bool_query(request.args.get('onlySelectedRouteVehicles'), default=False)

    try:
        vehicles, source_timestamp = fetch_bods_vehicles()
    except RuntimeError as error:
        return jsonify({'ok': False, 'message': str(error)}), 503

    go_north_west_routes = list_go_north_west_routes(vehicles)
    if selected_route and selected_route.lower() != 'all' and selected_route not in go_north_west_routes:
        selected_route = 'all'

    filtered_vehicles = vehicles
    if only_selected_route_vehicles and selected_route.lower() != 'all':
        filtered_vehicles = [
            vehicle
            for vehicle in vehicles
            if is_go_north_west_bee_vehicle(vehicle) and str(vehicle.get('service') or '').strip() == selected_route
        ]

    route_overlay = {'type': 'FeatureCollection', 'features': []}
    if show_route_overlay:
        route_overlay = build_go_north_west_route_overlay(selected_route)
        if selected_route.lower() != 'all' and not route_overlay.get('features'):
            live_points: list[list[float]] = []
            for vehicle in vehicles:
                if not is_go_north_west_bee_vehicle(vehicle):
                    continue
                if str(vehicle.get('service') or '').strip() != selected_route:
                    continue
                live_points.append([float(vehicle.get('longitude', 0.0)), float(vehicle.get('latitude', 0.0))])

            if len(live_points) >= 2:
                route_overlay['features'].append(
                    {
                        'type': 'Feature',
                        'geometry': {
                            'type': 'LineString',
                            'coordinates': live_points,
                        },
                        'properties': {
                            'vehicleId': 'live-current',
                            'service': selected_route,
                            'fallback': True,
                        },
                    }
                )

    return jsonify(
        {
            'ok': True,
            'vehicles': filtered_vehicles,
            'sourceTimestamp': source_timestamp,
            'goNorthWestRoutes': go_north_west_routes,
            'selectedRoute': selected_route,
            'routeOverlay': route_overlay,
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