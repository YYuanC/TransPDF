# 内置字体

TransPDF 在本地使用本目录中的字体，翻译过程中无需下载。字体选择与缺字回退由 `fonts.py` 实现。

## 思源宋体与思源黑体

- 思源黑体（Source Han Sans）和思源宋体（Source Han Serif）：CN / TW / HK，各包含 Regular 与 Bold。
- 原始设计：[Adobe Source Han Sans](https://github.com/adobe-fonts/source-han-sans) 与 [Source Han Serif](https://github.com/adobe-fonts/source-han-serif)。
- TrueType 转换：[Source-Han-TrueType](https://github.com/Pal3love/Source-Han-TrueType)。
- 本项目所用字体文件来源：[BabelDOC-Assets/fonts](https://github.com/funstory-ai/BabelDOC-Assets/tree/main/fonts)。
- 授权：SIL Open Font License 1.1，完整文本与版权声明见 [LICENSE-SourceHan.txt](LICENSE-SourceHan.txt)。Source 为 Adobe 商标，保留字体名称为 SOURCE。

自动选择会按原文的衬线／无衬线风格及粗体使用对应思源字体。`zh`、`zh-tw`、`zh-hk` 分别选择 CN 简体、TW 台湾繁体、HK 香港繁体字形。无法识别原字体名称时使用 PDF 文本跨度的样式标记。

## 霞鹜文楷

- `LXGWWenKaiGB-Regular.1.520.ttf`：[霞鹜文楷 GB](https://github.com/lxgw/LxgwWenKaiGB)。
- `LXGWWenKaiTC-Regular.1.520.ttf`：[霞鹜文楷 TC](https://github.com/lxgw/LxgwWenkaiTC)。
- 字体文件来源同样为 BabelDOC-Assets。
- 授权：SIL Open Font License 1.1，见 [LICENSE-WenKaiGB.txt](LICENSE-WenKaiGB.txt) 与 [LICENSE-WenKaiTC.txt](LICENSE-WenKaiTC.txt)；文件包含原始版权声明。

文楷可通过高级设置或 `cjk_font_family=wenkai` 选择，GB 用于简体、TC 用于繁体。仅内置 Regular 字重，粗体文本使用相应地区的思源粗体。

## 回退与嵌入

优先字体缺少字符时，使用 PyMuPDF TextWriter 的补充字体回退；中文字体不可用时回退到 PyMuPDF 内置 Droid Sans Fallback。导出时嵌入字体并尝试子集化，子集化失败则保留完整字体。

Liberation Sans / Serif / Mono 用于西文样式回退，遵循 SIL Open Font License 1.1；完整授权文本与从字体 name 表核实的版权声明见 [LICENSE-Liberation.txt](LICENSE-Liberation.txt)。
