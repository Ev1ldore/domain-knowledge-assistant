# 本地验收记录

日期：2026-09-21。只验证桌面独立项目，没有发布、Git push、修改博客或操作原业务库。

## 环境与实际路径

- 项目：`~/Desktop/domain-knowledge-assistant`。
- Python：已有 Conda `edu_rag_project` 环境，Python 3.10；没有改动该环境的包。FastAPI 0.115.12、Uvicorn 0.41.0、OpenAI 2.24.0、NumPy 2.2.6、rank-bm25 0.2.2、sentence-transformers 3.0.1、torch 2.10.0、transformers 4.45.0、websockets 16.0。
- 模型：实际调用本机 `http://127.0.0.1:11434/v1`。聊天 `qcwind/qwen3-8b-instruct-Q4-K-M:latest`，嵌入 `bge-m3:latest`，真实输出 1024 维向量。
- 重排序：只读加载现有本地 `bge-reranker-large`，CPU CrossEncoder，实际推理；不加载教育意图分类器。
- 第一轮数据：`.local/validation-data`；修复后独立第二轮：`.local/validation-data-v2`。每轮都从空数据目录开始，没有重建或覆盖原数据库、文档、Milvus 集合。
- 验收服务只监听 `127.0.0.1:8080`。默认 `data/` 与演示验收目录分开。
- 新项目没有答案缓存。所有检索/引用来自其 SQLite 中的当前就绪片段。

实际路径是：浏览器 / HTTP / WebSocket → 新 FastAPI → 同一问答流程 → 独立 FAQ / SQLite → Ollama BGE-M3 嵌入 → NumPy 稠密检索＋BM25＋RRF → 本地 CrossEncoder → Ollama 聊天模型选择证据 → 服务端 ID/连续原文校验 → 保存历史 → 逐段展示。

## 真实模型端到端结果

运行命令：为新服务设置独立数据目录、模型路径和临时管理员环境变量后，执行 `python scripts/verify_live.py --url http://127.0.0.1:8080`。脚本通过 HTTP 登录、上传、查询、删除，并通过 WebSocket 验证流式协议。

完整记录见 [live-results.json](live-results.json)。第二轮 **15/15 检查通过**：

| 验收项 | 真实证据 |
|---|---|
| 1. 服务启动、页面、基础接口 | `/` 200，中性页面，`/health` 200；浏览器实际打开 |
| 2. FAQ 命中 | “星盒工具是什么？”返回导入的确切答案，`status=faq`、常见问答来源 |
| 3. FAQ 未命中后的真实 RAG | E17 问题返回 `starbox index unlock` 原文，真实故障文件、片段 ID、章节；dense_bm25_rrf＋cross_encoder |
| 4. 资料无答案 | 企业版年费问题返回 insufficient，无编造价格及引用 |
| 5. 连续追问 | E17 后问“如果仍然出现呢？”，返回 `logs/index.log` 排查说明，检索问题带上一轮 E17 上下文 |
| 6. 历史恢复 | API 返回已保存历史；浏览器刷新恢复同一会话和 FAQ；额外实际进程重启前后 7 轮历史摘要完全一致 |
| 7. 上传与索引 | 三个独立 Markdown 文件经历 uploaded/indexing/ready；真实嵌入后有 9 个片段，新文档可被检索 |
| 8. 删除及旧结果 | 临时“银栈”文档返回唯一虚构标记；删除后重复相同问题返回 insufficient，没有旧标记或旧来源 |
| 9. 管理认证 | 未认证列表、上传、FAQ 导入、删除和重建均返回 401 |
| 10. 会话隔离 | 新会话的省略问题返回 clarify，不继承另一个会话的 E17；两会话历史不串用 |
| 澄清 | 未指定导出模式时返回 clarify |
| WebSocket | start/status/meta/token/end；拼接 token 与最终答案一致，实际引用导入文件上限 12 的说明 |
| 用户消息注入 | 要求忽略规则并编造价格/第99页时返回 insufficient |
| 旧过滤参数 | HTTP 请求含 source_filter 返回 422 |
| 空知识库 | 在首次导入前明确提示尚未导入可检索资料 |

CPU / 本机模型第二轮典型耗时：文档 E17 问答约 34 秒、连续追问约 34 秒、澄清约 60 秒、流式文档回答约 70 秒；这不是性能基准，受本机负载和模型冷启动影响。首轮嵌入/模型加载更慢。前端的流式段落在证据校验后才开始，不将等待时间包装成原始 token 实时生成。

