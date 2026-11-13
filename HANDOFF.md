# HANDOFF — 检索质量优化（2026-11-01）

## 2026-11-13 相邻块展开 + 版本替代关系（LIVE，A/B 实测后上线）

**一句话现状**：在证据级评测基础上做两项检索改进并 A/B 实测后上线——① **item2 小块检索+相邻块展开**（`KB_EXPAND_ADJACENT=1`）：top 命中合并同文档前后各 1 个 active 相邻块文本，补全被切开的前提/例外/连续条款，排序与可回答性判定不变；② **item3 版本替代关系**（`KB_SUPERSEDED_PENALTY=0.05` + `scripts/apply_superseded.py`）：12 对同名制度「废止」关系写入 `chunk.superseded_by`，旧版仅在新版同在候选时降权让位（时间性查询仍可检索旧版）。**证据级 hit@5 58%→70%，LLM 端到端答对 61%→70%、编造 21%→18%；v2 刷新口径后严格 hit@1/3/5 各 +1、零回归。**

| 配置（evidence_v1，33 可答题） | evidence_hit@5 | cross_clause@5 | item_cov@5 | LLM correct | LLM fabricated |
|---|---:|---:|---:|---:|---:|
| 基线 | 19（58%） | 5/13 | .707 | 20（61%） | 7（21%） |
| 仅展开 N=1 | 23（70%） | 7/13 | .793 | — | — |
| 仅版本 | 19 | 5/13 | .707 | — | — |
| **展开+版本（上线）** | **23** | **7/13** | **.793** | **23（70%）** | **6（18%）** |
| 展开 N=2（敏感性） | 24 | 8/13 | .803 | — | — |

| 项 | 值 |
|---|---|
| 展开收益/代价 | evidence_hit@1/5/10 = 15/23/27（基线 12/19/23）；延迟 P50 +133ms。N=2 仅再 +1 但尾部更高 → **N=1 默认**。LLM 逐题：修好 ev29/ev30（校历日期编造→答全）、ev15/ev24 转好；但带入冲突上下文使 ev01/04/06/13 完整性微降、ev21/33 出现编造 → complete 16→15（含判定噪声）。净 correct +3 / fabricated −1 |
| 版本 live 演示 | 「研究生导师岗位聘任办法」基线 top1-3 全 2023 旧版 → 上线后全 2026 新版；财务/会议费旧版降权让位。**evidence_v1 版本题未动**（ev31/32 已命中、ev33 召回缺失、ev10 属汇编已由版本族处理）——替代关系修的是「旧版压新版」排序，该盲测集未覆盖 |
| v2 回归（刷新口径后） | `results_v14_prod_final_20261113.json`：curated **hit@1/3/5 = 67/78/81（基线 66/77/80，各 +1）**、keyword@5 **84（+1）**、any@1/3/5 79/83/84、MRR .9627、short any 12/15、no-answer 47/48、partial 6/7、iso 9/10、cite 10/10 —— **零回归，净提升** |
| 口径刷新 | 版本降权把现行新版升 top1，v2 三道期望旧版的题需刷新 ground truth（非检索退化，any 口径本就接受新版）：**v35**（会议费 2023 修订）、**v36**（往来款项 2025）补 expected_docs；**v31**（博士匿名评阅 56 号废止 8 号）补 expected_docs + 新措辞 keyword「最多可回避三名专家」+ altgold 纳入 d13a165c3144b。备份 `dataset_heldout_v2{,_altgold}.json.bak-20261113` |
| 数据补丁 | `scripts/apply_superseded.py`：12 对逐对核验（新版含「废止」+旧版文号），341 块/12 文档写 `superseded_by`。**无需 Qdrant 同步**（排序读 chunks.jsonl 的 chunk_map，非向量 payload）。Phoenix `data/{chunks.jsonl,docs.json}.bak-superseded`，`--reverse` 回滚 |
| 代码 | `kb/common.py`（EXPAND_ADJACENT/SUPERSEDED_PENALTY）、`kb/search_core.py`（`_expand_adjacent` 在判定后增文本、`_superseded_docs` 在 rerank/RRF 两路降权）；+8 单测（ExpansionTest/SupersededTest），unit 115 / mcp-http 30 / ruff 全过 |
| 配置/备份 | Phoenix `.env` 追加 `KB_EXPAND_ADJACENT=1`、`KB_SUPERSEDED_PENALTY=0.05`（`.env.bak-20261113-v2exp`）；代码 `kb/{common,search_core}.py.bak-20261113-v2exp`。生产 8000 已重启生效（healthz 52322，展开/版本冒烟通过） |

**坑**：① 展开是双刃剑——补全split条款的同时可能带入冲突上下文（ev01 第六十二条「省级教育行政部门」与「北京市教委」并存使模型困惑），故只增文本、不改排序/判定，N 宜小。② 版本降权的「回归」全是口径问题：v31/v35/v36 现行新版升 top1 而 v2 期望旧版，刷新后严格指标反升；别把这类当真实退化去调参。③ 临时 A/B 进程用独立端口（8002-8007），`pkill -f` 会匹配到执行命令的 shell 自身——按端口/PID 清理。④ `ssh '... && nohup ... &'` 的 `&` 会把 `cd` 一起后台化，主 shell 仍在 home 目录 → 用换行结构 + 绝对路径。

**回滚**（关旋钮即可，无需回代码/数据）：

```bash
ssh phoenix 'cd ~/cuc-kb && sed -i "s/^KB_EXPAND_ADJACENT=1/KB_EXPAND_ADJACENT=0/;s/^KB_SUPERSEDED_PENALTY=0.05/KB_SUPERSEDED_PENALTY=0/" .env && sudo systemctl restart cuc-kb'
# 完全回滚数据+代码：
ssh phoenix 'cd ~/cuc-kb && set -a && . ./.env && set +a && .venv/bin/python -m scripts.apply_superseded --data data --reverse && cp -p kb/common.py.bak-20261113-v2exp kb/common.py && cp -p kb/search_core.py.bak-20261113-v2exp kb/search_core.py && sudo systemctl restart cuc-kb'
```

**遗留**：item3 仅覆盖 12 对显式替代关系；生效/失效时间、适用人群/校区/年级等元数据未回填（schema 已留 superseded_by，可扩展）。时间性查询（「2022 年当时的规定」）仍需 agent 层意图识别。展开 N 可按查询复杂度自适应（未做）。

## 2026-11-13 证据级评测 + 长文档定位读取（LIVE）

**一句话现状**：① 新增**证据级盲测集 + 评测 harness**（`eval/evidence_check.py`、`eval/heldout/dataset_evidence_v1.json`，39 题六类，逐条对 data-v9 核验证据词），检查返回片段是否真含答案证据，并用 opt-in `trace` 定位证据在哪一阶段丢失（dense/BM25 召回→融合池→候选限流→精排排序→top_k）；② **长文档读取工具支持深层定位**：`chunk_id` 锚点±相邻块 / `page` 页码范围 / `offset` 游标翻页，API + stdio MCP + HTTP MCP 三端同步，不再只读文档开头。**排序逻辑零改动**，v2 回归逐项一致。

