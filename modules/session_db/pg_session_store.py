# Copyright (c) Odoo SA 2017
# @author Nicolas Seinlet
# Copyright (c) ACSONE SA 2022
# @author Stéphane Bidoul
# Ported to Odoo 20 by Log'in Line
"""PostgreSQL backed HTTP session store for Odoo 20.

Odoo 20 replaced the werkzeug-derived ``odoo.tools._vendor.sessions`` with a
standalone :class:`odoo.http.session.SessionStore` (filesystem based) and the
store instance is obtained everywhere through the module level function
:func:`odoo.http.session.session_store`, which is ``functools.cache`` d.

That function is imported *by name* in several places (``odoo.http.router``,
``odoo.http.dispatcher``, ``odoo.addons.base.models.ir_http``,
``odoo.addons.base.models.res_device``, ``odoo.addons.web.controllers.utils``
...). Rebinding ``odoo.http.session.session_store`` would therefore only patch
the callers importing it after this module is loaded, and any site missed
would silently keep a filesystem store next to the database one.

Instead, we leave ``session_store()`` untouched and patch the *name* it
instantiates: ``odoo.http.session.SessionStore``. The cached function resolves
that name in its module globals at call time, so every caller, present or
future, receives the same store. We then clear the ``functools`` cache so a
store created before this module was imported (for instance by a test run)
is rebuilt through the replacement.
"""
import functools
import json
import logging
import os

import psycopg2

import odoo
from odoo.http import session as http_session
from odoo.http.session import (
    SESSION_LIFETIME,
    Session,
    SessionStore,
    _session_identifier_re,
)

_logger = logging.getLogger(__name__)

lock = None
if getattr(odoo, "evented", False):
    import gevent.lock

    lock = gevent.lock.RLock()
elif odoo.tools.config["workers"] == 0:
    import threading

    lock = threading.RLock()


