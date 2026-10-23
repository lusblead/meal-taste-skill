# 状态与持续学习

`scripts/taste_store.py` 需要Python 3.10+，只用标准库、不联网。以下 `<script>` 替换为脚本路径，`<BVID>`必须是已核实且导入的真实视频；示例不自带默认候选。`--state-dir`要放在子命令前。优先在独立临时目录练习，勿给真实档案写测试反馈。

## 命令

```text
python <script> init
python <script> status
python <script> taste --session meal-a
python <script> ingest <核实候选.json>
python <script> pool --session meal-a --limit 30
python <script> learn --kind topic --target 游戏解说 --stance like --scope long-term --text "我喜欢游戏解说"
python <script> learn --kind topic --target 技术 --stance dislike --scope session --session meal-a --text "今天不看技术"
python <script> feedback <BVID> --reaction like --text "这条有趣" --session meal-a
python <script> record <BVID> --event recommended --session meal-a
python <script> record <BVID> --event skip --session meal-a
```

- `learn`：kind为topic/creator，stance为like/dislike/neutral，scope为long-term/session。Agent负责从原话识别目标和范围；必须保留原话。long-term跨餐生效，neutral移除该明确规则；session必须有餐次ID，最长24小时，同目标临时规则覆盖长期规则，过期后恢复长期规则。
- `feedback`：`--reaction like/dislike/neutral`评价某条视频；不带reaction仅记原话。dislike排除该视频；like/neutral可纠正，不标watched。`--session`只是反馈来源上下文，**不会让普通评价学习在24小时后丢失**。
- `taste`：展示explicit_rules、有效session_rules及inferred.topics/creators。每个推断有like/dislike/neutral计数、不同视频数和weak/medium；单条weak，至少2个不同视频才medium。重复评价同一视频只算该视频最新评价。推断只作搜索/排序线索，不扩大为整类硬封禁。
- `pool`：排除14天前核实的缓存、明确已看、单条dislike、7天内推荐、当前餐次或24小时内skip、可用性unknown，再应用明确长期/本餐dislike。like写入preference_matches，推断写入inferred_taste_hints，仍由Agent语义排序。默认20条是原始池容量，不是最终交付数；最终选择5条。
- `--include-recent`只放宽近期推荐，不放宽已看/不喜欢/跳过/过期。适合换一个时回用同餐其他备选。

## 数据文件

默认在 `$CODEX_HOME/skill-data/meal-video`，未设则 `~/.codex/skill-data/meal-video`。文件均为schema_version 1：

- `profile.json`：通用defaults（每次5条、时长10–30分钟非硬限制）与明确偏好、收藏推测分开。新增preference_rules、session_rules、feedback_observations、learning_history；旧explicit_preferences和未知字段保留，init不会覆盖旧档案。
- `candidates.json`：items数组，BVID去重；watched_at和taste_reaction随元数据刷新保留。
- `events.json`：最近500条events；recommended/opened/watched/skip/feedback分开。

明确规则不因日志滚动消失；学习原话历史保留最近100条，视频评价聚合保留最近300个视频。这是有界的近期口味证据，不是完整观看历史，也不是训练模型。采用同目录临时文件和原子替换，损坏JSON报错而不重置；避免多个进程同时写同一目录，多文件更新不是数据库事务。

Agent可在保留未知字段的前提下小量维护这些非硬规则字段：

- `onboarding`：status=new/ready/limited，method=favorites/feedback/public，日期、来源与实际范围。首次初始化不要求用户填问卷；已授权读取后由Agent记录结果。
- `inferred_preferences`：收藏推测，含topic/creator、来源、日期、confidence及待确认说明。收藏不是明确喜欢。
- `viewing_preferences`：画质、趣味性、观看负担等要求，区分用户原话、软偏好和硬限制；用户报告的数据标user_report，不假装工具实测。
- `discovery_preferences`：偶尔想看的兴趣/创作者线索，可记frequency=occasional、force_each_batch=false。读完整status来应用，不因某次提及而固定每批占比。

初次无资料时只保留通用默认和空白集合。技能代码中不得放任何真实用户的兴趣种子、账号UID或私密收藏。

## 候选字段

导入格式为数组或带items的对象。必需字段：bvid（BV+10位字母数字）、title、creator、duration_seconds（正数或未知null）、url、checked_at、evidence、availability、origin、topics数组。

- url仅接受HTTPS bilibili.com或www.bilibili.com，与BVID一致的完整单视频；清理跟踪参数，拒绝分P/时间定位及fragment，避免保存成错误首P。
- checked_at是实际核实时间，带时区ISO 8601，不能未来或无时区；缓存新鲜不表示视频发布时间新。
- evidence为metadata/description/subtitles/audiovisual，表示实际取得的最高依据。
- availability为page_available/playback_verified/unknown。页面存在不等于播放成功。
- origin为favorite/search/feed/creator。可增加source_url、description_excerpt、verification_note、in_sampled_favorites、resolution等已知信息，未知不编造。

主题标签需尽量统一（如不要混写大小写/同义词），脚本对topic/creator仅trim+casefold精确匹配；Agent仍须识别同义题材、时长和观看要求。profile旧explicit_preferences、收藏推测不会自动变成精确过滤规则，推荐时必须读取完整档案。

## 反馈、纠正、隐私

助手核实页面的打开/关闭不写用户opened/watched；用户明确看过才记watched。用户指向不清时只问一句必要澄清，不猜BVID。

用户可要求查看明确/临时/推测三组口味，改正目标规则，用neutral撤回。导出和删除仅按指定范围处理，不触及通用memory或其他技能；不可回退操作遵循宿主确认规则。所有个人状态留在代码目录外，不进Git、ZIP或截图示例；分享前检查明确文件清单。