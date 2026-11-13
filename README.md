# 资源工具 · Resource Toolkit

基于 Electron 的本地资源提取与导入助手，支持 macOS 与 Windows。

- Android：通过 USB 调试和随软件分发的 ADB 导出、替换当前模型资源。
- iPhone：通过局域网代理和用户信任的 mitmproxy 证书获取原包、返回定制包。
- 资源在电脑与手机之间传输，不上传到项目服务器。

这是已有车辆模型资源链路的通用界面版本，并非任意 App 的通用提取器。目标服务的公开域名、路径和 Android 包名在 `profiles/service-profile.example.json` 中配置。请仅操作自己有权访问和修改的资源。本仓库不包含第三方模型、测试原包、账户信息、证书或私钥。

## 使用边界

Android 和 iOS 的 Unity Shader/平台目标不同，不能直接互换资源包。首次替换前建议导出备份；恢复时使用同一替换操作选择备份 ZIP。

iPhone 安装流程分为应用、收尾和保留验证。先划掉车辆 App，再重新进入爱车模型页面，保持前台至少 15 秒。新外观可能在后续启动才显示，看到新外观后再确认进入收尾。完成收尾后再次重开验证，随后关闭手机 Wi-Fi 代理，停止电脑代理，正常联网重开两次确认保留。

助手分别记录 ZIP 的真实 MD5/SHA256 和接口返回的更新标识。收到资源请求或准备好响应字节时，继续等待用户确认手机外观。官方标识收尾前需要用户确认定制模型已启用。检测到官方资源重新下载或目标资源变化时，停止成功判定并提示重新应用。

上述先启用、再收尾的顺序已在 iPhone 的 App 6.10.0、对应 ET5T 资源上观察到退出代理后保留定制颜色。0.2.1 的自动化回放、Mac 启动和界面已验证，完整新版真机流程与 Windows 实机仍需复核。官方后续资源更新可能再次替换模型。

Windows 安装包内部目录与运行数据目录使用英文。数据保存在 `%APPDATA%\ResourceToolkit`，升级时兼容迁移旧版证书和备份。Android 文件通过 ADB 标准输入输出传输，ADB 不接收电脑临时文件路径。写入前检查手机权限，优先使用临时目录校验后切换。手机拒绝创建同级目录时，仅在现有车型目录通过写入检查后使用本地完整备份、原目录写入、逐文件校验与失败回滚。完全拒绝写入时在修改前停止。

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
python3 tests/android_transport.py
python3 tests/confirmation_api.py
node tests/iphone_ui.cjs
```

测试覆盖用户确认后收尾、最低等待时间、分段响应、会话隔离、官方透传验证、Android 中文路径的二进制传输及权限受限时的失败恢复，以及可选配置协议的哈希绑定。可选 `current_configuration.json` 沿用 `coolgaipai-current-configuration-v1` 协议标识以兼容已有网页；未知配置不猜测，不影响模型导出。iPhone 不要求获取选配信息。

## 开源范围

源码使用 MIT。目标应用、第三方资源及运行环境的权利属于各自权利人。提交问题前请删除日志中的账号信息、设备标识、访问链接和令牌；不要上传证书私钥或车辆模型。
