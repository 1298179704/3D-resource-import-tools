import sys,json,tempfile,types,importlib.util
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from types import SimpleNamespace as N
class Response:
 @staticmethod
 def make(code,payload,headers=None):return N(status_code=code,content=payload,headers=headers)
sys.modules['mitmproxy']=N(http=N(Response=Response))
source=Path('backend/proxy_addon.py').read_text().replace('addons = [ResourceProxy()]','')
with tempfile.TemporaryDirectory() as d:
 root=Path(d);profile={'metadata_host':'metadata.example.test','download_host':'download.example.test','metadata_path':'/api/resources','resource_path':'/3D/42/model','filename':'model'};(root/'service-profile.json').write_text(json.dumps(profile));(root/'selected.zip').write_bytes(b'custom bytes')
 ns={'__file__':str(root/'proxy_addon.py')};exec(compile(source,'proxy_addon.py','exec'),ns)
 proxy=ns['ResourceProxy']()
 def metadata(md5='a'*32,filename='new_model_v9'):
  body={'data':{'filename':filename,'md5':md5,'url':'https://'+profile['download_host']+'/3D/42/'+filename}}
  out={};flow=N(metadata={},request=N(host=profile['metadata_host'],path=profile['metadata_path']),response=N(status_code=200,get_text=lambda:json.dumps(body),set_text=lambda text:out.update(json.loads(text))))
  proxy.response(flow);return out
 def download(url,byte_range=None):
  parsed=urlsplit(url);query={k:v[0] for k,v in parse_qs(parsed.query).items()};headers={} if byte_range is None else {'range':byte_range}
  flow=N(metadata={},request=N(method='GET',host=parsed.hostname,path=parsed.path+'?'+parsed.query,query=query,headers=headers),response=None);proxy.request(flow)
  if flow.response:proxy.response(flow)
  return flow
 first=metadata()['data'];assert first['md5']==proxy.trigger_digest and proxy.phase=='trigger'
 download(first['url'],'bytes=0-4');assert proxy.phase=='trigger'
 download(first['url'],'bytes=5-');assert proxy.phase=='final' and proxy.diagnostics['installStage']=='trigger-served'
 second=metadata()['data'];assert second['md5']=='a'*32 and second['filename']=='new_model_v9'
 final=download(second['url']);assert final.response.content==b'custom bytes' and proxy.diagnostics['installStage']=='final-served'
 assert metadata()['data']['md5']=='a'*32
 assert not metadata(md5='b'*32) and proxy.target is None
print('PASS 动态车型、首次触发、分段覆盖、官方身份收尾、真实字节保持、身份变化拒绝')
