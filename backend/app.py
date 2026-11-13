"""Local-only iPhone resource installer. Run with Python 3.12+."""
import base64
from android_bridge import AndroidBridge
import atexit
import signal
import re
import hashlib
import html
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
import urllib.request
import urllib.error
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
FROZEN = bool(getattr(sys, 'frozen', False))
DISTRIBUTION = Path(sys.executable).resolve().parents[3] if FROZEN and sys.platform == 'darwin' else Path(sys.executable).resolve().parent
DEFAULT_DATA = (Path.home() / 'Library' / 'Application Support' / 'ResourceTool') if sys.platform == 'darwin' else Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'ResourceTool'
STATE = Path(os.environ.get('RESOURCE_TOOL_DATA', str(DEFAULT_DATA if FROZEN else ROOT / 'data'))).resolve()
STATE.mkdir(parents=True, exist_ok=True)
if not (STATE / 'service-profile.json').exists() and (ROOT / 'service-profile.json').exists():
    shutil.copyfile(ROOT / 'service-profile.json', STATE / 'service-profile.json')
VERSION = '2026-11-13-resource-toolkit-0.2.1'
TOKEN = os.environ.get('RESOURCE_TOOL_CONTROL_TOKEN') or secrets.token_urlsafe(24)
PHONE_TOKEN = secrets.token_urlsafe(24)
LOCK = threading.RLock()
PROCESS = None
INFO = {}
PORT = 18900
PROXY_PORT = 18999
LOG = STATE / 'proxy.log'
LOG_HANDLE = None
STOPPED_AT = 0


def local_ip():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        try:
            connection.connect(('192.0.2.1', 80))
            return connection.getsockname()[0]
        except OSError:
            return '127.0.0.1'


def validate(payload):
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or any('..' in Path(n).parts or n.startswith(('/', '\\')) for n in names):
            raise ValueError('ZIP 路径或重复文件不合法')
        if sum(item.file_size for item in archive.infolist()) > 1024 * 1024 * 1024:
            raise ValueError('解压内容超过 1 GB')
        if archive.testzip():
            raise ValueError('ZIP 完整性检查失败')
        from bundle_check import ios_bundle, validate_inventory
        validate_inventory(archive)
        for name in names:
            if name.endswith('.ab'):
                if not ios_bundle(archive.read(name)):
                    raise ValueError(name + ' 不是 iOS 资源，请先转换设计')
    return {'bytes': len(payload), 'md5': hashlib.md5(payload).hexdigest(), 'files': len(names)}


def stop():
    global PROCESS, LOG_HANDLE, STOPPED_AT
    with LOCK:
        if PROCESS and PROCESS.poll() is None:
            PROCESS.terminate()
            try:
                PROCESS.wait(timeout=5)
            except subprocess.TimeoutExpired:
                PROCESS.kill()
                PROCESS.wait()
        PROCESS = None
        STOPPED_AT = time.time()
        if LOG_HANDLE:
            LOG_HANDLE.close()
            LOG_HANDLE = None


def start(mode="install"):
    global PROCESS, LOG_HANDLE
    with LOCK:
        stop()
        if mode == "install" and not INFO:
            raise ValueError('请先选择 iOS 安装 ZIP')
        bundled = Path(sys.executable).parent / ('mitmdump.exe' if os.name == 'nt' else 'mitmdump')
        packaged_proxy = DISTRIBUTION / 'runtime' / ('mitmproxy.app/Contents/MacOS/mitmdump' if sys.platform == 'darwin' else 'mitmdump.exe')
        internal_proxy = Path(sys.executable).resolve().parent.parent / 'Resources' / 'proxy-runtime' / 'mitmproxy.app' / 'Contents' / 'MacOS' / 'mitmdump'
        if FROZEN and sys.platform == 'darwin' and internal_proxy.exists():
            packaged_proxy = internal_proxy
        executable = str(packaged_proxy) if packaged_proxy.exists() else (str(bundled) if bundled.exists() else shutil.which('mitmdump'))
        portable_python = ROOT / 'runtime' / 'python.exe'
        if os.name == 'nt' and portable_python.exists():
            executable = str(portable_python)
        if not executable:
            raise ValueError('软件包缺少内置代理组件，请重新下载并完整解压最新版助手')
        if not Path(executable).exists():
            raise ValueError('软件包缺少内置代理组件，请重新下载并完整解压最新版助手')
        shutil.copyfile(ROOT / 'proxy_addon.py', STATE / 'proxy_addon.py')
        shutil.copyfile(ROOT / 'resource_capture.py', STATE / 'resource_capture.py')
        if mode == 'capture':
            (STATE / 'capture-status.json').unlink(missing_ok=True)
            (STATE / 'official-download.zip').unlink(missing_ok=True)
        profile_path = STATE / 'service-profile.json'
        if not profile_path.exists():
            raise ValueError('缺少服务适配配置，请联系工具提供者')
        profile = json.loads(profile_path.read_text())
        allowed = '^(' + '|'.join(re.escape(profile[k]) for k in ('metadata_host', 'download_host')) + '):443$'
        (STATE / 'connection-status.json').unlink(missing_ok=True)
        (STATE / 'install-control.json').unlink(missing_ok=True)
        LOG_HANDLE = LOG.open('w', encoding='utf-8')
        prefix = [executable, str(ROOT / 'proxy_runner.py')] if os.name == 'nt' and portable_python.exists() else [executable]
        PROCESS = subprocess.Popen(prefix + ['--listen-host', local_ip(), '--listen-port', str(PROXY_PORT), '--set', 'confdir=' + str(STATE / 'certificates'), '--set', 'flow_detail=0', '--allow-hosts', allowed, '-s', str(STATE / 'proxy_addon.py')], stdout=LOG_HANDLE, stderr=subprocess.STDOUT, env={**os.environ, 'PYTHONUNBUFFERED': '1', 'RESOURCE_TOOL_MODE': mode, 'RESOURCE_TOOL_PROBE_TOKEN': PHONE_TOKEN})
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if PROCESS.poll() is not None:
                raise ValueError('代理启动失败，请查看日志。端口可能被占用。')
            if (STATE / 'certificates' / 'mitmproxy-ca-cert.cer').exists():
                try:
                    with socket.create_connection((local_ip(), PROXY_PORT), timeout=0.3):
                        return
                except OSError:
                    pass
            time.sleep(0.2)
        stop()
        raise ValueError('代理启动超时，请查看日志。')


