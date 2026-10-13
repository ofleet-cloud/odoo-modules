import logging
import os
from unittest import mock

import psycopg2

from odoo.http import session as http_session
from odoo.sql_db import connection_info_for
from odoo.tests import TransactionCase
from odoo.tools import config

from odoo.addons.session_db.pg_session_store import PGSessionStore, SessionStoreSelector


def _make_postgres_uri(
    user=None, password=None, host=None, port=None, database=None, **kwargs
):
    uri = ["postgres://"]
    if user:
        uri.append(user)
        if password:
            uri.append(f":{password}")
        uri.append("@")
    if host:
        uri.append(host)
        if port:
            uri.append(f":{port}")
    uri.append("/")
    if database:
        uri.append(database)
    return "".join(uri)


class TestPGSessionStore(TransactionCase):
    def setUp(self):
        super().setUp()
        _, connection_info = connection_info_for(config["db_name"][0])
        self.uri = _make_postgres_uri(**connection_info)
        self.session_store = PGSessionStore(self.uri, session_cls=http_session.Session)

    def test_session_crud(self):
        session = self.session_store.new()
        session["test"] = "test"
        self.session_store.save(session)
        assert session.sid is not None
        assert self.session_store.get(session.sid)["test"] == "test"
        self.session_store.delete(session)
        assert self.session_store.get(session.sid).get("test") is None

    def test_get_invalid_sid(self):
        session = self.session_store.get("not a valid sid")
        self.assertTrue(session.is_new)
        self.assertTrue(self.session_store.is_valid_session_id(session.sid))

    def test_get_keep_sid(self):
        sid = self.session_store.generate_key()
        # unknown sid, keep_sid=False: brand new session
        session = self.session_store.get(sid)
        self.assertTrue(session.is_new)
        self.assertNotEqual(session.sid, sid)
        # unknown sid, keep_sid=True: empty session with the same sid
        session = self.session_store.get(sid, keep_sid=True)
        self.assertFalse(session.is_new)
        self.assertEqual(session.sid, sid)
        self.assertEqual(dict(session), {})

    def test_get_corrupted_payload(self):
        session = self.session_store.new()
        self.session_store.save(session)
        self.session_store._cr.execute(
            "UPDATE http_sessions SET payload='not json' WHERE sid=%s", (session.sid,)
        )
        loaded = self.session_store.get(session.sid)
        self.assertEqual(loaded.sid, session.sid)
        self.assertEqual(dict(loaded), {})
        self.session_store.delete(session)

    def test_rotate(self):
        session = self.session_store.new()
        session["test"] = "test"
        self.session_store.save(session)
        old_sid = session.sid
        # hard rotation: the old session is deleted and a new sid is generated
        self.session_store.rotate(session, self.env)
        self.assertNotEqual(session.sid, old_sid)
        self.assertEqual(self.session_store.get(session.sid)["test"], "test")
        self.assertNotEqual(self.session_store.get(old_sid).sid, old_sid)
        # soft rotation: the first 42 chars are kept, the old session points
        # to the new one
        old_sid = session.sid
        self.session_store.rotate(session, self.env, soft=True)
        self.assertNotEqual(session.sid, old_sid)
        self.assertEqual(session.sid[:42], old_sid[:42])
        self.assertEqual(self.session_store.get(old_sid)["next_sid"], session.sid)
        self.assertEqual(self.session_store.get(session.sid)["test"], "test")
        self.session_store.delete_from_identifiers([session.sid[:42]])

    def test_vacuum(self):
        session = self.session_store.new()
        self.session_store.save(session)
        self.session_store.vacuum()
        self.assertEqual(self.session_store.get(session.sid).sid, session.sid)
        self.session_store._cr.execute(
            "UPDATE http_sessions "
            "SET write_date = write_date - interval '1 day' WHERE sid=%s",
            (session.sid,),
        )
        self.session_store.vacuum(max_lifetime=3600)
        self.assertNotEqual(self.session_store.get(session.sid).sid, session.sid)

    def test_retry(self):
        """Test that session operations are retried before failing"""
        sid = self.session_store.generate_key()
        with (
            mock.patch("odoo.sql_db.Cursor.execute") as mock_execute,
            self.assertLogs(level=logging.WARNING) as logs,
        ):
            mock_execute.side_effect = psycopg2.OperationalError()
            try:
                self.session_store.get(sid)
            except psycopg2.OperationalError:  # pylint: disable=except-pass
                pass
            else:
                # We don't use self.assertRaises because Odoo is overriding
                # in a way that interferes with the Cursor.execute mock
                raise AssertionError("expected psycopg2.OperationalError")
            assert mock_execute.call_count == 5
            self.assertEqual(len(logs.records), 1)
            self.assertEqual(logs.records[0].levelno, logging.WARNING)
            self.assertIn("operation try 5/5 failed, aborting", logs.output[0])
        # when the error is resolved, it works again
        self.session_store.get(sid)

    def test_retry_connect_fail(self):
        sid = self.session_store.generate_key()
        with (
            mock.patch("odoo.sql_db.Cursor.execute") as mock_execute,
            mock.patch("odoo.sql_db.db_connect") as mock_db_connect,
        ):
            mock_execute.side_effect = psycopg2.OperationalError()
            mock_db_connect.side_effect = RuntimeError("connection failed")
            # get fails, and a RuntimeError is raised when trying to reconnect
            try:
                self.session_store.get(sid)
            except RuntimeError as e:  # pylint: disable=except-pass
                assert str(e) == "connection failed"
            else:
                # We don't use self.assertRaises because Odoo is overriding
                # in a way that interferes with the Cursor.execute mock
                raise AssertionError("expected RuntimeError")
            assert mock_execute.call_count == 1
        # when the error is resolved, it works again
        self.session_store.get(sid)

    def test_make_postgres_uri(self):
        connection_info = {
            "host": "localhost",
            "port": 5432,
            "database": "test",
            "user": "test",
            "password": "PASSWORD",
        }
        assert "postgres://test:PASSWORD@localhost:5432/test" == _make_postgres_uri(
            **connection_info
        )

    def test_get_missing_session_identifiers(self):
        session1 = self.session_store.new()
        session2 = self.session_store.new()
        self.session_store.save(session1)
        self.session_store.save(session2)
        missing = self.session_store.get_missing_session_identifiers(
            [session1.sid[:42], session2.sid[:42], "nonexistentsessionid"]
        )
        self.assertEqual(missing, {"nonexistentsessionid"})
        self.session_store.delete(session1)
        missing = self.session_store.get_missing_session_identifiers(
            [session1.sid[:42], session2.sid[:42], "nonexistentsessionid"]
        )
        self.assertEqual(missing, {session1.sid[:42], "nonexistentsessionid"})

    def test_delete_from_identifiers(self):
        session1 = self.session_store.new()
        session2 = self.session_store.new()
        self.session_store.save(session1)
        self.session_store.save(session2)
        self.session_store.delete_from_identifiers([session1.sid[:42], "f" * 42])
        self.assertNotEqual(self.session_store.get(session1.sid).sid, session1.sid)
        self.assertEqual(self.session_store.get(session2.sid).sid, session2.sid)

    def test_delete_from_identifiers_invalid_format(self):
        with self.assertRaises(ValueError):
            self.session_store.delete_from_identifiers("xxx")

    def test_delete_from_identifiers_ignores_invalid_identifier(self):
        session = self.session_store.new()
        self.session_store.save(session)
        # a partial identifier must never match
        self.session_store.delete_from_identifiers([session.sid[:10]])
        self.assertEqual(self.session_store.get(session.sid).sid, session.sid)
        self.session_store.delete(session)

    def test_session_store_factory(self):
        """odoo.http.session.session_store() builds the store through our selector"""
        self.assertIs(http_session.SessionStore, SessionStoreSelector)
        self.addCleanup(http_session.session_store.cache_clear)
        with mock.patch.dict(os.environ, {"SESSION_DB_URI": self.uri}):
            http_session.session_store.cache_clear()
            store = http_session.session_store()
            self.assertIsInstance(store, PGSessionStore)
            # cached
            self.assertIs(http_session.session_store(), store)
        with mock.patch.dict(os.environ, {"SESSION_DB_URI": ""}):
            http_session.session_store.cache_clear()
            store = http_session.session_store()
            self.assertNotIsInstance(store, PGSessionStore)
            self.assertEqual(type(store).__name__, "SessionStore")
