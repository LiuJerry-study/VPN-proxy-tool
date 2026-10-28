# 轻连 VPN

轻连是一个面向 Windows 的简洁本地代理客户端。它复用你已有的 sing-box 核心和节点，不需要注册账号，也不会连接任何第三方服务器。

> 轻连不提供节点，只管理你导入的节点和订阅。

## 快速开始

从 [Releases](https://github.com/LiuJerry-study/VPN-proxy-tool/releases) 下载最新的 Windows exe：

1. 把 exe 放到桌面或其他普通可写文件夹。
2. 双击运行。
3. 第一次启动会自动读取 exe 同级的 `v2rayN-windows-64`，迁移其中的节点；也可以在“订阅管理”里手动指定目录。
4. 选择节点，点击中间的大圆形按钮连接。
5. 连接会默认接管 Windows 系统代理；退出轻连或点击断开时会恢复原来的代理设置。

如果当前目录被系统或沙箱标记为低完整性，Edge 界面可能显示为空白页；把 exe 复制到普通目录后再运行即可。

单文件版不需要安装 Python，只需要 Windows 10/11 自带的 Microsoft Edge，或已安装的 Google Chrome。exe 可以单独复制到任何可写目录使用。

单文件版默认把数据保存在当前 Windows 用户目录，不会在 exe 旁边生成任何文件夹：

```text
%USERPROFILE%\AppData\LocalLow\QingLian\data\store.json
%USERPROFILE%\AppData\LocalLow\QingLian\runtime\
```

如果确实需要便携模式，可以在 exe 同目录放一个空的 `portable.flag` 文件，此时数据会改为放在 exe 旁边。旧版本留下的 `data/store.json` 会在首次启动新版本时自动复制到新位置。

**单文件 exe 内不包含任何节点、订阅、账号或已保存设置。**收到的 exe 第一次运行时仍是干净状态，会从当前电脑的 v2rayN 自动导入节点（如果存在）。

源码调试模式才需要 Python 3.10 以上：

- `启动轻连.vbs`
- `启动轻连.bat`
- 或执行 `pythonw app.py`

## 功能

- 一键连接 / 断开，系统代理自动接管与恢复
- 自动导入 v2rayN 节点，不修改 v2rayN 的数据库和配置
- 支持订阅链接：v2rayN 分享链接、base64 订阅、Clash / Mihomo YAML 订阅
- 支持手动粘贴 VLESS、VMess、Trojan、Hysteria2、TUIC、Shadowsocks、SOCKS、HTTP 节点
- 剪贴板一键导入节点/订阅，一键导出全部节点分享链接
- 节点延迟测试和全部测速
- 节点列表多选：批量复制分享链接、批量删除节点
- 规则分流 / 全局代理 / 直连三种模式
- 实时上下行速度、本次流量和运行时长
- 关闭窗口后留在系统托盘，托盘菜单适配 Windows 高 DPI 缩放
- 开机自动启动（在设置中自行开启）
- TUN 模式（需要管理员权限）
- 本地运行日志查看和敏感信息脱敏

## 分流模式

| 模式 | 行为 |
| --- | --- |
| 规则 | 国内域名/IP 直连，常见广告域名拦截，其他流量走代理 |
| 全局 | 所有流量走代理，局域网和私有地址直连 |
| 直连 | 暂停代理，只保留本地端口和系统代理框架 |

## 订阅和节点

### 从 v2rayN 迁移

默认是 exe 同级的 `v2rayN-windows-64` 文件夹。如果 v2rayN 在别的位置：

1. 打开“订阅管理”。
2. 在“从 v2rayN 迁移”区域点击“修改”。
3. 选择包含 `guiConfigs` 和 `bin` 的目录。
4. 点击“重新导入 v2rayN 节点”。

### 添加订阅

在“订阅管理”里粘贴订阅地址即可。支持：

- v2rayN 的 base64 节点列表
- 明文分享链接列表
- Clash / Mihomo 的 YAML 订阅

订阅更新会保留节点的稳定 ID，尽量保持当前选中节点不变。

### 手动导入

点击右上角 **+** 或“粘贴节点链接”，每行一个分享链接。支持：

```text
vless://...
vmess://...
trojan://...
hysteria2://...
tuic://...
ss://...
```

### 剪贴板一键导入 / 导出

在“节点列表”页：

- 点击 **“剪贴板导入”**：自动读取 Windows 剪贴板。剪贴板里可以是分享链接、base64 节点列表、Clash/Mihomo YAML，或一个 http(s) 订阅地址。
  - 如果是一次性分享链接，会作为普通节点导入。
  - 如果是一个订阅地址，会自动添加订阅；已经存在同一地址时改为更新订阅。
  - 重复节点会自动跳过，不会连续导入出多份。
- 点击 **“导出剪贴板”**：把当前全部节点的分享链接写入 Windows 剪贴板，每行一个，可直接粘贴到其他客户端。

> 导出的分享链接包含节点认证信息，请只粘贴到可信的客户端或位置。

### 多选与批量操作

在“节点列表”页点击 **“多选”** 进入多选模式：

- 点击节点卡片勾选或取消；按住 `Shift` 点击可连续选择；`Ctrl + A` 全选当前筛选结果，`Esc` 退出多选。
- **复制链接**：把选中节点的分享链接复制到剪贴板，每行一个，只包含选中的节点。
- **删除**：确认后批量删除选中的节点。只删除轻连本地的节点记录，不影响 v2rayN 或订阅服务端；如果包含当前连接的节点，会先断开并自动切换到其他节点。

> 复制的分享链接包含节点认证信息，请只粘贴到可信的客户端或位置。

## 设置

- **本地混合端口**：默认 `2080`，同时提供 HTTP 和 SOCKS5。
- **自动设置系统代理**：连接时写入当前 Windows 用户的系统代理设置，断开时恢复原值。
- **启动后自动连接**：打开轻连时自动连接上次选中的节点。
- **TUN 模式**：接管全部系统流量，需要管理员权限。开启后轻连会请求管理员身份重启，并自动恢复连接。
- **关闭窗口时留在托盘**：默认开启；可在托盘菜单里重新打开或退出。
- **开机自动启动**：只写入当前用户的启动项，关闭开关会删除该项。

## 数据与隐私

单文件版的所有运行数据默认都在当前 Windows 用户目录下：

| 路径 | 内容 |
| --- | --- |
| `%USERPROFILE%\AppData\LocalLow\QingLian\data\store.json` | 节点、订阅、偏好设置。包含节点认证信息，请勿分享 |
| `%USERPROFILE%\AppData\LocalLow\QingLian\runtime\` | 临时配置、日志、浏览器窗口数据、系统代理备份 |
| `core/`（打包在 exe 内） | sing-box 核心和分流规则 |

界面设置页会显示实际数据路径，并提供“打开数据目录”按钮。

如果 exe 同目录存在 `portable.flag`，则数据改存放到 exe 旁边的 `data` 和 `runtime` 目录，适合 U 盘等便携使用场景。

轻连的后端只监听 `127.0.0.1` 的随机端口。节点密码不会通过界面接口返回，运行日志也会做脱敏处理。

系统代理和开机启动是用户主动开启的 Windows 功能：

- 系统代理修改的是当前用户的 Internet Settings，断开或退出时恢复之前保存的值。
- 开机启动修改的是当前用户 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 下的 `QingLianVPN` 项。
- TUN 模式会创建虚拟网卡和路由，不开启时不会影响系统网络。

## 常见问题

### 双击后没有窗口

先确认 Edge 或 Chrome 已安装。轻连使用独立的应用窗口运行界面；不会在浏览器里打开普通标签页。

如果托盘已经出现，点击“打开轻连”即可重新打开窗口。

### 端口被占用

在“偏好设置”中把本地端口换成 `1024–65535` 之间的其他值，再重新连接。

### TUN 模式无法开启

TUN 需要管理员权限。打开开关时会弹出权限确认；如果取消 UAC，设置不会改变。

### 连接成功但浏览器没有走代理

确认“自动设置系统代理”已开启，并且没有其他代理软件同时接管系统代理。部分应用会使用自己的代理设置，需要在应用内单独配置。

### 如何退出

右键系统托盘里的轻连图标，选择“退出轻连”；或打开“偏好设置 → 应用 → 退出轻连”。

## 目录结构

```text
vpn/
├─ app.py                 源码入口
├─ backend/               本地 API、sing-box 配置、节点和订阅解析
├─ web/                   界面 HTML / CSS / JavaScript
├─ core/                  sing-box.exe、wintun.dll、分流规则
├─ vendor/                源码模式的 Python 依赖
├─ assets/                应用图标和版本信息
├─ 启动轻连.vbs
└─ README.md
```

编译好的 Windows exe 在 [Releases](https://github.com/LiuJerry-study/VPN-proxy-tool/releases) 页面。

源码模式下，`data` 和 `runtime` 会生成在源码目录；单文件版默认使用 `%USERPROFILE%\AppData\LocalLow\QingLian`，只有在放置 `portable.flag` 时才使用 exe 旁边目录。

## 数字签名

当前生成的是**未签名**测试版。要让 Windows 和 SmartScreen 在别的电脑上也信任它，需要一份由受信任 CA 签发的代码签名证书（通常导出为 `.pfx`），然后使用 Windows SDK 的 `signtool.exe` 进行 SHA-256 签名并加时间戳。

自签名证书只能在本机手动信任，不能解决其他电脑上的“未知发布者”提示。如果你有代码签名证书，可以直接把 `.pfx` 和密码交给构建环境完成签名。

签名命令示例：

```powershell
signtool sign /fd SHA256 /f your-cert.pfx /p your-password `
  /tr http://timestamp.digicert.com /td SHA256 "轻连 VPN.exe"
```

## 许可

轻连自身代码采用 MIT 许可。内置的 sing-box 和规则数据有各自的许可，详见 `THIRD_PARTY_NOTICES.md`。
