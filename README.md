# 资源工具 · Resource Toolkit

基于 Electron 的本地资源提取与导入助手，支持 macOS 与 Windows。

- Android：通过 USB 调试和随软件分发的 ADB 导出、替换当前模型资源。
- iPhone：通过局域网代理和用户信任的 mitmproxy 证书获取原包、返回定制包。
- 资源在电脑与手机之间传输，不上传到项目服务器。

这是已有车辆模型资源链路的通用界面版本，并非任意 App 的通用提取器。目标服务的公开域名、路径和 Android 包名在 `profiles/service-profile.example.json` 中配置。请仅操作自己有权访问和修改的资源。本仓库不包含第三方模型、测试原包、账户信息、证书或私钥。

## 使用边界

Android 和 iOS 的 Unity Shader/平台目标不同，不能直接互换资源包。首次替换前建议导出备份；恢复时使用同一替换操作选择备份 ZIP。

iPhone 保留本机资源时，助手使用两阶段下载：先返回临时更新标识触发获取，再在下一次资源检查中使用官方资源身份返回相同定制文件。临时标识不代表 ZIP 的真实 MD5；实际传输文件的校验与官方身份分开记录。两阶段完成前保持代理与助手运行。完成后关闭 iPhone 当前 Wi-Fi 的手动代理，再退出助手。

已在 Mac 上运行的 iOS App 验证官方身份收尾后冷启动保留定制字节。随机触发标识的完整新流程、真实 iPhone 持久化效果和 Windows 实机仍需验收。响应已返回不等于模型已激活；最终需要用户确认。官方资源更新或校验机制变化可能覆盖定制内容，不承诺永久保留。

## 开发

需要 Node.js/npm；后端开发需要兼容 mitmproxy 12 的 Python。终端位于仓库根目录：

```sh
python3 -m venv .build/venv
.build/venv/bin/python -m pip install -r backend/requirements.txt
cd desktop
npm ci
```

桌面开发启动前，将完整后端、平台工具和配置放到 `desktop/runtime/`，其结构与发行版资源目录相同：

```text
runtime/
  service-profile.json
  platform-tools/adb                 # Windows 为 adb.exe 及其两个 DLL
  backend/
    ResourceTool.app/                # macOS
    runtime/python.exe              # Windows
    app.py                          # Windows，及其余后端文件
```

然后运行 `npm start`。也可单独运行 `backend/app.py` 调试后端；服务配置位置由 `RESOURCE_TOOL_DATA` 指定，目录中应有 `service-profile.json`。不要把运行数据提交到 Git。

## 构建

在各目标系统上构建。运行时下载、依赖安装发生在开发者构建阶段；最终软件包含 Python/代理与 ADB，用户无需安装这些开发环境。

macOS：安装 PyInstaller，取得官方 `mitmproxy.app`，执行：

```sh
.build/venv/bin/python -m pip install pyinstaller
.build/venv/bin/python backend/build_offline.py --proxy /path/to/mitmproxy.app --profile profiles/service-profile.example.json --output .build/backend
cd desktop
RESOURCE_TOOL_BACKEND="/absolute/path/to/.build/backend/ResourceTool.app" RESOURCE_TOOL_ADB_SOURCE="/path/to/platform-tools" npm run build:mac
```

脚本输出临时目录中完成 ad-hoc 签名的 App 路径。ad-hoc 签名不等于 Apple 公证，下载后系统仍可能要求用户在隐私与安全性中允许打开。

Windows：使用 x64 Python 3.13，并从 Python 官网下载对应的 3.13 Windows embeddable package：

```powershell
py -3.13 scripts/prepare_windows.py --python-zip C:\downloads\python-3.13.9-embed-amd64.zip --output .build\backend
cd desktop
$env:RESOURCE_TOOL_BACKEND="C:\absolute\project\.build\backend"
$env:RESOURCE_TOOL_ADB_SOURCE="C:\downloads\platform-tools"
npm run build:win
```

ADB 必须来自官方 Android platform-tools，并保留 NOTICE.txt 和 Windows DLL。运行时与依赖各自适用上游许可证；MIT 仅覆盖本项目代码。构建前自行校验上游下载完整性。

## 测试

从仓库根目录运行（合成数据，不连接手机）：

```sh
python3 tests/resource_identity.py
python3 tests/configuration_export.py
```

测试覆盖资源身份收尾、分段响应、身份变化保护，以及可选配置协议的哈希绑定。可选 `current_configuration.json` 沿用 `coolgaipai-current-configuration-v1` 协议标识以兼容已有网页；未知配置不猜测，不影响模型导出。iPhone 不要求获取选配信息。

## 开源范围

源码使用 MIT。目标应用、第三方资源及运行环境的权利属于各自权利人。提交问题前请删除日志中的账号信息、设备标识、访问链接和令牌；不要上传证书私钥或车辆模型。
