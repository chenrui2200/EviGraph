/**
 * 公共检索方法 - 固定 2 跳路径检索（Entity/Term → Topic → Clause）
 *
 * Hit Test 和 AI Q&A 共用，确保检索行为一致。
 */
import { searchEntityTopicClause } from '../api/graph'

/**
 * 执行 2 跳路径检索
 *
 * @param {Object} options
 * @param {string|string[]} options.graphId - 单个 graph_id 或数组
 * @param {string} options.query - 检索查询
 * @param {number} [options.limit=15] - 每种根节点类型返回上限
 * @param {number} [options.similarityThreshold=50] - 相似度阈值 (50-100)
 * @param {string[]} [options.rootTypes=['Entity','Term']] - 根节点类型
 * @returns {Promise<{rows: Array, durationMs: number}>}
 */
export async function hitTestSearch({
  graphId,
  query,
  limit = 15,
  similarityThreshold = 50,
  rootTypes = ['Entity', 'Term'],
}) {
  const graphIds = Array.isArray(graphId) ? graphId : [graphId]
  const useEntity = rootTypes.includes('Entity')
  const useTerm = rootTypes.includes('Term')

  const startTime = Date.now()
  const allRows = []
  const seenUuids = new Set()

  for (const gid of graphIds) {
    // 并行查询 Entity 和 Term
    const promises = []
    if (useEntity) {
      promises.push(searchEntityTopicClause({ graph_id: gid, query, limit, root_type: 'Entity' }))
    }
    if (useTerm) {
      promises.push(searchEntityTopicClause({ graph_id: gid, query, limit, root_type: 'Term' }))
    }
    const responses = await Promise.all(promises)

    const entityRes = useEntity ? responses[0] : null
    const termRes = useTerm ? responses[useEntity ? 1 : 0] : null

    // Term 排前，Entity 排后，按 similarityThreshold 过滤，按 uuid 去重
    const termRows = (termRes?.success ? termRes.data.rows || [] : [])
      .filter(r => (r.relevance_score || 0) >= similarityThreshold)
      .map(r => ({ ...r, root_type: 'Term' }))
    const entityRows = (entityRes?.success ? entityRes.data.rows || [] : [])
      .filter(r => (r.relevance_score || 0) >= similarityThreshold)
      .map(r => ({ ...r, root_type: 'Entity' }))

    for (const row of [...termRows, ...entityRows]) {
      const uuid = row.object_node?.uuid
      if (uuid && !seenUuids.has(uuid)) {
        seenUuids.add(uuid)
        allRows.push(row)
      }
    }
  }

  return {
    rows: allRows,
    durationMs: Date.now() - startTime,
  }
}
