# Agent 接入

完整规范在 [agent-kit/AGENTS.md](../agent-kit/AGENTS.md)，辅助工具可以连同整个 `agent-kit/` 目录复制到任意位置。

## 只读检索

在“设置 → AI 知识库”建立合适的访问范围，复制 stdio MCP 配置到客户端。MCP 提供 `search_library`、`read_document`、`read_passage` 和 `library_info`；不提供写入工具。

按分类、来源授权时取交集；授权父分类会包含子分类。不要为一次未命中而擅自扩大访问范围。检索证据同时核对文件 ID、内容 SHA、提取 SHA 与目标段落标记。

## 成品入库

```sh
'/Applications/拾页资料库.app/Contents/Resources/python/bin/python3' -B \
  '/Applications/拾页资料库.app/Contents/Resources/runtime/agent_ingest.py' \
  --request-id 'project-final-v1' --wait 120 \
  '/path/to/final/report.md'
```

替换示例路径和请求号。相同请求重试沿用 request_id，内容更新使用新版本号。等待 `complete`、`success`、`index_ready` 与逐文件回执；需要正文时还应检查 `text_indexed`。随后用辅助工具分类、补标签并读回验证。不要直接写数据库、搬动对象目录或伪造回执。

HTTP 管理 API 只监听 `127.0.0.1`，验证 Host、Origin 和 `X-Vault-Token`。官方客户端从私有 runtime descriptor 读取动态端口与会话。所有 API 均不接受 URL 查询中的 token；图片、视频和安全阅读使用独立的 HttpOnly 媒体会话，不能写库或访问知识 API。

`runtime.json` 具有所有者会话信息，不可发送给模型、附入回执或纳入开源仓库。限定 profile 的 MCP 适合日常问答；所有者 CLI 仅交给已获授权的本机执行者。

Agent 接入包 1.3.0 可运行 `shiyue_agent.py --app /path/to/拾页资料库.app self-test`，会创建隔离临时库并清理，不接触真实个人库。未知版本或缺少能力字段时停止写入，保留只读操作。
