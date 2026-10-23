# Meal Taste Skill · 下饭视频

给已经在用Agent的人：吃饭前少刷一会儿，直接拿到 **5个适合自己的B站视频**。支持从Chrome或Edge收藏起步，也可以只说喜欢什么、不想看什么；以后每次反馈都会影响下一次筛选。

## 开始使用

将此仓库作为一个Agent Skill安装，技能名为 **meal-video**。

Codex手动安装：下载本仓库ZIP并解压，把含有 `SKILL.md`、`scripts/`、`references/`、`agents/` 的整个目录放到 `$CODEX_HOME/skills/meal-video`；未设置CODEX_HOME时为 `~/.codex/skills/meal-video`。不要覆盖已有个人数据。重新打开会话后用 `$meal-video` 调用。其他支持Agent Skills的宿主，按其技能安装说明放入同一目录结构。

然后选一种开始方式：

> 用 $meal-video，打开Chrome看我的B站收藏，初始化下饭口味，然后推荐5个。

> 用 $meal-video，通过Edge里的B站收藏初始化。

> 用 $meal-video。我喜欢游戏解说和人文故事，不想看美食探店，先推荐5个。

不想先配置也可以直接说“开饭了”。技能会用可访问的公开内容起步；个人收藏须有你的授权，登录界面由你本人处理。不要求填写完整兴趣问卷。

## 用得越多，怎么变化

- **明确喜好**：“我喜欢历史解说”“以后别推这个UP”——保存成可纠正的长期规则，影响搜索方向、过滤和排序。
- **单条评价**：“第2个挺好”“这条不喜欢”——记住这条的评价，并从多个不同视频逐渐归纳口味；不把一次不喜欢扩大成整类封禁。
- **临时心情**：“今天别看技术”“这顿只有15分钟”——只影响本餐，最多24小时。
- **继续选择**：“换一个”替换主推或指定的一条，另外4条保留；“换一批”换5个。“第3个看过了”记录已看并替换。
- **查看和纠正**：“看看你记住了什么”“这个UP现在可以推荐了”。明确偏好、本餐条件、推测分开显示。

默认优先约10–30分钟，允许弹性扩展；你明确只有多少时间时会服从。下饭不限于美食，趣味性、观看负担和题材均参与判断。助手核实后会关闭新建的视频检查页，只有你要求打开播放的页面才保留。

每项结果包含编号、标题链接、UP主、时长和一句理由。目标是5个；检索受限或找不到足够合适内容时，会说明实际核实到的数量。

## 运行条件与本地数据

- 需要有浏览器或网页搜索工具的Agent宿主；Chrome/Edge控制和登录态由宿主提供。
- 状态助手需要Python 3.10+，只用标准库，无API密钥或第三方包。
- 本仓库不会单独运行成播放器或常驻推荐服务；没有浏览器时可走反馈+公开搜索。

状态默认写入 `$CODEX_HOME/skill-data/meal-video`，未设则 `~/.codex/skill-data/meal-video`。Windows通常是 `C:\Users\<用户名>\.codex\skill-data\meal-video`。可用 `--state-dir`指定其他位置。

文件包括 `profile.json`（偏好）、`candidates.json`（候选）、`events.json`（反馈/推荐记录）。**仓库和发行包不含任何个人状态**。不读取或保存Cookie、令牌、完整观看历史，也不自动点赞、投币或修改收藏。可要求查看、纠错、按范围导出或清除个人记录。

## 开发与验证

```bash
python -m unittest discover -s tests -v
python scripts/taste_store.py --state-dir <临时数据目录> init
python scripts/taste_store.py --state-dir <临时数据目录> taste
```

完整CLI与数据约定见 [references/state.md](references/state.md)，收藏初始化见 [references/browser.md](references/browser.md)。

本版已验证Chrome收藏/详情读取与检查页关闭；Edge路径尚未做真实登录态验收。测试覆盖本地反馈学习，不代表推荐满意度已验证。页面可打开、实际可播放、完整观看是不同状态，输出会遵守已有证据。

设计复用与兼容边界见 [references/design-sources.md](references/design-sources.md)。版本0.1.0，采用[MIT许可](LICENSE)。