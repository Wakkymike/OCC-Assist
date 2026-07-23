from datetime import datetime, timezone

import app as app_module


def test_invalidate_user_sessions_marks_sessions_inactive():
    app_module.init_db()
    database = app_module.get_db()
    database.execute('DELETE FROM user_sessions')
    database.execute(
        'INSERT INTO user_sessions (user_id, session_token, created_at, last_activity_at, active) VALUES (?, ?, ?, ?, 1)',
        (7, 'token-123', datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()),
    )
    database.commit()

    app_module.invalidate_user_sessions(7)

    row = database.execute('SELECT active FROM user_sessions WHERE session_token = ?', ('token-123',)).fetchone()
    assert row is not None
    assert row['active'] == 0
