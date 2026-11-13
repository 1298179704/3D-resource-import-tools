import hashlib
import json
import os
import time
import secrets
from pathlib import Path
from urllib.parse import urlsplit
from mitmproxy import http

class ResourceProxy:
    def __init__(self):
        self.diagnostics = {'mode': os.environ.get('RESOURCE_TOOL_MODE', 'install'), 'connections': 0, 'probePassed': False, 'tlsFailure': None, 'metadataRequests': 0}
        self.capture = None
        if os.environ.get('RESOURCE_TOOL_MODE') == 'capture':
            import sys
            sys.path.insert(0, str(Path(__file__).parent))
            from resource_capture import OfficialCapture
            profile = json.loads(Path(__file__).with_name('service-profile.json').read_text())
            self.capture = OfficialCapture(Path(__file__).parent, profile)
            return
        self.payload = Path(__file__).with_name('selected.zip').read_bytes()
        self.digest = hashlib.md5(self.payload).hexdigest()
        self.profile = json.loads(Path(__file__).with_name('service-profile.json').read_text())
        self.target = None
        self.identity = None
        self.phase = 'trigger'
        self.session = secrets.token_hex(16)
        self.trigger_digest = secrets.token_hex(16)
        self.coverage = {'trigger': [], 'final': []}

    def record_diagnostics(self, **values):
        self.diagnostics.update(values)
        self.diagnostics['updatedAt'] = time.time()
        path = Path(__file__).with_name('connection-status.json')
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.diagnostics, ensure_ascii=False), encoding='utf-8')
        temporary.replace(path)

    def running(self):
        self.record_diagnostics()

    def client_connected(self, client):
        self.record_diagnostics(connections=self.diagnostics['connections'] + 1)

    def tls_established_client(self, data):
        address = data.context.server.address
        if address:
            self.record_diagnostics(lastTlsHost=address[0], tlsSucceeded=True)

    def tls_failed_client(self, data):
        address = data.context.server.address
        self.record_diagnostics(tlsFailure={'host': address[0] if address else '', 'message': '手机未接受当前电脑的代理证书，请核对是否安装了这台电脑下载的证书并开启完全信任'})

    def request(self, flow):
        if flow.request.host == 'local-helper.invalid' and flow.request.path.split('?')[0] == '/check':
            supplied = flow.request.query.get('key', '')
            expected = os.environ.get('RESOURCE_TOOL_PROBE_TOKEN', '')
            if expected and secrets.compare_digest(supplied, expected):
                self.record_diagnostics(probePassed=True)
                flow.response = http.Response.make(200, b'{"connected":true}', {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*', 'Cache-Control': 'no-store'})
            else:
                flow.response = http.Response.make(403, b'Invalid connection check')
            return
        if self.capture:
            if flow.request.host == self.capture.profile['metadata_host']:
                self.record_diagnostics(targetRequests=self.diagnostics.get('targetRequests', 0) + 1, resourceCheckPathSeen=self.capture.profile['metadata_path'] in flow.request.path)
            return
        if flow.request.host != self.profile['download_host'] or flow.request.path.split('?')[0] != self.target:
            return
        if flow.request.method != 'GET':
            return
        if flow.request.query.get('rt_session') != self.session:
            return
        phase = flow.request.query.get('rt_phase')
        if phase not in self.coverage:
            return
        start, end = 0, len(self.payload) - 1
        headers = {'Content-Type': 'application/octet-stream', 'Cache-Control': 'no-store', 'Accept-Ranges': 'bytes'}
        payload = self.payload
        status = 200
        requested = flow.request.headers.get('range')
        if requested:
            import re
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested)
            if not match or not any(match.groups()):
                flow.response = http.Response.make(416, b'', {'Content-Range': f'bytes */{len(payload)}'})
                return
            first, last = match.groups()
            start = int(first) if first else max(0, len(payload) - int(last))
            end = min(int(last), len(payload) - 1) if first and last else len(payload) - 1
            if start > end or start >= len(payload):
                flow.response = http.Response.make(416, b'', {'Content-Range': f'bytes */{len(payload)}'})
                return
            headers['Content-Range'] = f'bytes {start}-{end}/{len(payload)}'
            payload = payload[start:end + 1]
            status = 206
        headers['Content-Length'] = str(len(payload))
        flow.response = http.Response.make(status, payload, headers)
        flow.metadata['resource_tool_response'] = {'phase': phase, 'start': start, 'end': end}
        self.record_diagnostics(packageResponses=self.diagnostics.get('packageResponses', 0) + 1, responseBytes=len(payload), selectedMd5=self.digest, responseStatus=status)
        print(f'资源响应已准备 {status}，{len(payload)} 字节，MD5 {self.digest}', flush=True)

    def response(self, flow):
        served = flow.metadata.get('resource_tool_response')
        if served:
            intervals = sorted(self.coverage[served['phase']] + [(served['start'], served['end'])])
            covered = []
            for first, last in intervals:
                if covered and first <= covered[-1][1] + 1:
                    covered[-1] = (covered[-1][0], max(last, covered[-1][1]))
                else:
                    covered.append((first, last))
            self.coverage[served['phase']] = covered
            if covered == [(0, len(self.payload) - 1)]:
                if served['phase'] == 'trigger':
                    self.phase = 'final'
                    self.record_diagnostics(installStage='trigger-served')
                else:
                    self.record_diagnostics(installStage='final-served')
            return
        profile = self.capture.profile if self.capture else self.profile
        if flow.request.host == profile['metadata_host'] and profile['metadata_path'] in flow.request.path:
            self.record_diagnostics(metadataRequests=self.diagnostics['metadataRequests'] + 1, metadataHttpStatus=flow.response.status_code if flow.response else None)
        if self.capture:
            self.capture.response(flow)
            return
        if flow.request.host != self.profile['metadata_host'] or self.profile['metadata_path'] not in flow.request.path or not flow.response or flow.response.status_code != 200:
            return
        try:
            body = json.loads(flow.response.get_text())
            data = body['data']
            parsed = urlsplit(str(data.get('url', '')))
            if parsed.scheme != 'https' or parsed.hostname != self.profile['download_host'] or not parsed.path.lstrip('/').startswith('3D/'):
                return
            official = data.get('md5')
            if not isinstance(official, str) or len(official) != 32 or any(c not in '0123456789abcdefABCDEF' for c in official):
                self.record_diagnostics(installStage='error', installError='资源接口缺少有效更新标识，请重新开始')
                return
            identity = (data.get('filename'), parsed.path, official)
            if self.identity and identity != self.identity:
                self.target = None
                self.record_diagnostics(installStage='error', installError='手机资源已变化，请重新选择资源并开始')
                return
            self.identity = identity
            self.target = parsed.path
            phase = 'final' if self.phase == 'final' else 'trigger'
            if self.trigger_digest == official:
                self.trigger_digest = ('0' if official[0] != '0' else '1') + official[1:]
            if phase == 'trigger':
                data['md5'] = self.trigger_digest
            data['url'] = 'https://' + self.profile['download_host'] + self.target + '?rt_session=' + self.session + '&rt_phase=' + phase

            flow.response.set_text(json.dumps(body))
            self.record_diagnostics(metadataReplacements=self.diagnostics.get('metadataReplacements', 0) + 1, detectedFilename=data.get('filename'), resourcePath=self.target, selectedMd5=self.digest, selectedBytes=len(self.payload), selectedSha256=hashlib.sha256(self.payload).hexdigest(), officialMd5=official, returnedMd5=data['md5'], identityPhase=phase)
            print('已匹配车型资源，已返回资源更新标识', flush=True)
        except (ValueError, KeyError, TypeError):
            pass

addons = [ResourceProxy()]