| 项 | 值 |
|---|---|
| 证据级基线 | `eval/heldout/results_evidence_ev1_baseline_20261113.json`：33 可答题 **evidence_hit@1/@3/@5/@10 = 12/18/19/23**，item_coverage@5 .7071，supported_answer@5 18/33。分类@5：**cross_clause 5/13、multi_point 6/9、table 6/7、version_conflict 2/4**。误拒 ev09/ev10（深层贷款条款，证据在 top5 但 top1 精排分低于阈值）；误接受 ev37/ev38/ev39。证据丢失阶段（52 项）：ok 40 / ranking 4 / fusion_pool 6 / recall 2。**对比文档级 any@5≈满分 → 量化了「找对文件≠找对条款」** |
| LLM 端到端 | `results_evidence_ev1_llm_20261113.json`（`--llm`，qwen-plus 作答+判定，仅取 top5 原文，LLM 判定单独报告）：33 可答题 **correct 20（61%）/ complete 16（48%）/ fabricated 7（21%）/ refused 2**。编造主因：① cross_clause 证据不全（ev03 医院等级、ev09 还款年限、ev18 审批人）；② 版本冲突选错或臆造效力（ev10 用 2024 新政策 20000/25000 否汇编2026 12000/16000、ev31/ev33）；③ 冲突来源无权威标注（ev29/ev30 弃校历改用修学指南日期）。ev21 国奖 8000 vs 10000 属语料自身新旧标准矛盾 |
| 长文档读取 | API `GET /v1/documents/{id}/chunks` 新增 `offset/limit/chunk_id/before/after/page/page_end`（互斥校验，未知锚点 404）；每块带 `index`，响应带 `total_chunks/offset/returned/next_offset/anchor_index/anchor_chunk_id/page_range`；不传定位参数仍返回全文（向后兼容）。MCP `kb_document_chunks` 同步八参数 + 旧 API 客户端回退；**HTTP MCP 包装签名同步**（见坑①） |
| trace | `KB.search(trace=False)` opt-in，API `SearchRequest.trace`；返回 `dense/bm25/fused/selected/order/rerank_scores/rerank_triggered/rerank_failed/final`。默认关闭零开销，agent 工具不暴露，仅评测用 |
| 盲测集 | `build_evidence_v1.py` 构建并强制核验：每个证据词逐字命中 active chunk、无答案探测词零命中、查询与既有 755 条不重复。六类：cross_clause 13 / multi_point 9 / table 7 / version_conflict 4 / attribute_trap 3 / no_answer 3。语料 md5 `881e61e6…`（= 线上 data-v9） |
| 验证 | CI 三 job 全过：lint（ruff 0.14.3 + compileall + bash -n）、unit 107、mcp-http 30（新增 22 测试，含 HTTP/stdio schema 同步测试）。线上冒烟：anchor index 35 返回前后块、page 17 命中 6 块、offset 翻页、未知 chunk 404、trace 九字段、MCP HTTP 八参数。v2 回归 `results_v11_evidence_20261113.json` 与 v10 逐项一致（curated 66/77/80、any 79/83/84、short any 12/15/15、no-answer 47/48、partial 6/7、iso 9/10、cite 10/10） |
| 备份 | Phoenix `kb/{app,search_core,mcp_server,mcp_http_server}.py.bak-20261113-evidence` |
| 产物 | `eval/evidence_check.py`、`eval/heldout/{build_evidence_v1.py,dataset_evidence_v1.json}`、`tests/test_evidence_check.py`；`agent/{tools.openai,openapi}.json` 同步（平台侧配置，Phoenix 无 agent/ 目录） |

**坑**：① **HTTP MCP（`kb/mcp_http_server.py`）的工具 schema 从包装函数签名生成，不是直接用 `TOOL_DEFINITIONS`**——改 stdio 工具参数必须同步改 HTTP 包装签名，否则 agent 走 HTTP（Docker 容器）时新参数不暴露。本次冒烟抓到（HTTP 仍只 doc_id/limit），已修复并补 schema 同步测试。② 转专业 2024 版与 2025 修订版**都 active 且内容冲突**（春季 vs 秋季、限一二年级 vs 不限）；汇编 2022/2026 同理——版本冲突题暴露现行版本族降权只覆盖汇编、不覆盖普通制度修订（item 3 待办）。③ ev38 校园巴士 / ev39 心理咨询：语料有相关主题（班车路线 / 修学指南）但无所问属性（发车时刻 / 预约电话），属属性级误接受，非纯无答案。④ cross_clause 最弱：答案所需 2-3 条款散落 rank 1-9，top-5 难集齐——正是相邻块展开（item 2 已铺路）与候选多样性（item 4）的目标。

**回滚**：

```bash
ssh phoenix 'cd ~/cuc-kb && for f in kb/app.py kb/search_core.py kb/mcp_server.py kb/mcp_http_server.py; do cp -p $f.bak-20261113-evidence $f; done && sudo systemctl restart cuc-kb cuc-kb-mcp cuc-kb-mcp-gateway'
```

## 2026-11-12 评测多答案标注 + 精排失败降级（LIVE）

**一句话现状**：① held-out v2 新增**多答案标注**（`dataset_heldout_v2_altgold.json`，原数据集不动）：同一事实在汇编/FAQ/新版/副本里也能答时计为命中，和严格指标并列报告；② 精排需要但失败（云端超时/报错、无本地兜底）时，响应带 `degraded=true, degraded_reason="rerank_unavailable"`，默认 `KB_RERANK_FAILURE_POLICY=refuse` 判 `no_answer`；MCP 对 degraded 且有结果的返回 `answer_mode=related` + 「先用同一问法重试」提示。排序逻辑零改动。

