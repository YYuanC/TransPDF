# TransPDF

一个可自托管的 PDF 翻译工具，基于PyMuPDF。

TransPDF 支持 PDF 文本提取、OCR 和版面回填，支持一次上传多个文件、逐份下载或打包下载。文本块可以并发翻译；中文导出使用内置字体和地区字形，改善中文PDF显示和可复制文本的完整性。

项目 fork 自 [jctots/pdf-translate](https://github.com/jctots/pdf-translate)，沿用 MIT 许可证。

![alt text](/docs/image.png)

## 功能

- **网页界面**：选择文件、语言与翻译引擎，查看处理状态、切换文档并逐页对照预览。
- **批量处理**：文件按顺序处理，单个文件失败后继续处理其余文件；成功结果可单独下载，也可导出含 `results.json` 处理报告的 ZIP。同名文件使用编号目录区分。
- **并发翻译**：所有引擎支持每个 PDF 的文本块并发请求，默认 3 个线程，可设置为 1–128；OCR 与 PDF 写入仍按顺序执行。
- **中文字体**：自动匹配原文的中文字体；导出时嵌入字体并尝试子集化以减小文件体积。
- **扫描文档**：支持 Tesseract OCR 或 Ollama 视觉模型；可强制 OCR 以忽略不完整的文本层。
- **多效果输出**：译文 PDF、左右对照 PDF，以及不受原页面尺寸限制的对照 HTML。
- **自动化集成**：提供 REST API、可选 API 密钥认证、任务状态与取消接口，并保留 Paperless-ngx webhook 和历史文档回填工具。

## 技术实现

| 技术 | 用途 |
| --- | --- |
| **Python 3.11+** | 应用主体、配置读写、翻译调度与文件处理 |
| **Gradio** | 文件上传、参数配置、批量结果下载和左右预览界面 |
| **FastAPI + Pydantic + Uvicorn** | REST API、参数校验、接口文档及 ASGI 服务；与网页界面共用 7860 端口 |
| **PyMuPDF（`pymupdf`）** | PDF 读取、文本块和表格检测、页面渲染、字体提取、译文回填与对照 PDF 生成 |
| **Tesseract + `pytesseract` + Pillow** | 扫描页面的本地文字识别与图像处理；Tesseract 和源语言识别包需单独安装 |
| **HTTPX** | 请求翻译后端和 Ollama OCR 接口，处理连接超时、错误与部分后端重试 |
| **`ThreadPoolExecutor`** | 限制同时进行的文本块翻译请求，将结果写回对应页与文本块位置 |

翻译流程：

**提取文本／OCR → 可选文本合并与表格检测 → 并发调用翻译引擎 → 字体选择与译文回填 → 生成 PDF、对照 PDF 和 HTML**。

## 快速开始

### 从源码运行

目前仅支持从源码运行。Docker 部署和 PyInstaller 打包存在问题，暂不维护；仓库中的相关文件保留供参考。

需要 Python 3.11 或更新版本。

```bash
git clone https://github.com/YYuanC/TransPDF.git
cd TransPDF
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
```

Linux / macOS：

```bash
source .venv/bin/activate
```

安装依赖并启动：

```bash
python -m pip install -r requirements.txt
python app.py
```

启动后打开 <http://localhost:7860>。

如需 Tesseract OCR，请单独安装 [Tesseract](https://tesseract-ocr.github.io/tessdoc/Installation.html) 和源语言识别包，并确保 `tesseract` 命令在 `PATH` 中可用。使用 Ollama 翻译或 OCR 时，需另行启动 Ollama 并下载对应模型；LibreTranslate 也需单独安装或连接已有实例。

## 翻译引擎与数据流向

| 引擎 | 所需条件 | 文本发送位置 |
| --- | --- | --- |
| **Bing** | 网络连接，无需 API 密钥；非官方接口 | Bing 服务器 |
| **Google** | 网络连接，无需 API 密钥；非官方接口 | Google 服务器 |
| **LibreTranslate** | 可访问的 LibreTranslate 实例，可选 API 密钥 | 配置的实例地址 |
| **Ollama** | 可访问的 Ollama 实例与已下载的翻译模型，可选 Bearer 密钥 | 配置的实例地址 |

选择自己部署在本机或局域网中的 LibreTranslate / Ollama，可将翻译请求留在自己的基础设施内。若把其 URL 指向远程服务，文本也会发送到该远程服务。使用 Ollama OCR 时，页面图像发送到所配置的 Ollama 地址。

Bing / Google 接口仅可用于测试，该接口可能遇到限流或改变。

## 输出格式

| 文件 | 内容 |
| --- | --- |
| **译文 PDF** | 页面背景与原文位置基础上的译文，包含可选择和搜索的译文文本层 |
| **对照 PDF** | 每页左侧原文、右侧译文，便于对照检查 |
| **对照 HTML** | 原文与译文分栏排列，可重排阅读，不受原 PDF 文本框大小限制 |
| **批量 ZIP** | 编号目录中的成功结果，以及记录各文件成功／失败状态的 `results.json` |

网页会提供三种输出；多文件任务额外提供 ZIP。REST API 可通过 `outputs` 选择返回格式，多文件请求始终返回 ZIP，报告包含失败文件。

## REST API

交互式接口文档：<http://localhost:7860/docs>。

单文件翻译（以下为 Bash 示例；PowerShell 可使用 `curl.exe` 并将参数写在一行）：

```bash
curl --max-time 600 http://localhost:7860/api/translate \
  -F "file=@document.pdf" \
  -F "source=en" -F "target=zh" -F "service=Bing" \
  -F "translation_threads=4" \
  --output translated.pdf
```

批量翻译并下载全部格式：

```bash
curl --max-time 1800 http://localhost:7860/api/translate \
  -F "file=@first.pdf" -F "file=@second.pdf" \
  -F "source=en" -F "target=zh" -F "service=Bing" \
  -F "outputs=all" \
  --output translations.zip
```

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/health` | 版本、默认引擎、当前与最近任务状态 |
| `GET` | `/api/config` | 读取配置，API 密钥字段脱敏 |
| `PATCH` | `/api/config` | 部分更新配置，包括全局线程数和中文字体样式 |
| `POST` | `/api/translate` | 同步翻译一个或多个 PDF |
| `DELETE` | `/api/translate` | 请求取消正在执行的 API 翻译任务 |

API 同时执行一个任务，另一个可排队，再多的请求返回 429。HTTP 连接会持续到翻译完成，请按文档大小延长客户端与反向代理超时。取消任务会等待已经开始的后端请求结束；多文件响应即使部分或全部失败仍返回 200，需检查 ZIP 内的 `results.json`。

设置 `PDF_TRANSLATE_API_KEY` 后，请求中添加 `Authorization: Bearer <key>`；健康检查不需要密钥。这只保护 REST API，网页界面需要另行通过反向代理设置访问控制。详见 [API 指南](docs/api.md) 与 [隐私说明](docs/privacy.md)。

## 常用配置

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `PDF_TRANSLATE_BACKEND` | `Bing` | 首次启动时的网页默认引擎，以及 API 未指定 `service` 时的引擎 |
| `PDF_TRANSLATE_THREADS` | `3` | 每份 PDF 的文本块并发数；已保存的 `translation_threads` 优先，API 请求可单独覆盖 |
| `PDF_TRANSLATE_CJK_FONT_FAMILY` | `auto` | 中文字体初始样式：`auto`、`serif`、`sans-serif`、`wenkai`；已保存配置优先 |
| `LIBRETRANSLATE_URL` | `http://localhost:5000` | LibreTranslate 实例地址，需单独启动或连接已有实例 |
| `LIBRETRANSLATE_BLOCK_DELAY_MS` | `200` | LibreTranslate 每个工作线程单次翻译后的等待毫秒数 |
| `PDF_TRANSLATE_MAX_UPLOAD_MB` | `100` | REST API 一次请求所有上传文件的总大小上限（MB） |
| `PDF_TRANSLATE_API_KEY` | 空 | REST API 的可选 Bearer 密钥 |
| `PDF_TRANSLATE_DEBUG` | 关闭 | 调试日志；日志可能包含文档文本，请按实际需要开启 |

## 文档

- [使用指南](docs/user-guide.md) · [后端配置](docs/backends.md) · [API 指南](docs/api.md)
- [测试说明](docs/testing.md) · [Paperless-ngx 集成](paperless_webhook/README.md)· [贡献指南](CONTRIBUTING.md) 
- [隐私说明](docs/privacy.md)

## 开发与测试

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

## 使用限制

- 主要处理水平排列的普通文本，不专门恢复数学公式、复杂学术排版、竖排或旋转文字。
- 原页面作为背景保留，译文覆盖原文区域；复杂多栏、彩色背景、文本膨胀或表格检测误判可能出现遮挡和溢出。需要更舒适的阅读时，可使用 HTML 对照。
- 中文字体按文本块的原文样式选择，同一块内混合字体通常采用首个文本跨度的样式。当前部分思源字体导出的字符可能映射为兼容汉字，影响复制、搜索与文本精确匹配。
- 扫描件质量会影响 OCR；Tesseract 需要源语言识别包，Ollama 视觉模型需要足够的计算资源。
- 当前采用进程内任务状态，适合个人或受信任内网部署；无需配置分布式任务队列。
- 翻译请求时，并发数越高，对后端负载越大；出现限流、超时或本地模型资源不足时，请减少线程数。
- Bing 当前将 `auto` 按英语处理，非英语文档请明确选择源语言；其单次请求最多使用 1,000 个字符，较长文本块请改用其他引擎。
