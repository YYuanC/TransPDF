# 发布指南

## 工作流

| 工作流 | 触发方式 | 作用 |
| --- | --- | --- |
| `Tests` | 所有分支 push、PR、所有标签 push、手动 | 运行源码测试 |
| `Build application` | 手动，或由 `Release` 调用 | 构建 Windows、Linux、macOS 程序，检查 API 和网页启动，上传构建产物 |
| `Build and push Docker image` | 手动，或由 `Release` 调用 | 独立构建并发布 TransPDF 和 Paperless webhook 镜像到 GHCR |
| `Release` | 手动 | 先运行测试，再并行构建二进制程序和 Docker 镜像，最后创建 GitHub Release |

Release 会以手动运行时选中的分支提交为目标，自动创建并推送版本标签，先运行完整源码测试；测试成功后，二进制构建和 Docker 镜像构建并行运行。三个平台的程序启动检查和两个 Docker 镜像构建全部成功后，才会创建 GitHub Release。

## 发布预编译程序

1. 将要发布的改动（包括构建脚本）提交并推送到 GitHub。
2. 在 GitHub 的 **Actions → Release → Run workflow** 中，选择包含最新发布工作流的分支，填入要创建的 `tag`（如 `v1.0.0`）。测试版本如 `v1.0.0-rc.1` 可勾选 `prerelease`。
3. 等待工作流完成。它会在所选分支的当前提交上自动创建并推送标签，先运行测试；测试成功后并行运行二进制构建和 Docker 构建，再创建 Release、自动生成发布说明并上传二进制附件。

标签格式为 `v主版本.次版本.修订版本`，可附加预发布后缀。构建时会把该标签写入程序的 `VERSION`。如果标签已经存在，流程只允许它指向当前选中的提交，不会移动已有标签；每次新发布请使用新标签。

## 发布附件与运行

- `TransPDF-windows-<arch>.zip`
- `TransPDF-linux-<arch>.tar.gz`
- `TransPDF-macos-<arch>.tar.gz`
- `SHA256SUMS.txt`

`<arch>` 来自实际 GitHub runner 的 CPU 架构，例如 `x64` 或 `arm64`。下载时请按附件中的架构选择；这些构建不包含所有操作系统和架构组合。Linux / macOS 压缩包保留可执行权限，程序目录中的依赖和字体会一起打包。

解压后保留整个 `TransPDF` 目录，在该目录中运行 `TransPDF.exe`（Windows）或 `./TransPDF`（Linux/macOS），然后打开 <http://localhost:7860>。配置保存到该目录下的 `_internal/data/config.json`，请确保 `_internal` 目录可写。Python 已打包，Tesseract 及 OCR 识别包需另行安装。完整使用说明见 [README](../README.md#快速开始)。

在下载目录中校验文件（Linux）：

```bash
sha256sum -c SHA256SUMS.txt --ignore-missing
```

Windows PowerShell 可使用 `Get-FileHash .\TransPDF-windows-x64.zip -Algorithm SHA256`，并与 `SHA256SUMS.txt` 对照；macOS 可使用 `shasum -a 256 <文件名>`。

## 发布 Docker 镜像

在 **Actions → Build and push Docker image → Run workflow** 中选择要发布的分支或标签。该流程继续独立运行，发布到：

- `ghcr.io/yyuanc/transpdf`
- `ghcr.io/yyuanc/transpdf-paperless-webhook`

手动运行该工作流时，在默认分支运行会更新 `latest`；在 `v*` 版本标签运行会生成对应的版本镜像标签。由 `Release` 调用时，两个镜像都会使用输入的版本标签。Compose 默认使用本地源码构建，直接使用发布镜像的示例见 [README](../README.md#快速开始)。
