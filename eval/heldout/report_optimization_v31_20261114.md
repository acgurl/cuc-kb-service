# v3.1 后续优化实施报告（2026-11-14）

按 `report_evidence_v3_1.md` 的后续清单实施四项，全部在 Phoenix 独立端口做非破坏 A/B。**生产服务（8000 端口的代码、`.env`、`data/`）未改动，四项均未上线**，等待确认。

## 结论

| 项 | 做法 | 结果 | 建议 |
|---|---|---|---|
| ① 候选数 | `KB_CANDIDATE_LIMIT` 24→40 | hit@1 +3，hit@5 +2/−1，误拒 4→2，无新增误接受 | 上线 |
| ② 近重复折叠 | 新增 `KB_DEDUP_NEAR` | hit@5 +4/−1（多来源口径 197→200） | 上线 |
| ③ 同名新版关系 | 新增 `newer_version` 字段 + `KB_NEWER_VERSION`，21 对关系 | vc01 新版升到第 1，其余零变化 | 上线 |
| ④ agent 层 | 新增 agent 端到端评测；MCP 指令加「先检索再下结论」 | agent correct 184→190，拒答 11→2，未检索就作答 26→11 | 上线指令；多问法带来的混答另行处理 |

①②③ 合并后的组合配置（单次 top5 作答，214 道可答题）：

| 指标 | 线上 | 组合配置 |
|---|---:|---:|
| evidence_hit@1（多来源口径） | 135 | 139 |
| evidence_hit@5（多来源口径） | 197 | 200 |
| evidence_hit@10（多来源口径） | 207 | 208 |
| LLM correct | 203 | 205 |
| LLM complete | 164 | 168 |
| LLM fabricated | 7 | 7 |
| LLM refused | 5 | 1 |
| 误拒 | 4 | 2 |
| 不可答题检索层拒答 | 38 / 54 | 38 / 54 |
| 融合池丢失的证据项 | 10 | 3 |
| 延迟 P50 / P95（空闲、预热后交替各 60 条） | 924 / 1419 ms | 1033 / 1524 ms |

端到端净 +2 correct 是 5 题转对（b11_03、b13_04、b4b_05、b6_10、b16b10）减 3 题转错（b1_01、b16a04、b4a_06）。单次运行、题量 214，这个幅度在噪声范围内；确定的收益是检索侧（融合池丢失 10→3、误拒减半）。

## 口径：新增「多来源」数据集

311 个证据项里有 118 个在别的现行文件中逐字存在（汇编与单行本、同文副本）。严格口径只认标注的那一份文件，折叠重复后命中另一份会被算成漏检，所以新增 `dataset_evidence_v3_1_alt.json`：任一现行文件逐字含全部证据词即算命中（已废止文件和 watch 文件除外）。线上基线在两种口径下的 hit@5 分别是 190（严格）和 197（多来源）。①用严格口径对比，②③和组合配置用多来源口径对比。

## ① 候选池 / 候选数

| 配置 | hit@1 | hit@5 | hit@10 | 误拒 | 不可答拒答 | 融合池丢失 |
|---|---:|---:|---:|---:|---:|---:|
| 线上（池 40 / 精排 24） | 121 | 190 | 203 | 4 | 38 | 15 |
| 池 40 / 精排 40 | 124 | 191 | 205 | 2 | 38 | 5 |
| 池 80 / 精排 40 | 124 | 191 | 205 | 2 | 38 | 3 |
| 池 80 / 精排 60 | 122 | 191 | 205 | 3 | 40 | 0 |

- 精排 40 条就拿到了全部收益；池子加到 80 逐题结果与池 40 完全相同。
- 精排 60 条反而让 2 道题的 top1 被挤掉、1 道可答题新增误拒，不采用。
- 融合池里救回的证据大多落到「精排排序」环节（5→9），所以 hit@5 只 +1；收益主要在 hit@1 和误拒。
- 误拒减少的原因：b5_11、b13_10 的最佳证据原本没进精排，进了之后最高分过了 0.75。

