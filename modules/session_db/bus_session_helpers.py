# Copyright (c) Log'in Line 2026
"""Make the websocket session check of the ``bus`` addon work with a database store.

Since the 20.0 build of 2026-10-15, ``odoo.addons.bus.session_helpers.check_sessions``
(called by the bus dispatcher for every websocket, in the gevent process) does not
go through ``store.get()`` to find out whether a session changed: it opens the session
*file* returned by ``store.get_session_path(sid)`` and keeps its ``(mtime, inode)`` as a
change marker. With a :class:`~odoo.addons.session_db.pg_session_store.PGSessionStore`
there is no file (``store.path`` is ``None``), ``os.path.join`` raises ``TypeError``,
which ``check_sessions`` does not catch, and the whole dispatcher loop crashes.

This module ships a storage agnostic copy of ``check_sessions``: the change marker is
asked to the store through ``get_session_stamp(sid)`` when the store provides it, and
computed from the session file otherwise (plain filesystem store). Everything else is
identical to upstream.

``bus_dispatcher`` binds ``check_sessions`` by name at import time, so the replacement
must be in place before ``odoo.addons.bus.session_helpers`` finishes importing. When
the module is already imported (``bus`` in ``server_wide_modules``) it is patched on
the spot, together with ``bus_dispatcher``; otherwise a one-shot ``sys.meta_path``
finder patches it right after its first execution, before ``bus_dispatcher`` runs
its import line.

The copy follows upstream build ``20.0.20261015``. ``UPSTREAM_CHECK_SESSIONS_SHA1`` is
the SHA-1 of the upstream function source: a WARNING is logged when it differs, as a
reminder to re-synchronise this file.
"""
import hashlib
import hmac
import importlib.abc
import importlib.util
import inspect
import logging
import os
import sys
import time

from odoo.http.session import session_store
from odoo.sql_db import SQL
from odoo.tools.misc import consteq

_logger = logging.getLogger(__name__)

BUS_SESSION_HELPERS = "odoo.addons.bus.session_helpers"
BUS_DISPATCHER = "odoo.addons.bus.bus_dispatcher"
UPSTREAM_CHECK_SESSIONS_SHA1 = "d20399507717916cb55c51b9d2969fa31b4d5d3a"


def _session_stamp(store, sid):
    """Change marker of the stored session ``sid``, ``None`` when it does not exist.

    Database stores expose it through ``get_session_stamp``; for the filesystem store
    this is upstream's ``(mtime, inode)`` of the session file.
    """
    get_session_stamp = getattr(store, "get_session_stamp", None)
    if get_session_stamp is not None:
        return get_session_stamp(sid)
    try:
        # Open instead of stat (close to open consistency): NFS clients cache file
        # attributes for seconds (acregmin/acregmax) which could hide a delete from
        # another node sharing `session_dir`. Opening forces a revalidation.
        with open(store.get_session_path(sid), "rb", buffering=0) as f:
            st = os.fstat(f.fileno())
            return (st.st_mtime_ns, st.st_ino)
    except (OSError, ValueError):
        return None


