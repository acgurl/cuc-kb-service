# 证据级盲测 v2 优化实施报告（2026-11-14）

针对 `report_evidence_v2.md`「局限与后续」列出的候选改进逐项实施。所有改动先通过 CI 同命令测试，再在 Phoenix 独立端口做非破坏 A/B，验证后上线。**①+⑤ 已上线（version 类 6/6 correct、0 fabricated）；② 表格重嵌已上线（召回丢失 2→0、hit@1 17→19、零回归）；③ 核验不可行；④ 归 agent 层。** 线上①+②全量检索回归 hit@1/3/5/10=19/25/27/30。

## 一句话结论

- **① 版本替代（w33/w32）已端到端修复**：自适应降权 + 展开门控 + 引用层「已废止」标注三件套，version_superseded 类 LLM **correct 5/6→6/6、fabricated 2→0**；全量 LLM correct **30→31**、fabricated 2→1（剩余 1 为 w21 作答噪声，已证上下文不变）；检索 `old_above_new ['w33']→[]`、evidence_hit@1 17→18，**其余类别零回归**。
- **⑤ judge 解析重试**已实现（w20 那类截断噪声可自愈）。
- **③ 2017 研究生学籍补 item3：经严格核验不可行**（详见下，是对原报告假设的更正）。
- **④ w35 时长属性误接受：归 agent 层**，检索层不强改（避免 no-answer 回归）。
- **② 表格 embed_text 增强**：ingest 逻辑 + 数据脚本已就绪（107 块）；**重嵌 A/B 已验证**——召回丢失 2→0、hit@1 17→19、零回归（hit@5 瓶颈移到排序）。上线需改 live embed_text + Qdrant 107 向量，待确认。

## 测试与验证口径

- 本地：`python -m unittest discover -s tests -t .` → **136 通过（5 跳过）**，`ruff check .` 干净（CI 同命令）。新增测试覆盖自适应降权、展开门控、judge 重试、表格 caption、引用标注。
- A/B：Phoenix `~/cuc-kb-ab-opt`（独立代码副本，`data` 软链线上只读），端口 8002，`KB_SUPERSEDED_ADAPTIVE=1`。harness `eval/evidence_check.py`，LLM 用 qwen-plus（key 复用 RERANK_API_KEY）。

## ① 大分差替代对：自适应降权 + 展开门控 + 废止标注（w33/w32）

**根因（三层，缺一不可）**：
1. w33 旧版 `d2e3bc13731a0-0020`（第二十条「每年4-5月资格复核」）rerank=0.9801，新版最佳 0.8259，**分差 0.154 ≫ 固定降权 0.05**，翻不动 → 旧版压 top1。
2. 即便降权把旧版块压到新版之下，**相邻块展开会把旧版相邻的过时条款重新并进 top5 文本**（展开只增文本不改排序）。
3. 即便旧版块退出 top5，只要旧版仍在 top5 且**无权威标注**，LLM 仍会把已废止内容当现行规定（w32 检索全对仍臆造「2025-2 也有相同告知程序」即此）。

**改动**：
- `kb/common.py`：新增 `KB_SUPERSEDED_ADAPTIVE`（默认 0）、`KB_SUPERSEDED_MARGIN`（默认 0.01）。
- `kb/search_core.py`：
  - `_superseded_relations`（关系检测，新版同在候选才生效）+ `_superseded_penalties`：自适应时降权幅度 = `max(base, 旧最佳分 − 新最佳分 + margin)`，**保证新版压旧版**（仅保证成对关系，不强求全局 top1，无关高分文档不受影响）；rerank 与 RRF 两路一致。
  - **展开门控**：`SUPERSEDED_ADAPTIVE` 开启时，被替代旧版的结果不做相邻展开（避免把过时条款重新并入）；时间性查询（新版不在候选）不受影响，仍可检索+展开旧版。
  - `_citation` 增加 `superseded_by`（附加元数据，非破坏）。
- `kb/mcp_server.py`：`tool_search` 引用透传 `superseded_by`；INSTRUCTIONS 增加「superseded_by 非空=已废止，现行制度优先新版，仅历史询问才引用旧版并注明」。stdio/HTTP 共用 `tool_search`，两端同步。
- `eval/evidence_check.py`：LLM 上下文对已废止片段加「仅作历史参考，现行以新版为准」标注（与 MCP 指令一致，模拟生产 agent 所见）。

**A/B 实测（version_superseded 6 题）**：

| 口径 | prod | 仅自适应降权 | 自适应+展开门控+废止标注 |
|---|---:|---:|---:|
| LLM correct | 5/6 | 5/6 | **6/6** |
| LLM fabricated | 2（w32/w33） | 2 | **0** |
| old_above_new | [w33] | [] | [] |

