"""Optional v278 metadata. Callers supply evidence already bound to this resource."""
from datetime import datetime
import hashlib
import io
import json
import zipfile

SCHEMA = 'coolgaipai-current-configuration-v1'
CONFIGURATION_FILE = 'current_configuration.json'


def export_resource(payload, platform, configuration=None):
    if configuration is None:
        return payload, {'included': False, 'reason': '本次未获取到可靠选配信息'}
    try:
        if platform not in ('android', 'ios'):
            raise ValueError('平台无效')
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or archive.testzip():
                raise ValueError('资源 ZIP 无效')
            manifest = json.loads(archive.read('resources.json'))
            binding = {name: hashlib.sha256(archive.read(name)).hexdigest()
                       for name in sorted(names) if name == 'resources.json' or name.endswith('.ab')}
            if configuration.get('schema') != SCHEMA or configuration.get('platform') != platform:
                raise ValueError('配置协议或平台不一致')
            if configuration.get('resourceVersion') != manifest.get('resVersion'):
                raise ValueError('配置与资源版本不一致')
            if configuration.get('binding', {}).get('filesSha256') != binding:
                raise ValueError('配置与本次资源文件绑定不一致')
            captured_at = configuration.get('capturedAt')
            if not isinstance(captured_at, str) or datetime.fromisoformat(captured_at.replace('Z', '+00:00')).tzinfo is None:
                raise ValueError('缺少带时区的实际采集时间')
            source = configuration.get('source')
            if not isinstance(source, str) or not source.strip():
                raise ValueError('缺少实际证据来源')
            result = {'schema': SCHEMA, 'platform': platform, 'resourceVersion': manifest['resVersion'],
                      'capturedAt': captured_at, 'source': source, 'binding': {'filesSha256': binding}}
            if isinstance(configuration.get('vehicleModel'), str) and configuration['vehicleModel']:
                result['vehicleModel'] = configuration['vehicleModel']
            features = configuration.get('featureOptions', [])
            selections = configuration.get('selections', [])
            if not isinstance(features, list) or not isinstance(selections, list):
                raise ValueError('选配信息格式无效')
            clean_features = []
            for item in features:
                if not isinstance(item, dict) or any(not isinstance(item.get(k), (str, int)) or isinstance(item.get(k), bool) for k in ('feature_code', 'option_code')):
                    raise ValueError('原始选配代码格式无效')
                row = {key: item[key] for key in ('feature_code', 'option_code')}
                if 'enable' in item:
                    if not isinstance(item['enable'], bool):
                        raise ValueError('enable 必须为布尔值')
                    row['enable'] = item['enable']
                clean_features.append(row)
            clean_selections = []
            for item in selections:
                if not isinstance(item, dict) or item.get('status') not in ('current', 'user-confirmed'):
                    raise ValueError('只接受当前配置或用户确认值')
                if any(not isinstance(item.get(k), str) or not item[k] for k in ('id', 'value', 'source')):
                    raise ValueError('配置映射或证据来源缺失')
                identifier, value = item['id'], item['value']
                if identifier not in ('wheel', 'paint') and not (identifier.startswith(('variant:', 'feature:')) and identifier.split(':', 1)[1]):
                    raise ValueError('配置项目不受支持')
                if identifier in ('wheel', 'paint') and value != '__disabled__' and not (value.isascii() and value.isdigit()):
                    raise ValueError('轮毂和车漆必须使用有证据的零基索引')
                clean_selections.append({key: item[key] for key in ('id', 'value', 'status', 'source')})
            if not clean_features and not clean_selections:
                raise ValueError('本次没有已知选配项目')
            if clean_features:
                result['featureOptions'] = clean_features
            if clean_selections:
                result['selections'] = clean_selections
            output = io.BytesIO()
            with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as target:
                target.comment = archive.comment
                for info in archive.infolist():
                    if info.filename == CONFIGURATION_FILE:
                        continue
                    contents = archive.read(info.filename)
                    if info.filename == 'resource_summary.json':
                        summary = json.loads(contents)
                        files = summary.get('files')
                        # Only extend a demonstrably complete original inventory.
                        original_files = {name for name in names if not name.endswith('/') and name not in ('resource_summary.json', CONFIGURATION_FILE)}
                        if isinstance(files, list) and all(isinstance(n, str) for n in files) and original_files == set(files) - {'resource_summary.json', CONFIGURATION_FILE}:
                            if CONFIGURATION_FILE not in files:
                                files.append(CONFIGURATION_FILE)
                            contents = json.dumps(summary, ensure_ascii=False, indent=2).encode('utf-8')
                    target.writestr(info, contents)
                target.writestr(CONFIGURATION_FILE, json.dumps(result, ensure_ascii=False, indent=2).encode('utf-8'))
            return output.getvalue(), {'included': True}
    except (ValueError, KeyError, TypeError, AttributeError, zipfile.BadZipFile) as error:
        # Unavailable or unusable optional evidence never prevents resource export.
        return payload, {'included': False, 'reason': str(error)}
