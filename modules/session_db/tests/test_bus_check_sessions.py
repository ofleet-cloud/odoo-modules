import sys
from unittest import mock

from odoo.http import session as http_session
from odoo.sql_db import connection_info_for
from odoo.tests import TransactionCase, new_test_user
from odoo.tools import config

from odoo.addons.session_db import bus_session_helpers
from odoo.addons.session_db.pg_session_store import PGSessionStore

from .test_pg_session_store import _make_postgres_uri


class TestBusCheckSessions(TransactionCase):
    """The bus websocket session check must work with sessions stored in PostgreSQL."""

    def setUp(self):
        super().setUp()
        _, connection_info = connection_info_for(config["db_name"][0])
        self.uri = _make_postgres_uri(**connection_info)
        self.session_store = PGSessionStore(self.uri, session_cls=http_session.Session)
        patcher = mock.patch.object(
            bus_session_helpers, "session_store", return_value=self.session_store
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.helpers = sys.modules[bus_session_helpers.BUS_SESSION_HELPERS]
        self.helpers._stat_by_sid.clear()
        self.user = new_test_user(self.env, "bob", groups="base.group_user")

    def _logged_session(self, user=None):
        user = user or self.user
        session = self.session_store.new()
        session.uid = user.id
        session.session_token = user._compute_session_token(session.sid)
        self.session_store.save(session)
        self.addCleanup(self.session_store.delete, session)
        return session

    def test_bus_is_patched(self):
        """Both the helpers module and the dispatcher must use our implementation."""
        from odoo.addons.bus import bus_dispatcher, session_helpers

        self.assertIs(session_helpers.check_sessions, bus_session_helpers.check_sessions)
        self.assertIs(bus_dispatcher.check_sessions, bus_session_helpers.check_sessions)
        self.assertNotIn(bus_session_helpers._finder, sys.meta_path)

    def test_get_session_stamp(self):
        session = self.session_store.new()
        self.assertIsNone(self.session_store.get_session_stamp(session.sid))
        self.assertIsNone(self.session_store.get_session_stamp("not a valid sid"))
        self.session_store.save(session)
        stamp = self.session_store.get_session_stamp(session.sid)
        self.assertIsNotNone(stamp)
        self.session_store._cr.execute(
            "UPDATE http_sessions SET write_date = write_date - interval '1 second' "
            "WHERE sid=%s",
            (session.sid,),
        )
        self.assertNotEqual(self.session_store.get_session_stamp(session.sid), stamp)
        self.session_store.delete(session)
        self.assertIsNone(self.session_store.get_session_stamp(session.sid))

    def test_valid_session_resolved(self):
        session = self._logged_session()
        resolved = bus_session_helpers.check_sessions(self.env.cr, [session])
        self.assertIn(session.sid, resolved)
        self.assertEqual(resolved[session.sid].uid, self.user.id)

    def test_anonymous_session_resolved(self):
        session = self.session_store.new()
        self.assertIn(
            session.sid, bus_session_helpers.check_sessions(self.env.cr, [session])
        )

    def test_deleted_session_dropped(self):
        session = self._logged_session()
        self.session_store.delete(session)
        self.assertNotIn(
            session.sid, bus_session_helpers.check_sessions(self.env.cr, [session])
        )

    def test_token_change_dropped(self):
        session = self._logged_session()
        self.assertIn(
            session.sid, bus_session_helpers.check_sessions(self.env.cr, [session])
        )
        self.user.password = "bob_new_password"
        self.assertNotIn(
            session.sid, bus_session_helpers.check_sessions(self.env.cr, [session])
        )

    def test_changed_session_reloaded(self):
        """A session rewritten in the store is reloaded, an untouched one is reused."""
        session = self._logged_session()
        # first check: no stamp cached yet, the session is read from the store
        resolved = bus_session_helpers.check_sessions(self.env.cr, [session])
        self.assertIsNot(resolved[session.sid], session)
        self.assertEqual(resolved[session.sid].sid, session.sid)
        loaded = resolved[session.sid]
        # unchanged in the store: the in-memory session is kept as is
        resolved = bus_session_helpers.check_sessions(self.env.cr, [loaded])
        self.assertIs(resolved[session.sid], loaded)
        # rewritten in the store with a different write_date: reloaded
        session["marker"] = "changed"
        self.session_store.save(session)
        self.session_store._cr.execute(
            "UPDATE http_sessions SET write_date = write_date + interval '1 second' "
            "WHERE sid=%s",
            (session.sid,),
        )
        stale = self.session_store.session_cls(dict(session, marker="stale"), session.sid)
        resolved = bus_session_helpers.check_sessions(self.env.cr, [stale])
        self.assertEqual(resolved[session.sid]["marker"], "changed")

    def test_rotated_session_followed(self):
        session = self._logged_session()
        old_sid = session.sid
        self.session_store.rotate(session, self.env, soft=True)
        session.session_token = self.user._compute_session_token(session.sid)
        self.session_store.save(session)
        self.addCleanup(self.session_store.delete_from_identifiers, [session.sid[:42]])
        stale = self.session_store.get(old_sid)
        resolved = bus_session_helpers.check_sessions(self.env.cr, [stale])
        self.assertIn(old_sid, resolved)
        self.assertEqual(resolved[old_sid].sid, session.sid)

    def test_no_session_file_access(self):
        """The database store must never be asked for a session path."""
        session = self._logged_session()
        with mock.patch.object(
            PGSessionStore, "get_session_path", side_effect=AssertionError("path used")
        ):
            self.assertIn(
                session.sid, bus_session_helpers.check_sessions(self.env.cr, [session])
            )
