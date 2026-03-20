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
