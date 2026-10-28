# cuc-kb-service

中国传媒大学校内规章制度与常见问题知识库检索服务。把按部门/助手归档的 PDF 制度文档解析、切块并写入 Qdrant，对外提供带出处引用的混合检索 API、Web 检索页、SSO 登录、原文跳转，以及 stdio / HTTP 两种 MCP 接入方式。

## 功能

- **文档入库**：PDF 文本与表格提取、页锚点、文号/类型/部门/助手识别、内容去重、同文号多版本合并、条款级切分；支持导入 QA 问答表。
- **混合检索**：向量召回 + BM25（jieba 分词）+ RRF 融合，条件触发 rerank，再叠加文号、短语、词语覆盖和通用汇编降权等排序修正。
- **拒答判断**：基于 rerank 分数、dense/BM25 下限和价格/时间/数量等属性可答性检查，返回 `no_answer` 与 `confidence`。
- **出处引用**：每条结果返回文档名、文号、条款号、章节路径、页码、原件文件名和可跳页的原文链接。
- **鉴权**：OIDC（Casdoor）浏览器登录 + 会话 Cookie、OIDC Bearer Token、服务 Token 三种方式。
- **原文访问**：源 PDF 存放在私有 OCI 对象存储，按需签发短时 PAR 链接，不直接暴露桶。
- **检索评测**：`eval/` 下有多轮数据集、评测脚本和报告，覆盖文档命中率、短问句、无答案判定、页码引用和助手隔离。

## 架构

```
            ┌─────────────────────────────┐
            │  AIHub 助手 / Agent / 脚本  │
            └──────────┬──────────────────┘
                       │ HTTP / MCP
      ┌────────────────┼────────────────────┐
      │                │                    │
┌─────▼─────┐   ┌──────▼──────┐   ┌─────────▼─────────┐
│ app.py    │   │ mcp_server  │   │ mcp_http_server   │
│ FastAPI   │◄──│ stdio MCP   │   │ Streamable HTTP   │
│ Web UI    │   └─────────────┘   │ :8765/mcp         │
│ SSO 登录  │                     └─────────┬─────────┘
└─────┬─────┘                               │
      │                               ┌─────▼──────────────┐
      │                               │ mcp_gateway_proxy  │
      │                               │ Docker 网桥转发     │
      │                               └────────────────────┘
┌─────▼──────────┐   ┌──────────────┐   ┌──────────────────┐
│ search_core.py │──►│ Qdrant       │   │ OCI Object Store │
│ 混合检索+重排  │   │ dense 向量库 │   │ 私有 PDF 桶      │
└─────┬──────────┘   └──────────────┘   └────────▲─────────┘
      │                                          │ PAR
      │  chunks.jsonl / docs.json                │
      │                                    ┌─────┴──────┐
┌─────▼─────┐                              │ file_access│
│ ingest.py │◄──── PDF 目录 / QA CSV ──────┤ 签名链接   │
└───────────┘                              └────────────┘
```

| 文件 | 作用 |
| --- | --- |
| `app.py` | FastAPI 服务：Web 检索页、检索/文档/原文接口、SSO 登录回调 |
| `search_core.py` | 载入 `data/` 与 Qdrant，执行向量 + BM25 + RRF + rerank 检索，返回引用与拒答判断 |
| `ingest.py` | PDF/QA CSV 解析、切块、去重、生成 `data/` 产物并写入 Qdrant |
| `auth.py` | OIDC id_token 校验（JWKS 缓存）、会话 JWT 签发/读取、服务 Token 校验 |
| `common.py` | 全部环境变量与默认值 |
| `file_access.py` | OCI 私有桶 PAR 签发与缓存 |
| `mcp_server.py` | 纯标准库实现的 stdio MCP 服务，转发到 HTTP API |
| `mcp_http_server.py` | 常驻 Streamable HTTP MCP 端点，含 Token 与 Host 校验 |
| `mcp_gateway_proxy.py` | 监听 Docker 网桥地址并转发到 `127.0.0.1:8765`，避免服务绑定公网 |
| `agent/` | 给通用 Agent 用的 OpenAPI 描述与 OpenAI function-calling 工具定义 |
| `eval/`、`eval_v2/` | 评测脚本、数据集、结果与报告 |
| `p0_migrate.py`、`p1_alias.py` | 线上元数据修正与别名重嵌入等一次性脚本 |