只需改 `.env`，无代码改动。

## ② top-k 近重复折叠

`kb/search_core.py`：按精排顺序取结果时，若候选块自身 90% 以上的字符 5-gram 已包含在某条已返回结果里，就跳过它，并把它记到那条结果的 `duplicates`（chunk_id、doc_id、doc_title）；MCP 透传该字段，出处不丢。包含度按被丢弃的一方算，所以比已返回块内容更多的长块不会被折叠。top1 永远保留，可回答性判定不受影响。开关 `KB_DEDUP_NEAR`（默认关），阈值 `KB_DEDUP_THRESHOLD`（默认 0.9）。

多来源口径：hit@5 197→200，转对 b11_03、b13_04、b5_01、b6_10，hit@1、误拒、不可答拒答不变。唯一转错 b8_04：被折叠的那份副本原本靠相邻块展开带出了另一条证据，保留的那份文件相邻块措辞不同。

## ③ 同名文件的新版关系

逐年重发的说明、通知、协议没有废止条款，`apply_superseded.py` 覆盖不到。全库排查同名不同年份的现行文件共 19 组，逐组核对后收录 21 对关系（`scripts/apply_newer_version.py`）：贷款情况说明 2021–2025→2026、校运会交通管制通知 2023–2025→2026、研究生学位论文编写规则 2021→2025、住宿协议书 2024→2026、档案法 2018 页→2021 施行版等。排除了科级干部聘任办法（2018 条目是残页，旧版内容独有）、年度收费信息公开（各年内容不同）和缺日期的几组。

行为（`KB_NEWER_VERSION`，默认关）：旧一期带 `newer_version` 字段，新一期同在候选时旧一期让位，且不做相邻块展开，与已废止文件的处理一致；查询里写了旧一期的年份时不降权；引用里带 `newer_version`，MCP 指令说明「现行情况以更新一期为准」。数据补丁只写 `chunks.jsonl` / `docs.json`，不需要同步 Qdrant。

A/B：vc01（贷款额度）2026 版从第 2 升到第 1，其余 213 题逐题不变。vc03（国奖 8000 vs 10000）不受影响，那两份文件标题不同，不属于同名系列。

## ④ agent 层：端到端评测与指令修改

新增 `eval/agent_check.py`：把 `kb/mcp_server.py` 的 INSTRUCTIONS 和四个工具定义原样交给 qwen-plus，让它跑最多 4 轮工具调用再作答，然后判定。这是第一次量化 11-03 上线的并行多问法规则。

| 指标（268 题） | 单次 top5 作答 | agent，现行指令 | agent，新指令 |
|---|---:|---:|---:|
| 可答 correct | 203 | 184 | 190 |
| 可答 complete | 164 | 159 | 164 |
| 可答 fabricated | 7 | 22 | 24 |
| 可答 refused | 5 | 11 | 2 |
| 短查询 correct（37 题） | 35 | 24 | 28 |
| 不可答题拒答（54 题） | 50 | 39 | 36 |
| 不可答题编造 | 1 | 14 | 12 |
| 未检索就作答的题数 | — | 26 | 11 |
| 平均检索次数 / 题 | 1 | 3.55 | 3.16 |

**发现一：现行指令让模型不检索就下结论。** 26 道题一次都没检索，其中 18 道不是个人状态类问题（「校园邮箱账号」「操场跑道多长」「校内大模型有哪些」等）。模型的理由是「不属于校内制度/FAQ 范畴」，然后回答「知识库没有」，有几题还凭记忆给出网址和电话。根因是指令开头写的是「校内制度/FAQ 类问题才调用 kb_search」。

