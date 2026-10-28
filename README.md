# 每日情报简报 v1.0

每天 08:00 抓取 16 个信息源，按关键词筛出 10–12 条"对你可能有用"的，附一条工程知识点，生成 Markdown 并推送到微信。

## 目录结构

```
auto-report/
├── report.py          主程序（抓取 → 打分 → 筛选 → 输出 → 推送）
├── sources.json       数据源清单（加/删源改这里）
├── keywords.json      关键词权重（调"什么值得看"改这里）
├── lessons.json       每日一学（工程知识点，按天轮换）
├── .env               PUSHPLUS_TOKEN
├── run.bat            双击运行
├── .venv/             Python 环境（D 盘）
├── output/            每日情报
└── logs/              运行日志
```

## 现在的信息源

| 分类 | 来源 | 内容 |
|---|---|---|
| 香港 | 香港政府新闻公报（RSS） | 工务、基建、招聘、政策 |
| 政策 | 国家发改委（HTML） | 投资、规划、产业 |
| 专业视角 | Construction Dive（RSS） | 美国/全球工程与数据中心建设 |
| 专业视角 | Global Construction Review（RSS） | 全球大型项目 |
| 专业视角 | Construction Enquirer（RSS） | 英国建筑业新闻 |
| 专业视角 | The Construction Index（RSS） | 英国土木工程新闻 |
| 专业视角 | Tunnelling Journal（RSS） | 隧道与地下工程 |
| 专业视角 | Railway Gazette（RSS） | 铁路与轨道交通 |
| 全球视野 | The Economist · Leaders（RSS） | 全球政经社论 |
| 全球视野 | BBC News（RSS） | 国际新闻 |
| 全球财经 | WSJ Markets（RSS） | 华尔街日报市场版 |
| 全球财经 | CNBC（RSS） | 财经快讯 |
| 全球财经 | MarketWatch（RSS） | 市场与个股 |
| 研究前沿 | arXiv cs.CE（Atom） | 土木工程计算与 AI 论文 |
| 技术与变现 | Hacker News（RSS） | 技术趋势 |
| 技术与变现 | GitHub Trending（RSS） | 开源项目 |
| 技术与变现 | 少数派（RSS） | 中文效率工具 |
| 技术与变现 | Product Hunt（RSS） | 新产品（变现信号） |
| 技术与变现 | OpenAI Blog（RSS） | AI 行业动态 |
| 技术与变现 | Hugging Face Blog（RSS） | 开源模型与工具 |
| 技术与变现 | Ars Technica（RSS） | 科技深度报道 |
| 技术与变现 | TechCrunch（RSS） | 创业与融资 |
| 技术与变现 | TLDR AI（RSS） | AI 每日摘要 |

## 筛选逻辑（三层）

1. **硬门槛**：标题必须出现"工务/基建/建造/BIM/数字孪生/招聘/construction…"等硬词，否则直接丢（全球视野/变现类不受此限）；
2. **关键词加权**：不同词不同权重（招聘 6 分、BIM 4 分、规划 2 分），再叠加来源权重和时效；
3. **去重与限额**：标题相似度 >0.82 视为重复；每个分类有上限（招聘 2、香港 3、政策 2、专业视角 3、全球视野 2、研究 1、技术与变现 3），国际/全球类每个来源最多 1 条。

## 怎么改

- 加一个源：在 `sources.json` 里加一条（`type` 支持 `rss` / `html`）
- 调权重：改 `keywords.json`，比如觉得招聘信息太少，把"招聘"从 6 调到 8
- 换推送渠道：改 `push_wechat()`，或加邮件

## 下一步

- [ ] 加住建部、国务院政策文件库（HTML 解析）
- [ ] 监控目标企业招聘页（页面 hash 变化即提醒）
- [ ] 把每天的情报存成 SQLite，月底生成"月度趋势"
- [ ] 学 Git，传到 GitHub