- w33：从「答固定4-5月、判编造」→「不固定4-5月、每学年复核、动态调整、2019已废止」，correct+complete。
- w32：从「臆造2025-2也有告知程序」→ 正确引用 2026-219、不再牵连已废止 2025-2，correct+complete。
- **全量 v2 检索回归（40 题，ab1_full / ab3_full）**：evidence_hit@1/3/5/10 = 18/25/27/30（prod 17/25/27/30，@1 +1），item_coverage@5、supported_answer@5、各类别 hit@5、丢失阶段、误拒[]、误接受[w35] **逐项与 prod 一致**，零回归。
- **全量 v2 LLM 端到端（34 可答，ab3_full_llm）**：correct **30→31**、complete 29→31、fabricated **2→1**、refused 1→2。
  - w32、w33 两道版本编造**稳定修复**（fab→correct）。
  - w21 由 correct→fab 属**作答噪声而非回归**：已逐块核验 w21 在 8000/8002 的 top5 **完全一致**（`d2b82812f8394-0007/-0034、d2544ce7a8288-0016/-0017、d35526e874c2e-0019`），上下文不含任何已废止文档、无标注差异；同一上下文 qwen-plus 两次作答不同（一次正确读表、一次把「≥4+≥16」误加），是纯数字表格难题的模型非确定性。剩余 1 个 fabricated 即 w21（prod 的 {w32,w33} → ab 的 {w21}）。

> 关键教训：**版本冲突的端到端正确不能只靠排序**。降权解决「谁排前」，展开门控解决「过时文本回流」，废止标注解决「LLM 把旧版当现行」——三者叠加才修好 w33/w32。这与原报告 finding #6 的判断一致。

## ⑤ harness judge 解析重试（w20 噪声）

- 根因：w20 judge 输出在 512 token 处被截断（reason 过长），`parse_verdict` 判 not JSON → 记 incorrect。
- 改动：`parse_verdict` 标记 `_parse_failed`；新增 `judge_with_retry`（最多 3 次，逐次提高 max_tokens 512→1024→1536 并变换提示语避免缓存回放截断结果）。仅作用于可选 LLM 判定，不改确定性指标。+4 单测。

## ③ 2017 研究生学籍补 item3：不可行（更正原报告假设）

严格核验后**不新增任何关系**：
- 现行 `中传研字〔2024〕159号 研究生学籍管理规定`（d1545d0642df9 / dc6abc3c87568）**全文无「废止」条款**，不满足 `apply_superseded` 的「新版含废止+旧文号」核验。
- w01 的 top1 实为 **`中传研字〔2017〕238号 国际研究生学籍管理规定`**（deb81576e6dcf）——与普通研究生学籍是**不同制度（适用人群不同）**，且语料中无更新的国际版，不能标为被 2024-159 替代。
- 全库扫描（`_opt_find_superseded.py`，镜像 verify 逻辑）得 16 个候选，**全部为跨制度打包废止/共享文号/重复件/汇编引用**（如 2008〕88号 同时匹配 3 篇、治安→消防、第N号教育部令），均属 `apply_superseded` 明确排除类。
- **真正缺口是「适用人群」元数据未回填**（国际 vs 普通），属独立任务，非 item3 替代关系能解决。

## ④ w35 时长属性误接受：归 agent 层（检索层不改）

- 根因：query「补办新卡需要几个工作日」检出 time 属性，guard 把「**余额72小时后**到账」（补办后余额转移延迟）误当作「制卡时长」的答案 span（实体「补办」邻近即命中），属语义混淆；且 Phoenix `KB_ANSWERABILITY_OVERRIDE=0`，guard 仅报告不拒答，误接受实由 rerank 0.949≥0.75 决定。
- 区分「余额到账时长」与「制卡时长」需语义理解，**归 agent 层**；全局重启 guard 有 no-answer 回归风险（11-01 已因此关闭）。与「不在检索层打补丁、agent 层改写」的既定原则一致，故检索层不改，列为 agent 层后续。

## ② 表格块 embed_text 增强（w10/w20/w21）

- 根因：纯数字表格块的 `embed_text` 缺**表题/学科名**——caption（表1.1…（文学和艺术学）/表1.2…（工学））在表格**前一个文本块**里，表格本身无「文学/艺术/工学」词，BM25 无命中、dense 信号弱。
- 改动：
  - `kb/ingest.py`：`TABLE_CAPTION_RE` + `table_caption()`（取前文最后一个表题，截到首个「）」去污染）；表格块 emit 时把 caption 注入 **embed_text（不动 text，证据/引用保持逐字）**。+5 单测。
  - `scripts/enrich_table_embed.py`：对存量数据 dry-run/apply，**107 个 active 表格块**可增强，apply 产出 `_table_reembed.jsonl` 供既有 batch_embed/load_vectors 重嵌。
