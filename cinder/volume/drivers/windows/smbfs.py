# Copyright (c) 2014 Cloudbase Solutions SRL
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.


import os
import re

from oslo.config import cfg

from cinder import exception
from cinder import utils

from cinder.image import image_utils

from cinder.openstack.common import fileutils
from cinder.openstack.common.gettextutils import _
from cinder.openstack.common import log as logging

from cinder.volume.drivers import smbfs
from cinder.volume.drivers.windows import remotefs
from cinder.volume.drivers.windows import windows_utils

VERSION = '1.1.0'

LOG = logging.getLogger(__name__)

CONF = cfg.CONF
CONF.set_default('smbfs_shares_config', r'C:\OpenStack\smbfs_shares.txt')
CONF.set_default('smbfs_mount_point_base', r'C:\OpenStack\_mnt')
CONF.set_default('smbfs_default_volume_format', 'vhd')


class WindowsSmbfsDriver(smbfs.SmbfsDriver):
    VERSION = VERSION

    def __init__(self, *args, **kwargs):
        super(WindowsSmbfsDriver, self).__init__(*args, **kwargs)
        self.base = getattr(self.configuration,
                            'smbfs_mount_point_base',
                            CONF.smbfs_mount_point_base)
        opts = getattr(self.configuration,
                       'smbfs_mount_options',
                       CONF.smbfs_mount_options)
        self._remotefsclient = remotefs.WindowsRemoteFsClient(
            'cifs', root_helper=None, smbfs_mount_point_base=self.base,
            smbfs_mount_options=opts)
        self.vhdutils = vhdutils.VHDUtils()
        self.utils = windows_utils.WindowsUtils()

    def _do_create_volume(self, volume):
        volume_path = self.local_path(volume)
        volume_format = self.get_volume_format(volume)
        volume_size = volume['size'] << 30

        LOG.info('Volume_size:  %s' % volume_size)

        LOG.debug("Creating new volume at %s" % volume_path)

        if os.path.exists(volume_path):
            err_msg = _('File already exists at %s') % volume_path
            raise exception.InvalidVolume(err_msg)

        if volume_format not in ('vhd', 'vhdx'):
            err_msg = "Unsupported volume format: %s " % volume_format
            raise exception.InvalidVolume(err_msg)

        self.vhdutils.create_dynamic_vhd(volume_path, volume_size,
                                         volume_format)

    def _ensure_share_mounted(self, smbfs_share):
        mnt_flags = []
        if self.shares.get(smbfs_share) is not None:
            mnt_flags = self.shares[smbfs_share]
            mnt_options = self.parse_options(mnt_flags)[1]
        self._remotefsclient.mount(smbfs_share, mnt_options)

    def _delete_volume(self, volume_path):
        os.unlink(volume_path)

    def _get_capacity_info(self, smbfs_share):
        """Calculate available space on the SMBFS share.

        :param smbfs_share: example //172.18.194.100/var/smbfs
        """
        total_size, total_available = self._remotefsclient.get_capacity_info(
            smbfs_share)
        total_allocated = self._get_total_allocated(smbfs_share)
        return_value = [total_size, total_available, total_allocated]
        LOG.info('Smb share %s Total size %s Total allocated %s'
                 % (smbfs_share, total_size, total_allocated))
        return [float(x) for x in return_value]

    def _get_total_allocated(self, smbfs_share):
        elements = os.listdir(smbfs_share)
        total_allocated = 0
        for element in elements:
            element_path = os.path.join(smbfs_share, element)
            if not self._remotefsclient.is_symlink(element_path):
                if "snapshot" in element:
                    continue
                if re.search(r'\.vhdx?$', element):
                    total_allocated += self.vhdutils.get_vhd_size(
                        element_path)
                    continue
                if os.path.isdir(element_path):
                    total_allocated += self._get_total_allocated(element_path)
                    continue
            total_allocated += os.path.getsize(element_path)

        return total_allocated

    def _img_commit(self, snapshot_path):
        snapshot_info = self.vhdutils.get_vhd_info(snapshot_path)
        parent_path = snapshot_info["ParentPath"].lower()
        self.vhdutils.merge_snapshot(snapshot_path, parent_path)

    def _rebase_img(self, image, backing_file, volume_format):
        # Relative path names are not supported in this case.
        image_dir = os.path.dirname(image)
        backing_file_path = os.path.join(image_dir, backing_file)
        self.vhdutils.reconnect_parent(image, backing_file_path)

    def _img_info(self, path):
        # This code expects to deal only with relative filenames.
        vhd_info = self.vhdutils.get_vhd_info(path)
        parent_path = vhd_info.get("ParentPath")
        file_format = vhd_info.get("Format")

        if parent_path:
            backing_file_name = os.path.split(parent_path)[1].lower()
        else:
            backing_file_name = None

        class ImageInfo(object):
            def __init__(self, image, backing_file):
                self.image = image
                self.backing_file = backing_file
                self.file_format = file_format

        return ImageInfo(os.path.basename(path),
                         backing_file_name)

    def _create_snapshot(self, snapshot, backing_file, new_snap_path):
        """Create a differencing image
        :param snapshot: snapshot reference
        :param backing_file: parent path
        :param new_snap_path: filename of new differencing image
        """
        self.vhdutils.create_differencing_image(new_snap_path,
                                                backing_file)

    def _check_extend_volume_support(self, volume_filename, active_file):
        volume_format = os.path.splitext(volume_filename)[1][1:]
        if (volume_filename != active_file and
                volume_format != 'vhdx'):
            # Only vhdx differencing images can be resized
            msg = _('Extend volume is only supported for this '
                    'driver when no snapshots exist or the volume '
                    'format is vhdx.')
            raise exception.InvalidVolume(msg)

    def _extend_volume(self, volume_path, size_gb):
        self.vhdutils.resize_vhd(volume_path, size_gb << 30)

    @utils.synchronized('smbfs', external=False)
    def copy_volume_to_image(self, context, volume, image_service, image_meta):
        """Copy the volume to the specified image."""

        # If snapshots exist, flatten to a temporary image, and upload it

        active_file = self.get_active_image_from_info(volume)
        active_file_path = '%s/%s' % (self.local_volume_dir(volume),
                                      active_file)
        info = self.vhdutils.get_vhd_info(active_file_path)
        backing_file = info['ParentPath']

        root_file_fmt = self.get_volume_format(volume)

        temp_path = None
        qemu_version = self.get_qemu_version()
        # qemu-img < 1.7 does not properly support vhdx images
        needs_conversion = qemu_version < [1, 7] and root_file_fmt == 'vhdx'

        if needs_conversion:
            ext = 'vhd'
        else:
            ext = root_file_fmt

        try:
            if backing_file or needs_conversion:
                temp_path = '%s/%s.temp_image.%s.%s' % (
                    self.local_volume_dir(volume),
                    volume['id'],
                    image_meta['id'],
                    ext)

                self.vhdutils.convert_vhd(active_file_path, temp_path)
                upload_path = temp_path
            else:
                upload_path = active_file_path

            if root_file_fmt == 'vhd':
                root_file_fmt == 'vpc'

            image_utils.upload_volume(context,
                                      image_service,
                                      image_meta,
                                      upload_path,
                                      root_file_fmt)
        finally:
            if temp_path:
                os.unlink(temp_path)

    def copy_image_to_volume(self, context, volume, image_service, image_id):
        """Fetch the image from image_service and write it to the volume."""
        volume_format = self.get_volume_format(volume, qemu_format=True)
        image_meta = image_service.show(context, image_id)

        fetch_format = volume_format
        fetch_path = self.local_path(volume)
        fileutils.delete_if_exists(fetch_path)
        qemu_version = self.get_qemu_version()

        needs_conversion = False

        if (qemu_version < [1, 7] and (volume_format == 'vhdx' and
                                       image_meta['disk_format'] != 'vhdx')):
            needs_conversion = True
            fetch_format = 'vpc'
            fetch_path = '%s/%s.temp_image.%s.vhd' % (
                self.local_volume_dir(volume),
                volume['id'],
                image_meta['id'])

        image_utils.fetch_to_volume_format(
            context, image_service, image_id,
            fetch_path, fetch_format,
            self.configuration.volume_dd_blocksize)

        if needs_conversion:
            self.vhdutils.convert_vhd(fetch_path, self.local_path(volume))
            os.unlink(fetch_path)

        self.vhdutils.resize_vhd(self.local_path(volume),
                                 volume['size'] << 30)

    def _copy_volume_from_snapshot(self, snapshot, volume):
        """Copy data from snapshot to destination volume."""

        LOG.debug("snapshot: %(snap)s, volume: %(vol)s, "
                  "volume_size: %(size)s" %
                  {'snap': snapshot['id'],
                   'vol': volume['id'],
                   'size': snapshot['volume_size']})

        info_path = self._local_path_volume_info(snapshot['volume'])
        snap_info = self._read_info_file(info_path)
        vol_dir = self.local_volume_dir(snapshot['volume'])

        forward_file = snap_info[snapshot['id']]
        forward_path = os.path.join(vol_dir, forward_file)

        # Find the file which backs this file, which represents the point
        # when this snapshot was created.
        img_info = self._img_info(forward_path)
        snapshot_path = os.path.join(vol_dir, img_info.backing_file)

        LOG.debug("Will copy from snapshot at %s" % snapshot_path)

        volume_path = self.local_path(volume)
        fileutils.delete_if_exists(volume_path)
        self.vhdutils.convert_vhd(snapshot_path,
                                  volume_path)