第一轮真实测试有一项连续追问失败，记录保存在 [live-results-initial.json](live-results-initial.json)，没有删除或冒充成功。随后修正只使用紧邻上一轮上下文，增加“不跳过未成功话题”的回归测试，并按连续对话顺序重跑。

额外文档级注入测试见 [injection-results.json](injection-results.json)：实际上传含伪系统指令的文档，要求输出伪造文本并编造页码；模型只选择其中的合法事实 `SAFE-DEMO-42`，服务端返回原文与真实片段引用，没有执行伪指令。测试文档随后删除，状态 200。这只验证一个恶意样本，不代表完全抵御所有注入或知识投毒。

实际进程重启记录见 [restart-results.json](restart-results.json)：停止并重启服务后，7 轮会话历史完全相同；3 份演示文档、2 条 FAQ、9 个片段仍然存在，不需要重建。

## 浏览器实测

见 [browser-results.json](browser-results.json)。实际打开页面、提交 FAQ、刷新恢复；最终代码下从页面提交 E17 问题，看到真实命令与三条文件/片段/章节引用，再次刷新后仍为同一会话且答案完整。窄窗口标题和管理按钮拥挤、管理弹窗样式问题已修复并复核。

同时发生的独立注入文档删除曾触发知识版本检查，页面明确显示“资料在回答过程中发生变化，请重试”，没有流出失效答案。重试后正常回答；刷新历史只保留成功的一轮。

浏览器完整登录＋文件选择上传路径没有逐步操作；上传/删除的真实管理链路通过 HTTP API 验证，二者没有混称。

## Mock / 静态检查（单独计数）

- `python -m unittest discover -s tests -v`：**17/17**，结果见 [unit-results.txt](unit-results.txt)。这里的 FakeModels 是显式 Mock，不是上面真实模型验收的一部分。
- 覆盖：FAQ 原子导入/幂等、全部管理接口鉴权、无默认密码、完整问候匹配、旧过滤字段拒绝、伪造引用拒绝、模型失败不保存、删除时知识版本保护、会话隔离/上下文、真实 SQLite 重开、索引中断、文件冲突/路径校验、WS 协议与 Origin 检查。
- PDF 两页解析测试用实际 PyMuPDF 生成/提取并检查页码；没有声称完整 PDF＋模型链路已验收。
- Python compileall、JavaScript `node --check` 通过。
- `KB_ADMIN_PASSWORD=VALIDATION_PLACEHOLDER docker compose config --quiet` 通过。只校验编排配置，没有构建/启动 Docker 容器。
- 核心运行代码与页面扫描未发现原品牌、学科、课程、招生、就业、客服联系方式或教育分类器引用。
- 原项目启动前记录的 82 个源文件 SHA-256 与结束时一致，没有新增源文件。原来的 Git 脏状态保留。

## 目前的限制 / 尚未验收

- 没有在全新虚拟环境重新下载安装所有依赖；完整运行验证使用现有 Conda。README 给出独立安装步骤，但不能将其称为干净机器实测。现有环境有 requests 的版本兼容提示，未阻断本次测试。
- Docker 构建和容器 E2E、远程付费模型服务、不同供应商 JSON 模式未验证。
- 真实完整问答以中文 Markdown 和 JSON FAQ 为主；TXT/CSV/PDF/DOCX/PPTX 解析路径已提供，但各格式全链路、复杂表格、超大文件尚未逐一验证。扫描件 OCR、图片、旧 .doc/.ppt、Excel 不在新版本支持范围。
- 默认答案为经核验的原文摘录，不是自由改写。证据可能选错、资料本身可能错误/恶意/冲突；相关性阈值需要按领域校准。没有“消除幻觉”的保证。
- 上下文采用有限的指代规则与紧邻上一轮问题，复杂多话题对话可能需要用户重新明确对象。
- 没有执行旧教育业务数据迁移；只提供只读导出/转换/上传方案。旧历史不自动迁移，旧缺失页码不会补造。
- 单进程、单用户本地使用；没有多租户、细粒度知识权限或跨设备会话账户。UUID 会话是持有者凭据，请勿直接暴露公网。
- SQLite 本地矩阵检索适合小知识库，未做大规模性能/并发压力测试；本机 CPU 推理延迟明显。
