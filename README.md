# 拾页 · Shiyue

本地文件资料库与 Agent 知识接口。为文件建立正文索引、9 级分类和可核对的原文引用；原件可就地保留，也可校验后托管复制。**MIT 开源，默认离线运行。**

An offline-first personal file library for macOS, with hierarchical collections, Chinese full-text search, verifiable citations, backups, and local Agent/MCP interfaces. The source is licensed under MIT; bundled dependencies retain their own licenses.

## 平台与范围

| 部分 | 状态 |
|---|---|
| macOS 1.10.0 | Apple Silicon；部署目标 macOS 13+；实际验收环境见发行审查记录 |
| Agent 接入包 1.3.0 | 支持拾页 1.9.x / 1.10.x 和所需能力检查 |
| Android | 保留 1.2.0 历史源码，供开发和资料包交换实验；本版不发布新的正式 APK |
| Windows / Linux 桌面 | 未提供安装包；不要将 Python 核心测试当作跨平台桌面支持 |

## 能做什么

- 拖入文件或文件夹；扫描磁盘候选，勾选、预览后入库。
- 最多 9 级分类，侧栏拖动排序和移动分支，同一资料可有多个归属。
- 文件名、标签、正文检索；中文分词与字符匹配结合；按分类和标签限定知识检索。
- PDF/图片阅读、本地 OCR、静态网页预览、笔记、收藏与可恢复的回收站。
- 固定提取版本的原文引用；重建后旧段落仍可读回，防止引用静默漂移。
- 目录变化监听与补扫；一致性备份、可含外部原件的增量备份、独立目录恢复。
- Agent 幂等入库与明确回执；只读 stdio MCP 提供检索、文档与段落读取。
- 分类规则先预览再确认，可撤销批次新增归属；优先版本由用户明确指定。

“设置”集中资料维护、分类规则、后台任务和 AI 接入。可选的 Agent 协作页面依赖另行安装的小鲸控制台/协作台；普通资料管理、MCP 和独立入库 CLI 均不依赖它们。

## 使用安装包

1. 下载本版 `shiyue-1.10.0-macos-arm64.zip`，按发布目录中的 `SHA256SUMS` 核对校验值。
2. 解压，把实际的 `拾页资料库.app` 复制到“应用程序”后打开。
3. 添加少量资料试用：默认“就地索引”保留原位置；需要库内副本时选择“托管复制”。
4. 在“设置 → 保管与迁移”确认数据目录并安排备份。

社区构建使用临时签名，尚无 Apple Developer ID 签名或公证，macOS 可能要求用户自行确认来源。无需关闭 Gatekeeper 或系统完整性保护。应用包含固定 Python 与解析组件，使用成品无需安装 Python、Node 或模型。

## 从源码构建

需要 Apple Silicon Mac、Xcode Command Line Tools 和 Python 3.12+。首次构建会下载 `runtime-lock.json` 指定的 Python 归档并验证 SHA-256；其余运行依赖已经按哈希固定在 `vendor/` 中。运行时最低 Python 要求为 3.10，应用内置 3.13.18。

```sh
bash build-app.sh
open '拾页资料库.app'
```

构建从 Swift 源码编译全部三个本机助手，再组装全新的 App；原有构建保留到 `backups/builds/`。图标成品随源码提供，日常构建不需要 Chrome。

开发验证需要 Node 22+：

```sh
npm ci
npm test
SHIYUE_TEST_APP="$PWD/拾页资料库.app" \
  '拾页资料库.app/Contents/Resources/python/bin/python3' -B -m unittest discover -s tests -v
python3 tools/public_release.py --source --output dist
```

公开分享使用工具生成的干净源码包和 App 包。**不要直接压缩正在使用的开发目录或个人资料库。** 详见 [开发与发布](docs/DEVELOPMENT.md)。

## Agent 与数据保管

把 [Agent 操作规范](agent-kit/AGENTS.md) 交给 Agent，或从 [任务前缀](agent-kit/给Agent的任务开头.txt) 开始。HTTP/CLI 入库需要应用运行；只读 MCP 可以直接读取已建索引。调用方式与权限边界见 [Agent 接入](docs/AGENT.md)。

默认个人库是 `~/Library/Application Support/拾页资料库`。应用旁的 `data/`、环境变量 `SHIYE_DATA` 或已选择的恢复库可能覆盖默认位置，以“设置 → 保管与迁移”为准。

就地索引不是备份；同一磁盘上的托管副本也不是独立灾备。完整备份与资料包的覆盖范围不同，恢复会建立独立库。详见 [使用与迁移](docs/GUIDE.md)。

## 已知限制与安全边界

- 本版使用关键词检索，没有向量检索、自动语义分类或云端模型依赖。
- 知识搜索最多返回 20 份候选，无分页；未命中不表示磁盘上不存在。
- 复杂文件在独立解析进程中运行，35 秒超时；最多索引 200 万字符，超限保留原件和明确状态。
- 扫描最多 5 万候选、25 万目录项或 5 分钟，默认跳过隐藏/系统/缓存/应用包和符号链接。
- 本机 HTTP 管理会话是所有者权限；MCP profile 是应用过滤规则，不能隔离同一系统账户下的恶意程序。
- 拾页核心不上传文件。外部 AI 客户端或可选协作执行者可能把取到的内容发给其模型服务。
- 备份、托管对象与索引不额外加密；磁盘加密、账户权限和备份介质由使用者管理。

## 参与与许可

[更新记录](CHANGELOG.md) · [贡献指南](CONTRIBUTING.md) · [安全说明](SECURITY.md) · [发行审查](docs/RELEASE-AUDIT.md)

本项目代码与自制图标采用 [MIT](LICENSE)。第三方组件及其许可证见 [第三方声明](THIRD_PARTY_NOTICES.md)；素材、资料库文件不会因被导入而自动获得项目的开源许可。
