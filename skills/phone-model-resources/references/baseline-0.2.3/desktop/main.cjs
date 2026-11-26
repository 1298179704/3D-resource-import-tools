const { app, BrowserWindow, ipcMain, dialog } = require('electron');
const { spawn } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs/promises');
const crypto = require('node:crypto');
const readline = require('node:readline');
let window;
let backend;
let endpoint;
let quitting = false;
let operation = false;
const root = app.isPackaged ? process.resourcesPath : path.join(__dirname, 'runtime');
const homePage = path.join(__dirname, 'index.html');
app.setPath('userData', path.join(app.getPath('appData'), 'ResourceToolkit'));


function proxyExitOptions(status, destination) {
  const unfinished = status?.diagnostics?.mode === 'install' && status.diagnostics.phase !== 'complete';
  return {
    type: unfinished ? 'warning' : 'question',
    buttons: ['返回继续', '已关闭手机代理，' + (unfinished ? '中止并' : '') + destination],
    defaultId: 0,
    cancelId: 0,
    message: unfinished ? '资源收尾尚未完成' : '请先关闭 iPhone 的 Wi-Fi 代理',
    detail: (unfinished ? '现在中止，模型可能在后续启动恢复原厂。建议先完成收尾与保留验证。\n\n' : '') + '设置 → Wi-Fi → 当前网络 ⓘ → 配置代理 → 关闭 → 存储'
  };
}

async function request(name, data) {
  if (!endpoint) throw new Error('本地服务尚未启动');
  const url = new URL('/api/' + name, endpoint);
  url.search = new URL(endpoint).search;
  const response = await fetch(url, data === undefined ? {} : { method: 'POST', body: JSON.stringify(data), headers: { 'Content-Type': 'application/json' } });
  if (!response.ok) {
    const text = await response.text();
    let message = text;
    try { message = JSON.parse(text).error || text; } catch {}
    throw new Error(message);
  }
  return response.json();
}

async function migrateLocalSettings() {
  const destination = app.getPath('userData');
  await fs.mkdir(destination, { recursive: true });
  for (const previous of ['资源工具', '酷改派助手', 'Coolgaipai Desktop', 'Coolgaipai']) {
    const source = path.join(app.getPath('appData'), previous);
    for (const relative of ['service/certificates', 'service/android-backups', 'backup-prompts.json']) {
      const target = path.join(destination, relative);
      try { await fs.access(target); continue; } catch {}
      const original = path.join(source, relative);
      try { await fs.access(original); } catch { continue; }
      await fs.mkdir(path.dirname(target), { recursive: true });
      await fs.cp(original, target, { recursive: true, errorOnExist: true, force: false });
    }
  }
}

async function startBackend() {
  await migrateLocalSettings();
  const state = path.join(app.getPath('userData'), 'service');
  await fs.mkdir(state, { recursive: true });
  const service = path.join(root, 'backend');
  const executable = process.platform === 'darwin'
    ? path.join(service, 'ResourceTool.app/Contents/MacOS/ResourceTool')
    : path.join(service, 'runtime/python.exe');
  const args = process.platform === 'darwin' ? [] : [path.join(service, 'app.py')];
  const adb = path.join(root, 'platform-tools', process.platform === 'win32' ? 'adb.exe' : 'adb');
  await fs.copyFile(path.join(root, 'service-profile.json'), path.join(state, 'service-profile.json'));
  backend = spawn(executable, args, { windowsHide: true, env: { ...process.env, RESOURCE_TOOL_DATA: state, RESOURCE_TOOL_NO_BROWSER: '1', RESOURCE_TOOL_DESKTOP: '1', RESOURCE_TOOL_ADB: adb, RESOURCE_TOOL_CONTROL_TOKEN: crypto.randomBytes(24).toString('hex') }, stdio: ['ignore', 'pipe', 'pipe'] });
  const log = await fs.open(path.join(state, 'desktop-service.log'), 'a');
  backend.stderr.on('data', bytes => log.write(bytes).catch(() => {}));
  backend.on('exit', () => { log.close().catch(() => {}); if (!quitting) window?.webContents.send('service-failed'); });
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('内置服务启动超时，请重新打开助手')), 30000);
    const lines = readline.createInterface({ input: backend.stdout });
    lines.on('line', line => {
      if (/^http:\/\/127\.0\.0\.1:\d+\/\?key=/.test(line)) {
        endpoint = line;
        clearTimeout(timer);
        resolve();
      }
    });
    backend.once('error', error => { clearTimeout(timer); reject(error); });
    backend.once('exit', code => { clearTimeout(timer); reject(new Error('内置服务已退出，代码 ' + code)); });
  });
}

