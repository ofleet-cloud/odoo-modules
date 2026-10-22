# Ofleet Odoo Modules

This repository contains the necessary additional odoo modules needed in docker images designed to be deployed on ofleet for every odoo version.

## Odoo 20 branch (`v20`)

This branch targets **Odoo 20.0** (Python 3.12+). It is not compatible with
Odoo 19 or earlier: Odoo 20 turned `odoo.http` into a package and replaced the
session store API (`odoo.http.session.SessionStore`, `session_store()`,
`update_session_token()`), and `odoo.service.security` no longer exists. Use the
`v19` branch for Odoo 19.

| Module         | Status on Odoo 20                                                  |
| -------------- | ------------------------------------------------------------------ |
| `session_db`   | Ported, version `20.0.1.0.0`, tests pass                           |
| `saas_fleet`   | Ported, version `20.0.1.0.0`                                       |
| `filestore_s3` | **Dropped**, marked not installable (Odoo 19 is its last version) |

### session_db

Server-wide module (`--load=base,web,session_db`) storing HTTP sessions in a
PostgreSQL table `http_sessions` of the database given by the `SESSION_DB_URI`
environment variable (for example `postgres://user:pwd@host:5432/sessions`).
When the variable is unset, Odoo's filesystem store is used.

`PGSessionStore` subclasses `odoo.http.session.SessionStore` and only
reimplements the storage primitives (`save`, `delete`, `get`, `vacuum`,
`get_missing_session_identifiers`, `delete_from_identifiers`); key generation,
rotation and old-session cleanup are inherited.

Odoo 20 obtains the store through the `functools.cache`d function
`odoo.http.session.session_store()`, which is imported *by name* in
`odoo.http.router`, `odoo.http.dispatcher`, `odoo.addons.base.models.ir_http`,
`odoo.addons.base.models.res_device` and `odoo.addons.web.controllers.utils`.
Rebinding the function would only patch the callers importing it after the
module is loaded, so the module instead replaces the class that function
instantiates (`odoo.http.session.SessionStore`) with a selector returning a
`PGSessionStore` when `SESSION_DB_URI` is set, and clears the function's cache.
Every caller, including modules loaded later, therefore shares the same store.

Since build `20.0.20261015`, the bus dispatcher (websocket / longpolling process)
validates every websocket session through
`odoo.addons.bus.session_helpers.check_sessions`, which opens the session *file*
returned by `store.get_session_path()` instead of going through `store.get()`.
With a database store this raised an uncaught `TypeError` and crashed the
dispatcher loop. `session_db/bus_session_helpers.py` replaces that function
with a storage agnostic copy (the change marker comes from
`PGSessionStore.get_session_stamp()`, the `write_date` of the row) and installs
it on `bus.session_helpers` and `bus.bus_dispatcher`, through a one-shot import
hook when `bus` is imported after `session_db`. The copy is pinned to the
upstream build by the SHA-1 of the upstream function: a WARNING at startup
means upstream changed and the copy must be re-synchronised.

### saas_fleet

* `/impersonate?fleet_access_token=...&uid=...` logs the session in as `uid`
  using `odoo.http.session.update_session_token()` after invalidating the
  ormcache (the same sequence as Odoo's `/web/become`).
* `/restart` (POST, `fleet_access_token`) calls `odoo.service.server.restart()`.
* The ribbon shows the `odoo_tag` config option on every page.

### Testing on Odoo 20 with Docker

```bash
docker network create ofleet-test
docker run -d --name ofleet-pg --network ofleet-test -e POSTGRES_USER=odoo -e POSTGRES_PASSWORD=odoo -e POSTGRES_DB=sessions postgres:16
docker run --rm --network ofleet-test -e HOST=ofleet-pg -e USER=odoo -e PASSWORD=odoo   -e SESSION_DB_URI=postgres://odoo:odoo@ofleet-pg:5432/sessions   -v "$PWD/modules:/mnt/extra-addons:ro" odoo:20.0   odoo -d sdb_test -i session_db,saas_fleet --load=base,web,session_db   --test-tags /session_db,/saas_fleet --stop-after-init --without-demo=True
```
