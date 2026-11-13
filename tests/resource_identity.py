"""Regression tests for manual activation, identity finalization and pass-through verification."""
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace as N
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

class Response:
    @staticmethod
    def make(status, content, headers=None):
        return N(status_code=status, content=content, headers=headers or {})

sys.modules['mitmproxy'] = N(http=N(Response=Response))
source = Path('backend/proxy_addon.py').read_text().replace('addons = [ResourceProxy()]', '')
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    profile = {'metadata_host':'metadata.example.test', 'download_host':'cdn.example.test', 'metadata_path':'/api/resources', 'resource_path':'/3D/42/model'}
    (root/'service-profile.json').write_text(json.dumps(profile))
    (root/'selected.zip').write_bytes(b'custom bytes')
    namespace = {'__file__':str(root/'proxy_addon.py')}
    exec(compile(source, 'proxy_addon.py', 'exec'), namespace)
    now = [1000.0]
    with patch.object(namespace['time'], 'time', lambda:now[0]):
        proxy = namespace['ResourceProxy']()
        def metadata(md5='a'*32, filename='new_model_v9'):
            body = {'data':{'filename':filename, 'md5':md5, 'url':'https://cdn.example.test/3D/42/'+filename}}
            output = {}
            flow = N(metadata={}, request=N(host=profile['metadata_host'], path=profile['metadata_path']), response=N(status_code=200, get_text=lambda:json.dumps(body), set_text=lambda text:output.update(json.loads(text))))
            proxy.response(flow)
            return output or body
        def download(url, requested_range=None):
            parsed = urlsplit(url)
            query = {k:v[0] for k,v in parse_qs(parsed.query).items()}
            flow = N(metadata={}, request=N(method='GET', host=parsed.hostname, path=parsed.path+'?'+parsed.query, query=query, headers={'range':requested_range} if requested_range else {}), response=None)
            proxy.request(flow)
            if flow.response:
                proxy.response(flow)
            return flow
        def confirm(action, session=None):
            (root/'install-control.json').write_text(json.dumps({'id':str(now[0])+action, 'session':session or proxy.session, 'payloadMd5':proxy.digest, 'action':action}))
            proxy.apply_control()
        first = metadata()['data']
        assert first['md5'] == proxy.digest
        assert download(first['url'].replace(proxy.session, 'expired-session')).response.status_code==409
        download(first['url'], 'bytes=0-4')
        assert proxy.phase == 'trigger'
        download(first['url'], 'bytes=5-')
        assert proxy.phase == 'trigger' and proxy.diagnostics['installStage']=='await-model-confirmation'
        confirm('confirm-model')
        assert proxy.phase=='trigger', 'Must wait at least 15 seconds'
        now[0]+=16
        assert metadata()['data']['md5']==proxy.digest, 'No automatic finalization after serving bytes'
        confirm('confirm-model', 'stale-session')
        assert proxy.phase=='trigger'
        confirm('confirm-model')
        assert proxy.phase=='final'
        final=metadata()['data']
        assert final['md5']=='a'*32
        assert download(final['url']).response.content==b'custom bytes'
        assert proxy.phase=='final'
        now[0]+=16
        confirm('confirm-final')
        assert proxy.phase=='verify'
        official=metadata()['data']
        assert official['url']=='https://cdn.example.test/3D/42/new_model_v9', 'Official response must pass through unchanged'
        confirm('confirm-keep')
        assert proxy.phase=='verify'
        now[0]+=16
        confirm('confirm-keep')
        assert proxy.phase=='complete'
        assert download(first['url']).response.content==b'custom bytes', 'In-flight prior session URLs must retain selected bytes'
        assert metadata(md5='b'*32)['data']['md5']=='b'*32 and proxy.failed

    # Failed verification must not become a successful completion.
    (root/'install-control.json').unlink()
    proxy=namespace['ResourceProxy']()
    metadata()
    proxy.phase='verify'
    metadata()
    ordinary=N(metadata={}, request=N(host='cdn.example.test', path=proxy.target, query={}, method='GET', headers={}), response=None)
    proxy.request(ordinary)
    assert proxy.failed and proxy.diagnostics['installStage']=='error'
    # Stock restoration also needs a fresh update identity when ZIP checksum already equals official identity.
    proxy=namespace['ResourceProxy']()
    assert metadata(md5=proxy.digest)['data']['md5']!=proxy.digest
print('PASS manual confirmation, 15-second guards, segmented coverage, stable identities, stale sessions, official pass-through, interrupted URL retries, changed resources and re-download detection')
