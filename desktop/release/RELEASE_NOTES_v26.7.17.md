# Metis 26.7.17 · Design 正式加入

![Metis 26.7.17](https://github.com/linyeping/Metis/releases/download/v26.7.17/Metis-26.7.17-Design.jpg)

> 从这个版本开始，Metis 不再只有 Chat、Cowork 和 Code。Design 作为原生工作面正式加入：一个桌面端即可完成对话、协作、编码与设计。

## Metis Design

- **完整的项目工作面。** Design 把项目管理、Agent 协作、实时预览与导出交付组织在同一个 Studio 中。
- **项目入口 + 双栏 Studio。** 从项目主页创建或打开作品，在左侧通过自然语言迭代，在右侧即时检查真实渲染结果。
- **不只是静态稿。** 可创作网页、桌面与移动端原型、演示文稿、图片和交互式内容，并在隔离预览中运行。
- **面向交付的导出。** HTML、PDF、PPTX、图片、Markdown 与项目包使用统一的导出进度、成功和失败通知。
- **完整融入 Metis。** Design 跟随 Metis 深浅色主题、语言、模型配置、Windows 通知和桌面宠物任务状态，并能直接返回其他工作面。

## 桌面体验升级

- Chat / Cowork / Code / Design 切换链路进一步减负，恢复硬件合成，降低动画、iframe 重排和状态重算叠加造成的卡顿。
- 最小化到托盘后，任务完成会显示 Windows 通知和未读角标；会话支持标记未读、归档、重命名和删除。
- 工作区会话默认收起到四条，长标题支持悬停查看，操作菜单不再被侧栏裁剪。
- 新增 Metis 原生桌面宠物、自定义宠物导入、大小与速度调节，以及 Chat / Cowork / Code / Design 状态联动。
- 模型与额度错误页、连接器授权入口、Design 暗色预览和 PDF/PPTX/图片导出反馈均已整理。

## 本地运行时与安全

- HCS direct runner 具备格式化的会话数据盘模板、`runtime.hello` guest handshake、boot verifier 和跨 VM 生命周期持久化验证。
- HCS readiness receipt 与 kernel、initrd、rootfs 和 session-data template 指纹绑定，资产变化后自动失效。
- 大型工作区不再被静默截断到 2,000 文件 / 80 MB；默认复制完整工作集，显式保护线超出时整体失败，避免文件缺失和伪删除。
- 特权 VM 服务进一步限制调用者身份、路径与可执行能力边界。

## 安装

- 支持 Windows 10 / 11 64 位。
- 下载并运行 `Metis-Setup-26.7.17.exe`。
- 当前安装包尚未代码签名，Windows SmartScreen 可能显示提示。
- 安装包不包含任何本地 API Key；首次启动后请在设置中配置模型服务。
