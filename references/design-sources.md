# 设计来源与复用

核查日期：2026-10-23。

- [Agent Skills官方规范](https://agentskills.io/specification)：使用SKILL.md、YAML元信息与按需scripts/references，复用现有Agent宿主。
- [HermesYoutubeCurator](https://github.com/pariidanDKE/HermesYoutubeCurator)：借鉴候选采集与口味判断分离、少量深入及偏好/输出设置分开。需要Hermes、YouTube登录态、Python≥3.11/Playwright，不能直接当B站适配。本次未核实到根目录可复制许可证（LICENSE返回404），只参考思路，不复制代码或提示词。
- [bilibili-mcp-server](https://github.com/Zijian-Ni/bilibili-mcp-server)：README列出搜索、详情与收藏工具，[MIT许可证](https://github.com/Zijian-Ni/bilibili-mcp-server/blob/main/LICENSE)已核实。配置标为Alpha/0.1.0，Python≥3.10，收藏需要Cookie；首版已有浏览器读取路径，不引入额外凭据配置，仅列为未来可选适配，未安装/实测该MCP。

本skill采用小型、可解释的本地偏好档案：明确好恶作为规则；对不同视频的重复反馈累积为有证据的推测；临时条件随餐次过期。脚本为独立编写的Python标准库工具，不调用云端模型、不上传用户偏好，不声称训练了专用推荐模型或优于平台算法。

推荐需要宿主Agent的判断与浏览器/搜索工具。Chrome收藏和详情读取已做实际验证；Edge及其他宿主仅提供适配说明，尚未真实验收。学习规则与冷启动可用隔离数据验证，真实推荐是否有趣仍需用户反馈。