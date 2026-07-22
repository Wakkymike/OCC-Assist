import os
import tempfile
import unittest

os.environ['OCC_ASSIST_SUPERADMIN_PASSWORD'] = 'password'
os.environ['OCC_ASSIST_DB_PATH'] = os.path.join(tempfile.gettempdir(), 'occ_assist_test.db')

from app import app, init_db, get_db


class UserDeletionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = os.environ.get('OCC_ASSIST_DB_PATH')
        os.environ['OCC_ASSIST_DB_PATH'] = os.path.join(self.temp_dir.name, 'test.db')
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test-secret'
        self.client = app.test_client()
        with app.app_context():
            init_db()

    def tearDown(self):
        if self.original_db is None:
            os.environ.pop('OCC_ASSIST_DB_PATH', None)
        else:
            os.environ['OCC_ASSIST_DB_PATH'] = self.original_db
        self.temp_dir.cleanup()

    def test_admin_can_delete_user_and_related_data(self):
        with app.app_context():
            database = get_db()
            database.execute(
                'INSERT INTO users (email, password_hash, is_superadmin) VALUES (?, ?, 0)',
                ('delete-target@example.com', 'hash'),
            )
            user_id = database.execute('SELECT id FROM users WHERE email = ?', ('delete-target@example.com',)).fetchone()['id']
            database.execute('INSERT INTO permissions (user_id, permission_key, enabled) VALUES (?, ?, 1)', (user_id, 'tracking', 1))
            database.execute(
                'INSERT INTO driving_snapshots (user_id, driver_name, employee_number, segment_summary, status, breaches_json, total_driving_minutes, total_break_minutes, spreadover_minutes, current_continuous_driving_minutes, non_driving_first_window_minutes, created_at_epoch) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (user_id, 'Test Driver', '123', 'segment', 'ok', '[]', 0, 0, 0, 0, 0, 1),
            )
            database.execute('INSERT INTO user_settings (user_id, rotacloud_ical_url) VALUES (?, ?)', (user_id, 'https://example.com'))
            database.commit()

            self.client.post('/api/login', json={'email': 'michael.dodsworth@gonorthwest.co.uk', 'password': 'password'})
            response = self.client.delete(f'/api/users/{user_id}')

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertTrue(payload['ok'])
            self.assertIsNone(database.execute('SELECT id FROM users WHERE id = ?', (user_id,)).fetchone())
            self.assertIsNone(database.execute('SELECT id FROM permissions WHERE user_id = ?', (user_id,)).fetchone())
            self.assertIsNone(database.execute('SELECT id FROM driving_snapshots WHERE user_id = ?', (user_id,)).fetchone())
            self.assertIsNone(database.execute('SELECT user_id FROM user_settings WHERE user_id = ?', (user_id,)).fetchone())


if __name__ == '__main__':
    unittest.main()
