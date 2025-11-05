from odoo.api import Environment
from odoo.tools import config
from functools import cached_property

class SaasTag:
  value = cached_property(lambda self: config.get("odoo_tag", False))

setattr(Environment, "saas_tag", SaasTag().value)