## 目录

```
kb-service/
├── app.py                     # FastAPI 应用
├── search_core.py             # 检索核心
├── ingest.py                  # 入库脚本
├── auth.py  common.py  file_access.py
├── mcp_server.py              # stdio MCP
├── mcp_http_server.py         # HTTP MCP
├── mcp_gateway_proxy.py       # Docker 转发
├── run-mcp.sh                 # stdio MCP 启动包装
├── cuc-kb-mcp.service         # systemd: HTTP MCP
├── cuc-kb-mcp-gateway.service # systemd: Docker 网关转发
├── agent/                     # OpenAPI / tools 定义
├── eval/  eval_v2/            # 评测
├── data/                      # 运行时生成，不入库
└── .env.example
```

## 快速开始

前置条件：

- Python 3.10+
- 可访问的 Qdrant 实例
- 需要原文跳转时：OCI 对象存储桶与实例主体/配置文件凭据
- 需要登录时：OIDC（Casdoor 等）应用与回调地址

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # 填入密钥，.env 不要提交
```

启动 Qdrant（示例）：

```bash
docker run -d --name qdrant -p 127.0.0.1:6333:6333 qdrant/qdrant
```

入库。`--src` 指向知识文档根目录，第一层目录名会作为助手名：

```bash
python ingest.py \
  --src ../AIHub-全部助手-知识文档 \
  --qa ../QA知识导入模板.csv \
  --out data \
  --recreate
```

产物：`data/docs.json`、`data/chunks.jsonl`、`data/md/<doc_id>.md`。只解析不写库时加 `--skip-embed`；调试单批文档可用 `--include 关键词`、`--limit N`。

启动 API 与 Web 页：

```bash
uvicorn app:app --host 127.0.0.1 --port 8000
```

浏览器打开 `http://127.0.0.1:8000/`；脚本调用用 `Authorization: Bearer $KB_API_TOKEN`。

## 配置

全部配置见 [`.env.example`](.env.example)，按用途分组：

| 分组 | 关键变量 | 说明 |
| --- | --- | --- |
| 基础 | `KB_COLLECTION`、`KB_DATA_DIR`、`KB_EMBED_MODEL`、`KB_API_TOKEN` | 集合名（线上用 `cuc_kb_v2`）、数据目录、向量模型、服务 Token |
| 向量库 | `QDRANT_URL`、`QDRANT_API_KEY` | Qdrant 地址与密钥 |
| 缓存 | `FASTEMBED_CACHE`、`HF_HOME` | 模型缓存目录 |
| Rerank | `KB_RERANK_PROVIDER`、`RERANK_API_*`、`KB_RERANK_*` | 云端/本地重排、触发阈值、最低分、本地回退 |
| 排序 | `KB_GENERIC_*`、`KB_CANDIDATE_*`、`KB_COVERAGE_*`、`KB_PHRASE_*` | 通用汇编降权、候选池、覆盖与短语加分 |
| 可答性 | `KB_ANSWERABILITY_*`、`KB_ANSWER_SPAN_WINDOW` | 拒答阈值与答案片段窗口 |
| OCI | `KB_OCI_*`、`KB_PAR_TTL` | 私有 PDF 桶、PAR 有效期 |
| 登录 | `KB_OIDC_*`、`KB_SESSION_*`、`KB_PUBLIC_BASE_URL` | OIDC 应用、会话密钥与有效期、对外基址 |
| MCP | `KB_MCP_HOST`、`KB_MCP_PORT`、`KB_MCP_TOKEN`、`KB_MCP_ALLOWED_HOSTS` | HTTP MCP 监听、Token 与 Host 白名单 |

