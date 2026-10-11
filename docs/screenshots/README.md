# README 界面截图

这些图片由当前简截 PySide6 界面控件渲染，使用演示任务、文件名和配对信息；不包含实际用户数据。图片对应 R254，iPhone 二维码指向双菜单版快捷指令。

README 使用相对路径引用此目录，发布到 GitHub 时需要同时提交这些 PNG 文件。

需要更新截图时，在项目根目录运行：

```powershell
python tools/docs_screenshots.py
```

脚本在独立的 offscreen 进程中运行，演示数据写入 `build/docs-screenshots-data/`。不会启动生产索引、网络互传服务或录制，也不会修改用户实际的任务和设置。
