from datetime import datetime, timezone

import app as app_module
from app import calculate_vehicle_punctuality, fetch_bods_vehicles, service_is_active


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


def test_vehicle_punctuality_handles_gtfs_times_after_midnight():
    vehicle = {
        'recordedAt': '2024-01-02T00:10:00+00:00',
        'originAimedDepartureTime': '2024-01-01T00:00:00+00:00',
    }
    last_stop = {'id': 'stop-1', 'name': 'Test Stop'}
    trip_schedules = {
        'trip-1': {
            'routeId': 'route-1',
            'direction': 'outbound',
            'stops': [
                {'stopId': 'stop-1', 'name': 'Test Stop', 'arrivalTime': '24:10:00', 'departureTime': '24:10:00'},
            ],
        }
    }

    punctuality = calculate_vehicle_punctuality(
        vehicle,
        last_stop,
        trip_schedules,
        route_id='route-1',
        direction='outbound',
        reference_time='2024-01-02T00:10:00+00:00',
    )

    assert punctuality['status'] == 'on-time'
    assert punctuality['deltaSeconds'] == 0


def test_vehicle_punctuality_prefers_the_matching_stop_in_the_route_position():
    vehicle = {
        'recordedAt': '2024-01-01T12:15:00+00:00',
        'originAimedDepartureTime': '2024-01-01T12:00:00+00:00',
    }
    last_stop = {'id': 'stop-2', 'name': 'Second Stop'}
    trip_schedules = {
        'trip-1': {
            'routeId': 'route-1',
            'direction': 'outbound',
            'stops': [
                {'stopId': 'stop-1', 'name': 'First Stop', 'arrivalTime': '12:00:00', 'departureTime': '12:00:00'},
                {'stopId': 'stop-2', 'name': 'Second Stop', 'arrivalTime': '12:10:00', 'departureTime': '12:10:00'},
                {'stopId': 'stop-2', 'name': 'Second Stop', 'arrivalTime': '12:20:00', 'departureTime': '12:20:00'},
            ],
        }
    }
    route_sequence = {'stops': [{'id': 'stop-1'}, {'id': 'stop-2'}, {'id': 'stop-3'}]}

    punctuality = calculate_vehicle_punctuality(
        vehicle,
        last_stop,
        trip_schedules,
        route_id='route-1',
        direction='outbound',
        reference_time='2024-01-01T12:15:00+00:00',
        route_sequence=route_sequence,
    )

    assert punctuality['status'] == 'early'
    assert punctuality['deltaSeconds'] < 0


def test_vehicle_punctuality_uses_the_closest_service_day_for_midnight_services():
    vehicle = {
        'recordedAt': '2024-01-02T00:00:00+00:00',
        'originAimedDepartureTime': '2024-01-01T23:00:00+00:00',
    }
    last_stop = {'id': 'stop-1', 'name': 'Test Stop'}
    trip_schedules = {
        'trip-1': {
            'routeId': 'route-1',
            'direction': 'outbound',
            'stops': [
                {'stopId': 'stop-1', 'name': 'Test Stop', 'arrivalTime': '01:10:00', 'departureTime': '01:10:00'},
            ],
        }
    }

    punctuality = calculate_vehicle_punctuality(
        vehicle,
        last_stop,
        trip_schedules,
        route_id='route-1',
        direction='outbound',
        reference_time='2024-01-02T00:00:00+00:00',
    )

    assert punctuality['status'] == 'early'
    assert punctuality['deltaSeconds'] == -600


def test_service_is_active_uses_calendar_dates():
    service_calendar = {'service-1': ['20240102']}

    assert service_is_active('service-1', service_calendar, datetime(2024, 1, 2, tzinfo=timezone.utc)) is True
    assert service_is_active('service-1', service_calendar, datetime(2024, 1, 3, tzinfo=timezone.utc)) is False


def test_enrich_tracking_vehicles_builds_payload_without_crashing():
    cache = {
        'routes': [{
            'id': 'route-1',
            'label': 'Route 1',
            'lineName': 'Route 1',
        }],
        'routeStopSequences': {'route-1': {'outbound': {'stops': [{'id': 'stop-1', 'name': 'Test Stop'}]}}},
        'stops': [{'id': 'stop-1', 'name': 'Test Stop'}],
        'tripSchedules': {},
        'serviceCalendar': {},
    }
    vehicles = [{
        'id': 'vehicle-1',
        'service': 'Route 1',
        'direction': 'outbound',
        'latitude': 53.57,
        'longitude': -2.43,
        'recordedAt': '2024-01-01T12:00:00+00:00',
        'originAimedDepartureTime': '2024-01-01T12:00:00+00:00',
    }]

    enriched = app_module.enrich_tracking_vehicles(vehicles, cache)

    assert len(enriched) == 1
    assert enriched[0]['punctuality']['status'] == 'unknown'


def test_fetch_bods_vehicles_returns_empty_when_feed_is_unconfigured(monkeypatch):
    monkeypatch.setattr(app_module, 'get_bods_feed_url', lambda: None)

    vehicles, source_timestamp = fetch_bods_vehicles()

    assert vehicles == []
    assert source_timestamp == ''
