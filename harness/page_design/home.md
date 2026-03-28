Chunk Analysis 分析逻辑 — 阶段分解

# 阶段 1：项目初始化与 PDF 加载（前端 - onMounted / loadExistingProject）
从 URL 获取 projectId，调用 GET /api/graph/project/{id} 获取项目信息
从项目文件列表中识别 PDF 文件名
初始化 PDF.js，调用 GET /api/graph/project/{id}/document/{filename} 加载 PDF
根据项目状态决定后续行为：
graph_chunked → 直接加载已有分析数据（阶段 4）
graph_chunking → 启动进度轮询（阶段 3），同时尝试加载已完成的分析数据
ontology_generated → 自动触发 智能分析（阶段 2）
其他状态 → 显示"开始分析"按钮，等待用户确认

# 阶段 2：智能 Chunks 标注分析启动（前端触发 → 后端异步执行）
用户操作：🚀 开始智能分析 按钮

前端调用 POST /api/graph/chunk/intelligent（reset: false）
或 POST /api/graph/chunk/intelligent（reset: true）重置后重新分析
后端创建异步任务，返回 taskId，前端立即进入轮询模式
后端执行流程（backend/app/api/graph.py → chunking_task）：

## 阶段 2.1：文本准备
从项目的已上传文档中获取文本块（text_chunks）
检查断点恢复（ProjectManager.get_chunk_checkpoint_v2）
## 阶段 2.2：LLM 驱动的三级分块（核心引擎 LLMDrivenChunker.chunk()）
这是后端最核心的分析逻辑，在 backend/app/services/llm_driven_chunker.py 中实现：



### 2.2.1 目录提取	
读取文档开头，识别章节结构（编号、标题、位置边界）	TOC_SYSTEM_PROMPT → 提取 chapters[]

### 2.2.2 章节位置精化	
在文档全文中精确匹配每个章节标题的实际字符偏移位置	纯正则匹配（_refine_chapter_positions）

### 2.2.3 逐章处理	
对每个章节循环执行：	
├─ 条文提取	提取条文编号、标题、内容、要求类型（M/R/P）、款/项结构	CLAUSE_SYSTEM_PROMPT
├─ 语义三元组抽取	提取 triplets[]（Component → Action → Obj @ Condition）	核心！每条条文独立三元组
├─ 款/项结构化	提取 clause_items[]（款/项编号、内容、款级三元组）	嵌套在条文提取中
├─ 术语识别	识别 X.0.N 格式条文 → is_term_definition: true + terms[]	OCR 感知处理
├─ 表格/公式引用	提取 referenced_tables、referenced_formulas	
├─ 要素提取	提取 Term、Component、Parameter、Formula 等技术要素	ELEMENT_SYSTEM_PROMPT
└─ PDF 位置标注	估算要素在 PDF 中的页码和 bbox 坐标	字符偏移估算

### 2.2.4 保存结果	
去重后序列化 sections[]、clauses[]、elements[] 到 intelligent_chunks.json	
断点恢复机制：每处理完一个章节自动保存检查点，失败可从下一章节恢复
指数退避重试：LLM 调用失败自动重试最多 3 次
阶段 3：进度轮询与实时可视化（前端 startProgressPolling）
每 3 秒轮询一次：
GET /api/graph/task/{taskId} → 任务最终状态
GET /api/graph/chunk/{projectId}/progress → 章节级详细进度
每 2 次轮询刷新一次分析数据（实时更新标注）
实时日志抽屉显示 LLM 各步骤日志
进度条显示当前分析到第几章

# 阶段 4：分析数据加载与渲染（前端 loadAnalysis）
调用 GET /api/graph/chunk/{projectId}/analysis
重建 PDF BBox 标注（从 clauses[].pdf_location 读取）
自动展开第一个章节
构建统计摘要（章节数、条文数、要素数、M/R/P 比例）
阶段 5：MinerU PDF 解析（可选增强模式）
用户手动触发：🧠 MinerU 解析 按钮

