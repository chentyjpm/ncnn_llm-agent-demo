# macOS 安装包的 Vulkan 运行时

## 实际发现的问题

提交 `747c6b3` 的安装流水线 `36639786573` 中，Windows/Linux 安装产物通过，macOS ARM 应用的实际 self-test 失败。原生引擎本身已编译并通过启动检查，但冻结应用中的 probe 返回 `probe_unavailable`。PyInstaller 日志明确指出四个原生程序均缺少 `@rpath/libvulkan.dylib`。

原来的包只收集 MoltenVK。MoltenVK 是驱动实现，不等于 Vulkan-Loader；不能把它改名成 libvulkan.dylib 假装补齐加载器，也不能把开发机已安装的 SDK 当成最终用户环境。

## 修复

- 构建 runner 准备 `molten-vk` 与 `vulkan-loader`，用户不需要执行 brew。
- `scripts/macos_runtime.py` 同时收集真实 loader、MoltenVK、ICD 描述文件和两者许可证。
- ICD 描述仅使用包内相对路径；引擎子进程默认使用包内 ICD，显式的 `VK_DRIVER_FILES`/`VK_ICD_FILENAMES` 仍优先，保留无驱动回归测试。
- 仅对 staging 副本修正原生依赖，供 PyInstaller 正确收集重定位；固定提交原生归档与其校验值不被修改。
- 冻结后检查四个原生文件依赖，拒绝 SDK/Homebrew/临时构建目录绝对路径；检查完整运行时文件存在。
- 记录冻结后实际哈希，再重封装外层 app 的 ad-hoc 签名并执行严格验证。它不是 Developer ID 签名或 Apple 公证。

新增 14 项辅助回归覆盖路径、ICD 校验、依赖缺失、显式驱动覆盖和有界诊断。辅助测试使用明确 fixture；真正能否加载由 macOS runner 上的冻结应用 self-test、无外部 Python/ncnn PATH 的 HTTP 文档验收决定，不用单元测试冒充。

结果以本次修复提交对应的 Actions 和 artifacts 为准；本文不预先宣称成功。完整 Qwen Image 权重生图与物理 GPU 性能依然不属于安装验收范围。
