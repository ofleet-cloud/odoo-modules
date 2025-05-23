import logging
import odoo
from odoo import http

_logger = logging.getLogger(__name__)

_original_from_attachement = http.Stream.from_attachment

@classmethod
def from_attachment(cls, attachment):
    attachment.ensure_one()

    if attachment.store_fname:
        data = attachment._file_read(attachment.store_fname)

        return http.Stream(
            type='data',
            data=data,
            last_modified=attachment.write_date,
            size=len(data),
            mimetype=attachment.mimetype,
            download_name=attachment.name,
            conditional=True,
            etag=attachment.checksum,
        )

    return _original_from_attachement(attachment)


  
# Monkey patch of standard methods
_logger.debug("Monkey patching http from_attachment")
http.Stream.from_attachment = from_attachment
