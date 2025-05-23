from odoo import _, http
from odoo.http import request, Response
from odoo.service import security
from odoo.tools import config

from odoo.addons.web.controllers.main import ensure_db, Home

class ImpersonateController(Home):
    @http.route("/impersonate", type="http", auth="none", csrf=False, methods=["GET", "POST"])
    def impersonate(self, redirect=None, **kw):
        if 'fleet_access_token' not in request.params:
            return Response("Missing param fleet_access_token", status=400)
        if 'uid' not in request.params:
            return Response("Missing param uid", status=400) 
        
        ensure_db()
        request.params["login_success"] = False
        conf_access_fleet_token = config.get('fleet_access_token', None)
        param_access_fleet_token = request.params['fleet_access_token']
        
        if conf_access_fleet_token != param_access_fleet_token:
            return Response("Wrong token", status=401)
            
        uid = request.session.uid = int(request.params['uid'])

        request.env.registry.clear_caches()
        request.session.session_token = security.compute_session_token(
            request.session, request.env
        )

        request.params["login_success"] = True
        # Only usefull because Odoo verifies if the password is 'admin' to warn the user. 
        # It throws if no password is provided.
        request.params["password"] = 'x' 
        return request.redirect(super()._login_redirect(uid))
