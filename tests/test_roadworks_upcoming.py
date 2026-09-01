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