import copy
import mock
import os
import time

from cinder import db
from cinder import exception
from cinder import test

from cinder.volume.drivers import remotefs

class remoteFsDriverTestCase(test.TestCase):

    _FAKE_CONTEXT = 'fake_context'
    _FAKE_VOLUME_NAME = 'volume-4f711859-4928-4cb7-801a-a50c37ceaccc'
    _FAKE_VOLUME = {'id': '4f711859-4928-4cb7-801a-a50c37ceaccc',
                    'size': 1,
                    'provider_location': 'fake_share',
                    'name': _FAKE_VOLUME_NAME,
                    'status': 'available'}
    _FAKE_MNT_POINT = '/mnt/fake_hash'
    _FAKE_VOLUME_PATH = os.path.join(_FAKE_MNT_POINT,
                                     _FAKE_VOLUME_NAME)
    _FAKE_SNAPSHOT_ID = '5g811859-4928-4cb7-801a-a50c37ceacba'
    _FAKE_SNAPSHOT = {'context': _FAKE_CONTEXT,
                      'id': _FAKE_SNAPSHOT_ID,
                      'volume': _FAKE_VOLUME,
                      'status': 'available',
                      'volume_size': 1,
                      'volume_id': _FAKE_VOLUME['id']}
    _FAKE_SNAPSHOT_PATH = (_FAKE_VOLUME_PATH + '.' + _FAKE_SNAPSHOT_ID)

    _FAKE_SNAPSHOT_INFO = {'status': 'available'}
    _FAKE_BACKING_FILENAME = 'fake_backing_filename'
    _FAKE_INFO_PATH = 'fake_info_path'

    def setUp(self):
        super(remoteFsDriverTestCase, self).setUp()
        self._driver = remotefs.RemoteFSSnapDriver()
        self._driver._nova = mock.MagicMock()

    def test_do_create_snapshot(self):
        fake_backing_fmt = 'fake_backing_fmt'

        self._driver._local_volume_dir = mock.MagicMock(
            return_value=self._FAKE_VOLUME_PATH)
        fake_backing_path_full = os.path.join(
            self._driver._local_volume_dir(),
            self._FAKE_BACKING_FILENAME)

        self._driver._execute = mock.MagicMock()
        self._driver._set_rw_permissions_for_all = mock.MagicMock()
        self._driver._qemu_img_info = mock.MagicMock(
            return_value=mock.MagicMock(file_format=fake_backing_fmt))

        self._driver._do_create_snapshot(self._FAKE_SNAPSHOT,
                                         self._FAKE_BACKING_FILENAME,
                                         self._FAKE_SNAPSHOT_PATH)
        command1 = ['qemu-img', 'create', '-f', 'qcow2', '-o',
                    'backing_file=%s' % fake_backing_path_full,
                    self._FAKE_SNAPSHOT_PATH]
        command2 = ['qemu-img', 'rebase', '-u',
                    '-b', self._FAKE_BACKING_FILENAME,
                    '-F', fake_backing_fmt,
                    self._FAKE_SNAPSHOT_PATH]

        self._driver._execute.assert_any_call(*command1, run_as_root=True)
        self._driver._execute.assert_any_call(*command2, run_as_root=True)


    def test_create_snapsthot_offline(self):
        self._driver._local_path_volume_info = mock.MagicMock(
            return_value=self._FAKE_INFO_PATH)
        self._driver._read_info_file = mock.MagicMock(
            return_value=self._FAKE_SNAPSHOT_INFO)
        self._driver._do_create_snapshot = mock.MagicMock()
        self._driver._write_info_file = mock.MagicMock()
        self._driver.get_active_image_from_info = mock.MagicMock(
            return_value=self._FAKE_BACKING_FILENAME)
        self._driver._get_new_snap_path = mock.MagicMock(
            return_value=self._FAKE_SNAPSHOT_PATH)

        self._driver._create_snapshot(self._FAKE_SNAPSHOT)

        self._driver._do_create_snapshot.assert_called_with(
            self._FAKE_SNAPSHOT, self._FAKE_BACKING_FILENAME,
            self._FAKE_SNAPSHOT_PATH)
        self._driver._write_info_file.assert_called_with(self._FAKE_INFO_PATH,
            self._FAKE_SNAPSHOT_INFO)

    def test_create_snapshot_invalid_volume(self):
        fake_snapshot_copy = copy.deepcopy(self._FAKE_SNAPSHOT)
        fake_snapshot_copy['volume']['status'] = 'error'

        self.assertRaises(exception.InvalidVolume,
                          self._driver._create_snapshot,
                          fake_snapshot_copy)
