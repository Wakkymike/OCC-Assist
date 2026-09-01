from datetime import datetime, timezone

import app as app_module


def test_user_calendar_settings_include_annual_leave_url():
    app_module.init_db()
    database = app_module.get_db()
    database.execute('DELETE FROM user_settings')
    database.commit()

    app_module.save_user_calendar_settings(
        42,
        'https://example.com/rota.ics',
        'https://example.com/annual-leave.ics',
    )

    settings = app_module.get_user_calendar_settings(42)

    assert settings['rotacloudIcalUrl'] == 'https://example.com/rota.ics'
    assert settings['annualLeaveIcalUrl'] == 'https://example.com/annual-leave.ics'


def test_annual_leave_events_are_tagged_for_shift_display():
    event = {
        'start': datetime(2026, 9, 1, tzinfo=timezone.utc),
        'end': datetime(2026, 9, 2, tzinfo=timezone.utc),
        'summary': 'Booked',
        'location': '',
        'allDay': True,
    }

    tagged = app_module.tag_annual_leave_events([event])[0]

    assert tagged['eventType'] == 'annual_leave'
    assert tagged['summary'] == 'Annual leave: Booked'
    assert tagged['location'] == 'Annual leave'


def test_all_day_annual_leave_serializes_as_holiday_date():
    event = {
        'start': datetime(2026, 9, 1, tzinfo=timezone.utc),
        'end': datetime(2026, 9, 2, tzinfo=timezone.utc),
        'summary': 'Annual leave',
        'location': 'Annual leave',
        'eventType': 'annual_leave',
        'allDay': True,
    }

    serialized = app_module.serialize_shift_event(event)

    assert serialized['eventType'] == 'annual_leave'
    assert serialized['summary'] == 'Annual leave'
    assert serialized['location'] == 'Annual leave'
    assert serialized['windowLabel'] == 'Tue 01 Sep'