from datetime import datetime, timezone

import app as app_module


def test_visible_roadworks_include_upcoming_within_two_weeks():
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    entry = {
        'id': 'permit-1',
        'status': 'Permit granted',
        'startDate': '2026-09-14T12:00:00Z',
        'routeIds': ['route-1'],
        'routeLabels': ['1'],
    }

    serialized = app_module.serialize_visible_roadworks_entry(entry, now=now)

    assert serialized is not None
    assert serialized['isUpcoming'] is True
    assert serialized['lifecycleStatus'] == 'upcoming'
    assert serialized['status'] == 'Upcoming'
    assert serialized['sourceStatus'] == 'Permit granted'


def test_visible_roadworks_exclude_upcoming_beyond_two_weeks():
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    entry = {
        'id': 'permit-2',
        'status': 'Permit granted',
        'startDate': '2026-09-16T12:01:00Z',
    }

    assert app_module.serialize_visible_roadworks_entry(entry, now=now) is None


def test_visible_roadworks_keep_started_status():
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    entry = {
        'id': 'permit-3',
        'status': 'In progress',
        'startDate': '2026-09-01T11:59:00Z',
    }

    serialized = app_module.serialize_visible_roadworks_entry(entry, now=now)

    assert serialized is not None
    assert serialized['isUpcoming'] is False
    assert serialized['lifecycleStatus'] == 'active'
    assert serialized['status'] == 'In progress'


def test_visible_roadworks_keep_expired_for_48_hours():
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    entry = {
        'id': 'permit-4',
        'status': 'In progress',
        'endDate': '2026-08-30T12:01:00Z',
    }

    serialized = app_module.serialize_visible_roadworks_entry(entry, now=now)

    assert serialized is not None
    assert serialized['isExpired'] is True
    assert serialized['lifecycleStatus'] == 'expired'
    assert serialized['status'] == 'Expired'


def test_visible_roadworks_hide_expired_after_48_hours():
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    entry = {
        'id': 'permit-5',
        'status': 'In progress',
        'endDate': '2026-08-30T11:59:00Z',
    }

    assert app_module.serialize_visible_roadworks_entry(entry, now=now) is None


def test_visible_roadworks_mark_extended_as_active():
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    entry = {
        'id': 'permit-6',
        'status': 'In progress',
        'startDate': '2026-09-01T09:00:00Z',
        'endDate': '2026-09-02T09:00:00Z',
        'isExtended': True,
    }

    serialized = app_module.serialize_visible_roadworks_entry(entry, now=now)

    assert serialized is not None
    assert serialized['isExtended'] is True
    assert serialized['isExpired'] is False
    assert serialized['lifecycleStatus'] == 'active'
    assert serialized['status'] == 'Active (Extended)'


def test_roadworks_sort_active_then_expired_then_upcoming():
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    entries = [
        app_module.serialize_visible_roadworks_entry({'id': 'upcoming', 'title': 'C', 'startDate': '2026-09-02T12:00:00Z'}, now=now),
        app_module.serialize_visible_roadworks_entry({'id': 'expired', 'title': 'B', 'endDate': '2026-09-01T11:59:00Z'}, now=now),
        app_module.serialize_visible_roadworks_entry({'id': 'active', 'title': 'A', 'startDate': '2026-09-01T11:00:00Z'}, now=now),
    ]
    visible_entries = [entry for entry in entries if entry is not None]

    visible_entries.sort(key=app_module.roadworks_lifecycle_sort_key)

    assert [entry['id'] for entry in visible_entries] == ['active', 'expired', 'upcoming']