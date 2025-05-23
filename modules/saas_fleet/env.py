from odoo.api import Environment
from odoo.tools import config, lazy_property

@lazy_property
def saas_tag(self):
  return config.get("odoo_tag", False)

setattr(Environment, "saas_tag", saas_tag)