function register() {
  ipcMain.handle('helper', async (event, action, data = {}) => {
    if (event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame || !event.senderFrame.url.startsWith('file:')) throw new Error('页面来源无效');
    if (action === 'usage') {
      const detail = await fs.readFile(path.join(__dirname, 'AUTHORIZED_USE.txt'), 'utf8');
      await dialog.showMessageBox(window, { type: 'info', title: '授权使用说明', message: '仅供学习研究，请仅处理已获授权的资源', detail, buttons: ['我已了解'] });
      return {};
    }
    if (action === 'status') return request('status');
    if (action === 'iphone') return endpoint;
    if (action === 'home') {
      let status;
      try { status = await request('status'); } catch {}
      if (status?.running) {
        const choice = await dialog.showMessageBox(window, proxyExitOptions(status, '返回首页'));
        if (choice.response !== 1) return { cancelled: true };
        try { await request('stop', {}); }
        catch (error) { await dialog.showMessageBox(window, { type: 'error', message: '暂时无法结束当前操作', detail: error.message }); return { cancelled: true }; }
      }
      return {};
    }
    if (action === 'stop') return request('stop', {});
    if (action === 'diagnostics') {
      const report = await request('diagnostics');
      const { filePath } = await dialog.showSaveDialog(window, { defaultPath: 'helper-log.json' });
      if (filePath) await fs.writeFile(filePath, JSON.stringify(report, null, 2));
      return {};
    }
    if (!['devices', 'scan', 'capture', 'apply', 'prepare'].includes(action)) throw new Error('未知操作');
    if (operation) throw new Error('正在执行任务，请等待完成');
    operation = true;
    try {
      const preferencesPath = path.join(app.getPath('userData'), 'backup-prompts.json');
      const identity = crypto.createHash('sha256').update(String(data.serial) + '\n' + String(data.target)).digest('hex');
      let preferences = {};
      try { preferences = JSON.parse(await fs.readFile(preferencesPath, 'utf8')); } catch (error) { if (error.code !== 'ENOENT') throw error; }
      const preparing = action === 'prepare';
      if (preparing) {
        if (preferences[identity]) return {};
        const choice = await dialog.showMessageBox(window, { type: 'question', buttons: ['保存备份', '暂不备份'], defaultId: 0, cancelId: 1, message: '首次使用，要把当前模型备份到电脑吗？', detail: '以后需要恢复时，选择这份 ZIP 写入手机即可。备份保存手机当前的资源，请确认它是你希望保留的版本。' });
        if (choice.response === 1) {
          preferences[identity] = 'skipped';
          await fs.writeFile(preferencesPath, JSON.stringify(preferences));
          return {};
        }
        action = 'capture';
      }
      const body = { action, serial: data.serial, target: data.target };
      let exportPath;
      if (action === 'capture') {
        const choice = await dialog.showSaveDialog(window, { title: '保存当前模型备份', defaultPath: 'android-model-backup.zip', filters: [{ name: 'ZIP', extensions: ['zip'] }] });
        if (!choice.filePath) return { cancelled: true };
        exportPath = choice.filePath;
      }
      if (action === 'apply') {
        const { canceled, filePaths } = await dialog.showOpenDialog(window, { properties: ['openFile'], filters: [{ name: '完整车型资源', extensions: ['zip'] }] });
        if (canceled) return { cancelled: true };
        const stat = await fs.stat(filePaths[0]);
        if (stat.size > 256 * 1024 * 1024) throw new Error('资源文件超过 256 MB');
        body.payload = (await fs.readFile(filePaths[0])).toString('base64');
      }
      if (action === 'apply') {
        const result = await dialog.showMessageBox(window, { type: 'question', buttons: ['取消', '继续'], defaultId: 0, cancelId: 0, message: '替换手机当前模型？', detail: '替换时会关闭手机上的车辆应用，完成后请重新打开。' });
        if (result.response !== 1) return { cancelled: true };
      }
      const result = await request('android', body);
      if (action === 'capture') {
        const url = new URL('/api/android-original', endpoint); url.search = new URL(endpoint).search;
        const response = await fetch(url);
        if (!response.ok) throw new Error('导出失败');
        await fs.writeFile(exportPath, Buffer.from(await response.arrayBuffer()));
        result.saved = true;
        if (preparing) {
          preferences[identity] = 'saved';
          await fs.writeFile(preferencesPath, JSON.stringify(preferences));
        }
      }
      return result;
    } finally { operation = false; }
  });
}

async function closeApplication() {
  if (quitting) return;
  if (operation) { await dialog.showMessageBox(window, { message: '正在执行任务，请等待完成后退出。' }); return; }
  let status;
  try { status = await request('status'); } catch {}
  if (status?.running) {
    const result = await dialog.showMessageBox(window, proxyExitOptions(status, '退出'));
    if (result.response !== 1) return;
  }
  quitting = true;
  try { await request('quit', {}); } catch { backend?.kill(); }
  app.exit(0);
}

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => { window?.show(); window?.focus(); });
  app.whenReady().then(async () => {
    window = new BrowserWindow({ width: 1040, height: 800, minWidth: 800, minHeight: 640, backgroundColor: '#f4f6f3', title: '资源工具', autoHideMenuBar: true, webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, nodeIntegration: false, sandbox: true } });
    window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    window.webContents.on('will-navigate', event => event.preventDefault());
    window.on('close', event => { if (!quitting) { event.preventDefault(); closeApplication(); } });
    window.webContents.session.on('will-download', (event, item) => { item.setSaveDialogOptions({ title: '保存模型资源', defaultPath: item.getFilename() }); });
    register();
    await window.loadFile(homePage);
    try { await startBackend(); window.webContents.send('service-ready'); }
    catch (error) { await dialog.showMessageBox(window, { type: 'error', message: '助手启动失败', detail: error.message }); }
  });
  app.on('before-quit', event => { if (!quitting) { event.preventDefault(); closeApplication(); } });
}
