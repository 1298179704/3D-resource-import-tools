"""Read platform metadata for supported UnityFS bundles without texture decoders."""
import io
import lzma
import struct
import lz4.block

LIMIT = 256 * 1024 * 1024

def number(stream, fmt):
    size = struct.calcsize(fmt)
    data = stream.read(size)
    if len(data) != size:
        raise ValueError('资源头不完整')
    return struct.unpack(fmt, data)[0]

def string(stream):
    result = bytearray()
    for _ in range(4096):
        char = stream.read(1)
        if not char:
            raise ValueError('资源字符串不完整')
        if char == b'\0':
            return bytes(result)
        result.extend(char)
    raise ValueError('资源字符串过长')

def unpack(data, size, flags):
    if size > LIMIT:
        raise ValueError('资源块超过支持范围')
    mode = flags & 63
    if mode == 0:
        result = data
    elif mode in (2, 3):
        result = lz4.block.decompress(data, uncompressed_size=size)
    else:
        raise ValueError('当前资源压缩格式不受支持')
    if len(result) != size:
        raise ValueError('资源块大小不匹配')
    return result

def ios_bundle(data, platform=9):
    stream = io.BytesIO(data)
    if string(stream) != b'UnityFS':
        raise ValueError('需要 UnityFS 资源')
    version = number(stream, '>I')
    if version != 8:
        raise ValueError('当前只支持已验证的 UnityFS 版本 8')
    string(stream)
    string(stream)
    total = number(stream, '>Q')
    packed = number(stream, '>I')
    size = number(stream, '>I')
    flags = number(stream, '>I')
    if total != len(data) or flags & 0x400:
        raise ValueError('资源大小或加密格式不受支持')
    stream.seek((stream.tell() + 15) // 16 * 16)
    start = stream.tell()
    if flags & 0x80:
        stream.seek(len(data) - packed)
    info = io.BytesIO(unpack(stream.read(packed), size, flags))
    stream.seek(start if flags & 0x80 else start + packed)
    if flags & 0x200:
        stream.seek((stream.tell() + 15) // 16 * 16)
    info.read(16)
    count = number(info, '>I')
    if count > 100000:
        raise ValueError('资源块数量异常')
    blocks = [(number(info, '>I'), number(info, '>I'), number(info, '>H')) for _ in range(count)]
    if sum(block[0] for block in blocks) > LIMIT:
        raise ValueError('资源展开大小超过支持范围')
    count = number(info, '>I')
    if count > 100000:
        raise ValueError('资源文件数量异常')
    nodes = []
    for _ in range(count):
        offset, length, node_flags = number(info, '>Q'), number(info, '>Q'), number(info, '>I')
        name = string(info)
        nodes.append((offset, length, name))
    payload = b''.join(unpack(stream.read(packed_size), raw_size, block_flags) for raw_size, packed_size, block_flags in blocks)
    found = False
    for offset, length, name in nodes:
        if name.endswith((b'.resS', b'.resource')):
            continue
        asset = payload[offset:offset + length]
        if len(asset) < 48:
            raise ValueError('资源元数据不完整')
        revision = struct.unpack_from('>I', asset, 8)[0]
        if revision < 22:
            raise ValueError('序列化资源版本不受支持')
        endian = '>' if asset[16] else '<'
        meta = io.BytesIO(asset)
        meta.seek(48)
        string(meta)
        if number(meta, endian + 'i') != platform:
            return False
        found = True
    return found


def validate_inventory(archive):
    """Validate the package's own inventory, including exports without a summary."""
    import json
    names = set(archive.namelist())
    if 'resources.json' not in names:
        raise ValueError('ZIP 根目录缺少 resources.json，请选择导出的模型 ZIP')
    manifest = json.loads(archive.read('resources.json'))
    inventories = []
    if 'sceneBundles' in manifest:
        inventories.append(manifest['sceneBundles'])
    if 'resource_summary.json' in names:
        summary = json.loads(archive.read('resource_summary.json'))
        inventories.append(summary.get('files'))
    if not inventories:
        raise ValueError('资源包没有可核对的文件清单')
    for required in inventories:
        if not isinstance(required, list) or not required or any(not isinstance(name, str) for name in required):
            raise ValueError('资源文件清单格式无效')
        missing = sorted(set(required) - names)
        if missing:
            raise ValueError('ZIP 缺少资源文件：' + '、'.join(missing[:3]))
    return manifest
