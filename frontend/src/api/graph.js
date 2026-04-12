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
 * Reset and re-run intelligent chunk annotation
 * @param {Object} data - Contains project_id, reset (bool)
 * @returns {Promise}
 */
export function resetIntelligentChunks(data) {
  return requestWithRetry(() =>
    service({
      url: '/api/graph/chunk/intelligent',
      method: 'post',
      data
    })
  )
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
  const baseURL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001'
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
 * Search graph (Hit Test) - supports both single and multi-graph search
 * @param {Object} data - Contains graph_id or graph_ids, query, limit
 * @returns {Promise}
 */
export function searchGraph(data) {
  return service({
    url: '/api/report/tools/search',
    method: 'post',
    data
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
 * AI Q&A - retrieval from multiple graphs + LLM answering
 * @param {Object} data - Contains graph_ids, query
 * @returns {Promise}
 */
export function aiQa(data) {
  return service({
    url: '/api/graph/ai-qa',
    method: 'post',
    data
  })
}

/**
 * Chat with Report Agent (Advanced retrieval)
 * @param {Object} data - Contains simulation_id, message, chat_history
 * @returns {Promise}
 */
export function chatWithAgent(data) {
  return service({
    url: '/api/report/chat',
    method: 'post',
    data
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
// MinerU PDF 解析 API
// ============================================================================

/**
 * 调用 MinerU API 解析 PDF
 * @param {FormData} formData - 包含 pdf_file 和可选的 project_id, filename
 * @returns {Promise}
 */
export function mineruParse(formData) {
  return requestWithRetry(() =>
    service({
      url: '/api/graph/pdf/mineru-parse',
      method: 'post',
      data: formData,
      headers: {
        'Content-Type': 'multipart/form-data'
      }
    })
  )
}

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
 * @returns {Promise}
 */
export function reAnnotateMineru(projectId) {
  return service({
    url: '/api/graph/pdf/re-annotate',
    method: 'post',
    data: { project_id: projectId }
  })
}
