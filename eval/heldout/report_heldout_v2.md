# CUC KB 评测报告 — held-out v2 / no-answer v2（2026-11-07 官网语料）

## 背景

2026-11-07 全量重建后语料从 311 docs / 10,358 chunks 扩到 **2247 docs / 49,571 chunks**
（`cuc_kb_v3`，官网采集批次 +1936 件；embedding 换百炼 `qwen3.7-text-embedding` 1024d，
rerank 仍为 `qwen3-rerank`）。旧 held-out v1 / no-answer v1 按 311 件语料构建，已部分失效：
no-answer v1 的 80 个候选题中 **59 个探测词在新语料命中**（含旧 heldout 10 题拒答中的 8 题，
如附属医院/四六级/运动会/打印店）。按"每次重大检索变更用独立新集评测"的约定，构建 v2 新集并 live 测试。

## 评测集构建（均未参与调参）

`eval/heldout/dataset_heldout_v2.json`（构建脚本 `build_heldout_v2.py`）：

- **84 事实题**：全部针对官网批次新增文档，问点未被 v1–v5 / heldout-v1 使用；口语化改写；
  答案关键词逐条在 `data-v3/chunks.jsonl` active chunk 原文核验（空白归一后精确包含）。
  覆盖 12 个部门：本科生院 16 / 信息化处 16 / 国资处 12 / 学工部 10 / 财务处 7 / 档案馆 6 /
  组织部 4 / 后勤 3 / 督导 3 / 培训 3 / 团委 2 / 信通 2。
  2 题（v79 创新创业学分、v80 境外交流）与旧语料同名同文（官网重复采集），命中任一副本均计对。
- **16 短查询**（2–6 字）、**10 助手隔离题**、**10 页级引用题**（页码取自含关键词 chunk 的
  `page_start`，8/10 落在非首页）。

`eval/heldout/dataset_noanswer_v2.json`（构建脚本 `build_noanswer_v2.py`）：

- **48 严格拒答题**（6 类：heldout 5 / campus 11 / adjacent 11 / external 7 / instance 5 /
  personal 2 / offtopic 7）+ **7 partial 题**。每题探测词在 data-v3 active 语料（正文+标题）零命中。
- 旧题重新校验，59 题命中新语料剔除；na15（四六级报名费）live 复核发现 2019 报名通知 p2
  明确"四级15元/人，六级17元/人"→ 判为可答剔除（`REVIEW_V2`）。

## Live 结果（Phoenix 127.0.0.1:8000，cuc_kb_v3）

### held-out v2（`results_heldout_v2.json`）

| 指标 | 结果 |
|---|---|
| 事实题 hit@1 / @3 / @5 | **69 / 80 / 84**（84 题） |
| 事实题 keyword@5 | **84/84** |
| 事实题 MRR | **0.8883** |
| 短查询 answered | 15/16 |
| 短查询 hit@1 / @3 / @5 | 7 / 11 / 12 |
| 短查询 keyword@5 | 16/16 |
| 助手隔离 pass / hit / leak | **10/10 / 10 / 0** |
| 页码引用 pass | **10/10** |
| 延迟 P50 / P95 | 1834 / 2858 ms |
| 触发重排 | 120/120 |

### no-answer v2（`results_noanswer_v2_final.json`，复核后干净集复跑）

| 指标 | 结果 |
|---|---|
| 严格拒答 | **46/48**（95.8%） |
| 稳定失败 | nb13（开题报告字数，conf 0.7954）、nb22（我的体测成绩，conf 0.7573） |
| partial 拒答（单独探测） | 6/7；na50 校园卡余额被接受（conf 0.808） |
| 延迟 P50 / P95 | 1612 / 2778 ms |

### 对照（非干净 A/B，问题与语料均不同）

| 运行 | hit@1/@3/@5 | MRR | 拒答 |
|---|---|---|---|
| 11-01 基线：旧集 × 旧语料(311) | 82/85/87 | 0.965 | strict 45/45 |
| 11-07 上午：旧集 × 新语料(2247) | 75/81/83 | 0.9023 | 旧 strict 43/45（3 题探测词失效） |
| 本次：v2 新集 × 新语料 | 69/80/84 | 0.8883 | v2 strict 46/48 |

