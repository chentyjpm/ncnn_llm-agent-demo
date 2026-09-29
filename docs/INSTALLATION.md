# 安装版：一次构建、内置引擎、自动模型管理

源码与安装包不同。只有成功的 `Application installers` 对应 artifact 中才有该次构建实际产生的安装文件；本文不是运行成功证明。代码不依赖用户手工配置 ncnn_llm/ncnn_image。

## 使用流程

下载与你的系统对应的 `LocalAgent-installer-*`。Windows 双击 setup.exe 或解压便携版；macOS 使用 DMG 中应用；Linux 解压后启动或运行用户安装脚本。启动自动打开本机网页。在“安装与模型”中查看文字模型下载量并确认，下载、校验、转换、接入自动完成。

安装包带 Python 应用运行时、两个 ncnn 原生引擎、分别匹配的 Vulkan probe、H5、Office 库与固定模型转换器；macOS 另带 MoltenVK。无需用户安装 Python/编译器/SDK/Node，不用编辑 JSON 或提供可执行路径。

不包含权重、显卡驱动、Office/LibreOffice、OCR。两个引擎默认 auto：先尝试 Vulkan 计算预检，不可用就 CPU。图像权重可不安装，不影响文字聊天和文档功能。详情见 [VULKAN.md](VULKAN.md)。

## 构建

维护者运行一条 `Application installers`，在 4 个平台/架构分别编译两引擎和 probe，再封装解释器、文档库和界面。不能用同一 exe 通吃三种系统。

应用打包只接收相同提交、相同 workflow run 的两个已通过原生校验的 archive。校验源码构建与测试程序哈希，PyInstaller 重定位后记录实际分发哈希，冻结后的程序自测再验收 HTTP。

Windows 执行临时目录静默安装、安装后 HTTP/文档检查和卸载。macOS/Linux 检查冻结应用后生成 DMG/tar。Linux 还以空白用户目录经应用模型中心实际下载 0.5B、转换、激活、回答问题。PATH 中不提供外部 Python/ncnn，防止误用开发环境中的程序。

未加入商业代码签名或 Apple 公证；未知发布者提示不是安全验收。正式分发应由维护者添加自己的签名证书，不要要求用户关闭系统安全机制。Linux 包从 Ubuntu 24.04 构建，不保证更老 glibc 兼容。

## 模型下载与验证

来源固定，不接收浏览器任意 URL。“查看下载量”只读取元数据，将目录解析为不可变快照并展示大小。用户再次确认后才下载。

文字默认官方 `Qwen/Qwen2.5-0.5B-Instruct` 的已测试快照，固定 safetensors SHA-256。图像使用 `nihui-szyl/qwen-image-ncnn` 的 `qwenimage21`，先锁定当次快照和组件清单。模型源不同、规模不同，图像下载可能很大。

逐文件检查预期大小与 LFS SHA-256/普通 Git blob SHA-1。写入 `.part`，校验完成后才保留；失败重试可复用完整已校验文件，**不支持单个文件字节级断点续传**。模型准备在 staging，最后原子改名并记录 READY 才启用。取消或失败不回退假模型。

转换期间不接受新推理以避免资源竞争。安装前检查磁盘预留；网络失败明确显示，不使用不明来源的自动镜像。模型安装成功不等于生图画质、算力或 Agent 可靠性合格。

## 数据和退出

Windows `%LOCALAPPDATA%/LocalAgent`，macOS `~/Library/Application Support/LocalAgent`，Linux `$XDG_DATA_HOME/local-agent`（默认 `~/.local/share/local-agent`）。`models/`、`state/`、`workspace/` 分离，不写入只读程序资源目录。

重复启动尝试重新打开同一个用户实例；8765 被占用可自动换空闲端口，仍只监听回环。网页“安装与模型 → 退出本地服务”结束服务并保留记录。卸载不删除模型/会话/工作文件。Linux 安装脚本拒绝直接覆盖已有应用目录，升级前需关闭并保留旧应用。

源码旧入口 `python -m local_agent serve --config ...` 保留。`python packaging/launch.py` 是同一自动配置启动器的源码入口，但需先准备引擎资源，不代表源码 ZIP 已自带引擎。开发依赖见 `requirements-desktop.txt` / `requirements-office.txt`，普通用户不用安装它们。
