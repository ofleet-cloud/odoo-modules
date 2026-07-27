from odoo import http
from odoo.addons.web.controllers.home import Home
from odoo.http import request


class SoftRestartController(Home):
    @http.route("/restart", auth="none", type="http", csrf=False, methods=["POST"])
    def saas_restart(self):
        if "fleet_access_token" not in request.params:
            return request.make_json_response(
                {"status": "error", "message": "Missing fleet_access_token"}, status=400
            )

        return request.make_json_response({"status": "ok"}, status=200)
