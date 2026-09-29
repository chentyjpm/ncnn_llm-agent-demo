# Word / Excel / PowerPoint / Markdown 工作台

右上角“文档工作台”可载入最近回答、编辑草稿、预览、使用模板并导出。无需模型也可使用文档操作；它调用固定的库函数，不运行模型生成的 Python。

## 支持范围

DOCX 读取正文/标题/表格；导出新标题、段落、列表和简单表格。XLSX 按工作表读取单元格值/公式文本；导出第一个 Markdown 表格或 CSV，配置表头、筛选、冻结行、基础列宽，类似公式的文本按字符串写入，不执行公式。PPTX 按页和分组读取文字/表格；按 Markdown 一二级标题和段落生成文字幻灯片，长正文分页。MD 可直接读写和基础安全预览。

不是在线 Office、不是原文件无损编辑；不保证图片、批注、修订、备注、页眉页脚、动画或复杂版式。旧 DOC/XLS/PPT、宏格式、扫描件/OCR 不支持。导出新名称到会话 exports，原文件不覆盖。

## Agent 工具

`documents.read(path, offset=0, limit=12000)` 返回 content、total_chars、offset、next_offset、truncated 和说明；每次最多 16,000 字符，next_offset 非空应继续请求。`documents.create(format, content, title)` 只允许 md/docx/xlsx/pptx，最多 200,000 字符，返回新文件路径和大小。

源码需 `documents.enabled=true` 注册；安装版自动开启。H5 读取免审批，创建需要一次确认。直接在工作台点保存本身是用户明确导出动作，HTTP 也要求 confirm=true，不需要开放宿主脚本权限。

## 附件与 HTTP

普通聊天附加 DOCX/XLSX/PPTX 时先提取第一页文本，标记为不可信文件数据，再加入模型上下文；超长内容提示改用 Agent 分页。文件面板点击 Office 文件，可把提取文本放入草稿编辑器后另存新格式。

`GET /api/sessions/<id>/document?path=…&offset=…` 提取文本。
`POST /api/sessions/<id>/export` 接受 format/content/title/confirm。
所有接口沿用本机会话 token、同源限制和工作区相对路径保护。模型中心权限和文档权限相互独立。

## 安全和限制

OOXML 输入最多 16 MiB，展开后最多 32 MiB/5,000 成员，单成员最多 8 MiB；拒绝路径穿越、重复成员、加密、宏/嵌入对象、DTD/实体。XML 经 defusedxml 校验。XLSX 不加载外部链接，不计算公式；数据最多 20,000 单元格、每表 5,000 行。读取 PPT 最多 200 页，导出最多 80 页。大文档应拆分。

工作区检查不是恶意并发目录替换防护；只在可信单写者目录使用。文件内容和草稿可能敏感，日志/导出不应公开上传。

测试分别覆盖真实 Office 文件生成/读回、拒绝危险输入、实际 HTTP、Chromium 下载/预览；模型回归单独记录，不把文档格式测试说成大模型测试。
