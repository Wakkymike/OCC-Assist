from datetime import datetime, timezone

from app import calculate_vehicle_punctuality


def test_vehicle_punctuality_marks_early_vehicles_as_early():
    vehicle = {
        'latitude': 53.58,
        'longitude': -2.428,
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