| 项 | 值 |
|---|---|
| 多答案规则 | curated：active chunk 原文逐字含已核验答案关键词的其他文档，逐条人工读上下文，剔除 8 题答非所问的候选（`REJECT`，附理由）；另补 3 题措辞改写的新版（`MANUAL_ADD`：v25/v29/v31）。short：标题含查询词的 active 文档（确定性规则）。37 道 curated、8 道 short 有备选答案。脚本 `eval/heldout/build_altgold_v2.py --data data-v9` |
| 评测 | `eval/heldout/results_v10_altgold_degraded_20261112.json`：严格指标与 v9 逐项一致（curated 66/77/80、keyword@5 83/84、MRR .8516、short 8/10/10、iso 9/10 leak 0、cite 10/10、no-answer 47/48、partial 6/7）；**多答案 curated any@1/@3/@5 = 79/83/84、MRR .9627；short any 12/15/15、MRR .8229**；P50/P95 943/1551ms（重启后首轮冷缓存 1369/2452） |
| 降级实测 | 线上临时进程设 `RERANK_API_TIMEOUT=0.001`：dense 0.7491 原本会判有答案，现 `no_answer=true, degraded=true`，MCP related+3 条结果。评测中真实超时 1 次（v60），排名正确但被判 no_answer → curated_answered 84→83，这是该策略的代价，靠 agent 重试恢复 |
| 剩余 any@1 未中 | curated 5 题 v14(3)/v15(2)/v20(2)/v27(5)/v75(3)，均在 top5；short s04(2)/s05(3)/s14(3)/s10(未进 top10)：多为问法歧义或答案在长文档非首块，留给 agent 层多问法检索与后续端到端评测 |
| 代码 | `kb/common.py`（`KB_RERANK_FAILURE_POLICY` refuse/keep）、`kb/search_core.py`（`degraded`/`degraded_reason`）、`kb/mcp_server.py`（degraded→related+note）、`eval/live_check_v2.py`（`*_any_*` 指标 + `any_rank`）；+5 单测，68 tests OK、ruff 干净 |
| 备份 | Phoenix `kb/{common,search_core,mcp_server}.py.bak-20261112-degraded`、`eval/live_check_v2.py.bak-20261112-degraded`、`.env.bak-20261112-degraded`；`.env` 追加 `KB_RERANK_FAILURE_POLICY=refuse` |

**回滚**（只改策略不回代码：`.env` 改 `KB_RERANK_FAILURE_POLICY=keep` 后重启 cuc-kb；完全回滚）：

```bash
ssh phoenix 'cd ~/cuc-kb && for f in kb/common.py kb/search_core.py kb/mcp_server.py eval/live_check_v2.py .env; do cp -p $f.bak-20261112-degraded $f; done && sudo systemctl restart cuc-kb cuc-kb-mcp'
```

## 2026-11-10 汇编版本收敛（LIVE，内容零改动）

**一句话现状**：修正「2022/2026 学工汇编」的版本判定——**2026 汇编补 中传教务君 归属与 2026 年份；2022 汇编的 `publish_date` 从错配的 2025-11-18 改回 2022，并统一按汇编降权；2019 资助办法（含 16 个附件的汇编）也按汇编降权；新增「同族新版优先」排序（`KB_VERSION_FAMILY_PENALTY` 默认 0.05）**。查「国家助学贷款管理办法」在 中传教务君 / 新生帮手 / 无过滤 三个口径的 top-1 均变为 **2026 汇编 p337（date 2026）**，此前三个口径 top-1 全是 2022 汇编 p284（date 2025-11-18）。collection 仍 `cuc_kb_v4` = 2290 docs / 52,322 chunks，向量零改动。

| 项 | 值 |
|---|---|
| 根因 | ① 2026 汇编只挂 `新生帮手`，`assistant=中传教务君` 完全不可见；② 通用汇编降权只认「以中国传媒大学学生工作规章制度汇编开头」的标题，2026 命中、2022（年份前缀）漏判 → 2022 被当具体件，2026 被扣 0.10，0.904→0.804 掉到第 7；③ 2022 汇编 `publish_date=2025-11-18` 来自 `新版抓取-最终.csv` 第 13 行，把 jwc 政策页 2025-11-18 的《国际本科生学籍管理实施细则》条目日期安到了 2022 汇编上 |
| 代码 | `kb/common.py`：`is_generic_title` 增加任意位置的「学生（工作）规章制度汇编」标记；新增 `version_family()` / `edition_year()`；`kb/search_core.py`：rerank 与非 rerank 两条路径都按同族新版降权（旧版 −0.05，新版不动） |
| 数据 | `data-v8` = `data-v7` + 3 篇 payload（3327 chunks）：`d896015a41752` assistants `[中传教务君, 新生帮手]` + date 2026；`d6ac65db67ed2` date 2022 + `is_generic=true`；`d3402a19c1e0a`（2019 资助资金办法，16 个附件）`is_generic=true`。md5 docs `f7952db6…` / chunks `3777d289…` |
| 为什么没标 superseded | 2026 汇编不是 2022 的超集（如「研究生院〔2020〕019号」2022 有 129 chunks、2026 仅 15；社团刊物 15→0），整篇标 superseded 会丢内容。改为两版都 active、同族新版优先 |
| 评测 | `eval/heldout/results_v9_family_final_20261110.json`：curated 66/77/80、keyword@5 **83/84**、MRR 0.8516、short 8/10/10、isolation 9/10 leak 0、citation 10/10、no-answer 47/48、partial 6/7、P50/P95 876/1440ms。相对 v8：hit@3 +1、short +1/+2/+2、MRR +0.003；keyword@5 −1 仅 v31（旧措辞「需回避的专家最多为三名」→ 新版「最多可回避三名专家」，期望词失效，非检索损失）、v32/s05/s12 均因汇编降权回到具体件 |
| 备份/回滚 | Phoenix 数据 `data.bak-20261110-family`（= v7）、`data.bak-20261110-family-v2`（中间态）；代码 `kb/{common,search_core}.py.bak-20261110-family`（= 部署前 v8）。回滚见下 |
| 产物 | 本地 `kb-service/data-v8`、`版本收敛-20261110/{manifest.json}`；脚本 `scripts/apply_v8_family.py`（建 data-v8）、`scripts/sync_v8_family.py`（payload 同步，`--reverse`/`--verify`）；线上 manifest `batch-version-family-20261110/manifest.json` |

**坑**：① 2022 汇编日期错配来自「新版抓取」把 2025 条目日期写进 2022 文件，引用里看起来像 2025-11-18 修订版；② 2019 资助办法内部附的是 2016 版助学贷款办法，按具体件排序会压过现行版，必须按汇编降权；③ 2026 汇编是「转换之后」的半角标点版本，v31 的旧期望词在新版正文中措辞不同（三名 vs 3 名），评测期望需人工刷新，别当成回归调参。

**回滚**（恢复 v7 数据 + 反向 payload + v8 代码）：

```bash
ssh phoenix 'cd ~/cuc-kb && cp -p kb/common.py.bak-20261110-family kb/common.py && cp -p kb/search_core.py.bak-20261110-family kb/search_core.py && rm -rf data && mv data.bak-20261110-family data && set -a && . ./.env && set +a && .venv/bin/python -m scripts.sync_v8_family --reverse && sudo systemctl restart cuc-kb cuc-kb-mcp cuc-kb-mcp-gateway'
```

## 2026-11-10 检索提速（LIVE，零回归）

