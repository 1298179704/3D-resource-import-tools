"""Capture official resource responses or fetch their signed URL locally."""
import hashlib
import io
import json
import threading
import urllib.request
from urllib.parse import urlsplit
import zipfile
from pathlib import Path

LIMIT = 256 * 1024 * 1024

class OfficialCapture:
    def __init__(self, root, profile):
        self.root = Path(root)
        self.profile = profile
        self.lock = threading.Lock()
        self.started = False
        self.expected = None
        self.resource_url = None
        self.filename = None
        self.update('waiting', '等待手机打开车辆页面')

    def update(self, stage, message, **values):
        temporary = self.root / 'capture-status.tmp'
        temporary.write_text(json.dumps({'stage': stage, 'message': message, **values}, ensure_ascii=False), encoding='utf-8')
        temporary.replace(self.root / 'capture-status.json')

    def save(self, payload):
        digest = hashlib.md5(payload).hexdigest()
        if self.expected and digest.lower() != self.expected.lower():
            raise ValueError('官方包 MD5 与接口标识不一致')
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 1024 * 1024 * 1024:
                raise ValueError('资源展开大小超过支持范围')
            if archive.testzip():
                raise ValueError('官方 ZIP 不完整')
            if not archive.namelist():
                raise ValueError('车型资源包为空')
            if 'resource_summary.json' in archive.namelist():
                summary = json.loads(archive.read('resource_summary.json'))
                required = summary.get('files')
                if not isinstance(required, list) or not set(required).issubset(archive.namelist()):
                    raise ValueError('资源摘要清单中的文件不齐全')
        temporary = self.root / 'official-download.tmp'
        temporary.write_bytes(payload)
        temporary.replace(self.root / 'official-download.zip')
        self.update('downloaded', '完整车型资源已保存，可导出', md5=digest, bytes=len(payload), filename=self.filename)

    def metadata(self, data):
        with self.lock:
            if self.started:
                return
            url = data.get('url', '')
            parsed = urlsplit(url)
            if parsed.scheme != 'https' or parsed.hostname != self.profile['download_host'] or not parsed.path.lstrip('/').startswith('3D/'):
                self.update('error', '车型资源下载地址无效')
                return
            expected = data.get('md5')
            if not isinstance(expected, str) or len(expected) != 32 or any(c not in '0123456789abcdefABCDEF' for c in expected):
                self.update('error', '官方接口未提供有效 MD5')
                return
            self.resource_url = parsed
            self.filename = data.get('filename') or Path(parsed.path).name
            self.expected = expected
            self.started = True
            self.update('downloading', '电脑正在下载官方资源，手机可保留已有模型')
        threading.Thread(target=self.fetch, args=(url,), daemon=True).start()

    def fetch(self, url):
        try:
            # Redirects are rejected rather than forwarding a signed URL to a new host.
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *args, **kwargs):
                    return None
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            with opener.open(urllib.request.Request(url, headers={'Accept': 'application/octet-stream'}), timeout=45) as response:
                payload = response.read(LIMIT + 1)
                if response.status != 200 or len(payload) > LIMIT:
                    raise ValueError('官方资源响应状态或大小不受支持')
            with self.lock:
                self.save(payload)
        except Exception as error:
            with self.lock:
                current = json.loads((self.root / 'capture-status.json').read_text(encoding='utf-8'))
                if current['stage'] != 'downloaded':
                    self.update('waiting_download', '电脑下载未成功，等待手机官方响应；可停止后重试', errorType=type(error).__name__, httpStatus=getattr(error, 'code', None))

    def response(self, flow):
        if not flow.response:
            return
        if flow.request.host == self.profile['metadata_host'] and self.profile['metadata_path'] in flow.request.path and flow.response.status_code != 200:
            with self.lock:
                self.update('error', '资源检查接口返回失败', httpStatus=flow.response.status_code)
            return
        if flow.response.status_code != 200:
            return
        if flow.request.host == self.profile['metadata_host'] and self.profile['metadata_path'] in flow.request.path:
            try:
                data = json.loads(flow.response.get_text())['data']
                if isinstance(data, dict) and (str(data.get('path', '')).lstrip('/').startswith('3D/') or urlsplit(str(data.get('url', ''))).path.lstrip('/').startswith('3D/')):
                    self.metadata(data)
            except (ValueError, KeyError, TypeError):
                pass
        if self.resource_url and flow.request.host == self.resource_url.hostname and flow.request.path.split('?')[0] == self.resource_url.path:
            with self.lock:
                try:
                    if not self.expected:
                        return
                    payload = flow.response.content
                    if len(payload) > LIMIT:
                        raise ValueError('资源过大')
                    self.save(payload)
                except Exception:
                    self.update('error', '手机资源响应未通过完整性校验，请停止后重试')