修改：`kb/mcp_server.py` 新增 `SCOPE_RULE`，并入 INSTRUCTIONS 和 kb_search 工具描述。内容是：知识库也收办事指南、手册、简章、通知和设施服务说明；凡可能与本校有关的问题（含短问题）必须先检索；本校的网址、电话、地点、金额、时间只能来自检索原文；只有问候、询问助手自身、无关通用知识和个人状态查询可以不检索。`agent/tools.openai.json` 已同步。效果：未检索 26→11（剩下 8 道是个人状态题），可答题拒答 11→2，correct +6，短查询 correct 24→28。

**发现二：多问法检索让 agent 比单次作答更容易混答。** 一题平均发 3–4 个问法，模型看到 15–20 个片段，本科与研究生、新版与旧版、相近对象的规定混在一起。agent 的 correct 比单次作答低 13–19 题，编造多 15 题左右，新指令没有改善这一点。典型：b2_08 把本科生答辩条件混进研究生答案；b15b03、b15f01 采用了已标废止的旧版内容；b5_02 把国家励志奖学金标准当成校励志奖学金。这一项本轮没有处理，是下一步最值得做的事。

**口径限制**：agent 是 qwen-plus 单次运行，不代表生产里各助手实际用的模型；agent 自身不确定，两组之间 ±3 题以内不宜解读。首轮判定没把引用里的日期、链接给评测员，40 例「编造」里有 18 例是把真实引用信息判成编造，已修正后重判（22 例）。

## 测试

- 本地 CI 同命令：`python -m unittest discover -s tests -t .` 153 通过（5 跳过），`ruff check` 干净。
- 新增单测：`NewerVersionTest`（3）、`NearDuplicateTest`（3）、`tests/test_agent_check.py`（9）、evidence_check 的 `alt_docs` 与旧一期标注（2）。

## 上线步骤（待确认，未执行）

1. 部署 `kb/{common,search_core,mcp_server}.py`（先备份为 `.bak-20261114-v31opt`）。
2. `python -m scripts.apply_newer_version --data data --apply`（自动备份 `data/{chunks.jsonl,docs.json}.bak-newerversion`）。
3. `.env` 追加 `KB_CANDIDATE_LIMIT=40`、`KB_DEDUP_NEAR=1`、`KB_NEWER_VERSION=1`。
4. 重启 cuc-kb、cuc-kb-mcp、cuc-kb-mcp-gateway，跑 v3.1 回归核对。

回滚：三个开关置回默认并重启即可；数据用 `apply_newer_version --reverse`；指令回滚需还原 `kb/mcp_server.py`。

## 产物

- 代码：`kb/common.py`、`kb/search_core.py`、`kb/mcp_server.py`、`agent/tools.openai.json`、`scripts/apply_newer_version.py`、`eval/agent_check.py`、`eval/evidence_check.py`、`eval/heldout/build_evidence_v3_1.py`（`--alt-all`）
- 数据集：`eval/heldout/dataset_evidence_v3_1_alt.json`
- 检索 A/B：`results_evidence_ab31_{L40,P80L40,P80L60}.json`（严格口径）、`results_evidence_ev31alt_prod.json`、`results_evidence_ab31alt_{D,N,DN,L40DN}.json`（多来源口径，L40DN 含 LLM）
- agent：`results_agent_ag31_prod.jsonl`（首轮判定）、`results_agent_ag31_prod_rj.jsonl`（重判）、`results_agent_ag31_scope.jsonl`（新指令），各带 `_summary.json`
- Phoenix：A/B 用的代码与数据副本在 `~/cuc-kb-ab`（data 已打 `newer_version`）；`~/cuc-kb/eval/` 下新增了评测脚本、数据集和结果文件，`eval/evidence_check.py` 被更新（备份 `.bak-20261114-altdocs`）。

## 未做

- 多问法混答（发现二）没有处理。
- cov02 两份教学奖办法哪份现行，仍待裁定。
- 组合配置下没有重跑 agent 评测；agent 的两组结果都基于线上检索配置。
