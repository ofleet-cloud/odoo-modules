from odoo.api import Environment
from odoo.tools import config

# config.get("odoo_tag") is a process-wide static value (read once from
# odoo.conf at startup, never changes at runtime), so there's no need for
# a lazy/cached descriptor here.
Environment.saas_tag = config.get("odoo_tag", False)
