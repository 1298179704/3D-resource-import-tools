import sys,io,json,hashlib,zipfile,copy
sys.path.insert(0,'backend')
from configuration_export import export_resource
b=io.BytesIO()
with zipfile.ZipFile(b,'w') as z:
 z.writestr('resources.json',json.dumps({'resVersion':'test-v1'}));z.writestr('model.ab',b'test-only-model');z.writestr('resource_summary.json',json.dumps({'files':['model.ab','resources.json']}))
payload=b.getvalue()
with zipfile.ZipFile(io.BytesIO(payload)) as z: hashes={n:hashlib.sha256(z.read(n)).hexdigest() for n in ('model.ab','resources.json')}
configuration={'schema':'coolgaipai-current-configuration-v1','platform':'android','resourceVersion':'test-v1','capturedAt':'2026-11-12T08:00:00Z','source':'模拟测试，非实际车辆证据','binding':{'filesSha256':hashes},'featureOptions':[{'feature_code':'TEST_FEATURE','option_code':'TEST_OPTION','enable':False}],'selections':[{'id':'wheel','value':'0','status':'user-confirmed','source':'模拟测试'}]}
assert export_resource(payload,'android')[0]==payload
for platform in ['android','ios']:
 evidence=copy.deepcopy(configuration);evidence['platform']=platform
 for partial in [False,True]:
  c=copy.deepcopy(evidence)
  if partial:del c['selections']
  output,status=export_resource(payload,platform,c);assert status['included']
  with zipfile.ZipFile(io.BytesIO(output)) as z:
   d=json.loads(z.read('current_configuration.json'));assert d['featureOptions'][0]['enable'] is False
   assert 'current_configuration.json' in json.loads(z.read('resource_summary.json'))['files']
   assert d['binding']['filesSha256']==hashes
   for name in hashes:assert hashlib.sha256(z.read(name)).hexdigest()==hashes[name]
   if partial:assert 'selections' not in d
for label,edit in [('错误哈希',lambda c:c['binding']['filesSha256'].update({'model.ab':'0'*64})),('错误平台',lambda c:c.update(platform='ios')),('错误版本',lambda c:c.update(resourceVersion='wrong')),('默认值',lambda c:c['selections'][0].update(status='default')),('损坏结构',lambda c:c.update(featureOptions=None))]:
 c=copy.deepcopy(configuration);edit(c);output,status=export_resource(payload,'android',c);assert not status['included'] and output==payload,label
c=copy.deepcopy(configuration);c['selections'].append({**c['selections'][0],'value':'1'})
output,status=export_resource(payload,'android',c)
with zipfile.ZipFile(io.BytesIO(output)) as z:assert len(json.loads(z.read('current_configuration.json'))['selections'])==2
print('PASS 双平台、有/无JSON、部分配置、enable:false、错误平台/版本/哈希、默认值拒绝、冲突保留、原文件字节不变、完整清单更新')
