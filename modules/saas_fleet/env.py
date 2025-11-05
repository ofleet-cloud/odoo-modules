from odoo.api import Environment
from odoo.tools import config
import functools

@functools.cached_property
def saas_tag(self):
  return config.get("odoo_tag", False)

setattr(Environment, "saas_tag", saas_tag)
