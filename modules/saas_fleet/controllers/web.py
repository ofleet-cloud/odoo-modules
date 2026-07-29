import json
import logging
import os

import odoo
import psycopg2
from odoo import http
from odoo.addons.web.controllers.home import Home
from odoo.addons.web.controllers.utils import ensure_db
from odoo.http import request
from odoo.tools import config


class HealthCheckFilter(logging.Filter):
    """Prevents health check requests from cluttering the Werkzeug logs."""

    def __init__(self, path, name=""):
        super().__init__(name)
        self.path = path

    def filter(self, record):
        return self.path not in record.getMessage()


logging.getLogger("werkzeug").addFilter(HealthCheckFilter("GET /web/health"))


class HealthController(Home):
    @http.route("/web/health", type="http", auth="none", save_session=False)
    def health(self, db_server_status=False, filestore=False):
        # Safety net: throws an explicit exception if no database is
        # selectable (incorrect db_filter, database missing, broken routing)
        ensure_db()

        health_info = {"status": "pass"}
        status = 200

        if db_server_status:
            try:
                odoo.sql_db.db_connect("postgres").cursor().close()
                health_info["db_server_status"] = True
            except psycopg2.Error:
                health_info["db_server_status"] = False
                health_info["status"] = "fail"
                status = 500

        if filestore:
            try:
                fs_ok = self._check_filestore()
                health_info["filestore"] = fs_ok
                if not fs_ok:
                    raise Exception("filestore not accessible")
            except Exception:
                health_info["filestore"] = False
                health_info["status"] = "fail"
                status = 500

        data = json.dumps(health_info)
        headers = [("Content-Type", "application/json"), ("Cache-Control", "no-store")]
        return request.make_response(data, headers, status=status)

    def _check_filestore(self):
        """
        Check local FS only. S3 checks have been deliberately omitted:
        the custom S3 storage module is being phased out,
        instances still using it must not enable `filestore=1`.
        """

        data_dir = config.filestore(request.db) if request.db else None
        return bool(
            data_dir
            and os.path.isdir(data_dir)
            and os.access(data_dir, os.R_OK | os.W_OK)
        )
