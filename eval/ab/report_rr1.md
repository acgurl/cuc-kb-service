# Reranker 离线 A/B — rr1（2026-11-01）

方法与 [report_ab1.md](report_ab1.md) 相同：同一份线上 chunks，同样的留出集 heldout（87 + 10）和 v5（117 + 10），管线复用 `search_core.KB`，无指定助手，top_k=5。本轮只替换 reranker，嵌入模型取 bge-small-zh（线上）和 Qwen3-Embedding-0.6B（ab1 中最好）。

| reranker | 来源 | 运行方式 | 打分 |
|---|---|---|---|
| bge-reranker-base（线上） | HF | GPU CrossEncoder | sigmoid，与线上 CF 输出一致 |
| bge-reranker-v2-m3 | ModelScope | GPU CrossEncoder fp16，max_len 1024 | sigmoid |
| Qwen3-Reranker-0.6B | ModelScope | GPU 因果语言模型 fp16，按官方模板取 P(yes) | 0–1 |

`prod` = 线上条件触发规则；`always` = 每条都 rerank。拒答阈值全部沿用线上的 0.75，另见后文的标定分析。

## 结果

### heldout（未参与调参）

| 嵌入 + reranker | 模式 | Hit@1 | Hit@3 | Hit@5 | MRR | 答案@5 | 回答数 | 无答案 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| bge-small + bge-base（**线上**） | prod | 73 | 80 | 82 | 0.885 | 80 | 87 | 7/10 |
| | always | 74 | 81 | 83 | 0.897 | 82 | 87 | 7/10 |
| bge-small + **v2-m3** | prod | 76 | 83 | 85 | 0.919 | 84 | 84 | 8/10 |
| | always | 77 | 84 | 86 | 0.931 | **86** | 84 | 8/10 |
| bge-small + **Qwen3-RR** | prod | **78** | 86 | 86 | **0.937** | 83 | 87 | 6/10 |
| | always | 77 | **87** | **87** | 0.933 | 85 | 87 | 6/10 |
| Qwen3-emb + bge-base | prod | 75 | 83 | 83 | 0.906 | 82 | 86 | 7/10 |
| Qwen3-emb + v2-m3 | always | 76 | 84 | 86 | 0.925 | 86 | 84 | 9/10 |
| Qwen3-emb + Qwen3-RR | always | 77 | 86 | 87 | 0.931 | 84 | 87 | 7/10 |

### v5（调参集）

| 嵌入 + reranker | 模式 | Hit@1 | Hit@5 | MRR | 无答案 |
|---|---|---:|---:|---:|---:|
| bge-small + bge-base（线上） | prod | 99 | 115 | 0.896 | 10/10 |
| | always | 90 | 110 | 0.839 | 10/10 |
| bge-small + v2-m3 | prod | **106** | **117** | **0.941** | 9/10 |
| | always | 103 | 117 | 0.926 | 9/10 |
| bge-small + Qwen3-RR | prod | 105 | 116 | 0.931 | 7/10 |
| | always | 98 | 115 | 0.889 | 7/10 |

### 配对检验（两个集合并 204 题，对比线上 bge-small + bge-base prod；+赢/−输，二项检验）

| 组合 | Hit@1 | Hit@5 | 答案@5 |
|---|---|---|---|
| bge-small + v2-m3 prod | +16/−6，p=0.052 | +5/−0，p=0.062 | +5/−0，p=0.062 |
| bge-small + v2-m3 always | +18/−10，p=0.19 | **+6/−0，p=0.031** | **+7/−0，p=0.016** |
| bge-small + Qwen3-RR prod | **+17/−6，p=0.035** | +6/−1，p=0.13 | +5/−2，p=0.45 |
| bge-small + Qwen3-RR always | +18/−15，p=0.73 | +7/−2，p=0.18 | +7/−2，p=0.18 |
| Qwen3-emb + v2-m3 always | +18/−11，p=0.27 | +6/−0，p=0.031 | +7/−0，p=0.016 |

### 拒答阈值：在 v5 上标定，到 heldout 上检验（always 模式，Top5 的最高 rerank 分）