新集 hit@5=100% 说明新文档召回无问题；@1 低于旧集主要因为新语料内部同源干扰
（汇编/须知/FAQ/新版文件含同一条款），而非检索退化。

## Bad case 复核

事实题 15 题 rank>1（全部仍在 top5、keyword 全中）：

- **同源替代命中**（11 题）：v07 公租房（北京市管理办法同条款）、v14 校历（入学须知同日期）、
  v15 转专业（报名通知同志愿数）、v27 户口（入学须知同截止日）、v29 查重（研究生院〔2025〕1号
  新办法压过旧暂行规定）、v40 银行卡（2025-2026 新版缴费通知）、v59 非学历（AI 快问快答）、
  v67 Adobe（信息化常见问题）、v70 博士文献（培养工作规定同引 150 种）、v75 伦理（委员会章程）、
  v78 政审（就业实习政审说明）、v82 文体活动（2008 治安二十制度合订本含同文）。
  其中 v29/v40 实为**更新版本**排在旧版前，属合理行为。
- **generic 汇编抢占 top1**（2 题）：v25 博物馆预约、v32 图书馆赔偿被 2021 学工汇编压到 rank4/3；
  短查询 s12 社团注册 rank9 同因。P0 generic penalty 仍有效但不足以压住含同条款的超大汇编。
- 短查询：s03"党费"正确文档 rank1 但 no_answer=true（短查询假拒答）；s09/s15 期望过窄
  （研究生绩点说明、公务卡管理办法本身是合理 top1）；s10 户口迁移命中研究生入学须知（合理替代）。

拒答失败：

- **nb13**（真 FP）：问开题报告字数，Top1 是国家标准中*毕业论文*字数要求（5000/3000 字），
  问点不对应但 conf 0.7954 过阈值——adjacent 难负例按设计发挥作用。
- **nb22**（真 FP）：个人状态问"我的体测成绩"，2021 学工汇编 topical 强相关 conf 0.7573，
  刚好过 0.75 阈值；answerability guard 未拦住个人状态问。
- **na37**（抖动）：首跑误接受（conf 0.5859），两次复跑均正确拒答（conf 0.37）——
  rerank 超时降级路径会翻转判定，与 11-01 记录的 2/204 超时同类。
- **na50**（partial，信息性）：校园卡余额 personal 问被接受 conf 0.808；语料含"去 APP 查余额"
  指引，agent 层至多给查询渠道，不算编造。

## 结论与下一步（不在本集上调参）

1. 新语料召回完好（hit@5 100%、keyword 100%、隔离/引用 10/10）；排序精度受同源文档与
   generic 汇编干扰，MRR 0.8883。
2. 阈值 0.75 在新集上仍有效（46/48），但裕量薄：nb22 仅超 0.007。个人状态问需要专门的
   guard（"我的/我上学期"等 possessive + 成绩/余额/进度属性），不宜靠全局阈值。
3. generic 汇编（2021 学工汇编 308 页）含大量同条款，现有 penalty 不够；可考虑对含同条款的
   generic chunk 做条款级去重或更强降权——属于检索变更，须另设实验集验证。
4. 短查询假拒答（s03）与窄期望（s09/s15）延续既往结论：短查询改写放 agent 层。
5. rerank 超时降级会翻转 no_answer 判定（na37），建议降级时默认拒答或重试一次。
6. 旧 no-answer v1 正式退役（59/80 失效）；阈值标定改用 v2（48 strict + 7 partial）。

## 产物

- `eval/heldout/dataset_heldout_v2.json` / `build_heldout_v2.py`
- `eval/heldout/dataset_noanswer_v2.json` / `build_noanswer_v2.py`
- `eval/heldout/inventory_v3.py`（新语料盘点）、`peek_docs.py`（出题摘录工具）
- `eval/heldout/results_heldout_v2.json` / `report_heldout_v2.md`（Phoenix 自动生成）
- `eval/heldout/results_noanswer_v2.json`（首跑 49 题含 na15）/
  `results_noanswer_v2_final.json`（复核后 48 题）
- 工作文件 `_inventory_v3.tsv` / `_candidates_view.txt` / `_peek1-4.txt`（不入库）