def check_sessions(cr, sessions):
    """Counterpart of ``odoo.http.session.check`` that must not build a registry.

    Verify that the sessions are not expired and that the token saved in each one
    still matches the session token computed for its user.

    :param sessions: the sessions to check.
    :returns: valid sessions, keyed by the ``sid`` that was given. That key differs from
        the checked session's ``sid`` when a rotation happened.
    """
    helpers = sys.modules[BUS_SESSION_HELPERS]
    store = session_store()
    now = time.time()
    resolved_by_sid = {}
    to_check_by_sid = {}
    for stored_session in sessions:
        if stored_session.sid in resolved_by_sid or stored_session.sid in to_check_by_sid:
            continue
        if stored_session.uid is None:
            resolved_by_sid[stored_session.sid] = stored_session  # No user, no token to match.
            continue
        session_stat = _session_stamp(store, stored_session.sid)
        if session_stat is None:
            continue  # Session wasn't found in the store (= outdated).
        if helpers._stat_by_sid.get(stored_session.sid) == session_stat:
            stored = stored_session  # Unchanged since last check.
        else:
            stored = store.get(stored_session.sid)
            if stored.is_new:
                continue  # Session wasn't found in the store (= outdated).
            helpers._stat_by_sid[stored_session.sid] = session_stat
        if "next_sid" in stored:
            stored = store.get(stored["next_sid"])
            if stored.is_new:
                continue
        store.delete_old_sessions(stored)
        if "deletion_time" in stored and stored["deletion_time"] <= now:
            continue
        to_check_by_sid[stored_session.sid] = stored
    if not to_check_by_sid:
        return resolved_by_sid
    uids = {session.uid for session in to_check_by_sid.values()}
    query_params = helpers._get_session_token_query_params(cr, uids)
    cr.execute(
        SQL(
            "SELECT %(select)s FROM %(from)s %(joins)s WHERE %(where)s GROUP BY %(group_by)s",
            **query_params,
        ),
    )
    id_idx = next(i for i, column in enumerate(cr.description) if column.name == "id")
    keys_by_uid = {}
    # Mirror `_session_token_get_values` then `_session_token_hash_compute`.
    for row in cr.fetchall():
        field_values = tuple(
            (column.name, row[index]) for index, column in enumerate(cr.description)
        )
        key_tuple = tuple((k, v) for k, v in field_values if v is not None)
        keys_by_uid[row[id_idx]] = str(key_tuple).encode()

    for given_sid, stored_session in to_check_by_sid.items():
        key = keys_by_uid.get(stored_session.uid)
        if not key:
            continue
        token = hmac.new(key, stored_session.sid.encode(), hashlib.sha256).hexdigest()
        if consteq(token, stored_session.session_token):
            resolved_by_sid[given_sid] = stored_session
    return resolved_by_sid


def _patch_session_helpers(module):
    """Replace ``check_sessions`` in the imported ``bus.session_helpers`` module."""
    original = getattr(module, "check_sessions", None)
    if original is check_sessions:
        return
    if original is None:
        _logger.warning(
            "%s has no check_sessions function, nothing to patch", module.__name__
        )
        return
    try:
        source_sha1 = hashlib.sha1(inspect.getsource(original).encode()).hexdigest()
    except (OSError, TypeError):
        source_sha1 = None
    if source_sha1 != UPSTREAM_CHECK_SESSIONS_SHA1:
        _logger.warning(
            "%s.check_sessions differs from the version session_db was written "
            "against (sha1 %s, expected %s): please re-synchronise "
            "session_db/bus_session_helpers.py",
            module.__name__,
            source_sha1,
            UPSTREAM_CHECK_SESSIONS_SHA1,
        )
    module.check_sessions = check_sessions
    dispatcher = sys.modules.get(BUS_DISPATCHER)
    if dispatcher is not None and getattr(dispatcher, "check_sessions", None) is original:
        dispatcher.check_sessions = check_sessions
    _logger.debug("Patched %s.check_sessions for the session_db store", module.__name__)


class _PatchingLoader(importlib.abc.Loader):
    """Loader wrapper running :func:`_patch_session_helpers` after the module body."""

    def __init__(self, loader):
        self._loader = loader

    def __getattr__(self, name):
        return getattr(self._loader, name)

    def create_module(self, spec):
        return self._loader.create_module(spec)

    def exec_module(self, module):
        self._loader.exec_module(module)
        _patch_session_helpers(module)


class _BusSessionHelpersFinder(importlib.abc.MetaPathFinder):
    """One-shot finder patching ``bus.session_helpers`` on its first import."""

    def find_spec(self, fullname, path, target=None):
        if fullname != BUS_SESSION_HELPERS:
            return None
        self.uninstall()
        spec = None
        for finder in sys.meta_path:
            find_spec = getattr(finder, "find_spec", None)
            if find_spec is None:
                continue
            spec = find_spec(fullname, path, target)
            if spec is not None:
                break
        if spec is None or spec.loader is None:
            return spec
        spec.loader = _PatchingLoader(spec.loader)
        return spec

    def install(self):
        if self not in sys.meta_path:
            sys.meta_path.insert(0, self)

    def uninstall(self):
        if self in sys.meta_path:
            sys.meta_path.remove(self)


_finder = _BusSessionHelpersFinder()


def patch_bus_check_sessions():
    """Install the storage agnostic ``check_sessions`` in the ``bus`` addon."""
    module = sys.modules.get(BUS_SESSION_HELPERS)
    if module is not None:
        _patch_session_helpers(module)
        return
    _finder.install()