| 组合 | v5 标定阈值 | heldout 保留有答案题 | heldout 正确拒答 | heldout AUC | 固定 0.75 时 heldout 保留/拒答 |
|---|---:|---:|---:|---:|---:|
| bge-small + bge-base | 0.891 | 75/87 | 9/10 | 0.980 | 84/87，8/10 |
| bge-small + v2-m3 | 0.759 | 79/87 | 10/10 | 1.000 | 79/87，10/10 |
| bge-small + Qwen3-RR | 0.987 | **87/87** | **10/10** | 1.000 | 87/87，8/10 |

这里是纯阈值分析。管线输出的无答案数还会被属性可答性规则（`_judge_answer` 里找到答案片段就改判为可答）修改，所以和上面主表里的数字不完全相同。

### 线上部署：Phoenix（4 核 ARM，无 GPU）上 fastembed 的 CPU 延迟，24 个真实候选块

| 模型 | 每次 rerank 耗时 |
|---|---:|
| bge-reranker-base | 17～19 s |
| bge-reranker-v2-m3-int8 | 约 21 s |
| bge-reranker-v2-m3 | 约 60 s |

Cloudflare Workers AI 上只有 `@cf/baai/bge-reranker-base` 这一个 reranker（通过账户模型搜索 API 确认）。

## 结论

1. **reranker 才是该换的那一层。** 只换 reranker、嵌入保持 bge-small，两个新模型都显著优于线上：v2-m3（always）在 Hit@5 和答案@5 上只赢不输（p<0.05）；Qwen3-RR（prod）的 Hit@1 赢 17 题、输 6 题（p=0.035）。
2. **嵌入模型继续不用换。** 在 reranker 相同的情况下，Qwen3-emb 并不比 bge-small 更好（v2-m3 always：MRR 0.925 vs 0.931）。
3. **v2-m3 更稳**：两个集都提升，always 模式在 v5 上只掉 3 题（bge-base 掉 9 题）。强 reranker 下，"条件触发 rerank"这套规则可以去掉，统一走 always。
4. **Qwen3-RR 排序最强，但 always 模式在 v5 上会掉回 98。** 它在 0.75 阈值下拒答偏弱，原因是分数整体贴近 1；标定到 0.987 后两个集都分得开，但这个阈值离 1 太近，而且只有 20 道无答案样本支撑，目前还不能信。
5. **Phoenix 本地 CPU 跑不动**，线上只能走云端 rerank API。
6. **附带发现一个线上 bug**：fastembed 的 `TextCrossEncoder` 输出的是原始 logit（实测 3.35 / −10.19），CF 输出的是 sigmoid 概率（0.966 / 0.00004）。`search_core._rerank_scores` 在 CF 失败、回退到本地模型时没有做 sigmoid，导致 `KB_RERANK_MIN_SCORE`、`GENERIC_RERANK_PENALTY`、`PHRASE_BONUS` 的尺度全部错位，返回的 confidence 也可能大于 1。

## 建议

- **首选：bge-reranker-v2-m3，always 模式，阈值暂用 0.75**（heldout 纯阈值分析下 10/10 拒答，79/87 保留；上线前再用更大的无答案集复核）。云端选 SiliconFlow 或百炼，接口与现有 `CloudReranker` 兼容。上线前必须用云端 API 重跑本脚本，确认分数与本地一致。
- **候选：Qwen3-Reranker-0.6B**。先把无答案集扩充到 50 道以上，再判断 0.987 这个阈值是否站得住；也可以改用 log-odds 作为阈值，避免分数在 1 附近饱和。
- **代码改动**（在确定 reranker 之后做）：本地回退路径加 sigmoid；增加 `KB_RERANK_ALWAYS` 开关；拒答阈值改成按 reranker 配置；在 heldout 上逐项消融 `INTENT_PHRASES`、`DEFINITION_HINTS` 等人工加分，确认无效的就删掉。

## 文件

- `eval/ab/ab_embed.py`：`eval --rerankers bge-base,bge-v2-m3,qwen3-rr-0.6b`
- `eval/ab/calibrate_no_answer.py`：阈值标定
- `eval/ab/results_rr1.json`：逐条明细，含每条的 `best_rerank`
