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


def test_roadworks_page_is_standalone_permission_defaulted_from_tracking():
    app_module.init_db()
    database = app_module.get_db()
    database.execute('DELETE FROM permissions')
    database.execute('DELETE FROM users')
    cursor = database.execute(
        'INSERT INTO users (email, password_hash, is_superadmin) VALUES (?, ?, 0)',
        ('roadworks-permission@example.com', 'hash'),
    )
    user_id = cursor.lastrowid
    database.execute(
        'INSERT INTO permissions (user_id, permission_key, enabled) VALUES (?, ?, 1)',
        (user_id, 'tracking'),
    )
    database.commit()

    app_module.sync_user_permissions_schema(database)

    permissions = app_module.fetch_user_permissions(user_id)
    assert app_module.PERMISSIONS['roadworks_page'] == 'Roadworks'
    assert app_module.PAGE_PERMISSIONS['roadworks_page'] == 'roadworks_page'
    assert permissions['tracking'] is True
    assert permissions['roadworks_page'] is True