def confirm_installation(action, data):
    if not PROCESS or PROCESS.poll() is not None:
        raise ValueError('代理已停止，请重新开始。')
    status = json.loads((STATE / 'connection-status.json').read_text(encoding='utf-8'))
    transitions = {
        'confirm-model': ('trigger', 'await-model-confirmation', 'triggerPreparedAt'),
        'confirm-final': ('final', 'await-final-confirmation', 'finalPreparedAt'),
        'confirm-keep': ('verify', 'await-keep-confirmation', 'verificationStartedAt'),
    }
    phase, stage, timestamp = transitions[action]
    if data.get('session') != status.get('session') or status.get('selectedMd5') != INFO.get('md5'):
        raise ValueError('资源任务已更新，请按当前页面继续。')
    if data.get('confirmed') is not True:
        raise ValueError('请先确认手机上的模型显示结果。')
    if status.get('phase') != phase or status.get('installStage') != stage:
        raise ValueError(status.get('installError') or '请先完成当前手机操作。')
    if time.time() - status.get(timestamp, time.time()) < 15:
        raise ValueError('请在手机模型页面至少停留 15 秒，并确认外观。')
    control = STATE / 'install-control.json'
    if control.exists():
        previous = json.loads(control.read_text())
        if previous.get('session') == status['session'] and previous.get('action') == action:
            return {'queued': True}
    command = {'id': secrets.token_hex(16), 'session': status['session'], 'payloadMd5': INFO['md5'], 'action': action}
    temporary = control.with_suffix('.tmp')
    temporary.write_text(json.dumps(command), encoding='utf-8')
    temporary.replace(control)
    return {'queued': True}


def recent_events(name):
    try:
        lines = (STATE / name).read_text(encoding='utf-8', errors='replace').splitlines()[-200:]
    except OSError:
        return []
    events = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


