"""Run with Windows x64 Python 3.13; supply official Python 3.13 embeddable ZIP."""
import argparse, shutil, subprocess, sys, zipfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--python-zip',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
if sys.platform!='win32' or sys.version_info[:2]!=(3,13):raise SystemExit('Use Windows x64 Python 3.13')
root=Path(__file__).resolve().parents[1];dest=a.output.resolve();runtime=dest/'runtime';runtime.mkdir(parents=True,exist_ok=True)
with zipfile.ZipFile(a.python_zip) as z:
 for name in z.namelist():
  target=(runtime/name).resolve()
  if not target.is_relative_to(runtime):raise ValueError('Unsafe archive path')
 z.extractall(runtime)
subprocess.run([sys.executable,'-m','pip','install','--only-binary=:all:','--target',str(runtime/'Lib/site-packages'),'-r',str(root/'backend/requirements.txt')],check=True)
(runtime/'python313._pth').write_text('python313.zip\n.\nLib/site-packages\n..\nimport site\n')
for f in (root/'backend').iterdir():
 if f.suffix in ('.py','.html'):shutil.copy2(f,dest/f.name)
shutil.copy2(root/'profiles/service-profile.example.json',dest/'service-profile.json')
print(dest)