前端调用 POST /api/graph/pdf/mineru-parse（传入 project_id + filename）
后端读取项目 PDF 文件，调用 MinerU API（外部服务）
MinerU 返回 layout + content chunks（含 bbox_viewport、category_id）
后端对每个 content chunk 调用 LLM 提取名词实体（nouns[]）
返回 chunks 列表，前端切换到 MinerU 模式：
右侧面板：文本块列表 / 名词池 / 布局标注 三个标签页
左侧 PDF：按 category_id 颜色渲染 BBox 叠加层（标题/正文/表格/图片）
点击任意 BBox → 跳转到对应 chunk 详情
阶段 6：交互式知识标注与编辑
条文点击：展开条文详情，显示 Term / Condition / Action / Component 标签组，支持手动编辑保存（PATCH /api/graph/chunk/{projectId}/entity）
语义三元组展示：Component —M→ Obj @ Condition 格式
款/项结构展示：clause_items[] 独立展示，带款级三元组
知识实体池：All / Terms / Conditions / Actions / Components 五个标签页聚合所有条文中的实体
PDF 联动：点击条文 → PDF 跳转并高亮对应位置；点击 PDF BBox → 右侧展开对应条文
整体数据流图
PDF 文件
  │
  ├─→ [阶段 1] 前端 PDF.js 渲染 ─────────────────┐
  │                                            ▼
  ├─→ [阶段 2.1] 文本提取（后端 file_parser）    │
  │                                            │
  │    ┌───────────────────────────────────────┘
  │    ▼
  │  LLM 驱动的三级分块 (LLMDrivenChunker)
  │    │
  │    ├─→ [2.2.1] LLM 提取目录 ──→ 章节结构
  │    ├─→ [2.2.3] 逐章 LLM 条文提取 ──→ Clauses + Triplets + ClauseItems
  │    ├─→ [2.2.3] 逐章 LLM 要素提取 ──→ Elements (Term/Component/Parameter/Formula)
  │    └─→ [2.2.3] 位置标注 ──→ PDF 坐标估算
  │                                            │
  │    ▼
  │  intelligent_chunks.json (断点恢复支持)
  │
  ├─→ [阶段 5] MinerU API ──→ Layout/Content Chunks ──→ 名词提取
  │
  ▼
前端渲染
  ├─ 统计摘要（Summary Cards）
  ├─ 章节树（Chapter Tree）
  ├─ 条文详情（Clause Detail）+ 语义三元组
  ├─ PDF BBox 标注叠加层
  └─ 知识实体池（Entity Pool）
[12:47:30] INFO: LLM 提取目录: 29 个章节 ，再解释下这个提取目录是用的什么方法


目录提取方法
核心逻辑（_extract_table_of_contents）
输入：取文档开头前 8000 字符（MAX_CHARS_FOR_TOC = 8000）
LLM 调用：发送 TOC_SYSTEM_PROMPT + TOC_USER_PROMPT 构造的消息
输出解析：从 LLM 返回的 JSON 中提取 chapters[] 数组
System Prompt 关键设计（TOC_SYSTEM_PROMPT）
LLM 被赋予"工程规范文档目录分析专家"角色，指示它：

只分析目录部分：识别文档开头的"目录"、"Contents"、"第X章"等
记录位置：估算每个章节在文档中的字符偏移量（start_position, end_position）
章节类型识别：
标题含"术语"、"名词解释" → chapter_type: "term_definition"
标题含"附录"、"附表" → chapter_type: "appendix"
其他 → chapter_type: "normative"
OCR 噪声处理：忽略 # 前缀（如 # 3 电器和导体的选择）
位置精化（_refine_chapter_positions）
LLM 估算的位置是粗略的，所以紧接着还有一个正则精化步骤：

patterns = [
    title,                              # 精确标题
    f"第{chapter_num}章",               # 章节标记
    f"{chapter_num}\\s*[.、]\\s*{title}", # 编号+标题
]
# 在文档前 50000 字符中正则搜索实际位置
找到实际位置后，计算相邻章节间的精确边界（下一个章节起始 - 1）。

LLM 返回格式示例
{
  "chapters": [
    {
      "chapter_number": 1,
      "title": "总则",
      "chapter_type": "normative",
      "start_position": 0,
      "end_position": 8000
    },
    {
      "chapter_number": 2,
      "title": "术语与符号",
      "chapter_type": "term_definition",
      "start_position": 8000,
      "end_position": 25000
    }
  ]
}
重试机制
使用指数退避：_call_llm_with_retry 最多重试 3 次
初始延迟 2 秒，最大延迟 30 秒，乘以 2 倍递增
超过重试次数 → 抛出 LLMChunkerError
降级处理
如果 LLM 返回空目录（chapters: []），会降级为单章节模式，将全文作为 {"chapter_number": 1, "title": "全文", ...} 处理。

总结：目录提取 = LLM 理解文档语义（识别章节层级和类型）+ 正则精化位置边界，两步配合。29 个章节就是 LLM 从你的规范文档目录部分解析出来的章节数量。