`KB_RERANK_PROVIDER=openai`（兼容 `cloud` / `openai-compatible`）时必须配置 `RERANK_API_BASE` 与 `RERANK_API_MODEL`；云端失败会回退到本地 `bge-reranker-base`（`KB_RERANK_LOCAL_FALLBACK=1`）。

## HTTP API

除 `/healthz` 外均需鉴权：

```http
Authorization: Bearer <KB_API_TOKEN>
```

| 方法与路径 | 说明 |
| --- | --- |
| `GET /healthz` | 健康检查，返回已加载状态与知识块数 |
| `GET /v1/meta` | 文档数、知识块数、向量模型、可用助手与文档类型 |
| `POST /v1/search` | 混合检索 |
| `GET /v1/documents` | 文档列表（含页数、助手、原文链接） |
| `GET /v1/documents/{doc_id}/chunks` | 读取指定文档的知识块 |
| `GET /v1/files/{doc_id}` | 鉴权后 302 跳转到 OCI PAR 原文链接 |
| `GET /auth/login`、`/auth/callback`、`/auth/me`、`/auth/logout` | 浏览器 SSO 登录会话 |
| `GET /` | 内置 Web 检索页 |

检索请求：

```json
{
  "query": "学生公寓的床尺寸是多少？",
  "assistant": "中传小勤",
  "doc_type": ["制度"],
  "top_k": 5
}
```

`assistant` 与 `doc_type` 可省略；共享文档始终可见。响应主要字段：

```json
{
  "query": "...",
  "results": [
    {
      "text": "……",
      "score": 0.032,
      "dense_score": 0.71,
      "bm25_score": 8.4,
      "rerank_score": 0.93,
      "citation": {
        "doc_title": "……",
        "doc_no": "中传财字〔2024〕229号",
        "clause_no": "第二十三条",
        "page_start": 5,
        "page_end": 5,
        "section_path": ["……"],
        "source_file": "财务处小助手/xxx.pdf",
        "page_count": 12,
        "file_url": "https://.../v1/files/<doc_id>#page=5"
      },
      "is_qa": false,
      "is_generic": false
    }
  ],
  "no_answer": false,
  "confidence": 0.93,
  "answerability": null,
  "took_ms": 42
}
```

`no_answer=true` 时调用方不应基于知识库作答；可改写问题重试一次。`answerability` 仅在问题涉及价格、时间、数量、渠道、内容或责任主体时出现。

## MCP 接入

四个工具：`kb_search`、`kb_meta`、`kb_documents`、`kb_document_chunks`，均为只读，工具描述内置了检索时机、拒答处理和「参考文件」清单要求。

### stdio

```json
{
  "mcpServers": {
    "cuc-kb": {
      "command": "python",
      "args": ["/path/to/kb-service/mcp_server.py"],
      "env": {
        "KB_API_URL": "http://127.0.0.1:8000",
        "KB_API_TOKEN": "<token>"
      }
    }
  }
}
```

也可以用 `KB_API_TOKEN_FILE` 或 `KB_API_ENV_FILE` 从文件读取 Token，避免写在配置里。支持协议版本 `2024-11-05`、`2025-03-26`、`2025-06-18`。

### Streamable HTTP

```bash
python mcp_http_server.py      # 默认 127.0.0.1:8765/mcp
```

鉴权三选一：`Authorization: Bearer <KB_MCP_TOKEN>`、`X-MCP-Token: <KB_MCP_TOKEN>`、URL 参数 `?token=<KB_MCP_TOKEN>`。服务默认只绑定回环地址，并校验 Host 白名单；容器访问通过 `mcp_gateway_proxy.py` 从 Docker 网桥地址转发，不要把 8765 直接暴露到公网。HTTP MCP 依赖额外安装 `mcp` 包（线上使用独立虚拟环境 `.venv-mcp`）。

## 检索流程

