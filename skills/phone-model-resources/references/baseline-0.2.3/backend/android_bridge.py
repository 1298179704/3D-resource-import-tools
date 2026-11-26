"""ADB transport with streamed files and recoverable Android directory updates."""
import base64
from configuration_export import export_resource
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import tarfile
import tempfile
import time
import uuid
import zipfile


class AndroidBridge:
    def __init__(self, state):
        self.state = Path(state)
        self.adb = os.environ.get('RESOURCE_TOOL_ADB', '')

    def event(self, name, **fields):
        try:
            with (self.state / 'android-events.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps({'time': time.time(), 'event': name, **fields}, ensure_ascii=False) + '\n')
        except OSError:
            pass

    def command_bytes(self, *args, serial=None, timeout=180, input_data=None):
        if not self.adb or not Path(self.adb).is_file():
            raise ValueError('软件包缺少 ADB 组件，请重新完整解压')
        prefix = [self.adb]
        if serial:
            if not re.fullmatch(r'[A-Za-z0-9._:-]+', serial):
                raise ValueError('设备标识无效')
            prefix += ['-s', serial]
        result = subprocess.run(prefix + list(args), input=input_data, capture_output=True, timeout=timeout, creationflags=0x08000000 if os.name == 'nt' else 0)
        if result.returncode:
            message = result.stderr.decode('utf-8', errors='replace').strip()[-1200:]
            self.event('adb-command-failed', command=args[0], message=message.replace(serial, '[device]') if serial else message)
            raise ValueError(message or 'USB 操作失败，请检查手机授权和数据线')
        return result.stdout

    def command(self, *args, serial=None, timeout=180):
        return self.command_bytes(*args, serial=serial, timeout=timeout).decode('utf-8', errors='replace')

    def devices(self):
        rows = []
        for line in self.command('devices', '-l', timeout=15).splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                rows.append({'serial': parts[0], 'state': parts[1], 'model': next((p[6:] for p in parts[2:] if p.startswith('model:')), parts[0])})
        return rows

    def root(self):
        profile = json.loads((self.state / 'service-profile.json').read_text(encoding='utf-8'))
        package = profile.get('android_package', '')
        if not re.fullmatch(r'[A-Za-z0-9_.]+', package):
            raise ValueError('缺少安卓应用配置')
        return f'/sdcard/Android/data/{package}/files/unity/dyna_3d_res'

    def shell(self, serial, script):
        return self.command('shell', '-T', script, serial=serial)

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

    def read_folder(self, serial, target, folder):
        folder.mkdir(parents=True, exist_ok=False)
        payload = self.command_bytes('exec-out', 'tar -cf - -C ' + shlex.quote(target) + ' .', serial=serial)
        total = 0
        seen = set()
        with tarfile.open(fileobj=io.BytesIO(payload), mode='r:') as archive:
            for member in archive:
                path = PurePosixPath(member.name)
                if path.is_absolute() or '..' in path.parts or '\\' in member.name or ':' in member.name:
                    raise ValueError('手机资源路径无效')
                if member.isdir():
                    continue
                if not member.isfile():
                    raise ValueError('手机资源包含不支持的链接或特殊文件')
                normalized = path.as_posix().casefold()
                if normalized in seen:
                    raise ValueError('资源文件名大小写冲突，无法完整保存到电脑')
                seen.add(normalized)
                total += member.size
                if total > 1024**3:
                    raise ValueError('手机资源超过 1 GB')
                destination = folder.joinpath(*path.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError('手机资源读取不完整')
                destination.write_bytes(stream.read())
        if not (folder / 'resources.json').is_file():
            raise ValueError('资源读取不完整，请检查手机授权')

    def capture(self, serial, target, configuration=None):
        target = self.target(serial, target)
        self.event('capture-started', device=hashlib.sha256(serial.encode()).hexdigest()[:16], targetName=PurePosixPath(target).name)
        with tempfile.TemporaryDirectory(prefix='resource-', dir=self.state) as directory:
            folder = Path(directory) / 'resource'
            self.read_folder(serial, target, folder)
            payload = self.pack(folder)
        payload, status = export_resource(payload, 'android', configuration)
        temporary = self.state / 'android-original.tmp'
        temporary.write_bytes(payload)
        temporary.replace(self.state / 'android-original.zip')
        self.event('capture-completed', bytes=len(payload), md5=hashlib.md5(payload).hexdigest())
        return {'bytes': len(payload), 'md5': hashlib.md5(payload).hexdigest(), 'filename': PurePosixPath(target).name, 'configuration': status}

    def write_folder(self, serial, folder, target):
        files = sorted((p for p in folder.rglob('*') if p.is_file()), key=lambda p: (p.suffix == '.json', str(p)))
        for file in files:
            remote = target + '/' + file.relative_to(folder).as_posix()
            self.shell(serial, 'mkdir -p ' + shlex.quote(str(PurePosixPath(remote).parent)))
            self.command_bytes('shell', '-T', 'base64 -d > ' + shlex.quote(remote), serial=serial, input_data=base64.b64encode(file.read_bytes()))
        for file in files:
            remote = target + '/' + file.relative_to(folder).as_posix()
            digest = hashlib.md5(file.read_bytes()).hexdigest()
            actual = self.shell(serial, 'md5sum ' + shlex.quote(remote)).split()
            if not actual or digest != actual[0]:
                size = None
                try:
                    size = int(self.shell(serial, 'wc -c < ' + shlex.quote(remote)).strip())
                except (ValueError, OSError):
                    pass
                self.event('write-verification-failed', actualBytes=size, file=file.relative_to(folder).as_posix(), expectedBytes=file.stat().st_size, expectedMd5=digest, actualMd5=actual[0] if actual else None)
                raise ValueError('手机资源写入校验失败：' + file.relative_to(folder).as_posix() + '。请保存日志。')

    def save_json(self, path, data):
        temporary = path.with_suffix('.tmp')
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)

    def recover(self, serial, target, journal, backup, directory):
        transaction = json.loads(journal.read_text(encoding='utf-8'))
        if transaction.get('target') != target or transaction.get('backupSha256') != hashlib.sha256(backup.read_bytes()).hexdigest():
            raise ValueError('中断备份校验失败，请保存日志后联系支持')
        original = Path(directory) / ('rollback-' + uuid.uuid4().hex)
        with zipfile.ZipFile(backup) as archive:
            archive.extractall(original)
        self.write_folder(serial, original, target)
        for relative in transaction.get('addedFiles', []):
            self.shell(serial, 'rm -f ' + shlex.quote(target + '/' + relative))
        journal.unlink()
        self.event('interrupted-write-restored')

    def apply(self, serial, target, payload, restore=False):
        target = self.target(serial, target)
        identity = hashlib.sha256((serial + '\n' + target).encode()).hexdigest()
        self.event('apply-started', device=hashlib.sha256(serial.encode()).hexdigest()[:16], targetName=PurePosixPath(target).name, packageMd5=hashlib.md5(payload).hexdigest())
        backups = self.state / 'android-backups'
        backups.mkdir(exist_ok=True)
        backup = backups / (identity + '.zip')
        journal = backups / (identity + '.json')
        if restore:
            if not backup.exists():
                raise ValueError('该手机车型尚无本地备份')
            payload = backup.read_bytes()
        with tempfile.TemporaryDirectory(prefix='resource-', dir=self.state) as directory:
            folder = Path(directory) / 'resource'
            folder.mkdir()
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                names = archive.namelist()
                if len(names) != len({n.casefold() for n in names}) or any('..' in PurePosixPath(n).parts or '\\' in n or n.startswith('/') or ':' in n for n in names):
                    raise ValueError('ZIP 文件路径无效')
                if sum(i.file_size for i in archive.infolist()) > 1024**3 or archive.testzip():
                    raise ValueError('ZIP 完整性或大小校验失败')
                from bundle_check import ios_bundle, validate_inventory
                validate_inventory(archive)
                bundles = [n for n in names if n.endswith('.ab')]
                if not bundles or any(not ios_bundle(archive.read(n), platform=13) for n in bundles):
                    raise ValueError('请选择 Android 平台资源包')
                archive.extractall(folder)
            profile = json.loads((self.state / 'service-profile.json').read_text(encoding='utf-8'))
            stage = target + '.helper-stage-' + uuid.uuid4().hex
            old = target + '.helper-backup'
            q = shlex.quote
            staged = True
            try:
                self.shell(serial, 'mkdir ' + q(stage))
            except ValueError as stage_error:
                staged = False
                probe = target + '/.helper-write-' + uuid.uuid4().hex
                try:
                    self.shell(serial, 'touch ' + q(probe) + ' && rm -f ' + q(probe))
                except ValueError as write_error:
                    raise ValueError('手机拒绝写入该车型目录，尚未开始替换。请检查 USB 授权并保存日志。\n' + str(stage_error) + '\n' + str(write_error)) from write_error
            self.event('write-strategy-selected', strategy='staged-swap' if staged else 'backed-up-in-place')
            self.shell(serial, 'am force-stop ' + q(profile['android_package']))
            if journal.exists():
                try:
                    self.recover(serial, target, journal, backup, directory)
                except Exception as error:
                    if staged:
                        try:
                            self.shell(serial, 'rm -rf ' + q(stage))
                        except Exception:
                            pass
                    raise ValueError('上次写入中断，恢复备份未完成。请保持原手机连接后重试。\n' + str(error)) from error
            original = Path(directory) / 'original'
            try:
                self.read_folder(serial, target, original)
                original_payload = self.pack(original)
                temporary = backup.with_suffix('.tmp')
                with temporary.open('wb') as stream:
                    stream.write(original_payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(backup)
                if staged:
                    self.shell(serial, f'if [ -e {q(old)} ]; then test -f {q(target + "/resources.json")} && rm -rf {q(old)} || exit 1; fi')
                    self.write_folder(serial, folder, stage)
                    self.shell(serial, f'mv {q(target)} {q(old)} && (mv {q(stage)} {q(target)} || {{ mv {q(old)} {q(target)}; exit 1; }})')
                    try:
                        self.shell(serial, 'rm -rf ' + q(old))
                    except ValueError:
                        pass  # Preserve the recoverable old directory if cleanup is denied.
                else:
                    added = [f.relative_to(folder).as_posix() for f in folder.rglob('*') if f.is_file() and not (original / f.relative_to(folder)).exists()]
                    self.save_json(journal, {'target': target, 'backupSha256': hashlib.sha256(original_payload).hexdigest(), 'addedFiles': added})
                    try:
                        self.write_folder(serial, folder, target)
                        for previous in original.rglob('*'):
                            if previous.is_file() and not (folder / previous.relative_to(original)).is_file():
                                self.shell(serial, 'rm -f ' + q(target + '/' + previous.relative_to(original).as_posix()))
                    except Exception as error:
                        try:
                            self.recover(serial, target, journal, backup, directory)
                        except Exception as rollback_error:
                            raise ValueError('写入中断，自动恢复尚未完成。备份已保留，请重新连接原手机后重试。\n' + str(error) + '\n' + str(rollback_error)) from error
                        raise ValueError('写入失败，已恢复操作前资源。\n' + str(error)) from error
                    journal.unlink()
            except Exception:
                if staged:
                    try:
                        self.shell(serial, 'rm -rf ' + q(stage))
                    except Exception:
                        pass
                raise
        self.event('apply-completed', md5=hashlib.md5(payload).hexdigest())
        return {'message': '资源已恢复，请重新打开手机应用' if restore else '资源已写入并校验，请重新打开手机应用', 'md5': hashlib.md5(payload).hexdigest()}
