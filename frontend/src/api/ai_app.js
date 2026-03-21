import service from './index'

/**
 * Save AI Application
 * @param {Object} data
 * @returns {Promise}
 */
export function saveApp(data) {
  return service({
    url: '/api/ai-app/save',
    method: 'post',
    data
  })
}

/**
 * Get AI Application List
 * @param {Number} limit
 * @returns {Promise}
 */
export function getAppList(limit = 50) {
  return service({
    url: '/api/ai-app/list',
    method: 'get',
    params: { limit }
  })
}

/**
 * Get AI Application Detail
 * @param {String} appId
 * @returns {Promise}
 */
export function getApp(appId) {
  return service({
    url: `/api/ai-app/${appId}`,
    method: 'get'
  })
}

/**
 * Delete AI Application
 * @param {String} appId
 * @returns {Promise}
 */
export function deleteApp(appId) {
  return service({
    url: `/api/ai-app/${appId}`,
    method: 'delete'
  })
}
