# README 界面截图

这些图片由简截界面控件或示例截图合成，使用演示任务、文件名和配对信息；不包含实际用户数据。设置与轻提醒对应 R299，双栏补色示例对应 R300，任务界面对应 R296，美化界面对应 R295；其他功能示意沿用 R254，iPhone 二维码指向双菜单版快捷指令。

README 使用相对路径引用此目录，发布到 GitHub 时需要同时提交这些 PNG 文件。

需要更新截图时，在项目根目录运行：

```powershell
python tools/docs_screenshots.py
```

脚本在独立的 offscreen 进程中运行，演示数据写入 `build/docs-screenshots-data/`。不会启动生产索引、网络互传服务或录制，也不会修改用户实际的任务和设置。
