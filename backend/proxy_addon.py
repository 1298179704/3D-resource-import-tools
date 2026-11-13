import asyncio
import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit
from mitmproxy import http

WAIT_SECONDS = 15


class ResourceProxy:
    def __init__(self):
        self.root = Path(__file__).parent
        self.diagnostics = {'mode': os.environ.get('RESOURCE_TOOL_MODE', 'install'), 'connections': 0, 'probePassed': False, 'tlsFailure': None, 'metadataRequests': 0}
        self.capture = None
        self.control_task = None
        if self.diagnostics['mode'] == 'capture':
            import sys
            sys.path.insert(0, str(self.root))
            from resource_capture import OfficialCapture
            profile = json.loads((self.root / 'service-profile.json').read_text())
            self.capture = OfficialCapture(self.root, profile)
            return
        self.payload = (self.root / 'selected.zip').read_bytes()
        self.digest = hashlib.md5(self.payload).hexdigest()
        self.sha256 = hashlib.sha256(self.payload).hexdigest()
        self.profile = json.loads((self.root / 'service-profile.json').read_text())
        self.target = None
        self.identity = None
        self.phase = 'trigger'
        self.session = secrets.token_hex(16)
        self.trigger_digest = self.digest
        self.coverage = {'trigger': [], 'final': []}
        self.last_control_id = None
        self.failed = False
        self.diagnostics.update(session=self.session, phase=self.phase, installStage='waiting-resource-check', selectedMd5=self.digest, selectedSha256=self.sha256, selectedBytes=len(self.payload), waitSeconds=WAIT_SECONDS)

    def record_diagnostics(self, **values):
        self.diagnostics.update(values)
        self.diagnostics['updatedAt'] = time.time()
        path = self.root / 'connection-status.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.diagnostics, ensure_ascii=False), encoding='utf-8')
        temporary.replace(path)

    def event(self, name, **fields):
        entry = {'time': time.time(), 'session': self.session, 'event': name, 'phase': self.phase, **fields}
        with (self.root / 'install-events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + '\n')

    def running(self):
        self.record_diagnostics()
        if not self.capture:
            self.event('session-started', actualZipMd5=self.digest, actualZipSha256=self.sha256, bytes=len(self.payload))
            self.control_task = asyncio.get_running_loop().create_task(self.watch_control())

    async def watch_control(self):
        while True:
            self.apply_control()
            await asyncio.sleep(0.25)

    def apply_control(self):
        if self.capture or self.failed:
            return
        try:
            command = json.loads((self.root / 'install-control.json').read_text())
        except (OSError, ValueError):
            return
        if not isinstance(command, dict):
            return
        if command.get('session') != self.session or command.get('payloadMd5') != self.digest or command.get('id') == self.last_control_id:
            return
        self.last_control_id = command.get('id')
        action = command.get('action')
        transitions = {
            'confirm-model': ('trigger', 'await-model-confirmation', 'triggerPreparedAt', 'final', 'waiting-final-check'),
            'confirm-final': ('final', 'await-final-confirmation', 'finalPreparedAt', 'verify', 'waiting-verification'),
            'confirm-keep': ('verify', 'await-keep-confirmation', 'verificationStartedAt', 'complete', 'complete'),
        }
        if action not in transitions:
            return
        previous, stage, timestamp, following, next_stage = transitions[action]
        if self.phase != previous or self.diagnostics.get('installStage') != stage:
            return
        if time.time() - self.diagnostics.get(timestamp, time.time()) < WAIT_SECONDS:
            return
        self.last_control_id = command['id']
        self.phase = following
        self.event(action, userConfirmed=True)
        self.record_diagnostics(phase=self.phase, installStage=next_stage, lastConfirmation=action, confirmedAt=time.time())

    def done(self):
        if self.control_task:
            self.control_task.cancel()
        if not self.capture:
            self.event('proxy-stopped')

    def client_connected(self, client):
        self.record_diagnostics(connections=self.diagnostics['connections'] + 1)

    def tls_established_client(self, data):
        address = data.context.server.address
        if address:
            self.record_diagnostics(lastTlsHost=address[0], tlsSucceeded=True)

    def tls_failed_client(self, data):
        address = data.context.server.address
        self.record_diagnostics(tlsFailure={'host': address[0] if address else '', 'message': '请核对手机是否安装了当前电脑的证书，并开启完全信任。'})

    def fail(self, message):
        self.failed = True
        self.event('installation-error', message=message)
        self.record_diagnostics(installStage='error', installError=message)

    def request(self, flow):
        if flow.request.host == 'local-helper.invalid' and flow.request.path.split('?')[0] == '/check':
            expected = os.environ.get('RESOURCE_TOOL_PROBE_TOKEN', '')
            allowed = expected and secrets.compare_digest(flow.request.query.get('key', ''), expected)
            if allowed:
                self.record_diagnostics(probePassed=True)
            flow.response = http.Response.make(200 if allowed else 403, b'{"connected":true}' if allowed else b'Invalid connection check', {'Content-Type': 'application/json', 'Cache-Control': 'no-store', 'Access-Control-Allow-Origin': '*'})
            return
        if self.capture:
            if flow.request.host == self.capture.profile['metadata_host']:
                self.record_diagnostics(targetRequests=self.diagnostics.get('targetRequests', 0) + 1, resourceCheckPathSeen=self.capture.profile['metadata_path'] in flow.request.path)
            return
        self.apply_control()
        if flow.request.host != self.profile['download_host']:
            return
        path = flow.request.path.split('?')[0]
        request_session = flow.request.query.get('rt_session')
        if request_session and request_session != self.session:
            flow.response = http.Response.make(409, b'Resource session expired', {'Cache-Control': 'no-store'})
            return
        local_request = request_session == self.session
        if not local_request:
            if self.phase in ('verify', 'complete') and path == self.target:
                self.event('official-download-requested')
                self.fail('检测到原包重新下载，请重新应用资源并完成收尾。')
            return
        if self.failed or path != self.target:
            flow.response = http.Response.make(409, b'Resource session no longer valid', {'Cache-Control': 'no-store'})
            return
        phase = flow.request.query.get('rt_phase')
        if flow.request.method != 'GET' or phase not in self.coverage:
            flow.response = http.Response.make(405, b'Invalid resource request', {'Cache-Control': 'no-store'})
            return
        # Keep serving in-flight session URLs after a confirmation; never substitute official bytes.
        start, end = 0, len(self.payload) - 1
        headers = {'Content-Type': 'application/octet-stream', 'Cache-Control': 'no-store', 'Accept-Ranges': 'bytes'}
        requested = flow.request.headers.get('range')
        if requested:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested)
            if not match or not any(match.groups()):
                flow.response = http.Response.make(416, b'', {'Content-Range': f'bytes */{len(self.payload)}'})
                return
            first, last = match.groups()
            start = int(first) if first else max(0, len(self.payload) - int(last))
            end = min(int(last), len(self.payload) - 1) if first and last else len(self.payload) - 1
            if start > end or start >= len(self.payload):
                flow.response = http.Response.make(416, b'', {'Content-Range': f'bytes */{len(self.payload)}'})
                return
            headers['Content-Range'] = f'bytes {start}-{end}/{len(self.payload)}'
        payload = self.payload[start:end + 1]
        status = 206 if requested else 200
        headers['Content-Length'] = str(len(payload))
        flow.response = http.Response.make(status, payload, headers)
        flow.metadata['resource_tool_response'] = {'phase': phase, 'start': start, 'end': end}
        self.event('resource-response-prepared', responsePhase=phase, status=status, rangeStart=start, rangeEnd=end, responseBytes=len(payload), actualZipMd5=self.digest, actualZipSha256=self.sha256)
        self.record_diagnostics(packageResponses=self.diagnostics.get('packageResponses', 0) + 1, responseBytes=len(payload), responseStatus=status)

    def response(self, flow):
        if self.capture:
            profile = self.capture.profile
            if flow.request.host == profile['metadata_host'] and profile['metadata_path'] in flow.request.path:
                self.record_diagnostics(metadataRequests=self.diagnostics['metadataRequests'] + 1)
            self.capture.response(flow)
            return
        self.apply_control()
        served = flow.metadata.get('resource_tool_response')
        if served:
            intervals = sorted(self.coverage[served['phase']] + [(served['start'], served['end'])])
            merged = []
            for first, last in intervals:
                if merged and first <= merged[-1][1] + 1:
                    merged[-1] = (merged[-1][0], max(last, merged[-1][1]))
                else:
                    merged.append((first, last))
            self.coverage[served['phase']] = merged
            if merged == [(0, len(self.payload) - 1)] and self.phase == served['phase'] and not self.failed:
                timestamp = 'triggerPreparedAt' if self.phase == 'trigger' else 'finalPreparedAt'
                self.record_diagnostics(**{timestamp: self.diagnostics.get(timestamp, time.time()), 'installStage': 'await-model-confirmation' if self.phase == 'trigger' else 'await-final-confirmation'})
            return
        profile = self.profile
        if flow.request.host != profile['metadata_host'] or profile['metadata_path'] not in flow.request.path:
            return
        self.record_diagnostics(metadataRequests=self.diagnostics['metadataRequests'] + 1, metadataHttpStatus=flow.response.status_code if flow.response else None)
        if self.failed or not flow.response or flow.response.status_code != 200:
            return
        try:
            body = json.loads(flow.response.get_text())
            data = body['data']
            parsed = urlsplit(str(data.get('url', '')))
            if parsed.scheme != 'https' or parsed.hostname != profile['download_host'] or not parsed.path.lstrip('/').startswith('3D/'):
                return
            official = data.get('md5')
            if not isinstance(official, str) or not re.fullmatch(r'[a-fA-F0-9]{32}', official):
                self.fail('资源检查信息不完整，请重新开始。')
                return
            identity = (data.get('filename'), parsed.path, official.lower())
            if self.identity and identity != self.identity:
                self.fail('手机资源已变化，请重新选择对应资源包。')
                return
            self.identity = identity
            self.target = parsed.path
            if self.trigger_digest.lower() == official.lower():
                self.trigger_digest = secrets.token_hex(16)
                while self.trigger_digest.lower() == official.lower():
                    self.trigger_digest = secrets.token_hex(16)
            if self.phase in ('verify', 'complete'):
                self.event('official-metadata-forwarded', officialMd5=official, filename=data.get('filename'))
                if self.phase == 'verify':
                    self.record_diagnostics(verificationStartedAt=self.diagnostics.get('verificationStartedAt', time.time()), installStage='await-keep-confirmation')
                return
            if self.phase == 'trigger':
                data['md5'] = self.trigger_digest
            data['url'] = 'https://' + profile['download_host'] + self.target + '?rt_session=' + self.session + '&rt_phase=' + self.phase
            flow.response.set_text(json.dumps(body))
            self.event('metadata-returned', filename=data.get('filename'), officialMd5=official, returnedMd5=data['md5'], actualZipMd5=self.digest)
            self.record_diagnostics(detectedFilename=data.get('filename'), resourcePath=self.target, officialMd5=official, returnedMd5=data['md5'], identityPhase=self.phase)
        except (ValueError, KeyError, TypeError):
            return


addons = [ResourceProxy()]
