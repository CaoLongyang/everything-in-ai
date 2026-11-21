# ADR 0004：headline-radar 实现中的决定

日期：2026-11-21　状态：已采纳
相关：`docs/specs/headline-radar.md`（第 8 节作者的决定）

1. **加了 `numpy`（`numpy==2.4.4`，放在 `[news]`）。** 一次运行窗口内约 300 篇、每篇 1024 维，纯 Python 算相似度要几十秒。
   numpy 2.5 起要求 Python 3.12，本项目支持 3.11，所以固定在 2.4.4。numpy 和 feedparser 都在函数里才导入，
   没装 `[news]` 的人其他命令照常可用（F11-02 有测试守着）。
2. **默认相似度 `embed` 为 0.68**，不是规格草案里的 0.80。2026-11-21 用 13 个真实源、276 篇报道和本地 bge-m3 校准：
   0.80 时同一事件被切成好几组，0.70 起同一事件的中文和英文报道会分开，0.68 时 10 组全部是一件事、没有明显误并。
   只用了一天的数据；以后误并或漏并多了再调。`lexical` 的 0.35 只用固定样例定过。
3. **`sources` 每一项除了规格要求的 `publisher`、`url`，还存 `title` 和 `published`。** 这样正文列表永远能从
   frontmatter 重建，报道滑出时间窗口、缓存被删掉也不丢信息。`Source` 模型本来就允许额外字段，`eia validate` 不报警。
4. **`sources` 只增不减**；已有条目按来源链接的重合找回，大的组先认领，避免两组抢同一个文件。
5. **归组是按时间顺序的贪心算法**（和组的平均向量比）。结果和报道顺序有关，但稳定、快，够用。
6. **mypy 对 `feedparser` 单独放开**（`pyproject.toml` 的 `[[tool.mypy.overrides]]`），因为它没有类型信息。
   代码里不加 `# type: ignore`（docs/CONSTRAINTS.md Floor）；用到它的只有 `eia/headlines/feeds.py`。
7. 下载用标准库 `urllib`，不用 feedparser 自带的下载：超时 20 秒、上限 5 MB、User-Agent 固定为 `eia-headlines/1`，
   自动走系统代理。
8. 推荐源 2026-11-21 实测 13 个（10 家）。去掉的：联合早报（404）、人民网和 NHK（几个月没更新）。
   同一集团的 RFI 和 France 24 只放了 RFI。
