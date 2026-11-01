# 向量模型离线 A/B — ab1（2026-11-01）

## 方法

- **数据**：线上 `data/chunks.jsonl`（2026-11-01 拉取，10358 块，md5 `b301960b…`）。
- **数据集**
  - `heldout`：新建留出集 `eval/heldout/dataset_heldout_v1.json`。87 道有答案题，对应 87 份 v1–v5 从未引用过的文档；问法口语化改写，答案关键词逐条在原文核验；另有 10 道无答案题，探测词在语料中零命中。**未参与任何调参。**
  - `v5`：`dataset_v5_personas.json`，curated 101 + short 16，另有 no_answer 10。P0/P1 调参都在这个集上做过。
- **管线**：`eval/ab/ab_embed.py` 继承 `search_core.KB`，只替换查询编码和 dense 召回（numpy 暴力检索，等价于 Qdrant 余弦检索）。BM25、RRF、加分、rerank、拒答全部复用线上代码。
- **Rerank**：本地 GPU 跑 `BAAI/bge-reranker-base`（sigmoid）。用 `check_rerank.py` 的样例比对，与线上 CF `@cf/baai/bge-reranker-base` 的分数在 5～6 位有效数字上一致；同一（查询, 文档）对的分数跨模型缓存复用。
- **复现度**：bge-small + prod 模式在 heldout 上的全部指标与线上 API 实测逐项相同（Hit@1 73、Hit@3 80、Hit@5 82、MRR 0.8851、触发 rerank 78、无答案 7/10）。
- **三种 rerank 模式**：`off` 不 rerank；`prod` 按线上触发规则；`always` 每条都 rerank。
- 全部为无指定助手（`assistant=None`），top_k=5。

## 结果

### heldout（87 + 10，未参与调参）

| 模型 | 模式 | Hit@1 | Hit@3 | Hit@5 | MRR | 答案@5 | 无答案 | dense 文档@10 | dense 答案块@10 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| bge-small-zh（线上） | off | 67 | 84 | 86 | 0.861 | 82 | 2/10 | 84 | 79 |
| | **prod（线上现状）** | **73** | **80** | **82** | **0.885** | **80** | **7/10** | | |
| | always | 74 | 81 | 83 | 0.897 | 82 | 7/10 | | |
| bge-m3 | off | 70 | 85 | 86 | 0.888 | 81 | 3/10 | 83 | 82 |
| | prod | 73 | 84 | 84 | 0.899 | 82 | 6/10 | | |
| | always | 73 | 81 | 83 | 0.889 | 82 | 6/10 | | |
| Qwen3-Embedding-0.6B | off | 73 | 85 | 86 | 0.903 | 82 | 3/10 | 84 | 81 |
| | prod | **75** | 83 | 83 | **0.906** | 82 | 7/10 | | |
| | always | 73 | 81 | 83 | 0.889 | 82 | 7/10 | | |
| bge-large-zh-v1.5 | off | 74 | 83 | 86 | 0.909 | 82 | 3/10 | 82 | 78 |
| | prod | 73 | 81 | 84 | 0.891 | 83 | 6/10 | | |
| | always | 73 | 81 | 84 | 0.893 | 83 | 6/10 | | |

所有模型的 dense 文档@40 都是 86～87/87，也就是说召回阶段基本没有漏掉正确文档。

### v5（117 + 10，调参集）

| 模型 | 模式 | Hit@1 | Hit@3 | Hit@5 | MRR | 无答案 |
|---|---|---:|---:|---:|---:|---:|
| bge-small-zh | prod | 99 | 109 | 115 | 0.896 | 10/10 |
| | always | 90 | 105 | 110 | 0.839 | 10/10 |
| bge-m3 | prod | 100 | 109 | 114 | 0.900 | 8/10 |
| Qwen3-0.6B | prod | 98 | 109 | 114 | 0.890 | 9/10 |
| bge-large-zh | prod | 86 | 104 | 111 | 0.821 | 9/10 |

### 配对检验（对比 bge-small prod，Hit@1 翻转数 +赢/−输，二项检验）

- heldout：m3 prod +3/−3（p=1.0）；Qwen3 prod +3/−1（p=0.62）；large prod +2/−2（p=1.0）。**均不显著。**
- v5：只有 bge-large prod 显著变差，+2/−15（p<0.01）。原因是它的余弦分数整体偏低，几乎每条都触发 rerank，等于走了 always 模式。

### 查询编码耗时（本机 RTX 5060，单条）

bge-small（CPU ONNX）2.4 ms，bge-m3 14 ms，bge-large 16 ms，Qwen3-0.6B 47 ms。线上 Phoenix 是没有 GPU 的 ARM 机器，那里的 CPU 耗时没有测。

## 结论

1. **现在换向量模型没有可测出的收益。** 在 87 道留出题上，最好的 Qwen3-0.6B（prod）Hit@1 只多 2 道、MRR +0.021，配对检验 p=0.62，在噪声范围内。线上部署的代价却很明确：模型体积大约 25 倍，ARM CPU 上的查询延迟还没测，Qdrant 也要整体重建。
2. **召回不是瓶颈。** bge-small 的 dense@40 已经是 86/87，失败主要出在排序阶段：
   - `off` → `prod`：Hit@1 从 67 升到 73，但 Hit@5 从 86 降到 82。bge-reranker-base 把 4 道原本在 Top5 的正确文档挤出去了。
   - 换成 `always` 后，四个模型在 heldout 上都收敛到 Hit@1 73～74、MRR 约 0.89。**rerank 把向量模型之间的差异抹平了。**
3. **rerank 触发规则和阈值都是绑定 bge-small 标定的。** `always` 模式在 heldout 上和 prod 持平甚至略好，在 v5 上 Hit@1 却从 99 掉到 90，说明条件触发的收益主要来自对 v5 的拟合。换模型时，0.58/0.75 两个触发阈值和 0.45 的 dense 拒答下限都会失效（bge-large 就是例子）。
4. **拒答是真实短板。** 线上在 heldout 上无答案只有 7/10，v5 上是 10/10。不 rerank 时只有 2～3/10，说明拒答完全依赖 rerank 分数。

## 建议的下一步

- **换 reranker（优先级最高）**：用同一套脚本 A/B `bge-reranker-v2-m3` 和 `Qwen3-Reranker-0.6B`（从 ModelScope 下载，GPU 跑），并且在 always 模式下比较，去掉触发规则这个变量。如果新 reranker 在 heldout 上同时提高 Hit@5 和拒答，再考虑删掉 `INTENT_RERANK_HINTS`、`INTENT_PHRASES` 等人工规则。
- 向量模型维持 bge-small。等 reranker 定下来后，再按"新 reranker + always"重测一次 Qwen3-0.6B，看嵌入层是否还有剩余收益。
- 拒答阈值以后要在 heldout 这类独立集上标定，不能只看 v5。

## 文件

- `eval/heldout/build_heldout.py`：生成并核验留出集（无参数直接运行）
- `eval/heldout/dataset_heldout_v1.json`：留出集
- `eval/ab/ab_embed.py`：`embed` / `eval` 两个子命令
- `eval/ab/results_ab1.json`：每条查询的明细（含 dense 召回名次）
- `eval/ab/prod_heldout_rows.json`：线上 API 在 heldout 上的逐条结果