- 离线信号（`_opt_table_experiment.py`，真实 BM25）：caption 使 w10 分数 15→22.8、w21 进 rank41，但**单靠 BM25 仍不足以进 top40**，需 dense 重嵌验证。
- **重嵌 A/B 已做（非破坏，临时 collection `cuc_kb_v4_tablecap` + 8003 临时进程）**：107 块 batch 重嵌（39,739 tokens≈¥0.02，0 失败），克隆 cuc_kb_v4 覆盖 107 向量。全量 v2 回归：
  - **召回丢失 recall 2→0**（w10/w21 的 ≥16 表块由「双路均无」→ dense 召回，丢失阶段转 fusion_pool）。
  - evidence_hit@1 **17→19**（+2），hit@3/5/10、item_coverage@5、supported_answer@5 不变；误拒[]、误接受[w35] 不变，**零回归**。
  - hit@5 未升：召回缺口已补，瓶颈移到 fusion/排序（后续 ranking 工作才能转化为 hit@5）。
  - 结论：② 是安全的召回基础改进（+hit@1、零回归）。**已上线 live**（107 块 embed_text + Qdrant 107 向量，原向量存 `data/tablecap_orig_vectors.npz` 备回滚）；线上①+②全量回归与 A/B 逐项一致（hit@1=19、recall 丢失=0）。产物 `results_evidence_ab_tablecap{,_full}.json`、`results_evidence_prod_tablecap_full.json`、`scripts/{clone_collection_ab,apply_tablecap_vectors}.py`。

## 上线状态与回滚

**①（含展开门控+废止标注）+ ⑤ 已上线生产并验证**：
- Phoenix `.env` 追加 `KB_SUPERSEDED_ADAPTIVE=1`（`KB_SUPERSEDED_PENALTY=0.05` 保持）；已部署 `kb/{common,search_core,mcp_server}.py`、`eval/evidence_check.py`（md5 与本地一致）；重启 cuc-kb / cuc-kb-mcp。
- 线上 version 类回归 **6/6 correct、6/6 complete、0 fabricated、old_above_new=[]**（`results_evidence_prod_supadapt_version.json`）；w33 冒烟新版 top1、旧版带 `superseded_by` 标注且不展开。
- 备份 `kb/{common,search_core,mcp_server}.py.bak-20261114-supadapt`、`eval/evidence_check.py.bak-20261114-supadapt`、`.env.bak-20261114-supadapt`。
- **回滚**：`.env` 置 `KB_SUPERSEDED_ADAPTIVE=0` 重启即恢复固定降权+原展开（`superseded_by` 为附加字段，无害）；完全回滚另 cp 回 `.bak-20261114-supadapt`。

**② 已上线并验证**：live `data/chunks.jsonl` 注入 107 块 caption（备份 `data/chunks.jsonl.bak-20261114-tablecap`、`.bak-tablecap`）+ Qdrant cuc_kb_v4 upsert 107 向量（原向量存 `data/tablecap_orig_vectors.npz`）+ 重启 cuc-kb 重建 BM25；新 `kb/ingest.py`（未来 ingest 自动注入 caption）一并部署。线上①+②全量回归 hit@1=19、recall 丢失=0、零回归（`results_evidence_prod_tablecap_full.json`）。**回滚**：`cp data/chunks.jsonl.bak-20261114-tablecap data/chunks.jsonl` + 用 `scripts/apply_tablecap_vectors.py --vectors data/tablecap_orig_vectors.npz --apply` 还原向量 + 重启。

**③** 不做（核验不可行）；**④** 转 agent 层。

## 产物

- 代码：`kb/{common,search_core,ingest,mcp_server}.py`、`eval/evidence_check.py`、`scripts/enrich_table_embed.py`。
- 测试：`tests/test_search_core.py`（+SupersededExpansionTest 等）、`tests/test_evidence_check.py`（judge 重试/片段标注）、`tests/test_ingest.py`（新）。
- A/B 结果：`results_evidence_ab1_full.json`（检索回归）、`results_evidence_ab3_version_llm.json`（版本类端到端）、`results_evidence_ab3_full_llm.json`（全量端到端）、`results_evidence_ab_tablecap{,_full}.json`（②重嵌）。
- 线上验证：`results_evidence_prod_supadapt_version.json`（version 6/6）、`results_evidence_prod_supadapt_full.json`（全量检索，与 A/B 逐项一致）。
- 工作文件（不入库）：`eval/heldout/_opt_*.py`、`_opt_*.json`、`_opt_peek_*.txt`。