PAGE = (ROOT / 'ui.html').read_text(encoding='utf-8')
if os.environ.get('RESOURCE_TOOL_DESKTOP'):
    PAGE = PAGE.replace('</style>', 'header{display:none}main{margin-top:36px}</style>')


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, body, content_type='text/html; charset=utf-8'):
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        if content_type == 'application/json' and self.path.startswith('/api/diagnostics'):
            self.send_header('Content-Disposition', 'attachment; filename=helper-diagnostics.json')
        if content_type == 'application/zip':
            self.send_header('Content-Disposition', 'attachment; filename=iphone-original.zip')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def route(self):
        parsed = urlsplit(self.path)
        key = parse_qs(parsed.query).get('key', [''])[0]
        admin = self.client_address[0] in ('127.0.0.1', '::1') and secrets.compare_digest(key, TOKEN)
        phone = secrets.compare_digest(key, PHONE_TOKEN)
        return parsed.path, admin, phone

    def do_GET(self):
        path, admin, phone = self.route()
        if path in ('/phone', '/certificate') and phone:
            if path == '/certificate':
                certificate = STATE / 'certificates/mitmproxy-ca-cert.cer'
                if not certificate.exists():
                    return self.reply(503, '请先在电脑点击开始应用，然后重试。')
                return self.reply(200, certificate.read_bytes(), 'application/x-x509-ca-cert')
            ip = html.escape(local_ip())
            page = (ROOT / 'phone.html').read_text(encoding='utf-8')
            page = page.replace('__IP__', ip).replace('__PORT__', str(PROXY_PORT)).replace('__PHONE_TOKEN__', PHONE_TOKEN)
            return self.reply(200, page)
        if not admin:
            return self.reply(403, '此链接无效，请从本机启动工具。')
        if path == '/':
            return self.reply(200, PAGE)
        if path == '/qr':
            import qrcode
            import qrcode.image.svg
            qr = qrcode.make(f'http://{local_ip()}:{PORT}/phone?key={PHONE_TOKEN}', image_factory=qrcode.image.svg.SvgPathImage)
            output = io.BytesIO()
            qr.save(output)
            return self.reply(200, output.getvalue(), 'image/svg+xml')
        if path == '/api/diagnostics':
            report = {'version': VERSION, 'running': bool(PROCESS and PROCESS.poll() is None), 'address': local_ip(), 'proxyPort': PROXY_PORT}
            for name in ('capture-status', 'connection-status'):
                file = STATE / (name + '.json')
                report[name] = json.loads(file.read_text(encoding='utf-8')) if file.exists() else None
            profile = json.loads((STATE / 'service-profile.json').read_text()) if (STATE / 'service-profile.json').exists() else {}
            report['selectedPackage'] = INFO
            report['installationEvents'] = recent_events('install-events.jsonl')
            report['androidEvents'] = recent_events('android-events.jsonl')
            return self.reply(200, json.dumps(report, ensure_ascii=False, indent=2), 'application/json')
        if path == '/api/android-original':
            resource = STATE / 'android-original.zip'
            if not resource.exists():
                return self.reply(404, '尚未获取安卓资源')
            return self.reply(200, resource.read_bytes(), 'application/zip')
        if path == '/api/original':
            resource = STATE / 'official-download.zip'
            if not resource.exists():
                return self.reply(404, '尚未取得官方资源')
            payload = resource.read_bytes()
            try:
                with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                    if archive.testzip():
                        raise ValueError('ZIP 完整性检查失败')
            except Exception:
                return self.reply(422, '资源包完整性检查失败，请停止后重试')
            return self.reply(200, payload, 'application/zip')
        if path == '/api/status':
            with LOCK:
                running = bool(PROCESS and PROCESS.poll() is None)
                log = LOG.read_text(errors='replace')[-12000:] if LOG.exists() else ''
                return self.reply(200, json.dumps({'version': VERSION, 'diagnostics': json.loads((STATE / 'connection-status.json').read_text(encoding='utf-8')) if (STATE / 'connection-status.json').exists() else {}, 'probeUrl': 'http://local-helper.invalid/check?key=' + PHONE_TOKEN, 'package': INFO, 'running': running, 'stoppedAt': STOPPED_AT, 'phone': f'http://{local_ip()}:{PORT}/phone?key={PHONE_TOKEN}', 'log': log, 'capture': json.loads((STATE / 'capture-status.json').read_text(encoding='utf-8')) if (STATE / 'capture-status.json').exists() else None}), 'application/json')
        return self.reply(404, '未找到')

    def do_POST(self):
        global INFO
        path, admin, _ = self.route()
        if not admin:
            return self.reply(403, '{}', 'application/json')
        try:
            with LOCK:
                if path == '/api/confirm-disconnected':
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 0 < size <= 2048:
                        raise ValueError('确认信息无效')
                    data = json.loads(self.rfile.read(size))
                    status = json.loads((STATE / 'connection-status.json').read_text())
                    if PROCESS and PROCESS.poll() is None:
                        raise ValueError('请先关闭手机代理并停止电脑代理。')
                    if data.get('session') != status.get('session') or status.get('phase') != 'complete' or status.get('installStage') != 'complete' or status.get('selectedMd5') != INFO.get('md5'):
                        raise ValueError('请先完成资源收尾与保留验证。')
                    if data.get('confirmed') is not True or time.time() - STOPPED_AT < 30:
                        raise ValueError('请完成两次联网重开，每次保持模型页面至少 15 秒。')
                    with (STATE / 'install-events.jsonl').open('a', encoding='utf-8') as stream:
                        stream.write(json.dumps({'time': time.time(), 'session': status['session'], 'event': 'direct-restarts-user-confirmed', 'userConfirmed': True, 'actualZipMd5': INFO['md5']}) + '\n')
                    return self.reply(200, '{}', 'application/json')
                if path in ('/api/confirm-model', '/api/confirm-final', '/api/confirm-keep'):
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 0 < size <= 2048:
                        raise ValueError('确认信息无效')
                    data = json.loads(self.rfile.read(size))
                    result = confirm_installation(path.rsplit('/', 1)[-1], data)
                    return self.reply(200, json.dumps(result), 'application/json')
                if path == '/api/android':
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 0 < size <= 350 * 1024 * 1024:
                        raise ValueError('请求大小无效')
                    self.connection.settimeout(180)
                    data = json.loads(self.rfile.read(size))
                    bridge = AndroidBridge(STATE)
                    action = data.get('action')
                    if action == 'devices':
                        result = bridge.devices()
                    elif action == 'scan':
                        result = bridge.scan(data['serial'])
                    elif action == 'capture':
                        result = bridge.capture(data['serial'], data['target'], configuration=data.get('configuration'))
                    elif action in ('apply', 'restore'):
                        payload = base64.b64decode(data.get('payload', ''), validate=True)
                        result = bridge.apply(data['serial'], data['target'], payload, restore=action == 'restore')
                    else:
                        raise ValueError('未知安卓操作')
                    return self.reply(200, json.dumps(result, ensure_ascii=False), 'application/json')
                if path == '/api/upload':
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 0 < size <= 256 * 1024 * 1024:
                        raise ValueError('文件大小须在 256 MB 以内')
                    self.connection.settimeout(60)
                    payload = self.rfile.read(size)
                    if len(payload) != size:
                        raise ValueError('文件未完整传入本地工具')
                    info = validate(payload)
                    temporary = STATE / 'selected.tmp'
                    temporary.write_bytes(payload)
                    stop()
                    temporary.replace(STATE / 'selected.zip')
                    INFO = info
                elif path == '/api/capture':
                    start('capture')
                elif path == '/api/start':
                    start()
                elif path == '/api/quit':
                    stop()
                    self.reply(200, '{}', 'application/json')
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                elif path == '/api/stop':
                    stop()
                else:
                    return self.reply(404, '{}', 'application/json')
            self.reply(200, '{}', 'application/json')
        except Exception as error:
            self.reply(400, json.dumps({'error': str(error)}), 'application/json')


