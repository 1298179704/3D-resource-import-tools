"""No devices are used. Emulate Android permissions, interruption and binary pipes."""
import hashlib
import io
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
sys.path.insert(0, 'backend')
from android_bridge import AndroidBridge

class FakePhone(AndroidBridge):
    def __init__(self, state, phone, staged=True, writable=True):
        super().__init__(state)
        self.phone=phone
        self.target_path='/sdcard/Android/data/com.example.app/files/unity/dyna_3d_res/0.4.0/ec6_v1_0.4.0'
        self.staged=staged
        self.writable=writable
        self.commands=[]
        self.fail_write=False
        self.disconnected=False
        self.disconnect_on_write=False
        self.stopped=False
    def scan(self, serial):
        return [self.target_path]
    def command_bytes(self, *args, serial=None, timeout=180, input_data=None):
        assert serial=='test-device'
        assert args[0] not in ('pull','push'), 'Local filenames must never be handed to adb'
        assert str(self.state) not in ' '.join(args)
        self.commands.append(args)
        code=args[-1]
        translated=code.replace(self.target_path,shlex.quote(str(self.phone)))
        if self.disconnected:
            raise ValueError('USB disconnected')
        if code.startswith('am force-stop'):
            self.stopped=True
            return b''
        if code.startswith('mkdir ') and '.helper-stage-' in code and not self.staged:
            raise ValueError('remote secure_mkdirs failed: Operation not permitted')
        if code.startswith('touch ') and not self.writable:
            raise ValueError('Operation not permitted')
        if code.startswith('md5sum '):
            file=Path(shlex.split(translated)[1])
            return (hashlib.md5(file.read_bytes()).hexdigest()+'  file\n').encode()
        if code.startswith('cat > ') and self.fail_write:
            file=Path(shlex.split(translated)[2])
            file.write_bytes((input_data or b'')[:3])
            self.fail_write=False
            self.disconnected=self.disconnect_on_write
            raise ValueError('failed to read copy response: EOF')
        result=subprocess.run(['sh','-c',translated],input=input_data,capture_output=True)
        if result.returncode:
            raise ValueError(result.stderr.decode())
        return result.stdout

def package(content, extra=False):
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as archive:
        names=['mainscene.ab']+(['extra.ab'] if extra else [])
        archive.writestr('resources.json',json.dumps({'resVersion':'0.4.0.24','sceneBundles':names}))
        archive.writestr('mainscene.ab',content)
        if extra:archive.writestr('extra.ab',b'ANDROID extra')
    return buffer.getvalue()

class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='ADB测试 ')
        self.root=Path(self.temp.name)
        self.state=self.root/'用户 中文目录'
        self.state.mkdir()
        (self.state/'service-profile.json').write_text(json.dumps({'android_package':'com.example.app'}))
        self.phone=self.root/'phone'
        self.phone.mkdir()
        self.old=b'ANDROID original\x00\r\n\xff'
        with zipfile.ZipFile(io.BytesIO(package(self.old))) as archive:archive.extractall(self.phone)
        self.validator=patch('bundle_check.ios_bundle',lambda data,platform=9: platform==13 and data.startswith(b'ANDROID'))
        self.validator.start()
    def tearDown(self):
        self.validator.stop()
        self.temp.cleanup()
    def test_unicode_capture_uses_binary_pipe(self):
        bridge=FakePhone(self.state,self.phone)
        bridge.capture('test-device',bridge.target_path)
        with zipfile.ZipFile(self.state/'android-original.zip') as archive:
            self.assertEqual(archive.read('mainscene.ab'),self.old)
            self.assertIn('resources.json',archive.namelist())
    def test_atomic_stage_and_binary_integrity(self):
        bridge=FakePhone(self.state,self.phone)
        content=b'ANDROID new\x00\r\n\xff'
        bridge.apply('test-device',bridge.target_path,package(content))
        self.assertEqual((self.phone/'mainscene.ab').read_bytes(),content)
        self.assertTrue(list((self.state/'android-backups').glob('*.zip')))
    def test_parent_denied_existing_directory_supported(self):
        bridge=FakePhone(self.state,self.phone,staged=False)
        (self.phone/'obsolete.ab').write_bytes(b'ANDROID obsolete')
        bridge.apply('test-device',bridge.target_path,package(b'ANDROID fallback',extra=True))
        self.assertFalse((self.phone/'obsolete.ab').exists())
        self.assertEqual((self.phone/'mainscene.ab').read_bytes(),b'ANDROID fallback')
        self.assertTrue((self.phone/'extra.ab').exists())
        self.assertFalse(list((self.state/'android-backups').glob('*.json')))
    def test_total_permission_denial_does_not_stop_or_modify(self):
        bridge=FakePhone(self.state,self.phone,staged=False,writable=False)
        with self.assertRaisesRegex(ValueError,'尚未开始替换'):
            bridge.apply('test-device',bridge.target_path,package(b'ANDROID replacement'))
        self.assertFalse(bridge.stopped)
        self.assertEqual((self.phone/'mainscene.ab').read_bytes(),self.old)
    def test_partial_write_rolls_back_original_bytes(self):
        bridge=FakePhone(self.state,self.phone,staged=False)
        bridge.fail_write=True
        with self.assertRaisesRegex(ValueError,'已恢复操作前资源'):
            bridge.apply('test-device',bridge.target_path,package(b'ANDROID bad-transfer',extra=True))
        self.assertEqual((self.phone/'mainscene.ab').read_bytes(),self.old)
        self.assertFalse((self.phone/'extra.ab').exists())
        self.assertFalse(list((self.state/'android-backups').glob('*.json')))

    def test_disconnect_keeps_backup_and_recovers_before_retry(self):
        bridge=FakePhone(self.state,self.phone,staged=False)
        bridge.fail_write=True
        bridge.disconnect_on_write=True
        with self.assertRaisesRegex(ValueError,'备份已保留'):
            bridge.apply('test-device',bridge.target_path,package(b'ANDROID interrupted',extra=True))
        self.assertTrue(list((self.state/'android-backups').glob('*.json')))
        bridge.disconnected=False
        bridge.disconnect_on_write=False
        bridge.apply('test-device',bridge.target_path,package(b'ANDROID retried'))
        self.assertEqual((self.phone/'mainscene.ab').read_bytes(),b'ANDROID retried')
        self.assertFalse(list((self.state/'android-backups').glob('*.json')))
        with zipfile.ZipFile(next((self.state/'android-backups').glob('*.zip'))) as archive:
            self.assertEqual(archive.read('mainscene.ab'),self.old)

if __name__=='__main__':unittest.main()
