from datetime import datetime, timezone

import app as app_module
from app import calculate_vehicle_punctuality, fetch_bods_vehicles


def test_vehicle_punctuality_marks_early_vehicles_as_early():
    vehicle = {
        'originAimedDepartureTime': '2024-01-01T12:00:00+00:00',
        'recordedAt': '2024-01-01T12:00:00+00:00',
    }
    last_stop = {'id': 'stop-1', 'name': 'Test Stop'}
    trip_schedules = {
        'trip-1': {
            'routeId': 'route-1',
            'direction': 'outbound',
            'stops': [
                {'stopId': 'stop-1', 'name': 'Test Stop', 'arrivalTime': '12:05:00', 'departureTime': '12:05:00'},
            ],
        }
    }

    punctuality = calculate_vehicle_punctuality(
        vehicle,
        last_stop,
        trip_schedules,
        route_id='route-1',
        direction='outbound',
        reference_time='2024-01-01T12:00:00+00:00',
    )

    assert punctuality['status'] == 'early'
    assert punctuality['tone'] == 'red'
    assert punctuality['deltaSeconds'] < 0


def test_vehicle_punctuality_marks_heavily_late_vehicles_as_yellow():
    vehicle = {
        'latitude': 53.57,
        'longitude': -2.43,
        'originAimedDepartureTime': '2024-01-01T12:00:00+00:00',
        'destinationAimedArrivalTime': '2024-01-01T12:10:00+00:00',
        'recordedAt': '2024-01-01T12:05:00+00:00',
    }
    route_sequence = {
        'stops': [
            {'longitude': -2.43, 'latitude': 53.57},
            {'longitude': -2.428, 'latitude': 53.58},
        ]
    }

    punctuality = calculate_vehicle_punctuality(vehicle, route_sequence, datetime(2024, 1, 1, 12, 5, 0, tzinfo=timezone.utc))

    assert punctuality['status'] == 'late'
    assert punctuality['tone'] == 'yellow'
    assert punctuality['deltaSeconds'] > 299


def test_vehicle_punctuality_uses_stop_name_when_stop_id_is_missing():
    vehicle = {
        'recordedAt': '2024-01-01T12:05:00+00:00',
        'originAimedDepartureTime': '2024-01-01T12:00:00+00:00',
    }
    last_stop = {'id': 'stop-900', 'name': 'Central Station'}
    trip_schedules = {
        'trip-1': {
            'routeId': 'route-1',
            'direction': 'outbound',
            'stops': [
                {'stopId': 'stop-123', 'name': 'Central Station', 'arrivalTime': '12:05:00', 'departureTime': '12:05:00'},
            ],
        }
    }

    punctuality = calculate_vehicle_punctuality(
        vehicle,
        last_stop,
        trip_schedules,
        route_id='route-1',
        direction='outbound',
        reference_time='2024-01-01T12:05:00+00:00',
    )

    assert punctuality['status'] == 'on-time'
    assert punctuality['deltaSeconds'] == 0


def test_fetch_bods_vehicles_returns_empty_when_feed_is_unconfigured(monkeypatch):
    monkeypatch.setattr(app_module, 'get_bods_feed_url', lambda: None)

    vehicles, source_timestamp = fetch_bods_vehicles()

    assert vehicles == []
    assert source_timestamp == ''