def open_control_page(address):
    if sys.platform == 'darwin':
        subprocess.Popen(['/usr/bin/open', address], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        threading.Thread(target=webbrowser.open, args=(address,), daemon=True).start()


def main():
    global PORT
    endpoint = STATE / 'control-endpoint.json'
    if endpoint.exists():
        try:
            previous = json.loads(endpoint.read_text(encoding='utf-8'))
            address = previous['url']
            parsed = urlsplit(address)
            if parsed.hostname != '127.0.0.1' or not 18900 <= parsed.port <= 18920:
                raise ValueError('invalid endpoint')
            status_url = address.replace('/?key=', '/api/status?key=')
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(status_url, timeout=2) as response:
                status = json.load(response)
            if 'package' in status and 'running' in status:
                if status.get('version') == VERSION:
                    if not os.environ.get('RESOURCE_TOOL_NO_BROWSER'):
                        open_control_page(address)
                    print(address, flush=True)
                    return
                quit_url = address.replace('/?key=', '/api/quit?key=')
                request = urllib.request.Request(quit_url, data=b'{}', headers={'Content-Type': 'application/json'})
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=10) as response:
                    response.read()
                for attempt in range(30):
                    try:
                        with socket.create_connection(('127.0.0.1', parsed.port), timeout=0.2):
                            pass
                    except OSError:
                        break
                    time.sleep(0.1)
        except (OSError, ValueError, KeyError, TypeError):
            pass
    if local_ip() == '127.0.0.1':
        raise SystemExit('请先连接可供 iPhone 访问的局域网。')
    server = None
    for candidate in range(18900, 18921):
        try:
            server = ThreadingHTTPServer(('0.0.0.0', candidate), Handler)
            PORT = candidate
            break
        except OSError:
            continue
    if server is None:
        raise RuntimeError('本地控制端口均被占用，请关闭旧助手后重试')
    server.daemon_threads = True
    atexit.register(stop)
    def terminate(signum, frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    address = f'http://127.0.0.1:{PORT}/?key={TOKEN}'
    endpoint.write_text(json.dumps({'url': address}), encoding='utf-8')
    try:
        endpoint.chmod(0o600)
    except OSError:
        pass
    print(address, flush=True)
    if not os.environ.get('RESOURCE_TOOL_NO_BROWSER'):
        open_control_page(address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        stop()
        try:
            if json.loads(endpoint.read_text(encoding='utf-8')).get('url') == address:
                endpoint.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass

if __name__ == '__main__':
    try:
        main()
    except Exception:
        import traceback
        (STATE / 'startup-error.txt').write_text(traceback.format_exc(), encoding='utf-8')
        raise