**一句话现状**：检索链路无损提速——**BM25 稀疏倒排重写（分数逐位一致）+ BM25 与 embed→dense 并行 + 查询向量 LRU 缓存（`KB_EMBED_QUERY_CACHE` 可配，默认 2048，0 关闭）+ Qdrant 只取 chunk_id payload**。线上 BM25 阶段 100–200ms → 1–3ms，评测 **P50/P95 1607/2483 → 1144/1786ms（-29%/-28%）**，质量指标与 v7 基线逐项全同。数据/向量/排序参数零改动，collection 仍 `cuc_kb_v4` = 2290 docs / 52,322 chunks。

| 项 | 值 |
|---|---|
| 改动 | 仅 `kb/search_core.py`：`_build_bm25_index`（启动时建 postings+status/assistants/doc_type 布尔掩码，+2~4s 加载）、`_bm25_scores`（只算含词文档，零频贡献恒 0、按查询词序累加 → 与 rank_bm25.get_scores 逐位一致）、`bm25_search` 掩码过滤+stable sort（平局保持原 index 序）、`search()` 用 ThreadPoolExecutor 并行 BM25 与 embed→dense、`encode_query` LRU 缓存（云端向量确定性，重复查询省一次北京往返）、`dense_search` with_payload=["chunk_id"]（原来拉 40 条全文 payload 纯浪费） |
| 验证 | ① 本地 CI 同命令 62 测试全过（新增 SparseBM25Test/EmbedCacheTest 5 个）、ruff 0.14.3 干净；② `scripts/verify_bm25_parity.py --data-dir data-v7`：768 条查询全量分数逐位一致 + 1428 组过滤对比全同，本机基准 84.3 → 1.3 ms/query；③ 线上 Qdrant 实测 payload 选择器兼容；④ `eval/live_check_v2.py --tag v8_speed_20261110`：curated 66/76/80、keyword@5 84/84、MRR 0.8486、short 7/8/9、isolation 9/10 leak 0、citation 10/10、no-answer 47/48、partial 6/7 —— **与 v7 基线逐项一致** |
| 评测存档 | `eval/heldout/results_v8_speed_20261110.json`（线上同路径）；部署脚本 `../检索提速-20261110/deploy_phoenix.sh` |
| 备份 | Phoenix `kb/search_core.py.bak-20261110-speed`（= v7 版）；缓存配置化追加部署备份 `kb/{search_core,common}.py.bak-20261110-cache` |

**坑**：① rank_bm25 的 get_scores 对每个查询词全量列表推导扫 52k 个 Counter，是本地最大浪费；稀疏化必须保持查询词顺序（含重复词）累加才能逐位一致。② 测试 build_kb 手工装 BM25 不走 load()，bm25_search 保留首次使用懒建索引兜底。③ 剩余耗时大头是 embed/rerank 的 Phoenix→北京往返（各 0.3–0.9s，已 keep-alive），属网络拓扑问题，本次不动。

**回滚**：

```bash
ssh phoenix 'cd ~/cuc-kb && cp -p kb/search_core.py.bak-20261110-speed kb/search_core.py && sudo systemctl restart cuc-kb'
```

**遗留**：如需进一步提速，方向是把服务迁到离百炼更近的节点或换低延迟 rerank 端点（改部署拓扑，另议）；查询缓存只命中完全相同的 query 字符串。

## 2026-11-09 出处/日期元数据回填（LIVE，零回归）

**一句话现状**：2290 篇文档补上原始出处与日期——**2268 篇（99.0%）有 source_url，2187 篇（95.5%）有 publish_date**（2094 完整日期 / 93 仅年份），每条带 date_basis 依据。数据切到 **data-v7**（docs/chunks 每条新增 `source_url / publish_date / date_basis / provenance` 四字段，已有字段逐字节零改动），Qdrant payload 同步 52322 点（verify 209288 值 0 不一致）。collection 仍 `cuc_kb_v4` = 2290 docs / 52,322 chunks，**向量零改动**。

