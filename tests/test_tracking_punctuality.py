from datetime import datetime, timezone

import app as app_module
from app import calculate_vehicle_punctuality, fetch_bods_vehicles, select_nearest_route_stop, service_is_active


def test_serialize_tracking_stop_includes_naptan_code_and_next_arrivals():
    stop = {'stopId': '0100B123', 'name': 'Market Street', 'latitude': 53.4, 'longitude': -2.3}
    trip_schedules = {
        'trip-1': {
            'routeId': 'BNGN',
            'direction': 'outbound',
            'serviceId': 'bngn',
            'stops': [
                {'stopId': '0100B123', 'name': 'Market Street', 'arrivalTime': '12:05:00', 'departureTime': '12:05:00'},
            ],
        }
    }

    payload = app_module.serialize_tracking_stop(stop, trip_schedules, datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc))

    assert payload['naptan'] == '0100B123'
    assert payload['stopCode'] == '0100B123'
    assert payload['nextArrivals'][0]['routeId'] == 'BNGN'
    assert payload['nextArrivals'][0]['countdownSeconds'] == 300


def test_live_stop_arrivals_include_service_and_fleet_number():
    stop = {'stopId': '0100B123', 'name': 'Market Street', 'latitude': 53.4, 'longitude': -2.3}
    vehicles = [
        {
            'routeId': '10',
            'routeLabel': '10',
            'fleetNumber': '6123',
            'direction': 'outbound',
            'latitude': 53.39,
            'longitude': -2.3,
        }
    ]
    trip_schedules = {
        'trip-1': {
            'routeId': '10',
            'direction': 'outbound',
            'stops': [{'stopId': '0100B123', 'name': 'Market Street'}],
        }
    }

    arrivals = app_module.build_live_stop_arrivals(stop, vehicles, trip_schedules)

    assert arrivals[0]['service'] == '10'
    assert arrivals[0]['fleetNumber'] == '6123'
    assert arrivals[0]['countdownSeconds'] > 0


def test_board_matched_stop_arrivals_use_live_fleet_number():
    stop = {'stopId': '0100B123', 'name': 'Market Street', 'latitude': 53.4, 'longitude': -2.3}
    scheduled_arrivals = [{'tripId': 'trip-1', 'routeId': '10', 'direction': 'outbound'}]
    trip_schedules = {
        'trip-1': {
            'routeId': '10',
            'blockId': 'RB-77',
        }
    }
    live_vehicles = [
        {
            'boardNumber': 'RB-77',
            'routeLabel': '10',
            'fleetNumber': '6123',
            'direction': 'outbound',
            'latitude': 53.39,
            'longitude': -2.3,
        }
    ]

    arrivals = app_module.build_board_matched_stop_arrivals(
        stop,
        scheduled_arrivals,
        trip_schedules,
        live_vehicles,
        {'10': '10'},
    )

    assert arrivals[0]['service'] == '10'
    assert arrivals[0]['fleetNumber'] == '6123'
    assert arrivals[0]['boardNumber'] == 'RB-77'


def test_stop_departure_board_mixes_live_and_scheduled_entries():
    stop = {'stopId': '0100B123', 'name': 'Market Street', 'latitude': 53.4, 'longitude': -2.3}
    trip_schedules = {
        'trip-live': {
            'routeId': 'route-10',
            'direction': 'outbound',
            'blockId': 'RB-77',
            'headsign': 'Bolton Interchange',
            'stops': [
                {'stopId': 'origin', 'name': 'Depot', 'arrivalTime': '11:55:00', 'departureTime': '11:55:00'},
                {'stopId': '0100B123', 'name': 'Market Street', 'arrivalTime': '12:05:00', 'departureTime': '12:05:00'},
            ],
        },
        'trip-timetabled': {
            'routeId': 'route-10',
            'direction': 'outbound',
            'headsign': 'Bolton Interchange',
            'stops': [
                {'stopId': 'origin', 'name': 'Depot', 'arrivalTime': '12:15:00', 'departureTime': '12:15:00'},
                {'stopId': '0100B123', 'name': 'Market Street', 'arrivalTime': '12:25:00', 'departureTime': '12:25:00'},
            ],
        },
    }
    live_vehicles = [
        {
            'id': 'vehicle-1',
            'boardNumber': 'RB-77',
            'service': 'route-10',
            'fleetNumber': '6123',
            'destination': 'Bolton Interchange',
            'direction': 'outbound',
            'punctuality': {'deltaSeconds': 60},
        }
    ]

    board = app_module.build_stop_departure_board(
        stop,
        trip_schedules,
        live_vehicles,
        {'route-10': '10'},
        datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
    )

    assert len(board) == 2
    assert board[0]['isLive'] is True
    assert board[0]['service'] == '10'
    assert board[0]['fleetNumber'] == '6123'
    assert board[0]['boardNumber'] == 'RB-77'
    assert board[0]['destination'] == 'Bolton Interchange'
    assert board[0]['countdownSeconds'] == 360
    assert board[1]['isLive'] is False
    assert board[1]['source'] == 'scheduled'
    assert board[1]['scheduledTime']


