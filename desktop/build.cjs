const { packager } = require('@electron/packager');
const path = require('node:path');
const fs = require('node:fs/promises');
const { execFileSync } = require('node:child_process');
const platform = process.argv[2];
const arch = process.argv[3];
if (!['darwin', 'win32'].includes(platform) || !['arm64', 'x64'].includes(arch)) throw Error('Unsupported target');
const project = path.resolve(__dirname, '..');
const staging = path.join(project, '.build/desktop-runtime', platform);
(async () => {
  await fs.rm(staging, { recursive: true, force: true });
  await fs.mkdir(path.join(staging, 'backend'), { recursive: true });
  const backendSource = process.env.RESOURCE_TOOL_BACKEND;
  if (!backendSource) throw new Error('Set RESOURCE_TOOL_BACKEND to the self-contained backend build');
  if (platform === 'darwin') {
    await fs.cp(backendSource.endsWith('.app') ? backendSource : path.join(backendSource, 'ResourceTool.app'), path.join(staging, 'backend/ResourceTool.app'), { recursive: true, dereference: false, verbatimSymlinks: true });
  } else {
    await fs.cp(backendSource, path.join(staging, 'backend'), { recursive: true });
  }
  await fs.copyFile(process.env.RESOURCE_TOOL_PROFILE || path.join(project, 'profiles/service-profile.example.json'), path.join(staging, 'service-profile.json'));
  await fs.copyFile(path.join(__dirname, 'AUTHORIZED_USE.txt'), path.join(staging, 'AUTHORIZED_USE.txt'));
  const tools = process.env.RESOURCE_TOOL_ADB_SOURCE;
  if (!tools) throw new Error('Set RESOURCE_TOOL_ADB_SOURCE to the official platform-tools directory');
  await fs.mkdir(path.join(staging, 'platform-tools'), { recursive: true });
  const files = platform === 'darwin' ? ['adb', 'NOTICE.txt'] : ['adb.exe', 'AdbWinApi.dll', 'AdbWinUsbApi.dll', 'NOTICE.txt'];
  for (const file of files) await fs.copyFile(path.join(tools, file), path.join(staging, 'platform-tools', file));
  if (platform === 'darwin') {
    const adb = path.join(staging, 'platform-tools/adb');
    const thin = path.join(staging, 'platform-tools/adb-thin');
    execFileSync('lipo', [adb, '-thin', arch, '-output', thin]);
    await fs.rename(thin, adb);
    await fs.chmod(adb, 0o755);
    execFileSync('codesign', ['--force', '--sign', '-', adb]);
  }
  const outputs = await packager({ dir: __dirname, name: 'Resource Toolkit', executableName: 'Resource Toolkit', appBundleId: 'com.resourcetoolkit.desktop', appVersion: '0.2.1', platform, arch, electronZipDir: require('node:fs').existsSync(path.join(project, '.build/downloads', 'electron-v45.3.1-' + platform + '-' + arch + '.zip')) ? path.join(project, '.build/downloads') : undefined, out: path.join(project, '.build/dist'), overwrite: true, asar: true, prune: true, ignore: [/\/runtime($|\/)/, /\/build\.cjs$/, /\/package-lock\.json$/] });
  const resources = platform === 'darwin' ? path.join(outputs[0], 'Resource Toolkit.app/Contents/Resources') : path.join(outputs[0], 'resources');
  for (const file of ['backend', 'platform-tools', 'service-profile.json', 'AUTHORIZED_USE.txt']) {
    await fs.cp(path.join(staging, file), path.join(resources, file), { recursive: true, verbatimSymlinks: true });
  }
  const localeDir = platform === 'darwin' ? path.join(outputs[0], 'Resource Toolkit.app/Contents/Frameworks/Electron Framework.framework/Versions/A/Resources') : path.join(outputs[0], 'locales');
  for (const file of await fs.readdir(localeDir)) {
    const remove = platform === 'darwin' ? file.endsWith('.lproj') && !/^(en|zh)(_|\.)/.test(file) : file.endsWith('.pak') && !['en-US.pak', 'zh-CN.pak', 'zh-TW.pak'].includes(file);
    if (remove) await fs.rm(path.join(localeDir, file), { recursive: true, force: true });
  }
  if (platform === 'darwin') {
    const clean = path.join(require('node:os').tmpdir(), 'resource-toolkit-release');
    await fs.mkdir(clean, { recursive: true });
    const app = path.join(clean, 'Resource Toolkit.app');
    await fs.rm(app, { recursive: true, force: true });
    execFileSync('ditto', ['--noextattr', '--norsrc', path.join(outputs[0], 'Resource Toolkit.app'), app]);
    execFileSync('xattr', ['-cr', app]);
    const serviceApp = path.join(app, 'Contents/Resources/backend/ResourceTool.app');
    execFileSync('codesign', ['--force', '--deep', '--sign', '-', path.join(serviceApp, 'Contents/Resources/proxy-runtime/mitmproxy.app')]);
    execFileSync('codesign', ['--force', '--deep', '--sign', '-', serviceApp]);
    execFileSync('codesign', ['--force', '--deep', '--sign', '-', app]);
    execFileSync('codesign', ['--verify', '--deep', '--strict', app]);
    console.log('Signed app:', app);
  }
  console.log(outputs[0]);
})().catch(error => { console.error(error); process.exitCode = 1; });
