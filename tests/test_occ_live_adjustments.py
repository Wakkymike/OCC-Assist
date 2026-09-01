import json

import app as app_module


class FakeSharePointResponse:
    status_code = 200

    def json(self):
        return {'value': [{'Title': 'Adjustment 1'}, {'Title': 'Adjustment 2'}]}


def test_sharepoint_webhook_validation_token_returns_plain_text_200():
    app_module.app.config['TESTING'] = True
    client = app_module.app.test_client()

    response = client.post('/api/occ-live-adjustments/sharepoint-webhook?validationtoken=abc123')

    assert response.status_code == 200
    assert response.get_data(as_text=True) == 'abc123'
    assert response.content_type == 'text/plain'


def test_sharepoint_webhook_body_query_text_validation_token_returns_plain_text_200():
    app_module.app.config['TESTING'] = True
    client = app_module.app.test_client()

    response = client.post(
        '/api/occ-live-adjustments/sharepoint-webhook',
        data='validationtoken=body-token-123',
        content_type='text/plain',
    )

    assert response.status_code == 200
    assert response.get_data(as_text=True) == 'body-token-123'
    assert response.content_type == 'text/plain'


def test_sharepoint_webhook_raw_body_without_validationtoken_returns_empty_200():
    app_module.app.config['TESTING'] = True
    client = app_module.app.test_client()

    response = client.post(
        '/api/occ-live-adjustments/sharepoint-webhook',
        data='raw-body-token-123',
        content_type='text/plain',
    )

    assert response.status_code == 200
    assert response.get_data(as_text=True) == ''


def test_live_adjustments_page_validtoken_bypasses_login_for_sharepoint_handshake():
    app_module.app.config['TESTING'] = True
    client = app_module.app.test_client()

    response = client.post('/occ-live-adjustments?validtoken=page-token-123')

    assert response.status_code == 200
    assert response.get_data(as_text=True) == 'page-token-123'
    assert response.content_type == 'text/plain'


def test_sharepoint_webhook_accepts_notification_payload_without_storing(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, 'LIVE_ADJUSTMENTS_WEBHOOK_STORE_PATH', tmp_path / 'sharepoint-webhook-events.json')
    app_module.app.config['TESTING'] = True
    client = app_module.app.test_client()

    response = client.post(
        '/api/occ-live-adjustments/sharepoint-webhook',
        json={'value': [{'subscriptionId': 'sub-1', 'resource': 'lists/list-id/items/1'}]},
    )

    assert response.status_code == 200
    assert response.get_data(as_text=True) == ''
    assert not (tmp_path / 'sharepoint-webhook-events.json').exists()


def test_live_adjustments_page_post_accepts_notification_without_login(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, 'LIVE_ADJUSTMENTS_DIR', tmp_path)
    monkeypatch.setattr(app_module, 'LIVE_ADJUSTMENTS_WEBHOOK_STORE_PATH', tmp_path / 'sharepoint-webhook-events.json')
    app_module.app.config['TESTING'] = True
    client = app_module.app.test_client()

    response = client.post(
        '/occ-live-adjustments',
        json={'value': [{'subscriptionId': 'sub-2', 'resource': 'lists/list-id/items/2'}]},
    )

    assert response.status_code == 200
    assert response.get_data(as_text=True) == ''

    store = json.loads((tmp_path / 'sharepoint-webhook-events.json').read_text(encoding='utf-8'))
    assert store['receivedCount'] == 1
    assert store['events'][0]['notificationCount'] == 1
    assert store['events'][0]['payload']['value'][0]['subscriptionId'] == 'sub-2'


def test_occ_live_adjustments_page_has_standalone_permission():
    assert app_module.PERMISSIONS['live_adjustments'] == 'OCC Live Adjustments'
    assert app_module.PAGE_PERMISSIONS['occ_live_adjustments_page'] == 'live_adjustments'


def test_fetch_sharepoint_changes_updates_latest_adjustments_cache(monkeypatch):
    calls = []

    def fake_get(url, headers, cookies, timeout):
        calls.append({'url': url, 'headers': headers, 'cookies': cookies, 'timeout': timeout})
        return FakeSharePointResponse()

    monkeypatch.setattr(app_module, 'LIST_API_URL', 'https://sharepoint.example/_api/web/lists/items')
    monkeypatch.setattr(app_module, 'SHAREPOINT_COOKIES', {'FedAuth': 'fed', 'rtFa': 'rt'})
    monkeypatch.setattr(app_module.requests, 'get', fake_get)
    monkeypatch.setattr(app_module, 'latest_adjustments_data', [])

    app_module.fetch_sharepoint_changes()

    assert calls == [
        {
            'url': 'https://sharepoint.example/_api/web/lists/items',
            'headers': {'Accept': 'application/json;odata=nometadata', 'Content-Type': 'application/json'},
            'cookies': {'FedAuth': 'fed', 'rtFa': 'rt'},
            'timeout': 10,
        }
    ]
    assert app_module.latest_adjustments_data == [{'Title': 'Adjustment 1'}, {'Title': 'Adjustment 2'}]