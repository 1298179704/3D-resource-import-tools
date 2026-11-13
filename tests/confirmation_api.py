import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
sys.path.insert(0,'backend')
with tempfile.TemporaryDirectory() as directory:
    root=Path(directory)
    os.environ['RESOURCE_TOOL_DATA']=directory
    spec=importlib.util.spec_from_file_location('resource_app_test','backend/app.py')
    app=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)
    app.PROCESS=SimpleNamespace(poll=lambda:None)
    app.INFO={'md5':'c'*32}
    status={'session':'current','selectedMd5':'c'*32,'phase':'trigger','installStage':'await-model-confirmation','triggerPreparedAt':time.time()-2}
    file=root/'connection-status.json'
    file.write_text(json.dumps(status))
    try:app.confirm_installation('confirm-model',{'session':'current','confirmed':True});raise AssertionError('early confirmation accepted')
    except ValueError:pass
    status['triggerPreparedAt']=time.time()-16
    file.write_text(json.dumps(status))
    for data in ({'session':'stale','confirmed':True},{'session':'current','confirmed':False}):
        try:app.confirm_installation('confirm-model',data);raise AssertionError('invalid confirmation accepted')
        except ValueError:pass
    app.confirm_installation('confirm-model',{'session':'current','confirmed':True})
    control=json.loads((root/'install-control.json').read_text())
    assert control['session']=='current' and control['payloadMd5']=='c'*32
    app.confirm_installation('confirm-model',{'session':'current','confirmed':True})
    assert json.loads((root/'install-control.json').read_text())['id']==control['id']
print('PASS server wait guard, stale resource/session rejection and idempotent confirmation')
