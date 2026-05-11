import service, { requestWithRetry } from './index'

/**
 * Get system health status
 * @returns {Promise}
 */
export function getHealth() {
  return service({
    url: '/health',
    method: 'get'
  })
}

/**
 * Generate ontology (upload documents and simulation requirements)
 * @param {Object} data - Contains files, simulation_requirement, project_name, etc.
 * @returns {Promise}
 */
export function generateOntology(formData) {
  return requestWithRetry(() =>
    service({
      url: '/api/graph/ontology/generate',
      method: 'post',
      data: formData,
      headers: {
        'Content-Type': 'multipart/form-data'
      }
    })
  )
}

/**
 * Build graph
 * @param {Object} data - Contains project_id, graph_name, etc.
 * @returns {Promise}
 */
export function buildGraph(data) {
  return service({
    url: '/api/graph/build',
    method: 'post',
    data
  })
}

/**
 * Query task status
 * @param {String} taskId - Task ID
 * @returns {Promise}
 */
export function getTaskStatus(taskId) {
  return service({
    url: `/api/graph/task/${taskId}`,
    method: 'get'
  })
}

/**
 * Get SSE Events URL for task
 * @param {String} taskId
 * @returns {String}
 */
export function getTaskEventsURL(taskId) {
  const baseURL = import.meta.env.VITE_API_BASE_URL
  return `${baseURL}/api/graph/task/${taskId}/events`
}

/**
 * Get graph data
 * @param {String} graphId - Graph ID
 * @returns {Promise}
 */
export function getGraphData(graphId) {
  return service({
    url: `/api/graph/data/${graphId}`,
    method: 'get'
  })
}

/**
 * Get project information
 * @param {String} projectId - Project ID
 * @returns {Promise}
 */
export function getProject(projectId) {
  return service({
    url: `/api/graph/project/${projectId}`,
    method: 'get'
  })
}

/**
 * Update project information (e.g. name)
 * @param {String} projectId - Project ID
 * @param {Object} data - Contains name, etc.
 * @returns {Promise}
 */
export function updateProject(projectId, data) {
  return service({
    url: `/api/graph/project/${projectId}`,
    method: 'patch',
    data
  })
}

/**
 * Delete project
 * @param {String} projectId - Project ID
 * @returns {Promise} Returns success or error with referencing_apps if constrained
 */
export function deleteProject(projectId) {
  return service({
    url: `/api/graph/project/${projectId}`,
    method: 'delete'
  })
}

/**
 * Get project list
 * @param {Number} limit - Maximum number of projects to return
 * @returns {Promise}
 */
export function getProjectList(limit = 50) {
  return service({
    url: '/api/graph/project/list',
    method: 'get',
    params: { limit }
  })
}

/**
 * Object-first DFS search - results grouped by Object node
 * @param {Object} data - Contains graph_id, query, limit, max_depth, root_type
 * @returns {Promise}
 */
export function searchObjectFirst(data) {
  return service({
    url: '/api/report/tools/search-object-first',
    method: 'post',
    data
  })
}

/**
 * 专用路径检索：Entity/Term → Topic → Clause
 * 用于 Hit-Test 视图，固定 2 跳路径
 * @param {Object} data - Contains graph_id, query, limit, root_type
 * @returns {Promise}
 */
export function searchEntityTopicClause(data) {
  return service({
    url: '/api/report/tools/search-entity-topic-clause',
    method: 'post',
    data
  })
}

/**
 * bge-reranker-v2-m3 相关性重排：打分 + 分数阈值过滤 + top_k 截取
 * @param {Object} data - Contains rows, query, top_k, rerank_min_score
 * @returns {Promise}
 */
export function rerankFacts(data) {
  return service({
    url: '/api/report/tools/rerank',
    method: 'post',
    data
  })
}

/**
 * 问题意图摘要匹配：Topic 召回 + 关联 Entity 重排
 * @param {Object} data - Contains app_id (or graph_id), query, topic_limit, entity_limit, rerank_min_score
 * @returns {Promise}
 */
export function queryIntentMatch(data) {
  return service({
    url: '/api/report/query-topic',
    method: 'post',
    data
  })
}

/**
 * LLM 推理问答：基于过滤后的 facts 生成回答
 * @param {Object} data - Contains facts, query, temperature
 * @returns {Promise}
 */
export function llmAnswer(data) {
  return service({
    url: '/api/report/tools/llm-answer',
    method: 'post',
    data
  })
}

/**
 * 重新执行 Pipeline 的图谱构建阶段
 * @param {String} pipelineId - Pipeline ID
 * @returns {Promise}
 */
export function retryGraphBuilding(pipelineId) {
  return service({
    url: `/api/graph/kb-pipeline/${pipelineId}/retry-graph-building`,
    method: 'post'
  })
}

// ============================================================================
// 智能Chunks标注分析 API
// ============================================================================

/**
 * 获取章节级进度详情
 * @param {String} projectId - 项目ID
 * @returns {Promise}
 */
