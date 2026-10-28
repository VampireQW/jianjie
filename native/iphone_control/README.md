# iPhone BLE HID 实验组件

本组件使用 Windows 自带 BLE GATT 服务端与系统 .NET Framework，独立于 AirPlay 画面通道。当前已在本机普通桌面用户会话验证服务创建、加密特征创建与短时发布。iPhone 配对、实际鼠标/键盘输入与重连尚待真机验证。不要把 `ready` 当作已经控制手机。

## 运行与构建

- 程序：`control.exe`，x64（具体大小与 SHA-256 见 `manifest.json`）。
- 构建：`C:\python.exe native\iphone_control\build.py --force`，也可在打包代码调用该模块的 `build(force=False)`，返回 EXE 路径。
- 有界试验：`control.exe --probe 3`；仅发布 3 秒，无配对操作、无输入，正常停止并退出。`--probe-seconds 3` 同义。
- 标准模式：`control.exe`，从 stdin 读 UTF-8 JSON Lines，stdout 输出同格式事件。可使用 `CREATE_NO_WINDOW`。
- `--diagnostic` 开启每次通知的 `reportSent` 事件；默认关闭成功报文输出，避免高频鼠标事件淹没界面。
- 应在已登录桌面用户的普通权限会话启动。低完整性 AppContainer 会明确拒绝；无需管理员权限。

没有 Windows Kits/C++/WinRT SDK 的本机使用系统 `csc.exe` 与 `WinMetadata` 编译，无 NuGet、Python 包、第三方驱动或安装器。`WinRtProbe.cs` 和 PowerShell 脚本仅用于研究探针，不是常驻产品进程。

## 输入协议

```json
{"cmd":"mouse","buttons":0,"dx":12,"dy":-6,"wheel":0}
{"cmd":"mouse","buttons":1,"dx":0,"dy":0,"wheel":0}
{"cmd":"mouse","buttons":0,"dx":0,"dy":0,"wheel":0}
{"cmd":"key","mod":2,"keys":[4]}
{"cmd":"key","mod":0,"keys":[]}
{"cmd":"release"}
{"cmd":"status"}
{"cmd":"quit"}
```

鼠标 `buttons` 为 0–7，bit0 左键、bit1 右键、bit2 中键；`dx/dy/wheel` 为 -127–127 的整数，滚轮正值向上。数值超界会拒绝，调用方应拆分较大位移。键盘为 HID Usage Page 0x07 键码，最多同时 6 键，`mod` 为 8 位标准修饰键。示例 `keys:[4],mod:2` 为左 Shift+A。每次报告提供完整当前按键状态，按下后必须发送对应的抬起或 `release`。

stdin EOF、`quit`、Ctrl+C 均先尝试将鼠标和键盘全清零，再调用 `StopAdvertising` 并退出。组件不安装全局键鼠钩子、不捕获电脑输入；GUI 层负责前台限制、Esc 退出与焦点丢失释放。

`mouse/key/release/quit` 可带整数 `seq`。无论命令完成或失败，最终会输出一次 `inputAck`；没有 `seq` 的命令不增加 ACK，保持实验脚本兼容。调用方每次只写入一个输入命令，收到对应 ACK 后再写下一个，避免将旧输入堆积在管道中。ACK 不包含按键或鼠标输入内容，也不表示手机已实际执行操作。`quit` 在正常清理完成后 ACK，因此 `stopped` 可能先于该 ACK。`ready.inputAckSupported=true`、`notifyTimeoutMs=1000` 声明此协议。

```json
{"type":"inputAck","seq":123,"elapsedMs":1.1,"notifyMs":0.8,"notifyCount":1,"time":"..."}
```

`elapsedMs` 是本进程解析到完成命令的处理耗时，`notifyMs` 是该命令中实际 Notify 调用及等待的合计耗时，`notifyCount` 为实际调用次数；无订阅拒绝时后两项为零。这些数字不含手机执行与显示耗时。`ready.ackTimingSupported=true` 声明数值可用。

单次通知超时为 1 秒。释放分别尽力通知鼠标和键盘，非终止类失败仍会继续另一个；此类失败聚合为至多一次 `stage=release` 错误，且本地状态最终清零。调用方应把此阶段作为释放结果处理，避免再次触发释放循环。

**通知超时属于终止故障**：Cancel 请求不代表旧 WinRT 操作已经结束，因此本会话禁止后续任何 Notify，包括发送零状态释放。本地状态立即清零，输出一次 `stage=transport`、`code=NOTIFY_TIMEOUT_TERMINAL` 错误，仍 ACK 当前 seq，然后正常 StopAdvertising 并以 `reason=transport-fault`、退出码 3 结束；需用户重新开启控制。拿到 operation 后，回调注册等 await 阶段的其他异常也保守终止（`NOTIFY_TRANSPORT_TERMINAL`）；未返回 operation 的普通同步 factory 异常保持原处理。迟到完成仅关闭旧操作，不恢复会话。Cleanup 与进行中的 Notify 冲突时也采用此规则，不并行追加释放通知。

