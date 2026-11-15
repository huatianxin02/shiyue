# Android 历史源码（实验）

该目录保留拾页 Android 1.2.0：读取/导出 `.vaultpack`、平面分类、离线查找与阅读。它没有 macOS 1.10 的 9 级分类、段落 MCP 与完整库备份能力，不宣称跨端功能一致。

需要 Android SDK platform 36、build-tools 36.0.0 和 JDK 11+。可通过 ANDROID_HOME、JAVA_HOME 指定工具位置。

```sh
bash android/build-android.sh --debug
```

输出是 `android/build/` 下明确标为 debug 的 APK，每次使用临时开发签名，不能覆盖原正式安装。构建不再内置正式签名口令，现有私有 keystore 不进入公开源码。

正式签名必须显式指定 `--release`，并在环境中提供 SHIYUE_ANDROID_KEYSTORE、SHIYUE_ANDROID_ALIAS、SHIYUE_ANDROID_STORE_PASS、SHIYUE_ANDROID_KEY_PASS。密钥由维护者另行保管，不能提交仓库或写进命令行明文参数。

`python3 tools/android_smoke.py` 用自制资料验证桌面 JVM 上的资料包往返与损坏检测。`android/test/build-audit.sh` 仅制作隔离 Android 验收程序，运行前须另有模拟器/设备。本轮未完成 Android 真机操作，因此不发布新的正式 Android APK。