1. 用 `bge-small-zh-v1.5` 编码查询（带中文检索指令），Qdrant 取 dense Top 40。
2. jieba 搜索模式分词，BM25 取 Top 40；两路按 RRF（k=60）融合。
3. 排序修正：查询含文号时命中同文号加分；QA 块小幅加分；查询词在正文中的覆盖度加分（通用汇编不参与）。
4. 单文档最多保留 `KB_MAX_CHUNKS_PER_DOC` 个块，候选池 40、进重排 24 条。
5. 满足以下任一条件才 rerank：命中条件/流程/金额等意图词、dense 最高分偏低、长问句且 dense 偏低。云端 rerank 失败回退本地模型。
6. rerank 后：通用汇编在具体制度分数接近时降权；标题/正文命中「申请条件」「报销比例」等短语加分；定义与时限类问题在正文出现「是指」「最长不超过」时加分。
7. 生成引用与拒答判断：rerank 最高分低于 `KB_RERANK_MIN_SCORE` 判为无答案；无 rerank 时用 dense/BM25 下限兜底；属性类问题再检查 Top 5 是否真的包含对应答案片段。
8. 仅 `status=active` 的知识块参与检索，已标记 `superseded` 的旧版本保留但默认不召回。

## 评测

```bash
# 文档检索 / 短问句 / 无答案 / 页码引用（默认 dev split）
python eval/eval_v3.py --api http://127.0.0.1:8000 --token "$KB_API_TOKEN" \
  --data eval/dataset_v5_personas.json --split all --tag v5

# 无指定助手对照
python eval/eval_noassistant.py --api http://127.0.0.1:8000 \
  --token "$KB_API_TOKEN" --data eval/dataset_v5_personas.json --tag v5

# 只校验数据集与当前索引是否一致
python eval/validate_dataset.py
```

最新一轮（`eval/report_v5_4_p1.md`，2026-10-28，101 条文档检索 + 16 条短问句 + 10 条无答案 + 10 条页码引用）：

| 指标 | 无指定助手 | 指定正确助手 |
| --- | ---: | ---: |
| 文档命中@1 | 84/101 | 87/101 |
| 文档命中@3 | 94/101 | — |
| 文档命中@5 | 99/101 | 100/101 |
| MRR | 0.8904 | 0.9115 |
| 短问句命中@5 | 16/16 | 16/16 |
| 无答案准确率 | 10/10 | 10/10 |
| 页码引用正确 | 10/10 | 10/10 |
| P50 / P95 | 687 / 860 ms | 670 / 903 ms |

注意：

- 评测只覆盖检索、拒答和引用定位，不评测最终答案生成的事实一致性。
- 跨部门/人物数据集为人工构造，规模有限；QA 表本身已入库，精确查找 100% 只说明问答表通路可用。
- 扫描/截图型 PDF 未做 OCR，不参与文档检索评测。

## 部署

线上按 systemd 管理，工作目录 `/home/ubuntu/cuc-kb`，通过 `EnvironmentFile=.env` 注入配置：

```bash
sudo cp cuc-kb-mcp.service cuc-kb-mcp-gateway.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cuc-kb-mcp cuc-kb-mcp-gateway
```

- API 由反向代理终止 TLS 并转发到 `127.0.0.1:8000`；`KB_OIDC_REDIRECT_URIS` 必须与外部访问地址一致。
- 源 PDF 桶保持私有，仅通过 `/v1/files/{doc_id}` 鉴权后签发 PAR（默认 900 秒）。
- MCP HTTP 只绑定回环，容器通过网桥转发访问；公网入口不要直接放开 8765。
- 带 `--recreate` 重新入库会删除并重建集合；只想解析预览时用 `--skip-embed`。

## 仓库约定

- `.env`、`data/`、模型缓存、日志与备份均不入库。
- `QA知识导入模板.csv` 含个人联系方式，已加入 `.gitignore`；需要版本管理时请先脱敏。
- 一次性线上修正脚本（`p0_migrate.py`、`p1_alias.py`）默认 dry run，加 `--apply` 才写库。

## 使用范围

校内知识库服务代码，供中国传媒大学 AIHub 助手与相关应用使用。