def with_lock(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            if lock is not None:
                lock.acquire()
            return func(*args, **kwargs)
        finally:
            if lock is not None:
                lock.release()

    return wrapper


def with_cursor(func):
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        tries = 0
        while True:
            tries += 1
            try:
                self._ensure_connection()
                return func(self, *args, **kwargs)
            except (psycopg2.InterfaceError, psycopg2.OperationalError):
                self._close_connection()
                if tries > 4:
                    _logger.warning(
                        "session_db operation try %s/5 failed, aborting", tries
                    )
                    raise
                _logger.info("session_db operation try %s/5 failed, retrying", tries)

    return wrapper


class PGSessionStore(SessionStore):
    """Session store keeping the sessions in a PostgreSQL ``http_sessions`` table.

    Only the storage primitives (``save``, ``delete``, ``get``, ``vacuum``,
    ``get_missing_session_identifiers`` and ``delete_from_identifiers``) are
    reimplemented. ``generate_key``, ``is_valid_session_id``, ``new``,
    ``rotate`` and ``delete_old_sessions`` are storage agnostic and inherited
    from Odoo's :class:`~odoo.http.session.SessionStore`.
    """

    def __init__(self, uri, session_cls=Session):
        # Do not call SessionStore.__init__: it would create the filesystem
        # session directory, which this store never uses.
        self.path = None
        self.session_cls = session_cls
        self._uri = uri
        self._cr = None
        self._open_connection()
        self._setup_db()

    def __del__(self):
        self._close_connection()

    @with_lock
    def _ensure_connection(self):
        if self._cr is None:
            self._open_connection()

    @with_lock
    def _open_connection(self):
        self._close_connection()
        cnx = odoo.sql_db.db_connect(self._uri, allow_uri=True)
        self._cr = cnx.cursor()
        self._cr._cnx.autocommit = True

    @with_lock
    def _close_connection(self):
        """Return cursor to the pool."""
        if self._cr is not None:
            try:
                self._cr.close()
            except Exception:  # pylint: disable=except-pass
                pass
            self._cr = None

    @with_lock
    @with_cursor
    def _setup_db(self):
        self._cr.execute(
            """
                CREATE TABLE IF NOT EXISTS http_sessions (
                    sid varchar PRIMARY KEY,
                    write_date timestamp without time zone NOT NULL,
                    payload text NOT NULL
                )
            """
        )

    @with_lock
    @with_cursor
    def save(self, session):
        payload = json.dumps(dict(session))
        self._cr.execute(
            """
                INSERT INTO http_sessions(sid, write_date, payload)
                    VALUES (%(sid)s, now() at time zone 'UTC', %(payload)s)
                ON CONFLICT (sid)
                DO UPDATE SET payload = %(payload)s,
                              write_date = now() at time zone 'UTC'
            """,
            dict(sid=session.sid, payload=payload),
        )

    @with_lock
    @with_cursor
    def delete(self, session):
        self._cr.execute("DELETE FROM http_sessions WHERE sid=%s", (session.sid,))

    @with_lock
    @with_cursor
    def get(self, sid, *, keep_sid=False):
        """Get a session for the sid.

        Same contract as :meth:`odoo.http.session.SessionStore.get`:

        * an invalid sid yields a brand new session (new sid);
        * a valid sid without a row yields an empty session keeping the sid
          when ``keep_sid`` is set, a brand new session otherwise;
        * an unreadable payload yields an empty session keeping the sid.
        """
        if not self.is_valid_session_id(sid):
            return self.new()

        self._cr.execute("SELECT payload FROM http_sessions WHERE sid=%s", (sid,))
        row = self._cr.fetchone()
        if row is None:
            if keep_sid:
                _logger.debug("Session %s not found in db. Use empty session.", sid[:8])
                return self.session_cls({}, sid, new=False)
            _logger.debug("Session %s not found in db. Use new session.", sid[:8])
            return self.new()

        try:
            data = json.loads(row[0])
        except ValueError:
            _logger.debug("Could not load session data. Use empty session.", exc_info=True)
            return self.session_cls({}, sid, new=False)

        return self.session_cls(data, sid, new=False)

    @with_lock
    @with_cursor
    def get_session_stamp(self, sid):
        """Change marker of the stored session, ``None`` when it does not exist.

        Counterpart of the ``(mtime, inode)`` of a session file: ``save`` always
        refreshes ``write_date``, so a different value means the payload may have
        changed. Used by the bus websocket session check, see
        :mod:`odoo.addons.session_db.bus_session_helpers`.
        """
        if not self.is_valid_session_id(sid):
            return None
        self._cr.execute("SELECT write_date FROM http_sessions WHERE sid=%s", (sid,))
        row = self._cr.fetchone()
        return None if row is None else row[0]

    @with_lock
    @with_cursor
    def vacuum(self, max_lifetime=SESSION_LIFETIME):
        self._cr.execute(
            "DELETE FROM http_sessions "
            "WHERE now() at time zone 'UTC' - write_date > %s",
            (f"{max_lifetime} seconds",),
        )

    @with_lock
    @with_cursor
    def get_missing_session_identifiers(self, identifiers):
        """
        :param identifiers: session identifiers whose existence must be checked
                            identifiers are a part session sid (first 42 chars)
        :type identifiers: iterable
        :return: the identifiers which are not present in the database
        :rtype: set

        Note 1:
        Working with identifiers 42 characters long means that
        we don't have to work with the entire sid session,
        while maintaining sufficient entropy to avoid collisions.
        See details in ``generate_key``.

        Note 2:
        Scans the session store for inactive (GC'd) sessions.
        Performance is acceptable for an infrequent background job.
        """
        missing_identifiers = set()
        for identifier in identifiers:
            self._cr.execute(
                "SELECT sid FROM http_sessions WHERE sid LIKE %s||'%%' LIMIT 1",
                (identifier,),
            )
            if self._cr.rowcount == 0:
                missing_identifiers.add(identifier)
        return missing_identifiers

    @with_lock
    @with_cursor
    def delete_from_identifiers(self, identifiers):
        if isinstance(identifiers, str):
            raise ValueError(
                "Identifier format incorrect, "
                "did you pass in a string instead of a list?"
            )
        for identifier in identifiers:
            # Same guard as the filesystem store: never delete sessions that do
            # not match a full identifier, so that a forged ``res.device.log``
            # cannot be used to wipe sessions of another database.
            if not _session_identifier_re.match(identifier):
                _logger.warning("Ignoring invalid session identifier %r", identifier)
                continue
            self._cr.execute(
                "DELETE FROM http_sessions WHERE sid LIKE %s||'%%'", (identifier,)
            )


class SessionStoreSelector(SessionStore):
    """Stand-in for ``odoo.http.session.SessionStore``.

    Instantiating it returns a :class:`PGSessionStore` when the
    ``SESSION_DB_URI`` environment variable is set, and a regular filesystem
    :class:`odoo.http.session.SessionStore` otherwise. Subclassing it still
    behaves like subclassing the original class.
    """

    def __new__(cls, /, path=None, session_cls=Session):
        if cls is not SessionStoreSelector:
            return super().__new__(cls)
        session_db_uri = os.environ.get("SESSION_DB_URI")
        if session_db_uri:
            _logger.info("HTTP sessions stored in: db")
            return PGSessionStore(session_db_uri, session_cls=session_cls)
        return SessionStore(path=path, session_cls=session_cls)


# Monkey patch the class instantiated by odoo.http.session.session_store()
_logger.debug("Monkey patching session store")
http_session.SessionStore = SessionStoreSelector
# Drop any store built before this module was imported.
http_session.session_store.cache_clear()

# The bus websocket session check reads session files directly: give it a
# storage agnostic implementation (see bus_session_helpers).
from .bus_session_helpers import patch_bus_check_sessions  # noqa: E402

patch_bus_check_sessions()
