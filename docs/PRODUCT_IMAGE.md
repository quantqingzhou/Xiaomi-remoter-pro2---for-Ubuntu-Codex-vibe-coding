# 产品图片来源与加载方式

设置窗口显示小米官网“小米蓝牙遥控器 2 Pro 银灰色”的正面产品图。首次打开时从官方 CDN 获取约 108 KiB 的原图，之后使用本机缓存。请求不包含设备地址、按键配置、语音或账号信息。

- [官方产品页](https://www.mi.com/xiaomi-bluetooth-remote-2-pro)
- [官方商品页](https://www.mi.com/shop/buy/detail?product_id=23714)
- [原始图片](https://cdn.cnbj0.fds.api.mi-img.com/b2c-shopapi-pms/D5AEE02AB01C470A83B30E0A5F48F73A.png)
- 核实日期：2026-11-14。
- SHA-256：`cee92fa125135524a2e52814fbf161f59edab3192f1e8ec1cac250421a9d5cca`。

程序核对图片大小和哈希，避免官方换图后点击区域与实体按键不符。图片文件不作修改，窗口内仅缩放并裁去周围留白。网络失败时仍可使用文字按钮选择和配置按键；联网后点击“重新加载产品图”即可。

缓存默认在 `~/.cache/xiaomi-codex-remote/product-2-pro.png`，遵循 `XDG_CACHE_HOME`。删除缓存后，下次打开窗口会重新获取图片。图片下载不影响后台桥接、麦克风或其他声音输出。

开源源码包不包含图片文件或缓存，只包含加载代码和此来源说明。产品图片及商标的权利归小米及相应权利人所有，不纳入本项目代码的 GPL-3.0-only 授权；本项目不代表小米官方。
