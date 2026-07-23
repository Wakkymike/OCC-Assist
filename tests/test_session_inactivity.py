from datetime import datetime, timedelta, timezone

import app as app_module


def test_session_expired_after_inactivity(monkeypatch):
    app_module.app.config['SESSION_INACTIVITY_SECONDS'] = 60

    with app_module.app.test_request_context('/') as request_context:
        request_context.session['user_id'] = 1
        request_context.session['last_activity'] = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()

        app_module.prepare_database()

        assert request_context.session.get('user_id') is None
        assert request_context.session.get('last_activity') is None
