# HANDOFF — 检索质量优化（2026-11-01）

## 一句话现状

线上 reranker 已从 CF `bge-reranker-base` 切换为**百炼 `qwen3-rerank`**，每条查询都 rerank，拒答只看 rerank 阈值。留出集 Hit@1 从 73 提到 82（87 题），MRR 从 0.885 提到 0.965，无答案从 7/10 提到 10/10。代价是 P50 延迟从约 0.7 s 涨到约 1.5 s。向量模型保持 bge-small-zh，Qdrant 不需要重建。

## 线上状态（Phoenix `~/cuc-kb`）

> 2026-11-02：仓库已重构为 `kb/` 包（`refactor/kb-package` 分支），systemd 单元移到 `deploy/` 并改为 `uvicorn kb.app:app` / `python -m kb.xxx`。在重新部署之前，Phoenix 上仍是平铺布局，下面的文件名和回滚命令都按平铺布局。

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
