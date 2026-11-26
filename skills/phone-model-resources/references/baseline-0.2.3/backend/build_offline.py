"""Build on the target OS. Dependencies are installed on the build machine only."""
import argparse
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--proxy', required=True, type=Path, help='macOS mitmproxy.app or Windows mitmdump.exe')
parser.add_argument('--profile', required=True, type=Path, help='Service profile JSON')
parser.add_argument('--output', required=True, type=Path)
args = parser.parse_args()
args.proxy = args.proxy.resolve()
args.profile = args.profile.resolve()
if not args.proxy.exists() or not args.profile.is_file():
    parser.error('Proxy runtime or service profile does not exist')
source = Path(__file__).resolve().parent
output = args.output.resolve()
output.mkdir(parents=True, exist_ok=True)
command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--windowed', '--name', 'ResourceTool', '--distpath', str(output), '--workpath', str(output.parent / 'build-cache'), '--specpath', str(output.parent), '--exclude-module', 'UnityPy', '--exclude-module', 'PIL', '--collect-all', 'qrcode', '--add-data', str(source / 'ui.html') + ':.', '--add-data', str(source / 'phone.html') + ':.', '--add-data', str(source / 'proxy_addon.py') + ':.', '--add-data', str(source / 'resource_capture.py') + ':.', '--add-data', str(args.profile.resolve()) + ':profile-source', str(source / 'app.py')]
# Normalize the bundled profile basename without changing its content.
staging = output.parent / 'build-assets'
staging.mkdir(exist_ok=True)
shutil.copyfile(args.profile, staging / 'service-profile.json')
command[-1:-1] = ['--add-data', str(staging / 'service-profile.json') + ':.']
subprocess.run(command, check=True, env={**os.environ, 'PYINSTALLER_CONFIG_DIR': os.environ.get('PYINSTALLER_CONFIG_DIR', str(output.parent / 'pyinstaller-cache'))})
runtime = output / 'ResourceTool.app' / 'Contents' / 'Resources' / 'proxy-runtime' if sys.platform == 'darwin' else output / 'ResourceTool' / 'runtime'
runtime.mkdir(parents=True, exist_ok=True)
if sys.platform == 'darwin':
    target = runtime / 'mitmproxy.app'
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(args.proxy, target, symlinks=True)
    info_path = output / 'ResourceTool.app' / 'Contents' / 'Info.plist'
    with info_path.open('rb') as stream:
        info = plistlib.load(stream)
    info['LSUIElement'] = True
    with info_path.open('wb') as stream:
        plistlib.dump(info, stream)
else:
    # Official Windows portable distribution requires its companion runtime files.
    for item in args.proxy.parent.iterdir():
        target = runtime / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)
print('Offline runtime prepared at', output)
