"""ADB transport. Every command targets the explicitly selected device."""
from configuration_export import export_resource
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import tempfile
import uuid
import zipfile

class AndroidBridge:
    def __init__(self, state):
        self.state = Path(state)
        self.adb = os.environ.get('RESOURCE_TOOL_ADB', '')

    def command(self, *args, serial=None, timeout=180):
        if not self.adb or not Path(self.adb).is_file():
            raise ValueError('软件包缺少 ADB 组件，请重新完整解压')
        prefix = [self.adb]
        if serial:
            if not re.fullmatch(r'[A-Za-z0-9._:-]+', serial):
                raise ValueError('设备标识无效')
            prefix += ['-s', serial]
        result = subprocess.run(prefix + list(args), capture_output=True, timeout=timeout, creationflags=0x08000000 if os.name == 'nt' else 0)
        if result.returncode:
            raise ValueError(result.stderr.decode(errors='replace').strip()[-1200:] or 'USB 操作失败，请检查手机授权')
        return result.stdout.decode(errors='replace')

    def devices(self):
        rows = []
        for line in self.command('devices', '-l', timeout=15).splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                rows.append({'serial': parts[0], 'state': parts[1], 'model': next((p[6:] for p in parts[2:] if p.startswith('model:')), parts[0])})
        return rows

    def root(self):
        profile = json.loads((self.state / 'service-profile.json').read_text())
        package = profile.get('android_package', '')
        if not re.fullmatch(r'[A-Za-z0-9_.]+', package):
            raise ValueError('缺少安卓应用配置')
        return f'/sdcard/Android/data/{package}/files/unity/dyna_3d_res'

    def shell(self, serial, script):
        return self.command('shell', script, serial=serial)

    def scan(self, serial):
        root = self.root()
        self.shell(serial, 'find ' + shlex.quote(root) + " -type d -name '*.helper-backup' | while IFS= read -r old; do target=${old%.helper-backup}; if [ ! -e \"$target\" ]; then mv \"$old\" \"$target\" || exit 1; fi; done")
        output = self.shell(serial, 'find ' + shlex.quote(root) + ' -name resources.json -type f')
        return sorted({str(PurePosixPath(p).parent) for p in output.splitlines() if p.startswith(root + '/') and p.endswith('/resources.json') and '.helper-backup/' not in p and '.helper-stage-' not in p})

    def target(self, serial, target):
        if target not in self.scan(serial):
            raise ValueError('目标车型目录已变化，请重新扫描')
        return target

    def pack(self, folder):
        result = io.BytesIO()
        with zipfile.ZipFile(result, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for file in sorted(folder.rglob('*')):
                if file.is_file():
                    archive.write(file, file.relative_to(folder).as_posix())
        return result.getvalue()

    def capture(self, serial, target, configuration=None):
        target = self.target(serial, target)
        with tempfile.TemporaryDirectory(dir=self.state) as directory:
            folder = Path(directory) / 'resource'
            self.command('pull', target, str(folder), serial=serial)
            if not (folder / 'resources.json').is_file():
                raise ValueError('资源读取不完整')
            payload = self.pack(folder)
        payload, configuration_status = export_resource(payload, 'android', configuration)
        temporary = self.state / 'android-original.tmp'
        temporary.write_bytes(payload)
        temporary.replace(self.state / 'android-original.zip')
        return {'bytes': len(payload), 'md5': hashlib.md5(payload).hexdigest(), 'filename': PurePosixPath(target).name, 'configuration': configuration_status}

    def apply(self, serial, target, payload, restore=False):
        target = self.target(serial, target)
        identity = hashlib.sha256((serial + '\n' + target).encode()).hexdigest()
        backup = self.state / 'android-backups' / (identity + '.zip')
        backup.parent.mkdir(exist_ok=True)
        if restore:
            if not backup.exists():
                raise ValueError('这台手机的该车型尚无本地原始备份')
            payload = backup.read_bytes()
        with tempfile.TemporaryDirectory(dir=self.state) as directory:
            folder = Path(directory) / 'resource'
            folder.mkdir()
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                names = archive.namelist()
                if len(names) != len(set(names)) or any('..' in PurePosixPath(n).parts or '\\' in n or n.startswith('/') or ':' in n for n in names):
                    raise ValueError('ZIP 文件路径无效')
                if sum(i.file_size for i in archive.infolist()) > 1024**3 or archive.testzip():
                    raise ValueError('ZIP 完整性或大小校验失败')
                from bundle_check import ios_bundle, validate_inventory
                validate_inventory(archive)
                bundles = [name for name in names if name.endswith('.ab')]
                if not bundles or any(not ios_bundle(archive.read(name), platform=13) for name in bundles):
                    raise ValueError('请选择 Android 平台资源包')
                archive.extractall(folder)
            profile = json.loads((self.state / 'service-profile.json').read_text())
            self.command('shell', 'am', 'force-stop', profile['android_package'], serial=serial)
            stage = target + '.helper-stage-' + uuid.uuid4().hex
            old = target + '.helper-backup'
            q = shlex.quote
            # A surviving backup from an interrupted operation must be resolved first.
            self.shell(serial, f'if [ -e {q(old)} ]; then test -f {q(target + chr(47) + "resources.json")} && rm -rf {q(old)} || exit 1; fi')
            try:
                self.command('push', str(folder), stage, serial=serial)
                for file in folder.rglob('*'):
                    if file.is_file():
                        remote = stage + '/' + file.relative_to(folder).as_posix()
                        digest = hashlib.md5(file.read_bytes()).hexdigest()
                        actual = self.shell(serial, 'md5sum ' + q(remote)).split()[0]
                        if digest != actual:
                            raise ValueError('手机资源写入校验失败')
                self.shell(serial, f'mv {q(target)} {q(old)} && (mv {q(stage)} {q(target)} || {{ mv {q(old)} {q(target)}; exit 1; }})')
            except Exception:
                self.shell(serial, 'rm -rf ' + q(stage))
                raise
            self.shell(serial, 'rm -rf ' + q(old))
        return {'message': '资源已恢复，请重新打开手机应用' if restore else '资源已写入并校验，请重新打开手机应用', 'md5': hashlib.md5(payload).hexdigest()}
