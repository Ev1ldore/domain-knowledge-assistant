# 从原教育版迁移到独立通用版

此次交付选择新建桌面项目。没有修改原工作目录的源文件、未提交改动、config.ini、.env、知识文件或数据库；没有调用原 init_data.py、reset、clean、Git push。原项目继续按原方式运行。新服务默认端口同为 8080，二者同时运行时请为新服务设置 `PORT=8081` 等未占用端口。

## 当前代码中发现的耦合与处理

| 原位置 / 行为 | 新项目处理 |
|---|---|
| app.py 品牌问候、教育界面 | 中性固定问候；名称、介绍、欢迎语和建议问题可配置；问候只完整匹配 |
| app.py HTTP 只尝试 FAQ、WebSocket 另走问答 | HTTP / WS 都调用同一个 IntegratedQASystem.answer |
| new_main.py 的客服兜底、模型直接回答路径 | 统一明确的空库、不足、澄清、索引失败、模型失败状态 |
| rag_system.py 的教育 BERT 分类决定是否检索 | 不再引入分类器；FAQ 未命中必经知识检索 |
| prompts.py 的行业角色、课程/招生例子、客服电话 | 改成领域无关的证据选择和不可信文档约束 |
| VALID_SOURCES、source_filter、目录名推断学科 | 新接口不接受 source_filter；单知识库无需来源标签；文件 ID 是操作主键 |
| FAQ jpkb 的 subject_name 与教育 CSV | 中性 question/answer 导入；单独 SQLite faq 表；不自动导入旧库 |
| Redis 仅按问题缓存，可能绕过资料更新 | 新版本没有答案缓存；FAQ 每次读当前数据，检索读取当前就绪片段 |
| 文档只打来源/文件名，OCR 丢失页码 | 在新解析路径按 PDF 页、MD 标题、PPT 幻灯片保存实际元数据；未知就不填 |
| 原 Docker 启动自动初始化教育 FAQ/向量 | 新 Docker 只启动新应用；独立卷，无默认灌库 |

原代码 source_filter 的位置已检查：HTTP 请求模型、WebSocket 消息、IntegratedQASystem.query、RAGSystem.generate_answer / retrieve_and_merge、VectorStore.hybrid_search_with_rerank，以及 static/index.html、old_index.html、src/App.jsx。原 HyDE/子查询/回溯分支存在漏传过滤的情况，FAQ/Redis 也没有等价的来源约束。新项目没有复制这些旧分支：接口额外字段拒绝、检索不接收该参数、无按旧条件生成的答案缓存、页面无筛选器，避免只做视觉隐藏。原文件仍保留在原项目。

## 迁移步骤：先复制，再核对，最后切换

1. 保留原库和服务；按 README 在新项目启动空库。默认数据是新项目的 `data/assistant.sqlite3`，可用 ASSISTANT_DATA_DIR 指定新的专属路径。不要指向任何旧业务数据目录。
2. 配置新助手名称/介绍。模型服务可以继续使用已授权的服务，但 API key 由环境变量传入；不要直接复制旧 .env 或旧 config.ini。已有重排序权重可以配置为只读本地路径，无需复制多 GB 文件。
3. **只读导出 FAQ**。旧源码实际表是 `jpkb`，可通过现有数据库管理工具只读导出 `question, answer` 列为 CSV。此交付不自动访问或导出旧数据库。
4. 如果手里已有旧 CSV，可在本地转换（明确选择输入和新输出路径）：

   ```bash
   python scripts/convert_faq.py /path/to/old-export.csv /path/to/new-faq.json \
     --question-column 问题 --answer-column 答案
   ```

   普通 question/answer CSV 不需要列名参数。脚本只读输入、拒绝覆盖输出；忽略行业列。导入前人工检查：不同学科下同名问题可能答案不同，必须补充问题中的对象/条件或合并答案，不能随机保留某条。导入器会拒绝重复/归一化冲突。
5. 把想迁移的文档**逐份上传副本**到新服务。先用少量文档验证，再扩大范围。知识内容属于使用者自己的领域，框架不改写原文。两份同名文件应先改成不同文件名。
6. 新版本重新解析和向量化，不复用旧 Milvus 向量。旧 schema、模型签名和片段 ID 与新库不兼容，不能直接拷贝集合。旧文件已有缺失页码的历史回答无法“补出”页码；重新上传原始 PDF 才可得到新解析的页码。
7. 对比 FAQ、文档问题、无答案问题、澄清问题及来源，再决定使用哪个服务。原服务、原 MySQL/Milvus 和文档始终保留；退回原服务即可回滚。本次没有执行任何正式业务迁移。

## 历史及运行数据

旧 MySQL `conversations` 不自动迁入：其 session ID、历史来源、成功状态与新格式不同。新库只存新会话。若要保存旧历史，应先只读导出为档案；不要把它作为新检索事实来源，也不要伪造缺失引用。当前没有提供自动历史迁移程序。

新服务停止后，可以备份整个专属数据目录。更换嵌入模型时使用另一个数据目录并重建，不在已用索引上覆盖。管理员 token 存在进程内，重启必须重新登录；会话和问答历史持久保存在 SQLite，浏览器刷新会恢复当前会话。

验收资料在 `.local/validation-data*` 中，与默认空的 `data/` 分开。它们都是新建的虚构资料副本，不是原教育业务数据。
