# 小米蓝牙遥控器 2 Pro · Linux 版 Codex 语音编程工具

**专为 Linux 版 Codex 用户设计。** 把小米蓝牙遥控器 2 Pro 的麦克风和按键接入 Codex 听写，按住说话、松开转写、按确认键发送，用中文语音完成 vibe coding。项目标识为 `xiaomi-codex-remote`。

**0.1.0-alpha.4 · 实验版本** · [快速开始](docs/QUICKSTART.md) · [微信可选扩展](docs/WECHAT.md) · [验证范围](docs/TESTING.md) · [故障排查](docs/TROUBLESHOOTING.md)

本项目在 [SayAll for Linux](https://github.com/ZXC011/sayall-linux) 的蓝牙音频桥接基础上，提供持续运行的虚拟键盘、Codex 按键映射和断线恢复。Codex 功能使用客户端已有的听写能力，不安装本地语音识别模型。另提供默认关闭的微信语音输入扩展，需用户自行安装 Fcitx5 Vinput 并配置识别服务，详见[微信安装说明](docs/WECHAT.md)。

完整方案默认要求先停用已核实客户端的听写降音量辅助程序，再安装遥控器桥接。播放保护不生效时，遥控器不会启动 F5 听写；不提供跳过检查继续使用的选项。这样从触发源阻止听写主动压低其他应用的播放音量，而不持续强制改写它们的音量。

## 按键

| 按键 | Codex 前台 | 微信前台（扩展启用后） | 其他软件 |
| --- | --- | --- | --- |
| 语音键 | 按住用遥控器麦克风听写；松开结束 | 按住右 Alt 触发 Vinput，松开结束 | 保留麦克风收音，不注入听写键 |
| 中间确认键 | 发出 Enter；输入框有文字时可发送 | 发出 Enter；需微信设置为 Enter 发送 | 不注入 Enter |
| 返回键 | Backspace，支持长按退格、松手停止 | Backspace，支持长按退格、松手停止 | 原始 KEY_BACK，由软件决定其行为 |
| 方向、音量等 | 按原始事件转发 | 按原始事件转发 | 按原始事件转发 |

先点一下目标应用的消息输入框。程序识别前台窗口，不能判断窗口内部是否正在编辑消息。微信扩展默认关闭，未启用时按“其他软件”处理。

返回键按住期间，在 Codex、已启用的微信与其他软件之间切换，会释放当前输出并等待物理松手，下一次按下按新应用处理；Codex 与微信之间互相切换也会停止连续删除。听写期间不发送确认键；正常 stop 立即释放当前听写键，遗漏 stop 时，检测到松手或设备不可用持续约 1.5 秒后释放。另有 90 秒最长按住保护。

## 支持范围

实物验证环境：小米蓝牙遥控器 2 Pro（设备识别型号 `RC003`，VID `2717` / PID `32b8`）、Ubuntu 26.04、GNOME Wayland 会话中通过 XWayland 可识别的 Codex 窗口、PipeWire / WirePlumber、SayAll **0.3.0**。

核心流程已经做过麦克风、转写、发送、真实蓝牙断连恢复和整机重启测试；返回键在 Codex 和浏览器分别验证过。**本仓库的通用安装程序仅做过隔离目录测试，尚未在另一台干净电脑实装。** 详细区别见 [TESTING.md](docs/TESTING.md)。

当前测试环境发现了客户端听写时自动压低播放音量、音量恢复不完整的兼容风险。`alpha.2` 增加了必要的客户端保护、安装检查和运行时保护检查。保护已在本机应用，用户确认开始录音时 QQ 音乐没有变小；约 115 秒、277 次采样期间捕捉到 4 次听写辅助程序运行，记录到的 QQ 音乐、微信和客户端播放流增益均保持正常。**这次短时验证不代表所有通知音均已确认，也不代表长期稳定。** 清理旧音频规则后的重新登录或重启验证、另一台干净电脑安装仍待完成；具体证据与限制见[验证记录](docs/TESTING.md#客户端播放保护实测)。

焦点检测依赖 `xprop` 和 `WM_CLASS`，仅对配置中精确匹配的窗口生效。原生 Wayland 窗口、其他桌面环境和其他型号遥控器尚未验证。无法识别窗口时不会启动听写或发送 Enter；返回键保留 KEY_BACK。本机原型已有微信语音输入使用正常的反馈；本次新增的微信返回键、通用安装和其他环境仍待实测，模拟按键测试不能替代这些验收。

## 安装

先下载源码并进入项目目录：

```bash
git clone https://github.com/quantqingzhou/Xiaomi-remoter-pro2---for-Ubuntu-Codex-vibe-coding.git xiaomi-codex-remote
cd xiaomi-codex-remote
```

以下命令在本仓库目录执行；把所有示例地址 `AA:BB:CC:DD:EE:FF` 替换成自己的遥控器地址。

### 1. 准备依赖并配对

- Python 3.10+，`evdev`、`bleak`。
- BlueZ / `bluetoothctl`，PipeWire / `pw-cat`、`pw-loopback`、`pw-dump`，WirePlumber / `wpctl`。
- `pactl`、`xprop`、systemd 用户服务，以及可使用 F5 按住听写的 Codex 客户端。
- 单独安装 [SayAll](https://github.com/ZXC011/sayall-linux)，已验证版本为 0.3.0。本仓库不下载或包含它的软件包，也不需要 Vokie。

Ubuntu / Debian 的补充依赖示例：

```bash
sudo apt install python3-evdev python3-bleak x11-utils pulseaudio-utils bluez pipewire-bin wireplumber
```

使用系统蓝牙设置完成配对并设为信任。查看已配对设备：

```bash
bluetoothctl devices Paired
bluetoothctl info AA:BB:CC:DD:EE:FF
```

### 2. 先启用客户端播放保护

当前只适配已核实的客户端包版本 `26.1103.32239` 中的 `native/dictation-ducking` 辅助程序，原文件 SHA-256 为：

```text
6a8517562755ee33554c6d5571ba8febfadd188d87b12047a4c70cf230f86186
```

实际内容校验优先于版本号；其他发行渠道或更新版本即使名称相同，也不会被猜测修改。默认路径是 `/usr/lib/chatgpt/resources/native/dictation-ducking`。

先由普通用户预览：

```bash
/usr/bin/python3 -B scripts/manage.py protect-client
```

确认路径和计划后，结束当前听写并正常退出桌面客户端，再在系统终端执行。系统安装目录需要管理员权限时使用：

```bash
sudo /usr/bin/python3 -B scripts/manage.py protect-client --apply
```

如果客户端安装在当前用户有权修改的目录，使用普通用户执行即可；自定义路径通过 `--helper /绝对路径/native/dictation-ducking` 指定。后续安装桥接时也必须用 `--dictation-helper` 指定同一路径。

此步骤只替换这个已核实的辅助程序，保留原文件权限和属主，并在同目录保存权限为 `0600` 的原文件备份及校验元数据：`dictation-ducking.xiaomi-codex-original`、`dictation-ducking.xiaomi-codex-protection.json`。备份属于本地安装状态，不应上传到 Git。替代程序只等待客户端关闭标准输入，不访问音频设备、不调用音频工具、不调整任何音量。脚本不会自动调用 sudo、重启音频服务或结束客户端进程。

保护完成后重新打开客户端。若原文件内容未知、是符号链接，或备份无法通过校验，操作会拒绝执行。客户端更新覆盖保护后，运行时检查会阻止新听写，已按住的 F5 也会释放；需要先核实新版本并重新适配，不能直接用旧文件覆盖更新后的客户端。

### 3. 先预览，再安装用户文件

```bash
/usr/bin/python3 -B scripts/manage.py install --address AA:BB:CC:DD:EE:FF
```

默认只列出计划，不改文件、不启动服务。确认路径和配置范围后，停止已有 SayAll，再应用：

```bash
systemctl --user stop sayall-bridge.service
/usr/bin/python3 -B scripts/manage.py install --address AA:BB:CC:DD:EE:FF --apply
```

本步骤的用户文件安装由普通桌面用户运行，不使用 sudo；实际写入前会检查客户端播放保护是否已经生效。它会：

- 将运行文件复制到用户数据目录，服务不再依赖下载仓库的位置。
- 生成遥控器配置、两个 systemd 配置文件和待安装的权限规则。
- 备份 SayAll 配置，设置 HID 触发、自定义听写命令及确认键映射；其他映射和设置保留。
- 为运行时和安装记录设置用户私有文件权限。

遇到已有的 SayAll `ExecStart` 覆盖、返回键自定义映射、已有未受管理的目标文件时，会停止并说明冲突。请先备份和迁移旧方案，不要同时运行两套接管同一遥控器的桥接。

默认目录：

| 内容 | 路径 |
| --- | --- |
| 运行代码 | `~/.local/share/xiaomi-codex-remote/` |
| 本项目配置及待安装的规则 | `~/.config/xiaomi-codex-remote/` |
| SayAll 配置 | `~/.config/sayall-linux/config.json` |
| 用户服务及补充配置（drop-in） | `~/.config/systemd/user/` |
| 本地备份和安装清单 | `~/.local/state/xiaomi-codex-remote/install-state.json` |

支持绝对路径的 `XDG_DATA_HOME`、`XDG_CONFIG_HOME`、`XDG_STATE_HOME`。以安装计划显示的路径为准。使用非默认配置目录时，桥接使用生成的 `sayall_config` 路径；原版 SayAll GUI 不一定使用这个路径。

### 音频设置的改动范围

默认安装不会修改 PipeWire / WirePlumber 配置，也不会自动校正其他应用的音量或底层增益。完整方案要求修改的客户端文件仅为上文明确列出的降音量辅助程序；本机排障使用的额外音量记忆规则不包含在本项目中。

SayAll 运行时会创建虚拟音频节点；按住语音键时会切换系统默认输入，松开后尝试恢复。因此，其他跟随系统默认麦克风的应用可能受到影响；播放保护不等于隔离所有应用的录音输入。它也不会代替用户管理其他软件自己设置的音量或静音，相关限制见[故障排查](docs/TROUBLESHOOTING.md)。

### 4. 安装设备权限规则

规则按你的 MAC、VID/PID 匹配遥控器，不将用户加入 `input` 组。`uinput` 规则允许当前桌面用户合成键盘事件，是此桥接实现快捷键所需的权限。

确认生成的规则内容后执行：

```bash
sudo install -m 644 "${XDG_CONFIG_HOME:-$HOME/.config}/xiaomi-codex-remote/70-xiaomi-codex-remote.rules" /etc/udev/rules.d/70-xiaomi-codex-remote.rules
sudo install -m 644 "${XDG_CONFIG_HOME:-$HOME/.config}/xiaomi-codex-remote/60-xiaomi-codex-uinput.rules" /etc/udev/rules.d/60-xiaomi-codex-uinput.rules
sudo install -m 644 "${XDG_CONFIG_HOME:-$HOME/.config}/xiaomi-codex-remote/xiaomi-codex-uinput.conf" /etc/modules-load.d/xiaomi-codex-uinput.conf
sudo modprobe uinput
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=misc --sysname-match=uinput
```

重新连接遥控器，让输入节点获得更新后的访问权限；若当前会话的 uaccess 尚未更新，注销后重新登录。

### 5. 设置 F5，启动桥接

在 Codex 中把“按住听写”快捷键设为 **F5**，对应快捷键条目 `globalDictationHold`。安装程序不会修改 Codex 的快捷键文件。

```bash
systemctl --user daemon-reload
systemctl --user enable --now sayall-bridge.service
/usr/bin/python3 -B scripts/doctor.py
```

`doctor.py` 只检查依赖、权限、播放保护和服务状态，不注入按键、不录音、不申请共享桌面。确认两个服务已就绪后，点 Codex 输入框，按住语音键说话，松开等文字出现，再按确认键发送。

## 开机与重连

桥接在登录图形桌面后自动启动，不是登录前就能使用 Codex。遥控器待机时可能需要轻按普通方向键唤醒。BLE 断开会让包装器退出，由 SayAll 的 `Restart=on-failure` 重建音频订阅和按键接管；不是只重新抓取按键。恢复用时取决于设备和系统，不能保证固定秒数。

```bash
journalctl --user -u sayall-bridge -u xiaomi-codex-keys -n 60
systemctl --user restart sayall-bridge.service
```

修改项目配置后重启桥接。`examples/config.example.json` 只包含虚构设备地址；安装时会生成真实的本地配置，不应提交到 Git。

## 卸载与回退

在保留的仓库目录执行：

```bash
systemctl --user stop sayall-bridge.service xiaomi-codex-keys.service
/usr/bin/python3 -B scripts/manage.py uninstall
/usr/bin/python3 -B scripts/manage.py uninstall --apply
systemctl --user daemon-reload
```

卸载程序按清单恢复原设置：只恢复本项目管理的 SayAll 字段，保留后来修改的其他字段。如果管理的字段或源码已被用户修改，则停止，不会覆盖这些修改。写入失败有回退逻辑。不要删除安装清单后再卸载。空目录可能保留。

用户文件卸载会保留客户端播放保护，避免未经确认重新启用听写降音量。若需要完全恢复客户端原状，在桥接已停止、客户端已正常退出后，先预览再恢复。系统目录中的私有备份需要管理员权限读取时使用：

```bash
sudo /usr/bin/python3 -B scripts/manage.py restore-client
sudo /usr/bin/python3 -B scripts/manage.py restore-client --apply
```

自定义路径同样需要指定 `--helper`。只有当前文件仍精确等于本项目替代程序、原备份和元数据校验通过时才会恢复；客户端更新或用户修改后会拒绝覆盖。恢复后原始备份仍保留，以便审计。恢复原客户端程序也会恢复其原有听写降音量行为。

如果第 4 步安装过规则，且不再需要本项目，可移除本项目专用的系统文件：

```bash
sudo rm -f /etc/udev/rules.d/70-xiaomi-codex-remote.rules /etc/udev/rules.d/60-xiaomi-codex-uinput.rules /etc/modules-load.d/xiaomi-codex-uinput.conf
sudo udevadm control --reload-rules
```

重新连接设备或注销后，权限变更生效；其他软件安装的权限规则不受影响。卸载不会卸载 SayAll、蓝牙或音频服务；是否继续启用原版 SayAll 自启动，由你决定。

## 其他音频软件

微信文字输入可按[微信可选扩展](docs/WECHAT.md)自行安装并启用。识别由用户配置的 Vinput 服务处理；本项目不安装它的模型、配置密钥或替用户修改输入法设置。

其他能读取系统麦克风的应用可尝试选择 **SayAll Mic**，内部名 `sayall-mic`。当前方案是按住语音键收音，不是持续开麦；腾讯会议的实际收音和长时间稳定性尚未验证。

本项目不提供识别或上传服务。Codex 听写由客户端既有功能处理；微信扩展由用户选择的 Vinput 识别后端处理。是否联网、是否需要本地模型、是否保存历史由所选后端和软件设置决定。

## 开发与许可证

```bash
/usr/bin/python3 -B -m unittest discover -s tests -v
```

没有 SayAll 安装文件时，与它的事件循环有关的集成测试会跳过，其他测试使用模拟设备和临时目录。CI 不接触真实键盘、蓝牙、录音或桌面会话。

本项目采用 **GPL-3.0-only**，见 [许可证原文](LICENSE)。为保留标准法律文本，许可证及版权声明保留原文；项目说明、注释和程序提示采用中文。命令、配置键、代码标识及依赖名称保留其标准写法。依赖及来源见[第三方说明](THIRD_PARTY.md)，参与开发见[贡献指南](CONTRIBUTING.md)。
