# 依赖与来源说明

- **SayAll for Linux**，作者 ZXC011：<https://github.com/ZXC011/sayall-linux>。已验证的集成版本为 **0.3.0**，其已安装桥接源码声明许可证为 GPL-3.0-only。它负责遥控器的 BLE/ATVV 音频解码和 PipeWire 虚拟麦克风。本仓库加载用户单独安装的桥接程序，不附带 SayAll 源码、二进制文件或安装包。
- **python-evdev**：<https://github.com/gvalkov/python-evdev>。用于访问 Linux 输入事件和 uinput。
- **Bleak**：<https://github.com/hbldh/bleak>。SayAll 使用的蓝牙依赖。
- **Fcitx5 Vinput**：<https://github.com/xifan2333/fcitx5-vinput>。仅微信可选扩展需要，由用户自行安装并配置识别后端；本仓库不附带其程序、模型、服务凭据或用户配置。
- **BlueZ**、**PipeWire**、**WirePlumber**、systemd 及 X11 `xprop` 是单独安装的系统依赖，各自继续遵循原有许可证。

本仓库的集成代码采用 **GPL-3.0-only**，完整文本见 [LICENSE](LICENSE)。许可证及 Free Software Foundation 的版权声明保留英文原文，避免将中文说明误当成改写后的法律条款。本项目的许可证不会替代各项依赖自身的许可证。

发布目录不包含个人设备配置、SSH 信息、私人审阅记录、录音或已安装应用的凭据。本机排障使用的系统音频规则不随项目分发。

客户端播放保护仅包含本项目自有的替代程序和已核实原程序的校验值，不分发原客户端辅助程序。应用保护时生成的原文件备份与元数据只保存在用户本机，不应作为源码或安装包内容提交。