def test_departure_countdown_label_reads_due_now_within_a_minute():
    assert app_module.format_departure_countdown_label(60) == 'Due now'
    assert app_module.format_departure_countdown_label(0) == 'Due now'
    assert app_module.format_departure_countdown_label(300) == '5 min'


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


def test_vehicle_punctuality_uses_naptan_when_available():
    vehicle = {
        'recordedAt': '2024-01-01T12:20:00+00:00',
        'originAimedDepartureTime': '2024-01-01T12:00:00+00:00',
        'naptan': '350013456',
    }
    last_stop = {'naptan': '350013456', 'name': 'Northbound stop'}
    trip_schedules = {
        'trip-1': {
            'routeId': 'route-1',
            'direction': 'outbound',
            'stops': [
                {'stopId': 'stop-1', 'naptan': '350013456', 'name': 'Northbound stop', 'arrivalTime': '12:15:00', 'departureTime': '12:15:00'},
            ],
        }
    }

    punctuality = calculate_vehicle_punctuality(
        vehicle,
        last_stop,
        trip_schedules,
        route_id='route-1',
        direction='outbound',
        reference_time='2024-01-01T12:20:00+00:00',
    )

    assert punctuality['status'] == 'late'
    assert punctuality['deltaSeconds'] == 300


def test_serialize_tracking_stop_includes_naptan_code():
    stop = {
        'stopId': 'stop-1',
        'naptan': '350013456',
        'name': 'Northbound stop',
        'latitude': 53.57,
        'longitude': -2.43,
    }

    serialized = app_module.serialize_tracking_stop(stop)

    assert serialized['id'] == 'stop-1'
    assert serialized['naptan'] == '350013456'


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


def test_vehicle_punctuality_falls_back_to_route_position_when_stop_ids_do_not_match():
    vehicle = {
        'recordedAt': '2024-01-01T12:15:00+00:00',
        'originAimedDepartureTime': '2024-01-01T12:00:00+00:00',
    }
    last_stop = {'id': 'unknown-stop', 'name': 'Current position'}
    trip_schedules = {
        'trip-1': {
            'routeId': 'route-1',
            'direction': 'outbound',
            'stops': [
                {'stopId': 'stop-1', 'name': 'First Stop', 'arrivalTime': '12:00:00', 'departureTime': '12:00:00'},
                {'stopId': 'stop-2', 'name': 'Second Stop', 'arrivalTime': '12:10:00', 'departureTime': '12:10:00'},
            ],
        }
    }
    route_sequence = {'stops': [{'id': 'stop-1'}, {'id': 'stop-2'}]}

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
    assert punctuality['label'] != 'Unknown'


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


def test_vehicle_punctuality_prefers_arrival_times_when_present():
    vehicle = {
        'recordedAt': '2024-01-01T12:15:00+00:00',
        'originAimedDepartureTime': '2024-01-01T12:00:00+00:00',
    }
    last_stop = {'id': 'stop-1', 'name': 'Test Stop'}
    trip_schedules = {
        'trip-1': {
            'routeId': 'route-1',
            'direction': 'outbound',
            'stops': [
                {'stopId': 'stop-1', 'name': 'Test Stop', 'arrivalTime': '12:10:00', 'departureTime': '12:20:00'},
            ],
        }
    }

    punctuality = calculate_vehicle_punctuality(
        vehicle,
        last_stop,
        trip_schedules,
        route_id='route-1',
        direction='outbound',
        reference_time='2024-01-01T12:15:00+00:00',
    )

    assert punctuality['status'] == 'early'
    assert punctuality['deltaSeconds'] < 0


def test_select_nearest_route_stop_uses_the_closest_stop_on_the_route():
    vehicle = {'latitude': 53.57, 'longitude': -2.43}
    route_sequence = {
        'stops': [
            {'id': 'stop-1', 'name': 'First Stop', 'latitude': 53.5702, 'longitude': -2.4302},
            {'id': 'stop-2', 'name': 'Second Stop', 'latitude': 53.5708, 'longitude': -2.4308},
        ]
    }

    stop = select_nearest_route_stop(vehicle, route_sequence)

    assert stop is not None
    assert stop['id'] == 'stop-1'


def test_select_last_stop_passed_prefers_the_stop_closest_to_the_vehicle_on_the_route():
    vehicle = {'latitude': 53.5705, 'longitude': -2.4305}
    route_sequence = {
        'stops': [
            {'id': 'stop-1', 'name': 'First Stop', 'latitude': 53.5702, 'longitude': -2.4302},
            {'id': 'stop-2', 'name': 'Second Stop', 'latitude': 53.5708, 'longitude': -2.4308},
        ]
    }

    stop = app_module.select_last_stop_passed(vehicle, route_sequence)

    assert stop is not None
    assert stop['id'] == 'stop-2'


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
