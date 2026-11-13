const $ = id => document.getElementById(id);
let ready = false;
let busy = false;
let step = 0;
let serial = '';
let target = '';
async function task(callback) {
  if (busy) return;
  busy = true;
  $('message').textContent = '正在处理，请保持手机连接…';
  document.querySelectorAll('button').forEach(b => b.disabled = true);
  try { await callback(); }
  catch (error) { $('message').textContent = error.message.replace(/^Error invoking remote method '[^']+': Error: /, ''); }
  finally { busy = false; document.querySelectorAll('button').forEach(b => b.disabled = false); }
}
function selectOptions(rows, getValue, getLabel) {
  const select = document.createElement('select');
  for (const row of rows) {
    const option = document.createElement('option'); option.value = getValue(row); option.textContent = getLabel(row); select.append(option);
  }
  return select;
}
function showAndroid() {
  $('stepLabel').textContent = (step + 1) + ' / 3';
  $('androidContent').replaceChildren();
  $('back').hidden = step === 0;
  $('next').hidden = step === 2;
  if (step === 0) {
    $('androidTitle').textContent = '用数据线连接手机';
    $('androidIntro').textContent = '开启 USB 调试，在手机上点“允许此电脑调试”。';
    $('androidContent').textContent = '手机保持解锁。点击“检查连接”，再选择要操作的设备。';
    $('next').textContent = '检查连接';
  } else if (step === 1) {
    $('androidTitle').textContent = '选择车型资源';
    $('androidIntro').textContent = '先在手机应用中打开车辆页面，确保模型已经下载。';
    $('next').textContent = '扫描资源';
    $('androidContent').textContent = '点击“扫描资源”查找手机上的车型目录。';
  } else {
    $('androidTitle').textContent = '管理模型资源';
    $('androidIntro').textContent = target.split('/').pop();
    const group = document.createElement('div'); group.className = 'actionsGroup';
    for (const [action, label, description] of [['capture', '导出当前手机中的模型', '保存到电脑，用于编辑或备份'], ['apply', '替换手机当前模型', '选择电脑中的模型 ZIP']]) {
      const button = document.createElement('button');
      const title = document.createElement('strong');
      title.textContent = label;
      const detail = document.createElement('span');
      detail.textContent = description;
      button.append(title, detail);
      button.onclick = () => task(async () => {
        const result = await window.helper.call(action, { serial, target });
        $('message').textContent = result.cancelled ? '' : result.message || (result.saved ? '模型 ZIP 已保存，可导入网页编辑。' : '原包已获取，导出时可重新选择保存位置。');
      }); group.append(button);
    }
    $('androidContent').append(group);
  }
  $('message').textContent = '';
}
$('android').onclick = () => { if (!ready) return; $('platform').hidden = true; $('androidPanel').hidden = false; $('home').hidden = false; step = 0; showAndroid(); };
$('iphone').onclick = () => task(async () => {
  if (!ready) throw new Error('请等待内置服务启动');
  $('iphoneFrame').src = await window.helper.call('iphone');
  $('iphoneFrame').hidden = false; $('main').hidden = true; $('home').hidden = false; $('message').textContent = '';
});
$('home').onclick = () => task(async () => {
  const result = await window.helper.call('home');
  if (result.cancelled) { $('message').textContent = ''; return; }
  $('iphoneFrame').hidden = true; $('main').hidden = false; $('androidPanel').hidden = true; $('platform').hidden = false; $('home').hidden = true; $('message').textContent = '';
});
$('back').onclick = () => { step = Math.max(0, step - 1); showAndroid(); };
$('next').onclick = () => task(async () => {
  if (step === 0) {
    const existing = $('devices');
    if (existing) { serial = existing.value; step = 1; showAndroid(); return; }
    const devices = await window.helper.call('devices');
    const authorized = devices.filter(d => d.state === 'device');
    if (!authorized.length) throw new Error(devices.some(d => d.state === 'unauthorized') ? '请在手机上允许 USB 调试，再检查连接。' : '尚未发现已授权手机，请检查数据线和 USB 调试。');
    const select = selectOptions(authorized, d => d.serial, d => d.model + ' · ' + d.serial);select.id = 'devices';
    $('androidContent').replaceChildren(select); $('next').textContent = '使用这台手机'; $('message').textContent = '';
  } else {
    const existing = $('targets');
    if (existing) { target = existing.value; const result = await window.helper.call('prepare', { serial, target }); if (result.cancelled) { $('message').textContent = '已取消保存，可重新选择备份或暂不备份。'; return; } step = 2; showAndroid(); return; }
    const resources = await window.helper.call('scan', { serial });
    if (!resources.length) throw new Error('没有找到已下载的模型资源，请先在手机车辆页面完成模型下载。');
    const select = selectOptions(resources, r => r, r => r.split('/').slice(-2).join(' / '));select.id = 'targets';
    $('androidContent').replaceChildren(select); $('next').textContent = '使用这个资源'; $('message').textContent = '';
  }
});
$('diagnostics').onclick = () => task(async () => { await window.helper.call('diagnostics'); $('message').textContent = ''; });
window.helper.onReady(() => { ready = true; $('connection').textContent = '本地服务已就绪'; });
window.helper.onFailure(() => { ready = false; $('connection').textContent = '服务已停止，请重新打开助手'; });
window.helper.call('status').then(() => { ready = true; $('connection').textContent = '本地服务已就绪'; }).catch(() => {});