export function getChunkProgress(projectId) {
  return service({
    url: `/api/graph/chunk/${projectId}/progress`,
    method: 'get'
  })
}

/**
 * 获取格式化分析数据（含 PDF 定位信息）
 * @param {String} projectId - 项目ID
 * @returns {Promise}
 */
export function getChunkAnalysis(projectId) {
  return service({
    url: `/api/graph/chunk/${projectId}/analysis`,
    method: 'get'
  })
}

/**
 * 检查项目是否存在 intelligent_chunks.json
 * @param {String} projectId - 项目ID
 * @returns {Promise}
 */
export function checkHasIntelligentChunks(projectId) {
  return service({
    url: `/api/graph/chunk/${projectId}/has_intelligent_chunks`,
    method: 'get'
  })
}

/**
 * 启动/恢复智能Chunks标注分析任务
 * @param {Object} data - Contains project_id, reset (bool)
 * @returns {Promise}
 */
export function startChunking(data) {
  return requestWithRetry(() =>
    service({
      url: '/api/graph/chunk/intelligent',
      method: 'post',
      data
    })
  )
}

/**
 * 自动推断推荐的章节锚点和最小条款容器锚点
 * @param {String} projectId - 项目ID
 * @returns {Promise}
 */
export function inferChunkAnchors(projectId) {
  return requestWithRetry(() =>
    service({
      url: `/api/graph/chunk/${projectId}/infer-anchors`,
      method: 'post'
    })
  )
}

/**
 * 更新单个 clause 的知识实体（手动编辑）
 * @param {String} projectId - 项目ID
 * @param {Object} data - Contains clause_id, terms, conditions, actions, components
 * @returns {Promise}
 */
export function updateClauseEntity(projectId, data) {
  return service({
    url: `/api/graph/chunk/${projectId}/entity`,
    method: 'patch',
    data
  })
}

// ============================================================================
// KB Pipeline API
// ============================================================================

/**
 * 获取 MinIO 中的 PDF 文件列表
 * @param {String} prefix - 前缀过滤
 * @returns {Promise}
 */
export function getKbPipelineMinioFiles(prefix = '') {
  return service({
    url: '/api/graph/kb-pipeline/minio-files',
    method: 'get',
    params: { prefix }
  })
}

/**
 * 启动 KB Pipeline
 * @param {String} minio_object - MinIO 对象名称
 * @returns {Promise}
 */
export function startKbPipeline(minio_object) {
  return service({
    url: '/api/graph/kb-pipeline/start',
    method: 'post',
    data: { minio_object }
  })
}

/**
 * 获取 KB Pipeline 列表
 * @param {Number} limit - 最大数量
 * @returns {Promise}
 */
export function getKbPipelineList(limit = 100) {
  return service({
    url: '/api/graph/kb-pipeline/list',
    method: 'get',
    params: { limit }
  })
}

/**
 * 获取单个 KB Pipeline 状态
 * @param {String} pipelineId
 * @returns {Promise}
 */
export function getKbPipeline(pipelineId) {
  return service({
    url: `/api/graph/kb-pipeline/${pipelineId}`,
    method: 'get'
  })
}

/**
 * 获取 KB Pipeline SSE Events URL
 * @param {String} pipelineId
 * @returns {String}
 */
export function getKbPipelineEventsURL(pipelineId) {
  const baseURL = import.meta.env.VITE_API_BASE_URL
  return `${baseURL}/api/graph/kb-pipeline/${pipelineId}/events`
}

// ============================================================================
// MinerU PDF 解析 API
// ============================================================================

/**
 * 获取 MinerU 解析结果
 * @param {String} projectId - 项目ID
 * @returns {Promise}
 */
export function getMineruChunks(projectId) {
  return service({
    url: `/api/graph/pdf/mineru-parse/${projectId}`,
    method: 'get'
  })
}

/**
 * 从 mineru_parsed.json 重新生成 chunks.json
 * @param {String} projectId - 项目ID
 * @param {String} parseMethod - 解析方法，'auto' 或 'ocr'
 * @returns {Promise}
 */
export function reAnnotateMineru(projectId, parseMethod = 'ocr') {
  return service({
    url: '/api/graph/pdf/re-annotate',
    method: 'post',
    data: { project_id: projectId, parse_method: parseMethod }
  })
}

// ============================================================================
// 图谱检索测试 API
// ============================================================================

/**
 * 按名称模糊搜索节点
 * @param {Object} data - { graph_id, query, node_type, limit }
 */
export function searchNodes(data) {
  return service({
    url: '/api/graph/ops/search-nodes',
    method: 'post',
    data
  })
}

/**
 * 获取节点 1 跳邻域
 * @param {Object} data - { graph_id, node_uuid }
 */
export function getNodeNeighborhood(data) {
  return service({
    url: '/api/graph/ops/node-neighborhood',
    method: 'post',
    data
  })
}
