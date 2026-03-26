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
  return requestWithRetry(() =>
    service({
      url: '/api/graph/build',
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
