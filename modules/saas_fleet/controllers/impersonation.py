# pylint: disable=R0201,C0103
import logging

from odoo import http
from odoo.http import request
from odoo.http.session import update_session_token
from odoo.tools import config

from odoo.addons.web.controllers.home import Home
from odoo.addons.web.controllers.utils import ensure_db

_logger = logging.getLogger(__name__)


class ImpersonateController(Home):
    @http.route("/impersonate", type="http", auth="none", csrf=False, methods=["GET", "POST"])
    def impersonate(self, redirect=None, **kw):
        if 'fleet_access_token' not in request.params:
            return request.make_json_response(
                {"status": "error", "message": "Missing fleet_access_token"}, status=400
            )
        if 'uid' not in request.params:
            return request.make_json_response(
                {"status": "error", "message": "Missing uid"}, status=400
            )

        ensure_db()
        request.params["login_success"] = False
        conf_access_fleet_token = config.get('fleet_access_token', None)
        param_access_fleet_token = request.params['fleet_access_token']

        if conf_access_fleet_token != param_access_fleet_token:
            return request.make_json_response(
                {"status": "error", "message": "Invalid fleet_access_token"}, status=401
            )

        uid = request.session.uid = int(request.params['uid'])

        # Same sequence as web's Home.switch_to_admin (Odoo 20): invalidate the
        # ormcache (res.users._compute_session_token is cached) now that the
        # session uid changed, then store the matching session token.
        request.env.transaction.invalidate_ormcache()
        update_session_token(request.session, request.env)

        request.params["login_success"] = True
        # bus's Home._login_redirect override (bus/controllers/home.py) reads
        # request.params['password'] to warn when the password is 'admin' and
        # raises a KeyError when it is absent, so provide a dummy value.
        request.params["password"] = 'x'
        _logger.info("Impersonation of uid %s requested from odoo fleet", uid)
        return request.redirect(super()._login_redirect(uid))