0.1.2 通过 WinRT `IAsyncOperation.Completed` 回调与 TaskCompletionSource 等待完成，取消原 5 ms 轮询。仍然单命令在途、串行 Notify，不并行发送按钮。`status/stopped.performance` 才输出处理及 Notify 次数、均值、最大值、失败、超时和 `transportTerminal` 等聚合统计；不添加逐键诊断事件。纯 fake 操作基准和生产 JSON 调度器测试均不访问蓝牙；真实手机 RTT 待新版采样。

服务 UUID 为 0x1812，Report Map 为 103 字节，鼠标 Report ID=1，键盘 Report ID=2。两个 0x2A4D 输入特征的 0x2908 Report Reference 分别为 `[1,1]`、`[2,1]`。GATT 报告值不包含 Report ID；鼠标值为 4 字节 `buttons,x,y,wheel`，键盘为 8 字节 `modifier,reserved,key1..key6`。Report Map、HID Information、输入报告和 Report Reference 读取均要求链路加密，HID Control Point 写入同样要求加密。使用 Report Protocol，不提供 Boot Protocol 或绝对触摸定位。

## 事件语义

- `capability`：真实适配器能力、用户、Session 与当前名称。
- `advertisement`：原始 Windows 广告状态；本机曾先出现 `Aborted/Error=Success` 后转为 `Started`，应等待 `ready`。
- `ready`：本地 HID 服务已发布，可等待 iPhone；`inputVerified=false`，没有推断真机成功。
- `subscribed`：`mouseSubscribed`、`keyboardSubscribed` 与各自订阅数。键盘单独订阅不会触发鼠标控制可用。
- `connected` / `disconnected`：真实鼠标订阅从无到有 / 从有到无；包含两类订阅状态。它表示输入通道订阅状态，不是物理操作验收结果。
- `peer`：从真实订阅客户端查询的 `paired`、连接状态；不主动发起配对。
- `error`：阶段、错误码 / HRESULT；无订阅输入返回 `NO_SUBSCRIBERS`，不伪造发送成功。
- `status`：广告状态、暂停标记、进程私有内存与工作集、通知成功计数。
- `inputAck`：带 `seq` 输入命令已处理完毕；调用方工作线程直接消费，不需要派发到 GUI 事件循环。
- `stopped`：退出清理已执行，包含停止广告调用标记。Windows 在本机停止后仍可能保留旧 `Started` 属性；必须结合进程确已退出检查，不能单看属性。

`ready.adapterName`（兼容字段 `name`）来自 Windows `DeviceInformation.Name`，本机为 `KY - 上`，**未验证它等于实际广播 LocalName**。`computerName` 为 `KY`，`advertisedNameVerified=false`。GATT 广告参数没有 `LocalName` 属性，本组件不会修改系统电脑名。应以系统蓝牙名称与 iPhone 实际发现列表核对设备。

首次出现的单一订阅 peer 固定到 helper 退出；鼠标和键盘必须属于同一个 `Session.DeviceId.Id`。通知使用带 `selectedClient` 的定向重载，绝不广播给所有订阅者。出现第二台不同设备或无法读取设备 ID 时，普通输入立即阻断并输出 `stage=peer` 错误，保持阻断直至重新启动控制；原设备断开后不会自动换到另一台。安全清零只会尽力发给原固定设备。`peer.name` 提供选中设备名称，不输出或落盘设备 ID / MAC。该约束**不能证明蓝牙设备就是当前 AirPlay 手机**，仍需用户核对；事件明确 `sameAsAirPlayVerified=false`。

## 真机配对验证

1. 先在普通桌面会话启动标准模式，确认收到 `ready`。
2. 在 iPhone 打开「设置 → 辅助功能 → 触控 → 辅助触控 → 设备 → 蓝牙设备」，找到本机名称并由用户选择、确认配对；按需打开辅助触控。
3. 等待 `mouseSubscribed=true`；另查 `keyboardSubscribed` 与 `peer.paired`。未订阅时不要发送输入。
4. 先用少量相对位移，再验证单击、按下/移动/抬起拖动、滚轮，随后在文本框验证普通英文键码与释放。
5. 断开重连、重新打开服务、窗口失焦、Esc 与退出时释放需逐项实测；中文输入、多指手势、直接点画面定位不在本组件已验证能力中。

Apple 官方操作参考：[辅助触控指针设备](https://support.apple.com/en-us/111775)。组件本身不自动配对、取消配对或改动 iPhone 设置；这些步骤由用户在真机完成。
