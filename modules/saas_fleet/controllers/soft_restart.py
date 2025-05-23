
from odoo import _, http
from odoo.http import request, Response
from odoo.service import server
from odoo.tools import config

from odoo.addons.web.controllers.main import ensure_db, Home

import logging
_logger = logging.getLogger(__name__)

class SoftRestartController(Home):
  @http.route("/restart", auth="none", type="http", csrf=False, methods=["POST"])
  def saas_restart(self, **kw):
    if 'fleet_access_token' not in request.params:
      return Response("Missing param fleet_access_token", status=400)        
      
    ensure_db()
    conf_access_fleet_token = config.get('fleet_access_token', None)
    param_access_fleet_token = request.params['fleet_access_token']
    if conf_access_fleet_token != param_access_fleet_token:
      return Response("Wrong token", status=401)
    server.restart()
    _logger.warning("Server restart requested from odoo fleet")

    return Response("OK", status=200)