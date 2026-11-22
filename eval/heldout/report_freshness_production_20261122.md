# 版本证据与精排条数：生产验证（2026-11-22）

按用户要求直接上线。Phoenix `cuc-kb` 和 `cuc-kb-mcp` 已切换，`KB_MCP_PREFER_CURRENT=1`；40 条候选、最多 20 条送精排。不重启 Open-LLM-VTuber，不改语料/Qdrant。备用文件和回滚见 `HANDOFF.md`。上线前 CI：unit 178 项运行，5 跳过；MCP 34 项通过，Ruff 通过。上线后服务正常、NRestarts=0。

71 题通过 VTuber 配置实际使用的生产 HTTP MCP 地址发起，逐次调用，预热连接但不按题预热缓存。只测 MCP 和检索证据，不是 Agent 最终回答质量，也不是同轮生产 A/B。

| 指标 | 结果 |
|---|---:|
| MCP 调用成功 / 降级 | 71/71 / 0 |
| MCP 墙钟 P50 / P95 / max | 1329 / 2980 / 4894 ms |
| MCP 超过 2s | 7/71 |
| 后端所报 P50 / P95 | 1322 / 2973 ms |
| 现行题新版证据 @5 | 20/21 |
| 现行题出现标注旧证据 | 0/21 |
| 历史题指定版本 @5 | 14/14 |
| 对比题双版证据 @5 | 8/9 |
| 对照题 @5 | 23/23 |
| 库外版本未标 direct | 4/4（3 none，1 related） |

现行题 re01 未命中标注证据，仍为 direct；对比题 re03 未同时命中两版。可答题中 4 题为 related（re09、re10、re19、fr01），其中 fr01 带新版核读提示。慢请求主要时间在检索后端而非 MCP 转发，不能凭此次结果判断是 embedding、精排还是本地查询阶段；需按阶段计时再优化。2s 门槛未达到，生产按用户此次要求保留上线。

原始结果：Phoenix `~/cuc-kb-release-20261122.xAkmEb/production_results.json` 与 `.jsonl`。复测脚本：`eval/freshness_mcp_live.py`。
