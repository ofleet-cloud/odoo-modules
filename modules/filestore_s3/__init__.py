# -*- coding: utf-8 -*-
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, SUPERUSER_ID
from . import models
from .models import s3_globals
import datetime

def _post_install_hook(env):
    if s3_globals.get_s3_active():
        db_name = env.registry.db_name
        s3_url = 's3://%s' % (db_name)
        env['ir.config_parameter'].sudo().set_param('ir_attachment.location', s3_url)
        env['ir.attachment'].sudo()._copy_filestore_to_s3()

        # Update vacuum trigger to run at 2am
        cron = env['ir.cron'].search([('id', '=', env.ref('base.autovacuum_job').id)], limit=1)
        next_day = datetime.datetime.now() + datetime.timedelta(days=1)
        next_day = next_day.replace(hour=0, minute=0, second=0, microsecond=0)
        cron.write({'nextcall': next_day})

def post_init_hook(cr, registry):
    _post_install_hook(cr, registry)
