import json

import app as app_module


def test_sharepoint_webhook_validation_token_returns_plain_text_200():
    app_module.app.config['TESTING'] = True
    client = app_module.app.test_client()

    response = client.post('/api/occ-live-adjustments/sharepoint-webhook?validationToken=abc123')

    assert response.status_code == 200
    assert response.get_data(as_text=True) == 'abc123'
    assert response.content_type == 'text/plain; charset=utf-8'


def test_sharepoint_webhook_accepts_and_stores_notification_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, 'LIVE_ADJUSTMENTS_DIR', tmp_path)
    monkeypatch.setattr(app_module, 'LIVE_ADJUSTMENTS_WEBHOOK_STORE_PATH', tmp_path / 'sharepoint-webhook-events.json')
    app_module.app.config['TESTING'] = True
    client = app_module.app.test_client()

    response = client.post(
        '/api/occ-live-adjustments/sharepoint-webhook',
        json={'value': [{'subscriptionId': 'sub-1', 'resource': 'lists/list-id/items/1'}]},
    )

    assert response.status_code == 200
    assert response.get_json()['ok'] is True

    store = json.loads((tmp_path / 'sharepoint-webhook-events.json').read_text(encoding='utf-8'))
    assert store['receivedCount'] == 1
    assert store['events'][0]['notificationCount'] == 1
    assert store['events'][0]['payload']['value'][0]['subscriptionId'] == 'sub-1'


def test_occ_live_adjustments_page_has_standalone_permission():
    assert app_module.PERMISSIONS['live_adjustments'] == 'OCC Live Adjustments'
    assert app_module.PAGE_PERMISSIONS['occ_live_adjustments_page'] == 'live_adjustments'