| 项 | 值 |
|---|---|
| 来源映射 | 官网采集 1936 篇按 doc_id 直连 `doc_dates_final.json`；新版 43 篇按文件 basename 匹配 `新版抓取-最终.csv`；AIHub 原始件 289 篇 corpus sha256 → 各批 manifest docUrl（研究生教师批键名是 `url` 不是 `docUrl`）；网页知识 1 篇用清单 webUrl。原始件日期：文件名日期 > 文号年份 > 标题年份 |
| 缺口 | 22 篇手工收集件无链接记录、103 篇活文档无日期，如实留空不造假；合计 114 篇见 `元数据回填-20261109/待补清单.csv` |
| 数据 | 本地 `kb-service/data-v7`（= 线上 `~/cuc-kb/data`，md5 docs 6b25b7c3… / chunks 236f3d8d…）；data-v6 保留 |
| 新代码 | `scripts/sync_provenance.py`（scroll 一次按 doc_id 分组、按点 id 批量 set_payload，避开 doc_id 无索引全扫描；--verify / --reverse）；`_citation()`/`documents()`/`/v1/documents/{id}/chunks`/MCP 三工具/`static/index.html` 均输出新字段；ingest 透传四字段；agent/*.json 已同步 |
| 裁定/备份 | `元数据回填-20261109/{build_provenance.py, provenance.json, apply_local.py, deploy_phoenix.sh, 交接-出处日期回填-20261109.md}`；Phoenix `batch-provenance-20261109/`、`data.bak-20261109-provenance`（= v6）、四代码文件 + static `.bak-20261109-provenance` |
| 评测 | `eval/heldout/results_v7_provenance_20261109.json`：curated 66/76/80、keyword@5 84/84、MRR 0.8486、short 7/8/9、isolation 9/10 leak 0、citation 10/10、no-answer 47/48、partial 6/7 —— **与 v6 基线逐项一致**；P50/P95 1607/2483ms（历史区间内波动，v4 曾 1732/2787，质量指标全同）。本地 CI 同命令 57 测试全过、ruff 干净 |

**坑**：① 本地 `mcp_server.py` 曾落后线上（缺并行多问法 RETRY_RULE）——已先把线上版拿回本地再叠加本次改动，部署不回退功能；以后改 MCP 先 diff 线上。② REFERENCE_RULE 参考文件清单格式现为「N. 《文档名》（文号，日期，第 X、Y 页）— 文件名 — 原文链接 — 原始出处」。③ `.env.example` 行内注释会挂 test_repo 占位符测试，注释要独占一行。

**回滚**（恢复 v6 数据 + 删 payload 四字段）：

```bash
ssh phoenix 'cd ~/cuc-kb && for f in kb/search_core.py kb/app.py kb/mcp_server.py kb/ingest.py; do cp -p $f.bak-20261109-provenance $f; done && cp -p static/index.html.bak-20261109-provenance static/index.html && rm -rf data && mv data.bak-20261109-provenance data && set -a && . ./.env && set +a && .venv/bin/python -m scripts.sync_provenance --reverse && sudo systemctl restart cuc-kb cuc-kb-mcp cuc-kb-mcp-gateway'
```

**遗留**：114 篇待人工补出处/日期；未来采集入库须在 record 带 url/日期（ingest 已透传）；官网采集侧 data-official 历史目录未同步新字段（以 data-v7 为准）。

## 2026-11-09 助手归属重划（LIVE，零回归）

**一句话现状**：按语料实际结构重划助手归属——现有 12 标签全保留（问道中传=通用伞，189 条评测用其过滤），问道中传独占 1459 篇按部门/文号/内容补领域标签：**1383 篇 / 28369 chunks 追加**，76 篇校级通用保持原样。新增 13 个领域标签（研究生教育202/招生166/人事163/院系教学151/信息化服务118/科学研究106/学科建设104/国际交流101/校园安全93/工会36/教育基金会27/医疗健康24/校办企业9），过滤菜单 12→25（/v1/meta 动态列出，接口兼容）。**向量零改动**（仅 assistants payload）。collection 仍 `cuc_kb_v4` = 2290 docs / 52,322 chunks（44,415 active / 7,906 superseded / 1 inactive）。

| 项 | 值 |
|---|---|
| 规则 | 非 root 件 department→领域机械映射；root 原始 191 篇按文号前缀（人字→人事/研字→研究生教育/科研字→科学研究/校医字→医疗健康/保卫字→校园安全/教发字+校企字→校办企业/继教字→培训）+ 标题 + 逐篇内容裁定（融合门户页按服务部门、"部门信息"=督导处简介、"预约入校"=i中传APP指南→信息化、海南师德考核→人事 等）；UNMATCHED=0 |
| 同域扩充 | 培训小帮手 64→107（教育发展中心短训班41+继教字）、教务君 174→181、新生帮手 104→109、财务处 84→89、党建 29→41、督导 37→40、小资 208→210、小勤 50→53、档小管 59→60、团委 16→18 |
| 数据 | 本地 `kb-service/data-v6`（= 线上 `~/cuc-kb/data`，md5 docs 9673bfbf… / chunks c326186b…）；data-v5 保留 |
| 新代码 | `scripts/sync_assistants_repartition.py`（按标签 23 组 set_payload，Filter doc_id MatchAny；--reverse 回滚 / --verify 校验） |
| 裁定/备份 | `助手归属重划-20261109/{repartition_manifest.json, repartition_reverse.json, repartition_detail.tsv, 助手归属重划方案.md, repartition.py, apply_local.py, smoke_repartition.py}`；Phoenix `batch-repartition-20261109/`、`data.bak-20261109-repartition`（切换前=v5） |
| 评测 | `eval/heldout/results_v6_repartition_20261109.json`：curated 66/76/80、keyword@5 84/84、MRR 0.8486、short 7/8/9、isolation 9/10 leak 0、citation 10/10、no-answer 47/48、partial 6/7 —— **与 v5 structopt 基线逐项一致**；P50/P95 1253/1933ms。冒烟：研究生教育/人事/信息化服务 top-1 正确且标签符合，无过滤基线不变，superseded 零泄漏；25 标签 payload 点数与 data-v6 逐一吻合 |

**判别口径**：只增不删（现有助手池只扩不缩，无过滤检索不受影响——assistants 过滤仅显式传参时生效）；领域与现有 11 助手对应的直接加该助手名，不另造标签；校级通用（审计/法治合同/信息公开/党政办公/出版社/博物馆/体育场馆/国家法律）保持问道中传独占。

**回滚**（恢复 v5 数据 + 反向 payload）：

```bash
ssh phoenix 'cd ~/cuc-kb && rm -rf data && mv data.bak-20261109-repartition data && set -a && . ./.env && set +a && .venv/bin/python -m scripts.sync_assistants_repartition --reverse && sudo systemctl restart cuc-kb cuc-kb-mcp cuc-kb-mcp-gateway'
```

**遗留**：191 篇 root 原始件的 department 字段仍为默认信息化处（本次只改 assistants，领域标签已正确路由；如需部门字段也回填，可复用 repartition 的 domain_of 裁定另跑 bulk_dept_sync）。agent 层如需利用新标签做查询路由，/v1/meta 已列出 25 个可选值。

## 2026-11-08 文档结构优化（LIVE，零回归）

**一句话现状**：在 cuc_kb_v4 上做结构优化——176 篇低价值/真重复文档标 superseded（3534 active chunks 下线），1171 篇部门元数据回填。**向量零改动**（仅 payload status/department），collection 仍 `cuc_kb_v4` = 2290 docs / 52,322 chunks（**44,415 active / 7,906 superseded / 1 inactive**）。评测与基线逐项一致、零泄漏。

| 项 | 值 |
|---|---|
| 标记 | 176 篇 = 真重复 3（数据安全法/消防安全办法/新生入学须知，双向 6-gram 包含≥0.97，内容均在保留版）+ 新闻宣传 90 + 采购磋商公告 24 + 行政年度报告/审计 59。评测期望 390 篇全程保护未标记 |
| 部门回填 | 1171 篇 department 由默认"信息化处"→真实来源单位（研究生院177/招生处158/人事处114/…40 组）；信息化处标签 1464→293（余 196 为无二级目录的原始件，无法安全判定，保留） |
| 数据 | 本地 `kb-service/data-v5`（= 线上 `~/cuc-kb/data`，md5 docs 77a9f65f… / chunks 995ac12a…）；data-v4 保留 |
| 新代码 | `scripts/sync_payload_structopt.py`（按 doc_id 设 payload status，支持 value="active" 反向恢复）、`scripts/bulk_dept_sync.py`（按部门分组 MatchAny 批量 set_payload）、`scripts/smoke_structopt.py` |
| 裁定/备份 | `文档结构分析-20261108/optimization_manifest.json`（+ reverse_manifest.json）；Phoenix `batch-structopt-20261108/{manifest,reverse_manifest}.json`、`.env.bak-20261108-structopt`、`data.bak-20261108-structopt`（切换前=v4） |
| 评测 | `eval/heldout/results_v5_structopt_20261108.json`：curated 66/76/80、keyword@5 84/84、MRR 0.8486、short 7/8/9、isolation 9/10 leak 0、citation 10/10、no-answer 47/48、partial 6/7、P50/P95 1271/1830ms —— **与 v4 基线完全一致** |

**判别口径**：标题相同≠重复（复试方案 45 篇为各学院不同内容、学位基本要求(1)/(3)为不同分册，均保留）；真重复仅取双向包含≥0.97。低价值用高精度新闻/采购/报告标记，排除评测期望、表格/指南/政策意见/期刊名录（CSSCI/SSCI/A&HCI 保留供教师）。版本链（论文编写规则 6 版等）未自动收敛，留人工。

**坑**：① 脚本从 os.getenv 读配置、不自动加载 .env，须 `set -a; . ./.env; set +a` 后再跑，否则 COLLECTION 退回 cuc_kb 且 Qdrant 401。② doc_id 非索引字段，逐个 scroll 全扫描极慢（1171 篇部门跑了数分钟未完）→ 改 bulk_dept_sync 按部门 MatchAny 分组（40 次调用秒级）。③ 部署后短暂遇百炼 `Arrearage`（账户欠费）致 search 500，数分钟后同 key 自愈返回 200，非本次改动引起。

**回滚**（恢复 v4 数据 + 反向 payload）：

```bash
ssh phoenix 'cd ~/cuc-kb && rm -rf data && mv data.bak-20261108-structopt data && set -a && . ./.env && set +a && .venv/bin/python -m scripts.sync_payload_structopt --manifest batch-structopt-20261108/reverse_manifest.json --apply && sudo systemctl restart cuc-kb cuc-kb-mcp cuc-kb-mcp-gateway'
```

（reverse_manifest 把 176 篇恢复 active、1171 篇 department 恢复信息化处；.env 未改无需恢复。整库回滚仍可用 cuc_kb_v3。）

**遗留**：196 篇无二级来源目录的原始件部门仍为默认值；版本链家族与时效通知按年度收敛待人工裁定（见结构分析报告第七节 P2）。

## 2026-11-08 老旧文档更新批次部署（LIVE）

**一句话现状**：753 篇久远文档核查后的更新批次已上线——43 篇新版文档入库（2751 chunks 增量 Batch 嵌入，392,684 tokens ≈ ¥0.10），95 篇旧版标 superseded（部署复核撤销 5 篇误标，累计 9 篇 reverted）。collection `cuc_kb_v4` = **2290 docs / 52,322 chunks**（47,949 active / 4,372 superseded / 1 inactive）；`cuc_kb_v3` 保留回滚。43 个新 PDF（7.8MB）已传 OCI 桶，PAR 验证 200。

| 项 | 值 |
|---|---|
| Collection | `cuc_kb_v4`（1024 维 cosine，复用 v3 全量向量 + 2751 新向量，零重嵌旧件） |
| 数据 | `~/cuc-kb/data` = data-v3 + 43 新件 + 95 superseded 标记（本地 `kb-service/data-v4`、`官网采集-20261106/data-official` 同步） |
| 向量产物 | `~/cuc-kb/batch-qwen37-20261108/`：job.json / manifest.json / output.jsonl / vectors.npz(2751) / vectors-v4.npz(52322 合并) |
| 备份 | Phoenix：`.env.bak-20261108`（v3 态）、`data.bak-20261108`（切换前 = v3 合并集 2247/49571）、`data.bak-20261108-revert`（v4 百篇标记态、撤销前）；本地 data-official `*.bak?-20261108` 见交接文档 |
| 新代码 | `scripts/merge_update_20261108.py`（增量合并，保留 qa-csv 伪文档 52 chunks）、`eval/live_check_v2.py`（v2 集正式 live runner）；批次目录 `revert_false_positives_20261108.py` |
| 标记文件 | `官网采集-20261106/superseded_flags_20261108.json/.csv`（95 superseded + 9 reverted） |

**部署复核撤销的 5 篇误标**（6-gram 内容覆盖 + v2 评测检出；"新版"为附件空页快照或不同文档，旧版内容唯一）：dff9904ce7d48 税务发票报销说明（v37 检出，8件清单规则唯一源）、d7b32066deb2a 技术合同专用章办法、ddb8dab3bc435 科级干部聘任办法、d0538c958bc90 公派教师待遇规定、d69a0af30190f 公费医疗转诊制度（新版仅学生版，职工内容唯一；**旧版时效性待人工确认**）。其余启发式嫌疑件经内容核验保留标记（科研经费报销细则有 live 全量 active 副本、参观须知内容 81% 在他件、消防法/学科目录为法定废止、教学成果办法新版覆盖 65%）。

**验证**：healthz 52322；冒烟 5 例（3 新文档 top-1 + 2 superseded 旧版零泄漏）；MCP kb_search direct 正常（loopback + 172.17.0.1 容器路径）；公网 kb.acgurl.cn（EdgeOne）正常；`/v1/files` 302→PAR 200 %PDF；撤销件 5/5 恢复可检索。**v2 回归**（`eval/heldout/results_v4_20261108_final.json`，runner `eval/live_check_v2.py`）：curated hit@1/@3/@5 **66/76/80**、keyword@5 **84/84**、MRR 0.8486、answered 84/84；short 7/8/9（answered 15/16）；isolation 9/10 leak 0；citation 10/10；拒答 **47/48**（nb22 转拒答，nb13 稳定 FP conf .795）；partial 6/7；P50/P95 **1732/2787 ms**。对比 v3 基线（69/80/84、MRR .8883、拒答 46/48）：全部排序差异 = 8 个"期望旧版被合法取代、新版标题变更"的数据集失效项（curated v04/v21/v29/v31 + short s04/s06/s08 + iso i05，**keyword@5 全真**，v29/v31/s06/s08 top-1 即新版），无内容损失、无不明回归；拒答 +1。v2 集 expected_docs 需按语料变更做维护性刷新（非调参，待用户决定）。

**回滚**（恢复 v3 合并集 + cuc_kb_v3）：

```bash
ssh phoenix 'cd ~/cuc-kb && cp -p .env.bak-20261108 .env && rm -rf data && mv data.bak-20261108 data && sudo systemctl restart cuc-kb cuc-kb-mcp cuc-kb-mcp-gateway'
```

**遗留**：A&HCI 2021 xlsx（Wayback 限流放弃）、2 篇新版源页 CMS 删除（成绩/毕业论文管理办法）、13 篇待人工确认、公费医疗转诊旧版时效性——见 `官网采集-20261106/交接-老旧文档更新-20261108.md`。核查表 `.new.csv` 为标注版（原 `.csv` 被 Excel 锁定未替换，备份 `.csv.bak-20261108` 已建）。

## 2026-11-07 全量重建：qwen3.7-text-embedding + 官网采集批次入库（LIVE）

**一句话现状**：线上向量模型已从 fastembed bge-small-zh 切换为百炼 `qwen3.7-text-embedding`（1024 维），查询侧实时 API + `instruct`，文档侧走 Batch File API（半价）。同时入库官网采集批次，collection `cuc_kb_v3` = **2247 docs / 49,571 chunks**（311 旧 + 1936 新；7 个 KB flags 旧版标记 superseded，共 294 superseded + 1 inactive）。Batch 计费 9,745,600 tokens ≈ **¥2.44**；1936 个新 PDF（472MB）已传 OCI 桶，引用 PAR 验证 200。

| 项 | 值 |
|---|---|
| Collection | `cuc_kb_v3`（1024 维 cosine），`cuc_kb_v2`/`cuc_kb` 保留回滚 |
| 查询嵌入 | `KB_EMBED_PROVIDER=openai`，`EMBED_API_MODEL=qwen3.7-text-embedding`，`KB_EMBED_DIM=1024`；key 复用 `RERANK_API_KEY`（workspace key，已关该模型"用完即停"） |
| 查询 instruct | `KB_EMBED_QUERY_INSTRUCT` 默认英文检索指令（兼容端点忽略 text_type，instruct 生效） |
| 数据 | `~/cuc-kb/data` = 合并集（原 data → `data.bak-20261107-qwen`） |
| 向量产物 | `~/cuc-kb/batch-qwen37-full/`：job.json / manifest.json / output.jsonl(1.1GB) / vectors.npz(173MB) |
| 备份 | `kb/{common,search_core,ingest}.py.bak-20261107-qwen`、`.env.bak-20261107-qwen` |
| 新代码 | `kb/common.py`+`kb/search_core.py`（CloudEmbedder，keep-alive）、`scripts/{batch_embed,load_vectors,merge_data_v3,upload_pdfs_v3}.py`、`eval/live_check.py`；本地分支 `feat/qwen37-embed`（未提交） |

**回滚**（恢复 bge-small + cuc_kb_v2 + 旧数据）：

```bash
ssh phoenix 'cd ~/cuc-kb && for f in kb/common.py kb/search_core.py kb/ingest.py .env; do cp -p $f.bak-20261107-qwen $f; done && rm -rf data && mv data.bak-20261107-qwen data && sudo systemctl restart cuc-kb cuc-kb-mcp cuc-kb-mcp-gateway'
```

**验证**（2026-11-07，`eval/ab/results_qwen37_v3_live.json`）：healthz 49571；冒烟（宿舍床/审计处/成绩办法/游泳馆年卡）正常；MCP kb_search direct 0.952；新文档 `/v1/files` 302→PAR 200。留出集 live：hit@1/@3/@5 **75/81/83**，MRR 0.9023，answered 85/87；拒答 heldout 9/10、strict 43/45、partial 7/10；P50/P95 1770/2935 ms。

**对比 11-01 基线（82/85/87，MRR 0.965）下降，但语料从 311→2247 docs，不是干净 A/B**：4 个 hit@5 miss 中 3 个被 generic 汇编（2021 学工汇编/2023 修学指南）压过；3 个拒答失败（附属医院/四六级报名费/运动会日期）的探测词在新语料已有命中（分别 39/13/33 条），no-answer 集按旧语料"零命中"构建已部分失效。下一步：同语料 bge-small vs qwen3.7 A/B（ab_embed 离线 harness）、generic penalty 与阈值重标定、按新语料重建 no-answer 集。

## 一句话现状（2026-11-01 历史）

线上 reranker 已从 CF `bge-reranker-base` 切换为**百炼 `qwen3-rerank`**，每条查询都 rerank，拒答只看 rerank 阈值。留出集 Hit@1 从 73 提到 82（87 题），MRR 从 0.885 提到 0.965，无答案从 7/10 提到 10/10。代价是 P50 延迟从约 0.7 s 涨到约 1.5 s。向量模型保持 bge-small-zh，Qdrant 不需要重建。

## 线上状态（Phoenix `~/cuc-kb`）

> 2026-11-02：仓库已重构为 `kb/` 包（PR acgurl/cuc-kb-service#3），并已于 2026-11-02 03:06 UTC 部署到 Phoenix。三个服务分别以 `uvicorn kb.app:app`、`python -m kb.mcp_http_server`、`python -m kb.mcp_gateway_proxy` 运行（单元在 `deploy/`），`~/cuc-kb/run-mcp.sh` 指向 `kb/mcp_server.py`。根目录旧的平铺 `.py` 文件还在，但已不再使用；下表和下面回滚命令里的平铺文件名都是部署前的历史记录。
>
> 布局回滚：`cd ~/cuc-kb && sudo cp backup-20261102-pkg/*.service /etc/systemd/system/ && cp -p backup-20261102-pkg/run-mcp.sh . && sudo systemctl daemon-reload && sudo systemctl restart cuc-kb cuc-kb-mcp cuc-kb-mcp-gateway`。
>
> 以后部署：`git archive main kb scripts deploy static | ssh phoenix 'cd ~/cuc-kb && tar -x'`，改了单元文件要再拷到 `/etc/systemd/system/` 并 `daemon-reload`。

| 项 | 值 |
|---|---|
| 服务 | `cuc-kb.service`（uvicorn 127.0.0.1:8000），2026-11-01 08:05 UTC 重启后正常 |
| 代码 | `search_core.py`、`common.py` = `feat/qwen3-rerank` 的 `a9e19fd` |
| Rerank | `RERANK_API_BASE=https://dashscope.aliyuncs.com`，`RERANK_API_PATH=/compatible-api/v1/reranks`，`RERANK_API_MODEL=qwen3-rerank`，`RERANK_API_TIMEOUT=4` |
| 开关 | `KB_RERANK_ALWAYS=1`，`KB_ANSWERABILITY_OVERRIDE=0`，`KB_RERANK_LOCAL_FALLBACK=0`，`KB_RERANK_MIN_SCORE=0.75` |
| 备份 | keep-alive 版之前：`search_core.py.bak-20261101-ka`、`common.py.bak-20261101-ka`；rerank 切换之前：`search_core.py.bak-20261101-rr`、`common.py.bak-20261101-rr`、`.env.bak-20261101-rr` |

**回滚**（恢复成 CF bge-base 和旧逻辑）：

```bash
ssh phoenix 'cd ~/cuc-kb && for f in search_core.py common.py .env; do cp -p $f.bak-20261101-rr $f; done && sudo systemctl restart cuc-kb.service'
```

## Git

- 远端：`github` = https://github.com/acgurl/cuc-kb-service （私有，主仓库，GitHub Actions 跑 CI）；`origin` = 校内 GitLab 镜像（仅校园网可达，不跑 CI）。
- `main` = `e00c7ff`，refactor 和 qwen3-rerank 两个分支都已合入，并已删除。
- `ci/gitlab-pipeline`（PR acgurl/cuc-kb-service#1）：GitHub Actions CI、`tests/`、`constraints/prod*.txt`，以及 `_safe_next` 开放跳转修复（`/auth/login?next=/%5Cevil.test` 登录后会跳到外站）。修复已于 2026-11-01 部署到 Phoenix（备份 `app.py.bak-20261101-redirect`），线上冒烟通过；线上代码与该分支一致。
- 不提交的文件（已加入 `.gitignore`）：`eval/heldout/_candidates.txt`（出题用的原文样本）、`eval/ab/results_deploycheck.json`。

## 这次做了什么

1. **留出集** `eval/heldout/dataset_heldout_v1.json`（`build_heldout.py` 生成并核验）：87 道有答案题，对应 87 份 v1–v5 从未引用过的文档；问法口语化改写，答案关键词逐条在 `data/chunks.jsonl` 里核对。另有 10 道无答案题，探测词在语料中零命中。**之后所有改动都要在这个集上看结果**。v5 已被反复调参，会高估收益。
2. **离线 A/B 脚本** `eval/ab/ab_embed.py`：继承 `search_core.KB`，只替换查询编码、dense 召回（numpy）和 reranker，其余都是线上代码。bge-small + bge-base 的离线结果和线上 API 逐项一致；切换后线上复测也和离线预测一致。
3. **结论**（报告见 `eval/ab/report_ab1.md`、`report_rr1.md`、`report_rr2.md`）：
   - 换向量模型（bge-m3、Qwen3-Embedding-0.6B、bge-large-zh）都没有显著收益，dense@40 已经是 86/87。
   - 瓶颈在 reranker。qwen3-rerank 显著优于线上：204 题配对检验 Hit@1 赢 23 题、输 9 题（p=0.02），Hit@5 赢 7 题、输 0 题（p=0.016）。
   - 拒答剩下的错误全部来自属性可答性规则（`ATTRIBUTE_HINTS` / `SPAN_PATTERNS`）；只用 0.75 阈值，留出集上保留 86/87、拒答 10/10。
4. **代码**：新增 `KB_RERANK_ALWAYS` 和 `KB_ANSWERABILITY_OVERRIDE` 两个开关，默认值保持旧行为；修复了本地 fastembed 回退输出原始 logit、和云端 0–1 分数尺度不一致的 bug（现在经过 sigmoid）。
5. **部署可行性**：Phoenix 4 核 ARM 上，本地 rerank 24 个块要 17～60 s，不可行；CF Workers AI 只有 bge-reranker-base；从美西调百炼北京，24 块单次 0.9～2.3 s。

## 待办（按优先级）

1. ~~换掉线上用的百炼 Key~~：**线上已完成（2026-11-01）**。Phoenix 现在用控制台新建的专用 Key（sha256 前缀 f31ac91cd822），线上检索已验证。本机 bl 配置也已切换到新 Key。用户已在控制台吊销旧的个人 Key（前缀 4f34cd21fc93）；吊销后立即测试时旧 Key 仍能调用，可能有生效延迟，建议之后再确认一次。存有旧 Key 的两个备份（Phoenix `.env.bak-20261101-key`、本机 `~/.bailian/config.json.bak-20261101-key`）已删除。
2. ~~延迟和超时~~：**已完成（2026-11-01）**，reranker 改成 keep-alive 长连接，线上 P50 从 1.5 s 降到 0.85 s，P95 从 3～5 s 降到 1.35 s，见 `eval/ab/report_abl1.md`。之后可选：对冲请求、国际站端点。
3. **合并策略**：用户决定 refactor 和 rerank 两个分支怎么进 main。
4. ~~扩充无答案集~~：**已完成**。`eval/heldout/dataset_noanswer_v1.json` 新增 45 道严格无答案题和 10 道部分可答题，加上原有的共 65 道无答案题；`eval/ab/report_na1.md`。阈值 0.75 复核通过：拒答 65/65，保留 202/204，交叉验证中位 0.73，判定相同，**不改**。新的灰区是部分可答题（得分 0.61～0.79，线上拒掉 8/10），MCP 层已于 2026-11-01 09:11 UTC 部署（备份 `mcp_server.py.bak-20261101-mode`，已重启 `cuc-kb-mcp.service`，线上冒烟测试三档均正确）：`kb_search` 新增 `answer_mode`（direct / related / none，`KB_RELATED_MIN_CONFIDENCE=0.6`）；none 时不返回结果；related 时要求 agent 先说明“没有直接答案”，再给出相关规定。`agent/tools.openai.json`、`openapi.json` 已同步。按 na1 数据，10/10 部分可答题会进入 related，65 道严格无答案题中有 11 道也会进入 related，需要靠 agent 的相关性判断兜底。下一步：用 LLM 做端到端评测，统计部分回答率和编造率。
5. ~~消融人工加分~~：**已完成**（`eval/ab/ablate.py`、`report_abl1.md`）。精排后的短语/定义/期限加分零影响，已删除；通用汇编降权有用（关掉后 Hit@1 −6/−5），保留；覆盖度加分中性，保留。有答案题最低分 0.709 和无答案题最高分 0.723 已经交叠，所以第 4 项（扩无答案集）更紧迫。
6. **剩余排序失败**（qwen3-rerank，留出集）：h05/h22/h35/h69/h77，基本都是多份文件有相近条款，需要靠助手过滤或查询里的身份信息解决，不是 rerank 能解决的。

## 复现与环境

- **线上 API 复测**：把 `eval/heldout/dataset_heldout_v1.json` 和 `eval/dataset_v5_personas.json` 拷到 Phoenix，用 `.env` 里的 `KB_API_TOKEN` 调 `http://127.0.0.1:8000/v1/search`（`top_k=5`，不指定助手）。指标的定义和 `ab_embed.py` 的 `summarize()` 一致。
- **离线评测环境**（本机 Windows，RTX 5060）：`.venv-eval/`（torch 2.11+cu128、sentence-transformers、fastembed、modelscope），`HF_HOME=.cache/huggingface`，ModelScope 模型在 `.cache/modelscope/`。`data/chunks.jsonl` 和 `data/docs.json` 来自线上 2026-11-01（md5 `b301960b…`），嵌入矩阵在 `data/ab/`。以上目录都被 gitignore 了。
- **用户偏好**：模型从 ModelScope 下载，嵌入和 rerank 跑在 GPU 上。fastembed 默认走 CPU，只用于和线上逐字节对齐。
- **百炼调用**：A/B 脚本直接调 HTTP 接口，Key 从 `~/.bailian/config.json` 读，结果缓存在 `data/ab/rerank_cache_qwen3-rerank.json`。本地 bl CLI 是 2.5.0，最新 2.7.2，升级要先征得用户同意。

## 常见坑

- 阈值和 rerank 触发条件都绑定具体模型。换 embedding 或 reranker 后必须重新标定：用 `eval/ab/calibrate_no_answer.py`，在 v5 上标定，到 heldout 上检验。
- qwen3-rerank（百炼）和 Qwen3-Reranker-0.6B（开源）不是同一个模型，分数尺度完全不同。
- Qwen3-Reranker 这类因果语言模型打分时，只对最后一个 token 算 `lm_head`，否则会对整段序列算词表 logits，把 8 GB 显存撑爆。
- 跑后台任务时，`grep` 必须加 `--line-buffered`，Python 要设 `PYTHONUNBUFFERED=1`，否则看不到中间进度。
