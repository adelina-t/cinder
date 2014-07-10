#  Copyright 2014 Cloudbase Solutions Srl
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

import contextlib
import importlib
import mock
import os
import sys

from cinder import exception
from cinder import test

from cinder.image import image_utils


class WindowsSmbFsTestCase(test.TestCase):

    _FAKE_SHARE = '//1.2.3.4/share1'
    _FAKE_MNT_BASE = 'c:\openstack\mnt'
    _FAKE_HASH = 'db0bf952c1734092b83e8990bd321131'
    _FAKE_MNT_POINT = os.path.join(_FAKE_MNT_BASE, _FAKE_HASH)
    _FAKE_VOLUME_NAME = 'volume-4f711859-4928-4cb7-801a-a50c37ceaccc'
    _FAKE_SNAPSHOT_NAME = _FAKE_VOLUME_NAME + '-snapshot.vhdx'
    _FAKE_VOLUME_PATH = os.path.join(_FAKE_MNT_POINT,
                                     _FAKE_VOLUME_NAME)
    _FAKE_SNAPSHOT_PATH = os.path.join(_FAKE_MNT_POINT,
                                       _FAKE_SNAPSHOT_NAME)
    _FAKE_TOTAL_SIZE = '2048'
    _FAKE_TOTAL_AVAILABLE = '1024'
    _FAKE_TOTAL_ALLOCATED = 1024
    _FAKE_VOLUME = {'id': 'e8d76af4-cbb9-4b70-8e9e-5a133f1a1a66',
                    'size': 1,
                    'provider_location': _FAKE_SHARE}
    _FAKE_SHARE_OPTS = '-o username=Administrator,password=12345'
    _FAKE_VOLUME_PATH = os.path.join(_FAKE_MNT_POINT,
                                     _FAKE_VOLUME_NAME + '.vhdx')
    _FAKE_LISTDIR = [_FAKE_VOLUME_NAME + '.vhd',
                     _FAKE_VOLUME_NAME + '.vhdx', 'fake_folder']
    _FAKE_SNAPSHOT_INFO = {'ParentPath': _FAKE_SNAPSHOT_PATH}

    def setUp(self):
        super(WindowsSmbFsTestCase, self).setUp()
        self._mock_wmi = mock.MagicMock()

        self._platform_patcher = mock.patch('sys.platform', 'win32')

        mock.patch.dict(sys.modules, wmi=self._mock_wmi,
                        ctypes=self._mock_wmi).start()

        self._platform_patcher.start()
        # self._wmi_patcher.start()
        self.addCleanup(mock.patch.stopall)

        smbfs = importlib.import_module(
            'cinder.volume.drivers.windows.smbfs')
        smbfs.WindowsSmbfsDriver.__init__ = lambda x: None
        self._smbfs_driver = smbfs.WindowsSmbfsDriver()
        self._smbfs_driver._remotefsclient = mock.MagicMock()
        self._smbfs_driver.local_path = mock.Mock(
            return_value=self._FAKE_VOLUME_PATH)
        self._smbfs_driver.vhdutils = mock.MagicMock()

    def _test_create_volume(self, volume_exists=False, volume_format='vhdx'):
        self._smbfs_driver.create_dynamic_vhd = mock.MagicMock()
        fake_create = self._smbfs_driver.vhdutils.create_dynamic_vhd
        self._smbfs_driver.get_volume_format = mock.Mock(
            return_value=volume_format)

        with mock.patch('os.path.exists', new=lambda x: volume_exists):
            if volume_exists or volume_format not in ('vhd', 'vhdx'):
                self.assertRaises(exception.InvalidVolume,
                                  self._smbfs_driver._do_create_volume,
                                  self._FAKE_VOLUME)
            else:
                fake_vol_path = self._FAKE_VOLUME_PATH
                self._smbfs_driver._do_create_volume(self._FAKE_VOLUME)
                fake_create.assert_called_once_with(
                    fake_vol_path, self._FAKE_VOLUME['size'] << 30,
                    volume_format)

    def test_create_volume(self):
        self._test_create_volume()

    def test_create_existing_volume(self):
        self._test_create_volume(True)

    def test_create_volume_invalid_volume(self):
        self._test_create_volume(volume_format="qcow")

    def test_get_capacity_info(self):
        self._smbfs_driver._remotefsclient.get_capacity_info = mock.Mock(
            return_value=(self._FAKE_TOTAL_SIZE, self._FAKE_TOTAL_AVAILABLE))
        self._smbfs_driver._get_total_allocated = mock.Mock(
            return_value=self._FAKE_TOTAL_ALLOCATED)

        ret_val = self._smbfs_driver._get_capacity_info(self._FAKE_SHARE)
        expected_ret_val = [int(x) for x in [self._FAKE_TOTAL_SIZE,
                            self._FAKE_TOTAL_AVAILABLE,
                            self._FAKE_TOTAL_ALLOCATED]]
        self.assertEqual(ret_val, expected_ret_val)

    def test_get_total_allocated(self):
        fake_listdir = mock.Mock(side_effect=[self._FAKE_LISTDIR,
                                 self._FAKE_LISTDIR[:-1]])
        fake_folder_path = os.path.join(self._FAKE_SHARE, 'fake_folder')
        fake_isdir = lambda x: x == fake_folder_path
        self._smbfs_driver._remotefsclient.is_symlink = mock.Mock(
            return_value=False)
        fake_getsize = mock.Mock(return_value=self._FAKE_VOLUME['size'])
        self._smbfs_driver.vhdutils.get_vhd_size = mock.Mock(
            return_value=1)

        with mock.patch.multiple('os.path', isdir=fake_isdir,
                                 getsize=fake_getsize):
            with mock.patch('os.listdir', fake_listdir):
                ret_val = self._smbfs_driver._get_total_allocated(
                    self._FAKE_SHARE)
                self.assertEqual(ret_val, 4)

    def _test_get_img_info(self, backing_file=None):
        fake_vhd_info = {'Format': 'vhdx',
                         'ParentPath': backing_file}
        self._smbfs_driver.vhdutils.get_vhd_info.return_value = (
            fake_vhd_info)

        image_info = self._smbfs_driver._img_info(self._FAKE_VOLUME_PATH)
        self.assertEqual(self._FAKE_VOLUME_NAME + '.vhdx',
                         image_info.image)
        backing_file_name = backing_file and os.path.basename(backing_file)
        self.assertEqual(backing_file_name, image_info.backing_file)

    def test_get_img_info_without_backing_file(self):
        self._test_get_img_info()

    def test_get_snapshot_info(self):
        self._test_get_img_info(self._FAKE_VOLUME_PATH)

    def test_create_snapshot(self):
        self._smbfs_driver.vhdutils.create_differencing_image = (
            mock.MagicMock())
        fake_create_diff = (
            self._smbfs_driver.vhdutils.create_differencing_image)
        self._smbfs_driver._create_snapshot('fake_snapshot',
                                            self._FAKE_VOLUME_PATH,
                                            self._FAKE_SNAPSHOT_PATH)
        fake_create_diff.assert_called_once_with(self._FAKE_SNAPSHOT_PATH,
                                                 self._FAKE_VOLUME_PATH)

    def test_volume_extend_unsupported(self):
        fake_volume = 'volume.vhd'
        fake_active_file = 'volume-snapshot.vhd'
        self.assertRaises(exception.InvalidVolume,
                          self._smbfs_driver._check_extend_volume_support,
                          fake_volume, fake_active_file)

    def _test_copy_volume_to_image(self, has_parent, qemu_version=[1, 7]):
        fake_image = {'id': 'fake-image-id'}
        if has_parent:
            fake_volume_path = self._FAKE_SNAPSHOT_PATH
            fake_parent_path = self._FAKE_VOLUME_PATH
        else:
            fake_volume_path = self._FAKE_VOLUME_PATH
            fake_parent_path = None

        self._smbfs_driver.get_active_image_from_info = mock.Mock(
            return_value=os.path.split(fake_volume_path))
        self._smbfs_driver.local_volume_dir = mock.Mock(
            return_value=self._FAKE_MNT_POINT)
        self._smbfs_driver.get_qemu_version = mock.Mock(
            return_value=qemu_version)
        self._smbfs_driver.get_volume_format = mock.Mock(
            return_value='vhdx')
        self._smbfs_driver.vhdutils.get_vhd_info.return_value = (
            {'ParentPath': fake_parent_path})

        with contextlib.nested(
            mock.patch.object(image_utils, 'upload_volume'),
            mock.patch('os.unlink')) as (
                fake_upload_volume,
                fake_unlink):

            self._smbfs_driver.copy_volume_to_image(
                None, self._FAKE_VOLUME, None, fake_image)

            expected_conversion = has_parent or qemu_version < [1, 7]
            did_conversion = self._smbfs_driver.vhdutils.convert_vhd.called

            self.assertEqual(expected_conversion, did_conversion)
            self.assertEqual(expected_conversion, fake_unlink.called)
            self.assertTrue(fake_upload_volume.called)

    def test_copy_volume_to_image_having_snapshot(self):
        self._test_copy_volume_to_image(True)

    def test_copy_volume_to_image_old_qemu(self):
        self._test_copy_volume_to_image(False, [1, 5])

    def _test_copy_image_to_volume(self, qemu_version=[1, 7]):
        fake_image_service = mock.MagicMock()
        fake_image_service.show.return_value = (
            {'id': 'fake_image_id', 'disk_format': 'raw'})

        self._smbfs_driver.get_volume_format = mock.Mock(
            return_value='vhdx')
        self._smbfs_driver.local_path = mock.Mock(
            return_value=self._FAKE_VOLUME_PATH)
        self._smbfs_driver.local_volume_dir = mock.Mock(
            return_value=self._FAKE_MNT_POINT)
        self._smbfs_driver.get_qemu_version = mock.Mock(
            return_value=qemu_version)
        self._smbfs_driver.configuration = mock.MagicMock()
        self._smbfs_driver.configuration.volume_dd_blocksize = 4096

        with contextlib.nested(
                mock.patch.object(image_utils,
                                  'fetch_to_volume_format'),
                mock.patch('os.unlink')) as (
                    fake_fetch,
                    fake_unlink):

            self._smbfs_driver.copy_image_to_volume(
                None, self._FAKE_VOLUME, fake_image_service,
                'fake_image_id')

            self.assertTrue(fake_fetch.called)
            if qemu_version < [1, 7]:
                self.assertTrue(
                    self._smbfs_driver.vhdutils.convert_vhd.called)
                self.assertTrue(fake_unlink.called)

    def test_copy_image_to_volume(self):
        self._test_copy_image_to_volume()

    def test_copy_image_to_volume_with_conversion(self):
        self._test_copy_image_to_volume([1, 5])

    def test_copy_volume_from_snapshot(self):
        fake_snapshot_id = 'fake_snapshot_id'
        fake_snapshot = {'id': fake_snapshot_id,
                         'volume': self._FAKE_VOLUME,
                         'volume_size': self._FAKE_VOLUME['size']}
        fake_volume_info = {fake_snapshot_id: 'fake_snapshot_file_name'}
        fake_img_info = mock.MagicMock()
        fake_img_info.backing_file = self._FAKE_VOLUME_NAME + '.vhdx'

        self._smbfs_driver._local_path_volume_info = mock.Mock(
            return_value=self._FAKE_VOLUME_PATH + '.info')
        self._smbfs_driver.local_volume_dir = mock.Mock(
            return_value=self._FAKE_MNT_POINT)
        self._smbfs_driver._read_info_file = mock.Mock(
            return_value=fake_volume_info)
        self._smbfs_driver._img_info = mock.Mock(
            return_value=fake_img_info)

        with mock.patch('os.unlink') as fake_unlink:
            self._smbfs_driver._copy_volume_from_snapshot(
                fake_snapshot, self._FAKE_VOLUME)

            self.assertTrue(fake_unlink.called)
            self.assertTrue(self._smbfs_driver.vhdutils.convert_vhd.called)

    def test_img_commit(self):
        fake_vhd_info = {
            'ParentPath': self._FAKE_VOLUME_PATH,
        }
        self._smbfs_driver.vhdutils.get_vhd_info.return_value = fake_vhd_info

        self._smbfs_driver._img_commit(self._FAKE_SNAPSHOT_PATH)
        self._smbfs_driver.vhdutils.merge_snapshot.assert_called_once_with(
            self._FAKE_SNAPSHOT_PATH, self._FAKE_VOLUME_PATH)

    def test_rebase_img(self):
        self._smbfs_driver._rebase_img(
            self._FAKE_SNAPSHOT_PATH,
            self._FAKE_VOLUME_NAME + '.vhdx', 'vhdx')
        self._smbfs_driver.vhdutils.reconnect_parent.assert_called_once_with(
            self._FAKE_SNAPSHOT_PATH, self._FAKE_VOLUME_PATH)
