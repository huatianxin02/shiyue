# 开发、验证与发行

## 构建环境

Mac 构建要求 Apple Silicon、macOS 13+、Xcode Command Line Tools 与 Python 3.12+。`build-app.sh` 校验固定的运行时和 vendor 文件，编译全部 Swift 助手、构建独立 App、临时签名并检查签名。旧生成 App 移至 `backups/builds/`；个人数据与历史源码不会进入新 App。

初次准备 Python 需要联网下载公共归档，下载后严格校验 SHA-256。缓存存在时可离线构建。使用发布 App 不依赖 Homebrew、Node、外部 Python 或源码目录。

运行测试前先构建 App，随后：

```sh
npm ci
npm test
SHIYUE_TEST_APP="$PWD/拾页资料库.app" \
  '拾页资料库.app/Contents/Resources/python/bin/python3' -B -m unittest discover -s tests -v
'拾页资料库.app/Contents/Resources/python/bin/python3' -B \
  agent-kit/shiyue_agent.py --app "$PWD/拾页资料库.app" self-test
```

所有写入类验证必须使用临时库。不要把真实 runtime descriptor、数据库、原件、签名密钥或未脱敏截图放入 Issue、PR、源码包或 CI artifact。

## 制作公开发布包

```sh
python3 tools/generate_sbom.py
python3 tools/kit_checksums.py
python3 tools/public_release.py --check
python3 tools/public_release.py --source --app --output dist
```

导出工具使用显式白名单，不会递归打包整个开发目录。隐私检查会阻止个人绝对路径、常见凭证、数据库与私钥；App 中的源码指纹必须与当前源码一致。输出包括干净源码目录、源码 ZIP、macOS ZIP 和 SHA256SUMS。目标已存在时停止，使用新的输出目录，不覆盖已交付版本。

从新导出的源码目录再次执行构建、`npm ci` 和测试，是一次发布的必要核对。最后使用隔离资料库进行真实原生窗口验收，核对图片/PDF/HTML/视频预览、导入、检索、设置与退出，并演练完整备份恢复。

本项目附带 GitHub Actions 配置（macos-15）和依赖更新配置。配置存在不等于已在线运行；未推送仓库的本地发行要注明 CI 未执行。工作流仅检查构建和测试，不自动上传用户数据，也不自动发布 Release。

## 发行边界

公开社区包采用临时签名，未获 Apple Developer ID 签名或公证。维护者日后具备自己的开发者证书后，可在独立签名环节重新签名并提交公证；私钥和口令不得提交仓库。不要用修改系统安全设置的脚本解决分发问题。

Android 历史源码单独见 `android/README.md`。无对应真机验证时，只提供源码和开发构建说明，不将 debug APK 标为正式版。
