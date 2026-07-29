from odoo import api, models, fields, tools
from odoo.http import Stream

import requests
import boto3
import botocore
from botocore.exceptions import ClientError
import logging
import re
import os
import errno
from . import s3_globals

_logger = logging.getLogger(__name__)


class S3Attachment(models.Model):
    """Extends ir.attachment to implement the S3 storage engine
    """
    _inherit = "ir.attachment"

    def _s3_key_from_fname(self, store_fname):
        # db_name = self.env.registry.db_name
        if not store_fname:
            return store_fname
        return store_fname.replace('\\', '/').lstrip('/')

    @api.model
    def _file_read_s3(self, fname):
        storage = self._storage()
        s3_bucket = s3_globals.get_s3_bucket()
        key = self._s3_key_from_fname(fname)

        try:
            return s3_bucket.Object(key).get()['Body'].read()
        except Exception:
            _logger.exception('S3: _file_read failed on key:%s, trying trash', key)

            try:
                # Check the trash
                trash_key_list = key.split('/')
                trash_key_list.insert(1, 'trash')
                trash_key = '/'.join(trash_key_list)
                # Try reading trash key
                s3_trash_key = s3_bucket.Object(trash_key)
                data = s3_trash_key.get()['Body'].read()
                _logger.debug('S3: _file_read read key:%s from bucket trash bin', s3_trash_key)
                # Restore the file
                s3_bucket.Object(key).copy_from(CopySource='%s/%s' % (s3_trash_key.bucket_name, s3_trash_key.key))
                s3_trash_key.delete()
                _logger.debug('S3: _file_read --::-- restored the key:%s from bucket trash bin key %s', s3_trash_key.key, key)
                return data
            except Exception:
                _logger.exception('S3: _file_read not found in trash either: %s', key)
                raise

    @api.model
    def _file_read_s3_or_cache(self, fname):
        cache_domain = s3_globals.get_cache_domain()

        if cache_domain:
            try:
                access_key, secret_key, endpoint, region, bucket = s3_globals.get_s3_config()
                key = self._s3_key_from_fname(fname)

                response = requests.post(
                    '%s/s3' % cache_domain,
                    json={
                        'credentials': {
                            'accessKeyId': access_key,
                            'secretAccessKey': secret_key,
                        },
                        'region': region,
                        'endpoint': endpoint,
                        'bucket': bucket,
                        'key': key,
                    },
                    timeout=5,
                )

                _logger.debug('Cache: _file_read read key:%s from cache successfully', key)

                return response.content
            except Exception as e:
                _logger.info("Unable to handle cache read error")
                _logger.error(e)
                return self._file_read_s3(fname=fname)
        else:
            return self._file_read_s3(fname=fname)
    
    @api.model
    def _file_read(self, fname):
        s3_active = s3_globals.get_s3_active()

        if s3_active:
            disk_first = s3_globals.get_disk_first()

            if disk_first:
                full_path = super(S3Attachment, self)._full_path(fname)
                try:
                    with open(full_path, 'rb') as f:
                        return f.read()
                except (IOError, OSError) as e:
                    if e.errno == errno.ENOENT:
                        _logger.info("Unable to read file %s from filesystem, fallback to s3 or cache", full_path)
                        return self._file_read_s3_or_cache(fname=fname)
                    _logger.info("Unable to handle read disk error")
                    return b''
            else:
                return self._file_read_s3_or_cache(fname=fname)
        else:
            return super(S3Attachment, self)._file_read(fname=fname)

    @api.model
    def _file_write_s3(self, bin_value, checksum):
        storage = self._storage()
        s3_bucket = s3_globals.get_s3_bucket()

        fname, full_path = self._get_path(bin_value, checksum)
        key = self._s3_key_from_fname(fname)

        s3_key = s3_bucket.Object(key)
        metadata = {
            'name': self.name or '',
            'res_id': str(self.res_id) or '',
            'res_model': self.res_model or '',
            'description': self.description or '',
            'create_date': str(self.create_date or '')
        }

        try:
            s3_key.put(Body=bin_value, Metadata=metadata)
        except ClientError as ex:
            if ex.response['Error']['Code'] == 'OperationAborted' and ex.response['Error']['Message'] == 'A conflicting conditional operation is currently in progress against this resource. Please try again.':
                _logger.warning('S3: _file_write  key:%s was not able to upload because of a concurrent operation, skipping', key)
                pass
            else:
                raise ex

        # Storing this info because can be usefull for later having public urls for assets
        _logger.debug('S3: _file_write  key:%s was successfully uploaded', key)
        
        # Returning the file name
        return fname
    
    @api.model
    def _file_write(self, bin_value, checksum):
        # If Odoo feeds a Stream object accidentally as bin_value
        from odoo.http import Stream
        if isinstance(bin_value, Stream):
            return self._file_write_stream(bin_value, checksum)

        s3_active = s3_globals.get_s3_active()

        if s3_active:
            disk_first = s3_globals.get_disk_first()

            if disk_first:
                return super(S3Attachment, self)._file_write(bin_value, checksum)
            else:
                return self._file_write_s3(bin_value, checksum)
        else:
            return super(S3Attachment, self)._file_write(bin_value, checksum)

    def _file_write_stream(self, stream, checksum):
        """
        stream is an odoo.http.Stream instance.
        It can be:
        - type='path' -> stream.path points to a temp file
        - type='data' -> stream.data is bytes
        - type='iterator' -> yields bytes chunks
        """

        _logger.info("S3: _file_write_stream called with stream.type=%s", stream.type)

        # normalize to bytes
        if stream.type == 'data':
            content = stream.data
        elif stream.type == 'path':
            with open(stream.path, 'rb') as f:
                content = f.read()
        else:  # iterator/chunks
            content = b''.join(chunk for chunk in stream)

        # Reuse your existing S3 write
        return self._file_write(content, checksum)
    
    def _to_http_stream(self):
        s3_active = s3_globals.get_s3_active()

        if s3_active:
            self.ensure_one()

            if self.store_fname:
                data = self._file_read(self.store_fname)

                return Stream(
                    type='data',
                    data=data,
                    last_modified=self.write_date,
                    size=len(data),
                    mimetype=self.mimetype,
                    download_name=self.name,
                    conditional=True,
                    etag=self.checksum,
                )

        return super(S3Attachment, self)._to_http_stream()

    @api.model
    @api.autovacuum
    def _file_gc_s3(self):
        s3_active = s3_globals.get_s3_active()

        if s3_active:
            disk_first = s3_globals.get_disk_first()

            if not disk_first:
                storage = self._storage()
                s3_bucket = None

                try:
                    s3_bucket = s3_globals.get_s3_bucket()
                    _logger.debug('S3: _file_gc_s3 connected (%s)', storage)
                except Exception:
                    _logger.exception('S3: _file_gc_s3 unable to connect (%s)', storage)
                    return False

                # Continue in a new transaction. The LOCK statement below must be the
                # first one in the current transaction, otherwise the database snapshot
                # used by it may not contain the most recent changes made to the table
                # ir_attachment! Indeed, if concurrent transactions create attachments,
                # the LOCK statement will wait until those concurrent transactions end.
                # But this transaction will not see the new attachements if it has done
                # other requests before the LOCK (like the method _storage() above).
                cr = self._cr
                cr.commit()

                # prevent all concurrent updates on ir_attachment while collecting!
                cr.execute("LOCK ir_attachment IN SHARE MODE")

                try:
                    # retrieve the file names from the checklist
                    checklist = {}
                    prefix = "checklist/"
                    for obj in s3_bucket.objects.filter(Prefix=prefix):
                        real_fname = obj.key[len(prefix):]
                        if real_fname:
                            checklist[real_fname] = obj.key

                    # determine which files to keep among the checklist
                    whitelist = set()
                    keys = tuple(checklist.keys())
                    if keys:
                        for names in cr.split_for_in_conditions(keys):
                            cr.execute(
                                "SELECT store_fname FROM ir_attachment WHERE store_fname IN %s",
                                [names],
                            )
                            whitelist.update(row[0] for row in cr.fetchall())

                    # remove garbage files, and clean up checklist
                    removed = 0
                    for real_fname, checklist_key in checklist.items():
                        if real_fname in whitelist:
                            continue

                        src_key = real_fname
                        dst_key = f"trash/{real_fname}"

                        src_obj = s3_bucket.Object(src_key)
                        dst_obj = s3_bucket.Object(dst_key)

                        # copy -> delete source -> delete marker
                        dst_obj.copy_from(CopySource={'Bucket': s3_bucket.name, 'Key': src_key})
                        src_obj.delete()
                        s3_bucket.Object(checklist_key).delete()

                        removed += 1
                        _logger.debug('S3: GC moved %s -> %s and deleted marker %s', src_key, dst_key, checklist_key)

                except ClientError as ex:
                    _logger.exception('S3: _file_gc_s3 (key:%s) (checklist_key:%s) %s:%s', real_key_name, check_key_name,
                                ex.response['Error']['Code'], ex.response['Error']['Message'])

                except Exception as ex:
                    _logger.exception('S3: _file_gc_s3 was not able to gc (key:%s) (checklist_key:%s)', real_key_name, check_key_name)

                # commit to release the lock
                cr.commit()
                _logger.info("S3: filestore gc %d checked, %d removed", len(checklist), removed)

    def _mark_for_gc_s3(self, fname):
        """ We will mark for garbage collection in both s3 and filesystem
        Just the garbage collection in s3 will move to trash and not delete"""
        storage = self._storage()
        s3_bucket = None

        try:
            s3_bucket = s3_globals.get_s3_bucket()
            _logger.debug('S3: File mark as gc. Connected Sucessfuly (%s)', storage)
        except Exception:
            _logger.exception('S3: File mark as gc. Was not able to connect (%s), gonna try other filestore', storage)
            # Only try filesystem if the not copied to S3
            if not self.env['ir.config_parameter'].sudo().get_param('ir_attachment.location_s3_copied_to', False):
                return super(S3Attachment, self)._mark_for_gc(fname)

        new_key = f"checklist/{self._s3_key_from_fname(fname)}"

        try:
            s3_key = s3_bucket.Object(new_key)
            # Just create an empty file to
            s3_key.put(Body='')
            _logger.debug('S3: _mark_for_gc key:%s marked for garbage collection', new_key)
        except Exception:
            _logger.exception('S3: _mark_for_gc Was not able to save key:%s', new_key)
            # Only try filesystem if the not copied to S3
            if not self.env['ir.config_parameter'].sudo().get_param('ir_attachment.location_s3_copied_to', False):
                return super(S3Attachment, self)._mark_for_gc(fname)
    
    def _mark_for_gc(self, fname):
        s3_active = s3_globals.get_s3_active()

        if s3_active:
            disk_first = s3_globals.get_disk_first()

            if not disk_first:
                self._mark_for_gc_s3(fname)
        else:
            return super(S3Attachment, self)._mark_for_gc(fname)

    def _copy_filestore_to_s3(self):
        try:
            self._run_copy_filestore_to_s3()
            _logger.info('S3: filestore copied to S3 successfully')
        except Exception:
            _logger.exception("S3: filestore copy to S3 aborted!")
        return {}

    @api.model
    def _run_copy_filestore_to_s3(self):
        storage = self._storage()
        is_copied = self.env['ir.config_parameter'].sudo().get_param('ir_attachment.location_s3_copied_to', False)
        
        if not is_copied:
            access_key, secret_key, endpoint, region, bucket = s3_globals.get_s3_config()
            db_name = self.env.registry.db_name
            s3_url = 's3://%s' % (db_name)
            full_path = self._full_path('')
            s3_bucket = None
            try:
                s3_bucket = s3_globals.get_s3_bucket()
                _logger.debug('S3: Copy filestore to S3. Connected Sucessfuly (%s)', storage)
            except Exception:
                _logger.exception('S3: Copy filestore to S3. Was not able to connect (%s), gonna try other filestore',
                              storage)
            s3 = s3_globals.get_s3_client()
            for root, dirs, files in os.walk(full_path):
                for file_name in files:
                    path = os.path.join(root, file_name)
                    bucket = s3_bucket.name
                    rel = path[len(full_path):].replace('\\', '/').lstrip('/')
                    s3.upload_file(path, bucket, rel)
                    _logger.debug('S3: Copy filestore to S3. Loading file %s', path[len(full_path):])
            self.env['ir.config_parameter'].sudo().set_param('ir_attachment.location_s3_copied_to', '%s' % s3_url)

    def check_s3_filestore(self):
        """This command is here for being trigger using odoo shell:

        e.g.:

        $> a, b = env['ir.attachment'].search([]).check_s3_filestore()
        $> filter(lambda x: x['s3_lost']==True, a)
        $> print b # will show totals
        $> env.cr.commit() # need to do this to update the table s3_lost field to know if something is lost

        """
        storage = self._storage()

        s3_bucket = None

        try:
            s3_bucket = s3_globals.get_s3_bucket()
            _logger.debug('S3: _file_gc_s3 connected Sucessfuly (%s)', storage)
        except Exception:
            _logger.exception('S3: _file_gc_s3 was not able to connect (%s)', storage)
            return False

        status_res = []
        totals = {
            'lost_count': 0,
        }

        for att in self:
            status = {'name': att.name, 'fname': att.store_fname}

            try:
                if not att.store_fname:
                    raise ValueError('There is no store_fname')
                key = self._s3_key_from_fname(att.store_fname)
                obj = s3_bucket.Object(key)
                obj.load()  # HEAD ; lève 404 si absent
                _logger.debug('S3: check_s3_filestore ok %s', key)
            except ClientError as ex:
                code = ex.response.get('Error', {}).get('Code')
                if str(code) == '404':
                    totals['lost_count'] += 1
                _logger.exception('S3: check_s3_filestore fail %s', key)
                status['error'] = ex.response.get('Error', {}).get('Message')
            except Exception as e:
                status['error'] = str(e)
            status_res.append(status)

        return status_res, totals
