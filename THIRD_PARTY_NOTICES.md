# 第三方组件与许可

拾页自有源码采用 MIT。以下组件保留原有许可，MIT 不替代这些声明。

| 组件 | 固定版本 | 许可与来源 |
|---|---|---|
| CPython | 3.13.18，python-build-standalone 20261026，arm64 macOS | Python-2.0 及其内含声明；https://github.com/astral-sh/python-build-standalone |
| pypdf | 7.1.0 | BSD-3-Clause；https://github.com/py-pdf/pypdf |
| jieba | 0.42.1 | MIT；https://github.com/fxsjy/jieba |
| jsdom | 26.1.0 | MIT；仅开发测试使用，不进入 App |

PDF 与分词组件的原文许可在 `THIRD_PARTY_LICENSES/`；Python 运行时内保留自己的 `lib/python3.13/LICENSE.txt` 以及 pip 和其依赖的许可证。`THIRD_PARTY_LICENSES/python-build-standalone/` 另保留构建发行方 20261026 对应提交的完整许可集合和来源哈希，涵盖不同目标平台的可能依赖；它不表示所有这些依赖均编入本 macOS 发行包。

构建运行时下载地址与校验值见 `runtime-lock.json`，PDF 和分词组件文件哈希见 `vendor-lock.json`。开发依赖固定在 `package-lock.json`。机器可读组件清单见 `SBOM.cdx.json`，其中 npm 组件是开发依赖。SBOM 不替代各上游组件的完整许可文本。

Cocoa、WebKit、PDFKit、Vision、AVFoundation、ImageIO、UniformTypeIdentifiers 和 CoreServices 由 macOS 提供，应用通过系统框架调用，没有复制其 SDK 源码。源码中的自制图标采用项目 MIT 许可。导入或分享的用户资料保留其自身权利状态，不属于项目开源内容。
