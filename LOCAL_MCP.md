# 简截本地 MCP · 正式版

此功能从 R293 起属于同一个正式版，不区分个人版和公开版。

唯一程序：`dist/简截.exe`。正常启动是简截界面；Agent 使用同一程序加 `--mcp` 参数启动标准 stdio MCP。MCP 模式不会创建界面、注册热键或打开第二份任务库。

## 使用

1. 打开正式简截。在托盘菜单勾选“本地 MCP（允许 Agent 管理任务）”。不使用 Agent 的用户可保持关闭。
2. 在 WorkBuddy 或其他 MCP 客户端配置下面的服务。首次连接的信任确认由用户本人完成。
3. 在微信对已连接的 WorkBuddy 说：“把准备会议材料放进简截今日 TODO，整理照片放进待办。”

    {"mcpServers":{"jianjie-local-tasks":{"type":"stdio","command":"简截.exe 的绝对路径","args":["--mcp"],"disabled":false}}}

把 `command` 替换为你实际存放程序的绝对路径，例如 `C:\Tools\JianJie\JianJie.exe`；配置保存在所用 MCP 客户端的服务设置中。

## 任务

- 今日 TODO 与待办是同一窗口的两个 tab，转移保留 ID、截止日期和提醒。
- Agent 未指定计划时默认待办，明确说今天时使用 target=today。
- 今日页包含此前未完成项；未来计划项放在待办。
- 查询返回真实 ID 和 version；修改携带版本，冲突时不会覆盖。
- 写入使用 request_id 防止重试重复创建，支持批量原子保存、软删除、恢复和整批撤销。
- 界面正在编辑时，Agent 写入返回暂忙。

## 工具

| 工具 | 用途 |
| --- | --- |
| jianjie_status | 本机运行状态与北京时间 |
| jianjie_list_tasks | 查询、搜索、分页 |
| jianjie_create_tasks | 批量新增 |
| jianjie_update_tasks | 修改、转移、完成、删除、恢复 |
| jianjie_undo | 按 batch_id 撤销 |

数据沿用正式简截已有存储位置。端点凭据位于 `%APPDATA%\jianjie\agent-endpoint.json`，仅监听随机回环端口，凭据每次启用更新。无凭据和浏览器跨域请求都会被拒绝。关闭本地 MCP 后会移除端点文件。

旧个人版任务在正式版首次启动时按原 ID 归并，以较新的修改为准；不会重复添加。旧文件保留用于恢复，并保存一次 tasks-before-mcp-unification.json 快照。同时间戳的不同内容不会覆盖正式数据，归并报告保存在任务文件 agent 字段中。

## 其他 Agent 与模型

支持 stdio MCP 的 Agent 复用同一 EXE 与五项工具。普通第三方模型 API 由其调用程序执行工具调用即可：`local_tasks/tools.py` 提供工具定义，`local_tasks/client.py` 的 call(name, arguments) 调用同一个正式任务服务。不需要网页、云服务或自建模型管理。

命令行也可用：简截.exe --mcp --call jianjie_status '{}'

## 维护

唯一源码与构建入口沿用正式简截项目的 main.pyw、简截.spec 和既有构建流程。local_tasks 是该项目的普通模块，随正式 EXE 打包，不再构建独立助手 EXE。

本地任务测试：python -m unittest discover -s tests/local_mcp -p 'test_*.py' -v

打包任务界面检查：简截.exe --task-smoke --smoke-log <日志路径>，使用临时任务文件，不改真实任务。

R293 验证：16 项任务/MCP/归并测试通过；正式 EXE 的 stdio 管道、五项工具发现、任务落盘与界面刷新通过，原有任务保留。旧整包 --smoke 自测在文件搜索检查处失败并停滞，未计为通过；其自测函数字节码与原 R292 相同。

R294：修复任务窗口保存位置落在屏幕外的问题。打开和重新呼出时按当前显示器工作区恢复；保留有效副屏负坐标，支持屏幕间隙和最小化恢复。21 项相关检查、打包验收及真实快捷键隐藏后重开验证通过。

R296：任务窗口增加最小化按钮。最小化保留草稿，再次从主面板或任务栏恢复原窗口；MCP 继续沿用同一任务服务。
