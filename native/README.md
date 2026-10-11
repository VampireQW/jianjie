# 文件搜索 C++ 内核

界面、目录监听和现有文件目录库沿用 Python；关键词候选查询、精确匹配、父链路径解析、快速索引构建与增量同步由 `search_core.cpp` 实现。

`fsearch-fast.db` 与 `fsearch.db` 同目录，保存磁盘倒排索引。编码后的 Unicode 单字和双字片段支持一字/两字中文，也支持多个关键词的子串交集。FTS5 只保存文档编号，候选再按原始小写文件名核对，避免片段组合误匹配。源库中的顺序变更队列用于同步；快速索引事务先提交，变更队列后确认。重建采用源库读快照和快速库 WAL 事务，完成前保留旧版本。

查询每次结束即关闭连接；SQLite 每个读连接页缓存 2 MiB，写连接 4 MiB，关闭 mmap，临时数据使用文件。路径备忘限制 4096 项且字符串累计不超过 1 MiB，最多返回 500 条。后台构建优先放在现有短生命周期索引进程中，退出后由系统释放构建占用。

## 构建

将以下官方 ZIP 解压到 `build/native-toolchain/`：

- [Zig 0.15.2 Windows x64](https://ziglang.org/download/0.15.2/zig-x86_64-windows-0.15.2.zip)，SHA256 `3a0ed1e8799a2f8ce2a6e6290a9ff22e6906f8227865911fb7ddedc3cc14cb0c`。
- [SQLite 3.54.0 amalgamation](https://sqlite.org/2026/sqlite-amalgamation-3540000.zip)，SHA3-256 `7b670a62fdfbd672b75fef004cb703c8a3e87d3a5cc7d675b4a08337004a2d93`。

`python native/build.py` 编译 DLL。正式 PyInstaller spec 会自动编译最新源文件，再把 DLL 放到 `native/jj_search.dll`；编译器、源码、缓存不随 EXE 分发，运行时不需要安装 C++ 编译环境。
