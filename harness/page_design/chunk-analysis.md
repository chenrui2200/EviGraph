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

## 阶段 2.1：MinerU PDF 解析
从项目的已上传文档中获取文本块（text_chunks）
检查断点恢复（ProjectManager.get_chunk_checkpoint_v2）

## 阶段 2.2：LLM 驱动的三级分块（核心引擎 LLMDrivenChunker.chunk()）
这是后端最核心的分析逻辑，在 backend/app/services/llm_driven_chunker.py 中实现：



### 2.2.1 文本提取
前端调用 POST /api/graph/pdf/mineru-parse（传入 project_id + filename）
后端读取项目 PDF 文件，调用 MinerU API（外部服务）
MinerU 返回 layout + content chunks 参考格式如下
```
{
  "layout": [
    {
      "page_info": {"page_no": 0, "width": 1654, "height": 2339},
      "layout_dets": [
        {
          "category_id": 1,
          "bbox": [89, 77, 501, 183],
          "poly": [249.7, 215.4, 1392.6, 215.4, 1392.6, 510.4, 249.7, 510.4],
          "score": 0.99
        }
      ]
    }
  ],
  "info": {
    "pdf_info": [
      {
        "page_idx": 0,
        "page_size": [595.3, 841.9],
        "para_blocks": [
          {
            "type": "text",
            "bbox": [90, 80.2, 501, 180.2],
            "lines": [
              {
                "bbox": [90, 80.2, 501, 92.2],
                "spans": [
                  {
                    "content": "1.0.1 为使低压配电设计中...",
                    "type": "text",
                    "score": 1
                  }
                ]
              }
            ]
          },
          {
            "type": "title",
            "bbox": [271, 190, 324, 209],
            "lines": [
              {
                "spans": [{"content": "2 术语", "type": "text", "score": 1}]
              }
            ]
          }
        ],
        "discarded_blocks": [],
        "_version_name": "0.7.1"
      }
    ]
  },
  "content": [
    {
      "type": "text",
      "text": "1.0.1 为使低压配电设计中，做到保障人身和财产安全...制订本规范。",
      "page_idx": 0
    },
    {
      "type": "title",
      "text": "2 术语",
      "text_level": 1,
      "page_idx": 0
    }
  ]
}
```
返回的数据结构含义
1. 顶层结构
整个返回数据是一个 JSON 对象（或 Python 字典），主要包含三个键：
layout: 侧重于视觉层面的版面检测（哪里是图、哪里是文字块）。
info: 侧重于详细的解析信息（OCR 结果、段落结构、阅读顺序等）。
content: 侧重于最终提取出的纯文本内容，方便下游任务直接使用。
2. layout 部分（版面检测）
这部分记录了文档每一页的视觉元素检测结果，主要用于还原文档的视觉结构。
layout (列表): 包含每一页的检测信息。
page_info: 页面基本信息。
page_no: 页码（从 0 开始）。
height, width: 页面的像素尺寸。
layout_dets (列表): 页面上检测到的具体元素块。
category_id: 元素类别 ID（例如：0 可能代表标题，1 代表正文文本，2 代表表格等，具体映射需参考模型定义）。
bbox: 边界框坐标 [x_min, y_min, x_max, y_max]，表示元素所在的矩形区域。
poly: 多边形坐标，比 bbox 更精确地描述不规则文本区域的形状。
score: 检测置信度（0-1 之间，越接近 1 越可信）。
3. info 部分（详细解析信息）
这部分包含了最丰富的解析细节，适合需要知道文字具体位置、行结构或需要调试解析效果的场景。
pdf_info (列表): 每一页的详细解析数据。
preproc_blocks / para_blocks: 预处理块或段落块。这是 OCR 和文本识别的核心结果。
type: 块类型（如 text, title, table 等）。
bbox: 该文本块在页面上的坐标。
lines: 块内包含的行列表。
spans: 行内包含的 span（片段）列表，通常对应具有相同格式的连续文字。
content: 具体的文本内容（注意：您提供的文件中此处显示为乱码，见下文“特别注意”）。
layout_bboxes: 更高阶的版面区域划分（例如将页面划分为单栏、双栏区域），layout_label 可能表示区域类型（如 "H" 可能代表 Header 或某种特定布局）。
discarded_blocks: 被丢弃的块。通常是一些页眉、页脚、页码或噪声，解析器认为不需要保留的内容。
page_size: 页面尺寸。
_parse_type: 解析类型（如 "txt" 表示按文本解析）。
_version_name: 解析引擎的版本号（如 "0.7.1"）。
4. content 部分（最终提取内容）
这部分是清洗和整理后的结果，最适合直接用于 RAG（检索增强生成）、知识库构建或文本分析。
content (列表): 按阅读顺序排列的文本块列表。
type: 内容类型（如 text, title）。
text: 提取出的具体文本字符串。
page_idx: 该内容所属的页码索引。
text_level: (可选) 文本层级，用于区分标题级别（如 1 级标题、正文等）。


针对minerU的接口返回，实现下面功能
PDF 联动：点击pdf上文本bbox文本框 → 右侧展开对应条文，显示该段文本关联的条款、元素、公式、参数、表项
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
  │    ├─→ 利用llm 驱动Text分块分析，逐个chunk实现 LLM 要素提取 ──→ Elements (Term/Component/Parameter/Formula)
  │    ├─→ 逐章 LLM 提取语义三元组，包含一下几个方面 
  │    │      1 术语定义查询   →  Term --defines--> Clause 
  │    │        适用对象查询   →  Clause --applies_to--> Component
  │    │        规范要求查询   →  Clause --mandates--> Action/Requirement
  │    │      2 情况查询      →  Situation --in_situation--> Action 
  │    │        参数数值查询   →  Action --requires--> Parameter
  │    └─→ 然后把 Term + Situation(可能缺失) 结合起来实现关系链接
                关联关系需要记录清楚 (Term + Situation) - (Chunk) - (Page info)
  │                                            │
  │  intelligent_chunks.json (断点恢复支持)
  │
  ▼
前端渲染
  ├─ 章节树（Chapter Tree）
  ├─ 文段详情（Clause Detail）+ 语义三元组
  └─ 知识实体池（Element Pool）


### 2.2.4 保存结果	
去重后序列化 LLMDrivenChunker 驱动检索的数据保存到 intelligent_chunks.json	
断点恢复机制：每处理完一个章节自动保存检查点，失败可从下一章节恢复
指数退避重试：LLM 调用失败自动重试最多 3 次
阶段 3：进度轮询与实时可视化（前端 startProgressPolling）
每 3 秒轮询一次：
GET /api/graph/task/{taskId} → 任务最终状态
GET /api/graph/chunk/{projectId}/progress → 章节级详细进度
每 2 次轮询刷新一次分析数据（实时更新标注）
实时日志抽屉显示 LLM 各步骤日志
进度条显示当前分析到第几章

# 阶段 3：分析数据加载与渲染（前端 loadAnalysis）
调用 GET /api/graph/chunk/{projectId}/analysis
重建 PDF BBox 标注（从 clauses[].pdf_location 读取）
自动展开第一个章节
构建统计摘要（章节数、条文数、要素数、M/R/P 比例）


# 附录 目录提取方法
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