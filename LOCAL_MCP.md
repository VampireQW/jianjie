# 简截本地 MCP · 正式版

此功能从 R293 起属于同一个正式版，不区分个人版和公开版。

唯一程序：`dist/简截.exe`。正常启动是简截界面；Agent 使用同一程序加 `--mcp` 参数启动标准 stdio MCP。MCP 模式不会创建界面、注册热键或打开第二份任务库。

## 使用

任务清单右上角的 **MCP** 入口提供连接状态、当前程序路径和一键复制。选择“复制给 Agent”，可将说明与配置一起粘贴到 Agent 对话中；使用 JSON 设置的客户端也可以选择“复制 JSON”。复制操作不会自动开启本地接口或修改其他程序的配置。

1. 在 WorkBuddy 或其他 MCP 客户端配置下面的服务。首次连接的信任确认由用户本人完成。
2. 在简截托盘菜单勾选“本地 MCP（允许 Agent 管理任务）”。Agent 与简截的启动顺序不限，不使用 Agent 的用户可保持关闭。
3. 在微信对已连接的 WorkBuddy 说：“把准备会议材料放进简截今日 TODO，整理照片放进待办。”

    {"mcpServers":{"jianjie-local-tasks":{"type":"stdio","command":"简截.exe 的绝对路径","args":["--mcp"],"disabled":false}}}

把 `command` 替换为你实际存放程序的绝对路径，例如 `C:\Tools\JianJie\JianJie.exe`；配置保存在所用 MCP 客户端的服务设置中。

## 任务

- 今日 TODO 与待办是同一窗口的两个 tab，转移保留 ID、截止日期和提醒。
- Agent 未指定计划时默认待办，明确说今天时使用 target=today。
- 今日页包含此前未完成项；未来计划项放在待办。
- 查询返回真实 ID 和 version；修改携带版本，冲突时不会覆盖。
- 写入使用 request_id 防止重试重复创建，支持批量原子保存、软删除、恢复和整批撤销。
- 界面正在编辑时，写入请求先排队，结束编辑后自动重试。

## 离线待提交（R298）

Agent 可以先启动，也可以在简截关闭时提交任务。写入请求先保存到本机队列，立即返回 `delivery_status=queued`；这表示“等待提交”，**不表示任务已添加**。简截启动并开启本地 MCP 后会自动提交，Agent 无需重启或重复发送。即使 Agent 已退出，简截也会处理保存的请求。

- `jianjie_status` 的 `app_available` 表示简截服务是否就绪；MCP 已连接而简截未启动时为 false，仍能排队写入。
- `jianjie_queue` 的 `list` 查看队列摘要；`get` 加原 request_id 查看最终结果。`state=applied` 才确认清单已经保存。
- `cancel` 取消尚未提交的请求；已经在提交或结果不确定的请求不能冒充取消成功。
- `retry` 重试失败或待提交的原请求。版本冲突保留为 conflict，需重新查询最新任务，再以新 request_id 提交；不会自动覆盖用户修改。
- 同一操作复用原 request_id 防止重复提交。`target=today` 固定为首次提交时的北京时间日期，跨日补交不会改变计划日期。
- 后台提交不会自动唤醒 Agent 发言；Agent 下次交互时可查询结果。读取现有任务仍需简截运行。
- 队列位于 `%APPDATA%\jianjie\mcp-outbox.sqlite3`，只保存在当前 Windows 账户本机。最多保留 256 条待处理请求；最近 200 条已完成请求保留完整结果，较早请求保留 ID 与摘要用于去重。

从旧版升级后，已经运行的旧 MCP 进程需要在 Agent 中**重新连接该服务一次**以加载新版；此后日常启停简截无需重新连接。移动 EXE 后仍需更新配置路径。

## 工具

| 工具 | 用途 |
| --- | --- |
| jianjie_status | 简截服务就绪状态与队列数量 |
| jianjie_list_tasks | 查询、搜索、分页 |
| jianjie_create_tasks | 批量新增 |
| jianjie_update_tasks | 修改、转移、完成、删除、恢复 |
| jianjie_undo | 按 batch_id 撤销 |
| jianjie_queue | 离线请求查询、取消与重试 |

数据沿用正式简截已有存储位置。端点凭据位于 `%APPDATA%\jianjie\agent-endpoint.json`，仅监听随机回环端口，凭据每次启用更新。无凭据和浏览器跨域请求都会被拒绝。关闭本地 MCP 后会移除端点文件。

旧个人版任务在正式版首次启动时按原 ID 归并，以较新的修改为准；不会重复添加。旧文件保留用于恢复，并保存一次 tasks-before-mcp-unification.json 快照。同时间戳的不同内容不会覆盖正式数据，归并报告保存在任务文件 agent 字段中。

## 其他 Agent 与模型

支持 stdio MCP 的 Agent 复用同一 EXE 与六项工具。普通第三方模型 API 由其调用程序执行工具调用即可：`local_tasks/tools.py` 提供工具定义，`local_tasks/client.py` 的 call(name, arguments) 调用同一个正式任务服务。不需要网页、云服务或自建模型管理。

命令行也可用：简截.exe --mcp --call jianjie_status '{}'

## 维护

唯一源码与构建入口沿用正式简截项目的 main.pyw、简截.spec 和既有构建流程。local_tasks 是该项目的普通模块，随正式 EXE 打包，不再构建独立助手 EXE。

本地任务测试：python -m unittest discover -s tests/local_mcp -p 'test_*.py' -v

打包任务界面检查：简截.exe --task-smoke --smoke-log <日志路径>，使用临时任务文件，不改真实任务。

R293 验证：16 项任务/MCP/归并测试通过；正式 EXE 的 stdio 管道、五项工具发现、任务落盘与界面刷新通过，原有任务保留。旧整包 --smoke 自测在文件搜索检查处失败并停滞，未计为通过；其自测函数字节码与原 R292 相同。

R294：修复任务窗口保存位置落在屏幕外的问题。打开和重新呼出时按当前显示器工作区恢复；保留有效副屏负坐标，支持屏幕间隙和最小化恢复。21 项相关检查、打包验收及真实快捷键隐藏后重开验证通过。

R296：任务窗口增加最小化按钮。最小化保留草稿，再次从主面板或任务栏恢复原窗口；MCP 继续沿用同一任务服务。
