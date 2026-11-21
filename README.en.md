# Xiaomi Bluetooth Remote 2 Pro for Codex on Linux

[简体中文](README.md) · **English**

**Designed for Linux users of the Codex desktop client.** Use a Xiaomi Bluetooth Remote 2 Pro for voice coding: hold its voice button to dictate, release to finish, and press the center button to send.

**0.1.0-alpha.10 — pre-release.** The settings window and desktop notifications currently use Chinese. This README provides English setup and operation instructions.

Built on [SayAll for Linux](https://github.com/ZXC011/sayall-linux), this project adds application-specific keys, playback protection, reconnect checks, a visual key editor, and status diagnostics. Codex dictation uses the client's existing speech recognition; this project does not install a local speech recognition model. It is an independent community project, not an official Xiaomi or OpenAI product.

## Supported environment

Hardware testing has used the **Xiaomi Bluetooth Remote 2 Pro**, device model `RC003`, VID `2717`, PID `32b8`, with Ubuntu 26.04, GNOME Wayland, PipeWire/WirePlumber, and SayAll **0.3.0**. Application detection uses `xprop` and exact `WM_CLASS` matches through XWayland. Native Wayland windows, other desktops and other remote models are not yet verified.

The prototype has passed real dictation, release-to-stop, center-button send, Bluetooth reconnect, and reboot checks. The generic installer has been tested in isolated directories, **not on a second clean computer**. Automated tests do not establish hardware compatibility or successful cloud transcription.

**alpha.9 fixes startup key capture**: it waits for all remote keys to be released before exclusively grabbing the device, discards old events, and retries if another press races with the grab. This prevents a wake-up F5 press from reaching the desktop while its release is intercepted. The user verified WeChat → Codex → WeChat dictation without unintended recording or stuck holds, and reported completing a reboot on 2026-11-17 before approving publication. See the bilingual [release notes](docs/releases/0.1.0-alpha.9.md).

## Default controls

| Button | Codex in foreground | WeChat with optional extension | Other applications |
| --- | --- | --- | --- |
| Voice | Hold for F5 dictation; release to finish | Hold Right Alt for Vinput; release to finish | Remote microphone only |
| Center / OK | Enter to send | Enter; WeChat must use Enter to send | No Enter injection |
| Back | Backspace, including hold-to-repeat | Backspace, including hold-to-repeat | Original Linux `KEY_BACK` |
| Direction / volume | Forward original events | Forward original events | Forward original events |

Click the target input field first. Window detection cannot determine focus within an application. Sending is blocked while dictation is still active. Focus changes, disconnection and mapping changes release held outputs. Voice has a dedicated lifecycle and cannot be assigned as an ordinary shortcut.

The alpha.8 release adds optional Claude Desktop Ctrl+D hold-to-dictate, Enter to send, and Backspace on the remote Back button. The user verified these three controls on one machine. Claude must run through XWayland and its shortcut must be checked first; no extra speech backend is added. Claude also supports the global mouse mode described below; a dedicated Claude tab shows its fixed keys and links to shared Other apps settings. See [CLAUDE.md](docs/CLAUDE.md).

In normal mode, OK sends Enter while GNOME explicitly reports that its Activities overview is open. This fixes confirmations being skipped when overview focus appeared as an unknown application. Once closed, the existing application mapping applies again. This uses a read-only `gdbus` query; failures do not grant extra Enter behavior. The user has verified global mouse operation, the existing Claude controls, and repeated overview selection on this machine.

## Optional global mouse navigation (alpha.8)

[Download the alpha.10 pre-release](https://github.com/quantqingzhou/Xiaomi-remoter-pro2---for-Ubuntu-Codex-vibe-coding/releases/tag/v0.1.0-alpha.10). To enable mouse navigation, expand the mouse controls in the settings window, identify the Home button, enable the feature, and save. Mouse navigation works in all applications, including Claude. Legacy configurations keep mouse navigation disabled.

Hold Home for about 0.6 seconds to enter or exit mouse mode. A short Home press retains its configured action (for example Super), dispatched on release. In mouse mode, directions move the pointer with acceleration; OK clicks on a short release or right-clicks after a long hold; volume buttons scroll. Back retains the configured WeChat action (Backspace by default), sends Backspace in a recognized Claude window, and sends Esc elsewhere. Backspace supports hold-to-repeat. TV and Power retain their application mappings, including WeChat Alt+A screenshots.

Click the input field and exit mouse mode before dictating or sending. Voice is suppressed in mouse mode with an on-screen reminder. Dragging and screenshot rectangle selection are not included yet. Mouse mode persists across application switches and works without recognizing each application. Native Wayland app-specific dictation and Backspace detection remain unsupported. Release direction or scroll buttons after 10 seconds before continuing.

A small persistent, click-through indicator shows mouse mode without stealing focus. It requires XWayland, `python3-gi`, `python3-cairo`, and `gir1.2-gtk-3.0` (the settings window still uses GTK 4). No desktop-sharing portal or audio configuration changes are involved. Switching recognized application categories stops pending movement, scrolling, deletion, and clicks but keeps mouse mode active. Disconnecting, detecting buttons, or changing configuration exits the mode. Movement and scroll speed are adjustable in five steps. See the [Chinese guide](docs/MOUSE_NAVIGATION.md) for details.

## Installation

Commands below run from the project directory. Replace `AA:BB:CC:DD:EE:FF` with your remote's address.

```bash
git clone https://github.com/quantqingzhou/Xiaomi-remoter-pro2---for-Ubuntu-Codex-vibe-coding.git xiaomi-codex-remote
cd xiaomi-codex-remote
sudo apt install python3-evdev python3-bleak x11-utils pulseaudio-utils bluez pipewire-bin wireplumber python3-gi gir1.2-gtk-4.0 libnotify-bin
```

Use Python 3.10 or newer. On Ubuntu/Debian, `/usr/bin/python3` uses the system packages above. Separately install [SayAll for Linux](https://github.com/ZXC011/sayall-linux) 0.3.0. Its package and dependencies are not bundled here. Vokie and a local transcription model are not required for Codex.

Pair and trust the remote using the desktop Bluetooth settings, then verify:

```bash
bluetoothctl devices Paired
bluetoothctl info AA:BB:CC:DD:EE:FF
```

### 1. Apply client playback protection

The tested client's dictation helper can lower playback volume during recording. This project requires a verified replacement that performs no audio operations. Without working protection, remote-triggered Codex dictation is blocked.

Preview as your desktop user:

```bash
/usr/bin/python3 -B scripts/manage.py protect-client
```

After checking the plan, finish dictation and quit the desktop client normally. For the default system installation, apply in a terminal:

```bash
sudo /usr/bin/python3 -B scripts/manage.py protect-client --apply
```

Then reopen the client. The default helper is `/usr/lib/chatgpt/resources/native/dictation-ducking`. Only verified original bytes are replaced; unknown content and symlinks are rejected. The verified original SHA-256 is `6a8517562755ee33554c6d5571ba8febfadd188d87b12047a4c70cf230f86186`, observed in client packages `26.1103.32239` and `26.1111.21308`. File content, not a version label, determines compatibility.

The original and verification metadata are backed up beside the helper with private permissions. Do not publish those backups. A custom client location requires `--helper /absolute/path/native/dictation-ducking` here and the same path as `--dictation-helper` during installation. A user-writable client installation does not require sudo for this step.

### 2. Preview and install user files

```bash
/usr/bin/python3 -B scripts/manage.py install --address AA:BB:CC:DD:EE:FF
```

Review the plan. Stop any existing SayAll bridge and this project's notifier before applying. Run installation as your ordinary desktop user, **without sudo**:

```bash
systemctl --user stop sayall-bridge.service
# If the notifier was previously enabled:
# systemctl --user stop xiaomi-codex-status.service
/usr/bin/python3 -B scripts/manage.py install --address AA:BB:CC:DD:EE:FF --apply
```

The installer copies runtime code to `~/.local/share/xiaomi-codex-remote/`, creates user configuration and services, and backs up the SayAll settings it changes. Conflicting or unmanaged existing files are rejected. Do not run two bridges that capture the same remote. Existing installations need migration using their installation records; copying a new source checkout alone does not upgrade installed services.

### 3. Install device permissions

Review the generated rules, then install them:

```bash
sudo install -m 644 "${XDG_CONFIG_HOME:-$HOME/.config}/xiaomi-codex-remote/70-xiaomi-codex-remote.rules" /etc/udev/rules.d/70-xiaomi-codex-remote.rules
sudo install -m 644 "${XDG_CONFIG_HOME:-$HOME/.config}/xiaomi-codex-remote/60-xiaomi-codex-uinput.rules" /etc/udev/rules.d/60-xiaomi-codex-uinput.rules
sudo install -m 644 "${XDG_CONFIG_HOME:-$HOME/.config}/xiaomi-codex-remote/xiaomi-codex-uinput.conf" /etc/modules-load.d/xiaomi-codex-uinput.conf
sudo modprobe uinput
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=misc --sysname-match=uinput
```

Reconnect the remote. Log out and back in if the session's device access has not refreshed. The remote rule matches your MAC and VID/PID; the installer does not add you to the `input` group. Access to `uinput` is used to synthesize shortcuts.

### 4. Set F5 and start

Set the Codex client's **hold-to-dictate** shortcut to **F5** (`globalDictationHold`). The installer does not edit the client's shortcut settings.

```bash
systemctl --user daemon-reload
systemctl --user enable --now sayall-bridge.service
/usr/bin/python3 -B scripts/doctor.py
```

Select the Codex message field, hold the remote voice button, speak, release, wait for text, then press the center button. For optional fault notifications:

```bash
systemctl --user enable --now xiaomi-codex-status.service
```

The notifier stays quiet on healthy startup. Persistent faults and subsequent recovery generate notifications with debounce and rate limiting. It does not automatically repair audio or Bluetooth.

## Visual key settings and diagnostics

Open **小米遥控器按键设置** from the application menu, or run from the source checkout:

```bash
/usr/bin/python3 -B scripts/settings.py
```

- Select **Codex**, **微信** (WeChat), **Claude**, or **其他软件** (Other apps) at the top of the right panel.
- **查看按键模式** previews **普通模式** (Normal) or **鼠标模式** (Mouse); labels beside the product image show each button's configured action. This only changes the preview. It does not activate mouse mode or save settings. All four tabs can preview global mouse mode.
- **固定操作** marks fixed mouse actions and hides shortcut editing. **与普通模式共用设置** marks shared mappings: changing WeChat Back or TV here also changes its normal-mode action. Home shows its short action and long mode toggle separately; only the short action is editable. Offline text buttons show the same information.
- **当前：…** below Bluetooth reports the actual backend mode, paused state, or unknown status. In alpha.8, opening settings keeps global mouse mode active. Starting button detection still exits it. Draft labels update immediately; runtime mappings change only after saving and bridge confirmation.
- The Claude tab displays Ctrl+D dictation, Enter send and Backspace as fixed controls after Claude integration is enabled. Other keys show the current shared Other apps mapping; **编辑“其他软件”的共用设置** takes you there to edit. The window shows its version below the product name.
- Click a button in the remote product image and choose an action. **自定义组合键** selects a custom shortcut; **录入键盘快捷键** captures one in this window.
- To make the Home button open GNOME Activities, select the **Super** preset rather than physically pressing Super during shortcut capture. It emits one press/release, without repeating. Configure each application profile if you want it everywhere. Other desktops may assign a different action to Super.
- **待识别** means the physical button needs binding. Click **识别…键**, press that remote button, then **保存设置** (Save). Unbound custom actions cannot be saved.
- **测试实体按键** starts a short detection session. Remote keys are used only for detection while active. Leaving the window or closing it stops detection.
- **更多操作** contains reload, restore defaults, undo the previous save and relearn. Saved mappings are read automatically by the bridge. Closing the window does not stop it.
- The top-right **×** closes settings and prompts if there are unsaved edits.
- On the main page, below shortcut recording, **暂停后台** pauses custom key mappings, the remote microphone and fault notifications; **恢复运行** resumes them. Raw Bluetooth keys may still reach the desktop: pausing does not disable the hardware. **登录后自动启动** controls the next login only; turn it off and also pause to stop the project now and after login. Controls verify the resulting service state and preserve notification preferences across retries. They operate only the project’s user services, without sudo; the user reported no major issues with pause/resume, startup toggling and dictation on the test machine; broader validation remains pending.
- Saving first reports that the file was written, then confirms that the bridge has read it. Only pending or failed configuration status stays visible. If a configuration is damaged, **更多操作 → 备份损坏配置并恢复默认** backs up the original bytes before restoring defaults, without replacing the previous valid backup.
- The Bluetooth badge shows connected, disconnected or unknown. Click it or **状态与日志** (Status and logs) to inspect Bluetooth, battery, HID, SayAll, microphone, key service, playback protection and notifications.
- alpha.10 adds a battery percentage beside Bluetooth and in Status and logs. It reuses the same read-only device query, refreshed about every eight seconds while the window is open. At 20% or below the indicator uses a low-battery color. Disconnected, unavailable or unreported values show **未知** (unknown), without retaining an old percentage. Device reports may update with a delay; this feature does not wake or reconnect the remote.
- Logs default to **错误与警告** (Errors and warnings); **全部日志** shows all recent records, and **复制日志** copies them. The viewer reads up to 200 records from the last 24 hours, masking Bluetooth addresses and home-directory usernames. Nothing is uploaded automatically.

The product image is fetched from Xiaomi's official CDN and cached locally on first use. The source package contains no product bitmap. If offline without a cache, a text-button list still allows key configuration. Status queries are read-only and run in the background; a microphone being present does not prove actual speech or transcription is working.

Mappings live in `~/.config/xiaomi-codex-remote/keymap.json`, with the previous version in `keymap.previous.json`. Voice cannot be remapped; normal keys cannot use F5 or Right Alt. Switching application context during dictation releases its shortcut; release the physical voice button before starting again. Shortcut capture also supports Page Up/Down, Shift+Tab and keypad Enter. Dedicated double-click actions, separate long-press actions and macros are not supported.

## WeChat and meeting applications

WeChat dictation is optional and disabled by default. Install and configure [Fcitx5 Vinput](https://github.com/xifan2333/fcitx5-vinput) yourself, including its recognition backend and Right Alt hold shortcut. Check WeChat's actual `WM_CLASS` using `xprop`, then supply matching `--wechat-window-class` values during installation. Configure Enter to send. This extension does not reuse Codex's cloud dictation service or include credentials or models. Detailed configuration is in [WECHAT.md](docs/WECHAT.md) (Chinese).

For Tencent Meeting or another audio application, select **SayAll Mic** as input and your normal speakers/HDMI as output. Hold the remote voice button while talking. The current setup is push-to-talk, not a continuous microphone. One real Tencent Meeting session was reported to transmit clear speech; the audio-settings test page did not reliably show a meter. Incoming screen-sharing black screens are a separate application issue, not a verified remote feature.

## Audio behavior and client updates

The project does not install PipeWire/WirePlumber configuration overrides or continuously force other applications' volumes. It does replace the verified client helper as described above. SayAll creates virtual audio nodes and may switch the default input while the voice button is held, attempting to restore it afterward. Applications that follow the default microphone can therefore be affected. Playback protection is not a guarantee about every application's own volume, mute or input settings.

Client updates may replace the protected helper. Check without changing anything:

```bash
/usr/bin/python3 -B scripts/doctor.py --client-only
```

For `known_original`, preview and reapply protection after quitting the client. For `unknown`, retain the new file and investigate compatibility; do not blindly overwrite it. Re-pairing Bluetooth or restarting system audio is not required just to reapply protection.

## Removal and development

Stop the project services before uninstalling. Preview first:

```bash
/usr/bin/python3 -B scripts/manage.py uninstall
/usr/bin/python3 -B scripts/manage.py uninstall --apply
```

The saved installation record is required for safe restoration and conflict checks. Device rules and client helper restoration are separate steps; see [the removal instructions](README.md#卸载与回退). Keep local backups until restoration is complete.

Run automated tests with:

```bash
/usr/bin/python3 -B -m unittest discover -s tests -v
```

The release has 221 passing tests on the development machine. SayAll integration tests are skipped where SayAll is not installed. GUI checks use isolated configuration; they do not replace hardware acceptance or installation on other systems. See [validation scope](docs/TESTING.md), [troubleshooting](docs/TROUBLESHOOTING.md) and [contribution guidelines](CONTRIBUTING.md) (Chinese).

Project code is **GPL-3.0-only**; see [LICENSE](LICENSE) and [third-party notes](THIRD_PARTY.md). Product images and trademarks remain with their respective owners and are not licensed by this project's GPL notice. The package does not include personal device configuration, recordings, account credentials or proprietary client backups.
