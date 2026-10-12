# TransPDF 安全说明

## 报告漏洞

请勿在公开 Issue 中发布可利用的漏洞细节。请通过 [维护者的 GitHub 个人资料](https://github.com/YYuanC) 中提供的私密联系方式报告，包含复现步骤、影响范围和版本；若未提供联系方式，请先通过 Issue 请求私密沟通渠道，不要附带漏洞细节或敏感文档。

## 部署范围

TransPDF 面向个人或受信任内网的自托管场景。对外部署时，应通过反向代理设置身份验证、TLS 和访问限制。

- 网页界面默认不提供身份验证。`PDF_TRANSLATE_API_KEY` 只保护 REST API，不保护 Gradio 界面及其调用接口。
- REST API 默认开放；配置 `PDF_TRANSLATE_API_KEY` 后，除 `/api/health` 外的 API 需要 Bearer 密钥。
- Bing 和 Google 使用非官方 HTTPS 接口，文档文本发送到对应服务；本地翻译需选择部署在自己基础设施内的 LibreTranslate 或 Ollama。
- 配置文件可能含有服务密钥，调试日志可能含有原文与译文。请限制 `data/` 的访问权限，不要将其提交到仓库或打包进镜像。
- Paperless webhook 可通过 `WEBHOOK_API_KEY` 保护入站请求；使用非空密钥时，需要在 Paperless 工作流中配置对应请求头。

更多配置见 [隐私说明](docs/privacy.md)。
