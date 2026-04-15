/**
 * 公共检索方法 - 固定 2 跳路径检索（Entity/Term → Topic → Clause）
 *
 * Hit Test 和 AI Q&A 共用，确保检索行为一致。
 *
 * 优化：单次后端请求同时搜索 Entity 和 Term，共享 embedding 计算，
 * 减少网络往返和重复计算。
 */
import { searchEntityTopicClause } from '../api/graph'

// 简单缓存：相同查询 30 秒内直接返回，减少重复请求
const _cache = new Map()
const CACHE_TTL = 30000

/**
 * 执行 2 跳路径检索（单次 API 调用）
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

  const cacheKey = `${graphIds.join(',')}|${query}|${limit}|${similarityThreshold}|${rootTypes.join(',')}`
  const cached = _cache.get(cacheKey)
  if (cached && Date.now() - cached.ts < CACHE_TTL) {
    return { rows: cached.rows, durationMs: 0 }
  }

  const startTime = Date.now()
  const allRows = []
  const seenUuids = new Set()

  for (const gid of graphIds) {
    // 单次请求同时搜索 Entity 和 Term，后端共享 embedding 计算
    const res = await searchEntityTopicClause({
      graph_id: gid,
      query,
      limit,
      root_types: rootTypes,
    })

    if (res?.success) {
      const rows = (res.data.rows || [])
        .filter(r => (r.relevance_score || 0) >= similarityThreshold)
        // 标注 root_type（后端 Term 优先排列，通过 labels 判断）
        .map(r => ({
          ...r,
          root_type: r.object_node?.labels?.includes('Term') ? 'Term' : 'Entity',
        }))

      for (const row of rows) {
        const uuid = row.object_node?.uuid
        if (uuid && !seenUuids.has(uuid)) {
          seenUuids.add(uuid)
          allRows.push(row)
        }
      }
    }
  }

  _cache.set(cacheKey, { rows: allRows, ts: Date.now() })
  return {
    rows: allRows,
    durationMs: Date.now() - startTime,
  }
}
