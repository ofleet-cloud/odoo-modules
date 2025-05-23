
from odoo import _, http
from odoo.exceptions import AccessDenied
from odoo.http import request
from odoo.service import server
from odoo.tools import config

from odoo.addons.web.controllers.home import Home
from odoo.addons.web.controllers.utils import ensure_db

import logging
_logger = logging.getLogger(__name__)

class SoftRestartController(Home):
  @http.route("/restart", auth="none", type="http", csrf=False, methods=["POST"])
  def saas_restart(self):
    if 'fleet_access_token' not in request.params:
      return request.make_json_response(
          {"status": "error", "message": "Missing fleet_access_token"}, status=400
      )        
      
    ensure_db()
    conf_access_fleet_token = config.get('fleet_access_token', None)
    param_access_fleet_token = request.params['fleet_access_token']
    if conf_access_fleet_token != param_access_fleet_token:
      return request.make_json_response(
          {"status": "error", "message": "Invalid fleet_access_token"}, status=401
      )
    server.restart()
    _logger.warning("Server restart requested from odoo fleet")

    return request.make_json_response({"status": "ok"}, status=200)