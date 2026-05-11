<template>
  <div class="chunk-analysis-view">
    <!-- Header -->
    <header class="ca-header">
      <div class="header-left">
        <div class="brand" @click="router.push('/')">Knowledge EviGraph</div>
      </div>

      <div class="header-right">
        <StepNavigator
          :projectId="currentProjectId"
          :projectStatus="analysisStatus"
          :currentStep="1"
        />
        <div class="step-divider"></div>
        <span class="status-indicator" :class="'status-' + analysisStatus">
          <span class="dot" :style="{ background: statusDotColor }"></span>
          {{ statusLabel }}
        </span>

        <button
          v-if="analysisStatus === 'graph_chunked' || analysisStatus === 'graph_completed'"
          class="goto-build-btn"
          @click="goToGraphBuild">
          进入图谱构建 →
        </button>
      </div>
    </header>

    <!-- Main Content: Left PDF + Right Analysis -->
    <main class="ca-main">
      <!-- ========== LEFT: PDF Preview ========== -->
      <div class="pdf-panel">
        <!-- PDF 工具栏 -->
        <div class="pdf-toolbar">
          <div class="pdf-toolbar-title">
            <span v-if="pdfFileName">{{ pdfFileName }}</span>
            <span v-else class="pdf-placeholder">暂无PDF文件</span>
          </div>
          <div class="pdf-toolbar-actions">
            <!-- MinerU 标注开关 -->
            <button
              class="mineru-toggle"
              :class="{ active: mineruMode }"
              @click="toggleMineruMode"
              title="切换 MinerU 布局标注">
              {{ mineruMode ? '📊 分析模式' : '🔵 MinerU 标注' }}
            </button>
          </div>
          <div class="pdf-page-count" v-if="totalPages > 0">
            {{ totalPages }} 页
          </div>
        </div>

        <!-- PDF 渲染区域 -->
        <div class="pdf-body" ref="viewerContainer">
          <!-- 空白状态 -->
          <div v-if="!pdfUrl" class="pdf-empty">
            <div class="pdf-empty-icon">📄</div>
            <p>请等待PDF文件加载...</p>
            <p class="pdf-empty-hint">分析过程中的条文标注将实时显示在此区域</p>
          </div>

          <!-- 全量 PDF：每页一个 canvas + SVG 叠加层 -->
          <div v-if="pdfUrl && renderedPages.length > 0" class="pdf-scroll-container">
            <div
              v-for="rp in renderedPages"
              :key="rp.pageNum"
              class="pdf-page-wrapper"
              :data-page="rp.pageNum"
            >
              <canvas
                :ref="el => setCanvasRef(el, rp.pageNum)"
                class="pdf-canvas"
              ></canvas>

              <!-- SVG BBox 叠加层（每个页面独立） -->
              <svg
                v-if="getPageAnnotations(rp.pageNum).length > 0"
                class="bbox-overlay"
                :viewBox="`0 0 ${rp.pageWidth} ${rp.pageHeight}`"
                :style="{ width: rp.canvasWidth + 'px', height: rp.canvasHeight + 'px', top: '0', left: '0' }"
              >
                <template v-for="ann in getPageAnnotations(rp.pageNum)" :key="ann.clauseId">
                  <rect
                    v-if="ann.isMineru"
                    :x="ann.bbox[0]"
                    :y="ann.bbox[1]"
                    :width="ann.bbox[2] - ann.bbox[0]"
                    :height="ann.bbox[3] - ann.bbox[1]"
                    class="bbox-rect bbox-mineru"
                    :class="{ 'bbox-active': highlightedClauseId === ann.clauseId }"
                    :style="{ stroke: categoryIdColor(ann.categoryId) }"
                    @click="onMineruBboxClick(ann)"
                  />
                  <rect
                    v-else
                    :x="ann.bbox[0]"
                    :y="ann.bbox[1]"
                    :width="ann.bbox[2] - ann.bbox[0]"
                    :height="ann.bbox[3] - ann.bbox[1]"
                    class="bbox-rect"
                    :class="['bbox-' + ann.type, { 'bbox-active': highlightedClauseId === ann.clauseId }]"
                    @click="onBboxClick(ann)"
                  />
                </template>
              </svg>
            </div>
          </div>

          <!-- 加载状态 -->
          <div v-if="pdfLoading" class="pdf-loading">
            <div class="spinner"></div>
            <span>正在渲染全部 {{ totalPages }} 页...</span>
          </div>
        </div>

        <!-- 图例 -->
        <div class="bbox-legend" v-if="pdfUrl">
          <template v-if="!mineruMode">
            <span class="legend-item">
              <span class="legend-dot clause-dot"></span> Clause
            </span>
            <span class="legend-item">
              <span class="legend-dot element-dot"></span> Element
            </span>
            <span class="legend-item">
              <span class="legend-dot term-dot"></span> Term
            </span>
          </template>
          <template v-else>
            <span class="legend-item">
              <span class="legend-dot" style="border-color:#ea580c;background:#fff7ed"></span> 标题
            </span>
            <span class="legend-item">
              <span class="legend-dot" style="border-color:#2563eb;background:#eff6ff"></span> 正文
            </span>
            <span class="legend-item">
              <span class="legend-dot" style="border-color:#16a34a;background:#f0fdf4"></span> 表格
            </span>
            <span class="legend-item">
              <span class="legend-dot" style="border-color:#9333ea;background:#f3e8ff"></span> 图片
            </span>
            <span class="legend-item">
              <span class="legend-dot" style="border-color:#ca8a04;background:#fef9c3"></span> 公式
            </span>
            <span v-if="mineruSummary" class="legend-count">
              {{ mineruChunks.length }} 块
            </span>
          </template>
        </div>
      </div>

      <!-- ========== RIGHT: Analysis Panel ========== -->
      <div class="analysis-panel">
        <!-- ===== MinerU 解析结果面板 ===== -->
        <div v-if="mineruMode" class="mineru-panel">
          <div class="mineru-panel-header">
            <div class="mineru-panel-title">
              <span>🧠 MinerU 布局解析</span>
              <span v-if="mineruSummary" class="mineru-summary-badges">
                <span class="badge">{{ mineruSummary.total_pages }} 页</span>
                <span class="badge">{{ mineruChunks.length }} 块</span>
              </span>
            </div>
            <button class="re-annotate-btn" @click="handleReAnnotate" :disabled="reAnnotating">
              {{ reAnnotating ? '标注中...' : '重新标注' }}
            </button>
            <button class="close-mineru-btn" @click="toggleMineruMode">×</button>
          </div>

          <!-- 布局统计 -->
          <div class="mineru-layout-info">
            <div class="layout-info-row">
              <span class="info-label">标题块:</span>
              <span class="info-value">{{ mineruChunks.filter(c => c.category_id === 0).length }}</span>
              <span class="info-label" style="margin-left:12px">正文块:</span>
              <span class="info-value">{{ mineruChunks.filter(c => c.category_id === 1).length }}</span>
            </div>
            <div class="layout-info-row">
              <span class="info-label">表格块:</span>
              <span class="info-value">{{ mineruChunks.filter(c => c.category_id === 2 || c.category_id === 4).length }}</span>
              <span class="info-label" style="margin-left:12px">图片块:</span>
              <span class="info-value">{{ mineruChunks.filter(c => c.category_id === 3 || c.category_id === 5).length }}</span>
            </div>
          </div>

          <!-- Chunks 列表（联动 PDF 标注） -->
          <div class="mineru-chunk-list" ref="mineruchunkListRef">

            <div
              v-for="chunk in mineruChunks"
              :key="chunk.chunk_id"
              class="mineru-chunk-item"
              :class="{ 'chunk-active': mineruSelectedChunk?.chunk_id === chunk.chunk_id }"
              :data-chunk-id="chunk.chunk_id"
              @click="onMineruChunkClick(chunk)"
            >
              <div class="chunk-item-header">
                <span
                  class="chunk-type-badge"
                  :style="{ background: categoryIdColor(chunk.category_id || 1) + '22', color: categoryIdColor(chunk.category_id || 1) }"
                >{{ categoryIdLabel(chunk.category_id) }}</span>
                <span class="chunk-block-type">{{ chunk.block_type || '' }}</span>
                <span class="chunk-page">P{{ (chunk.page_idx || 0) + 1 }}</span>
                <span class="chunk-id">{{ chunk.chunk_id }}</span>
              </div>
              <div class="chunk-item-content">{{ chunk.content }}</div>
            </div>
          </div>

          <!-- 选中块详情 -->
          <div v-if="mineruSelectedChunk" class="mineru-chunk-detail">
            <div class="detail-header">
              <span class="detail-type">{{ categoryIdLabel(mineruSelectedChunk.category_id) }}</span>
              <span class="detail-block-type">{{ mineruSelectedChunk.block_type || '' }}</span>
              <span class="detail-page">页 {{ (mineruSelectedChunk.page_idx || 0) + 1 }}</span>
            </div>
            <div class="detail-content">{{ mineruSelectedChunk.content }}</div>

            <div v-if="mineruSelectedChunk.type === 'table' && mineruSelectedChunk.table_caption" class="detail-table-caption">
              <span class="detail-table-caption-label">表头：</span>
              <span>{{ mineruSelectedChunk.table_caption }}</span>
            </div>
            <!-- 表格内容 -->
            <div v-if="mineruSelectedChunk.type === 'table' && mineruSelectedChunk.table_content" class="detail-table-content">
              <div class="detail-table-label">表格内容</div>
              <div class="detail-table-markdown" v-html="mineruSelectedChunk.table_content"></div>
            </div>
            <div v-if="mineruSelectedChunk.type === 'table' && mineruSelectedChunk.table_footnote" class="detail-table-footnote">
              <div class="detail-table-label">表注</div>
              <div class="detail-table-footnote-text">{{ mineruSelectedChunk.table_footnote }}</div>
            </div>
            <!-- 图片块渲染 -->
            <div v-if="mineruSelectedChunk.type === 'image'" class="detail-image-section">
              <div class="detail-image-caption" v-if="mineruSelectedChunk.image_caption">
                <span class="detail-image-caption-label">图注：</span>
                <span>{{ mineruSelectedChunk.image_caption }}</span>
              </div>
              <div class="detail-image-preview" v-if="mineruSelectedChunk.image_content">
                <img :src="mineruSelectedChunk.image_content" :alt="mineruSelectedChunk.image_caption || '图片'" style="max-width: 300px; max-height: 200px; object-fit: contain; border: 1px solid #ddd; border-radius: 4px;" />
              </div>
              <div class="detail-image-meta" v-if="mineruSelectedChunk.image_img_path">
                <span class="detail-image-meta-label">文件：</span>
                <span class="detail-image-meta-value">{{ mineruSelectedChunk.image_img_path }}</span>
              </div>
            </div>
            <div v-if="mineruSelectedChunk.bbox_viewport || mineruSelectedChunk.bbox_pdf" class="detail-bbox">
              <div class="detail-bbox-line">page index: {{ (mineruSelectedChunk.page_idx || 0) + 1 }}</div>
              <div class="detail-bbox-line" v-if="mineruSelectedChunk.bbox_viewport">bbox_viewport: [{{ mineruSelectedChunk.bbox_viewport.join(', ') }}]</div>
              <div class="detail-bbox-line" v-if="mineruSelectedChunk.bbox_pdf">bbox_pdf: [{{ mineruSelectedChunk.bbox_pdf.join(', ') }}]</div>
            </div>
          </div>
        </div>

        <!-- 统计摘要 + 章节树（与 MinerU 面板互斥） -->
        <div v-if="!mineruMode" class="analysis-content">
          <div class="analysis-panel-header">
            <div class="analysis-panel-title">
              <span>🧠 智能分析</span>
            </div>
            <!-- 分析进度条 -->
            <div v-if="analysisStatus === 'graph_chunking'" class="inline-progress">
              <div class="inline-progress-bar">
                <div class="inline-progress-fill" :style="{ width: progressPercent + '%' }"></div>
              </div>
              <span class="inline-progress-text">{{ progressPercent }}%</span>
            </div>
            <button class="re-analyse-btn" @click="handleResetChunking">
              <span v-if="!starting">🔄 重新分析</span>
              <span v-else class="spinner-sm"></span>
            </button>
          </div>

          <!-- 章节树 -->
          <div class="chapter-tree" v-if="analysisData">
          <div class="tree-header">
            <span>章节树</span>
            <div class="tree-stats" v-if="analysisData.summary">
              <span class="stat-badge clauses">
                <span class="stat-icon">⚖️</span>
                <span class="stat-num">{{ analysisData.summary.total_clauses }}</span>
                <span class="stat-label">条款</span>
              </span>
              <span class="stat-badge terms">
                <span class="stat-icon">📖</span>
                <span class="stat-num">{{ analysisData.summary.total_terms }}</span>
                <span class="stat-label">术语</span>
              </span>
              <span class="stat-badge entities">
                <span class="stat-icon">💎</span>
                <span class="stat-num">{{ analysisData.summary.total_entities }}</span>
                <span class="stat-label">实体</span>
              </span>
            </div>
            <div class="tree-actions">
              <button class="expand-all-btn" @click="toggleAllChapters">
                {{ allExpanded ? '全部收起' : '全部展开' }}
              </button>
            </div>
          </div>

          <div class="tree-body">
            <div v-for="chapter in analysisData.chapter_tree" :key="chapter.chapter.chapter_number"
                 class="chapter-item">
              <div class="chapter-header"
                   :class="{ expanded: expandedChapters[chapter.chapter.chapter_number] }"
                   @click="toggleChapter(chapter.chapter.chapter_number)">
                <span class="chapter-toggle">{{ expandedChapters[chapter.chapter.chapter_number] ? '▼' : '▶' }}</span>
                <span class="chapter-title">
                  {{ chapter.chapter.chapter_number }}. {{ chapter.chapter.title }}
                </span>
                <span class="chapter-meta">[{{ chapter.chapter.clause_count }}条]</span>
              </div>

              <div class="clause-list" v-show="expandedChapters[chapter.chapter.chapter_number]">
                <template v-for="clause in chapter.clauses" :key="clause.clause_id">
                  <!-- 两级锚点：只渲染没有 parent_container_id 的顶级条款 -->
                  <div v-if="!clause.parent_container_id"
                       class="clause-item"
                       :class="{ 'clause-active': highlightedClauseId === clause.clause_id, 'clause-container': clause.is_clause_container }">
                    <div class="clause-row" @click="handleClauseClick(clause)">
                      <span class="clause-id" :class="{ 'container-id': clause.is_clause_container }">{{ clause.clause_id }}</span>
                      <span class="clause-title">{{ clause.clause_title || clause.content?.substring(0, 40) + '...' }}</span>
                      <span v-if="clause.is_clause_container" class="container-badge">容器</span>
                    </div>

                    <!-- 条文详情 -->
                    <div class="clause-detail" v-if="expandedClauseId === clause.clause_id">
                      <!-- PDF 位置信息（支持多 bbox） -->
                      <div class="entity-row" v-if="clause.bboxs?.length || clause.page || clause.pdf_location?.page">
                        <span class="entity-label" style="color:#6b7280">📍 位置</span>
                        <template v-if="clause.bboxs?.length">
                          <span v-for="(item, idx) in clause.bboxs" :key="idx" class="entity-tag" style="background:#f3f4f6;color:#374151;border-color:#d1d5db">
                            P{{ item[0] }}: [
                            {{ item.slice(1,3).join(',') }},
                            {{ item.slice(3).join(',') }}
                            ]
                          </span>
                        </template>
                        <template v-else>
                          <span class="entity-tag" style="background:#f3f4f6;color:#374151;border-color:#d1d5db">
                            第 {{ clause.pdf_location?.page ?? clause.page }} 页
                          </span>
                          <span v-if="clause.pdf_location?.bbox || clause.bbox" class="entity-tag" style="background:#f3f4f6;color:#374151;border-color:#d1d5db;font-family:monospace;font-size:10px">
                            bbox: [
                            {{ (clause.pdf_location?.bbox ?? clause.bbox)?.slice(0,2).join(', ') }} ,
                            {{ (clause.pdf_location?.bbox ?? clause.bbox)?.slice(2).join(', ') }}
                            ]
                          </span>
                        </template>
                      </div>

                      <!-- Term -->
                      <div class="entity-row" v-if="clause.terms?.length || editingClauseId === clause.clause_id">
                        <span class="entity-label term-label">🔵 Term</span>
                        <div class="entity-tags">
                          <template v-for="(t, i) in (editingClauseId === clause.clause_id ? editingTerms : clause.terms)" :key="i">
                            <span v-if="typeof t === 'string'" class="entity-tag term-tag">{{ t }}</span>
                            <span v-else class="term-item" @click.stop="toggleTermDef(t.term_name)">
                              <span class="entity-tag term-tag" :class="{ active: expandedTermDefs.has(t.term_name) }">
                                {{ t.term_name }}
                              </span>
                              <span v-if="t.definition" class="term-def-arrow">{{ expandedTermDefs.has(t.term_name) ? '▲' : '▼' }}</span>
                              <div v-if="expandedTermDefs.has(t.term_name) && t.definition" class="term-definition">
                                <span class="def-connector">(解释)</span>
                                {{ t.definition }}
                              </div>
                            </span>
                          </template>
                        </div>
                        <button class="edit-btn" @click.stop="startEditEntity(clause, 'terms')">
                          {{ editingClauseId === clause.clause_id ? '取消' : '编辑' }}
                        </button>
                      </div>
                      <div v-if="editingClauseId === clause.clause_id && editingField === 'terms'" class="entity-editor">
                        <input v-model="editingValue" class="entity-input"
                               placeholder="输入Term，多个用逗号分隔" @keyup.enter="saveEntity(clause.clause_id, 'terms')"/>
                        <button class="save-btn" @click="saveEntity(clause.clause_id, 'terms')">保存</button>
                      </div>

                      <!-- Condition -->
                      <div class="entity-row" v-if="clause.conditions?.length || editingClauseId === clause.clause_id">
                        <span class="entity-label cond-label">🟡 Cond</span>
                        <div class="entity-tags">
                          <span v-for="(c, i) in (editingClauseId === clause.clause_id ? editingConditions : clause.conditions)"
                                :key="i" class="entity-tag cond-tag">{{ c }}</span>
                        </div>
                        <button class="edit-btn" @click.stop="startEditEntity(clause, 'conditions')">
                          {{ editingClauseId === clause.clause_id ? '取消' : '编辑' }}
                        </button>
                      </div>
                      <div v-if="editingClauseId === clause.clause_id && editingField === 'conditions'" class="entity-editor">
                        <input v-model="editingValue" class="entity-input"
                               placeholder="输入条件，多个用逗号分隔" @keyup.enter="saveEntity(clause.clause_id, 'conditions')"/>
                        <button class="save-btn" @click="saveEntity(clause.clause_id, 'conditions')">保存</button>
                      </div>

                      <!-- Action -->
                      <div class="entity-row" v-if="clause.actions?.length || editingClauseId === clause.clause_id">
                        <span class="entity-label action-label">🔷 Act</span>
                        <div class="entity-tags">
                          <span v-for="(a, i) in (editingClauseId === clause.clause_id ? editingActions : clause.actions)"
                                :key="i" class="entity-tag action-tag">{{ a }}</span>
                        </div>
                        <button class="edit-btn" @click.stop="startEditEntity(clause, 'actions')">
                          {{ editingClauseId === clause.clause_id ? '取消' : '编辑' }}
                        </button>
                      </div>
                      <div v-if="editingClauseId === clause.clause_id && editingField === 'actions'" class="entity-editor">
                        <input v-model="editingValue" class="entity-input"
                               placeholder="输入动作，多个用逗号分隔" @keyup.enter="saveEntity(clause.clause_id, 'actions')"/>
                        <button class="save-btn" @click="saveEntity(clause.clause_id, 'actions')">保存</button>
                      </div>

                      <!-- Component -->
                      <div class="entity-row" v-if="clause.components?.length">
                        <span class="entity-label comp-label">🟣 Comp</span>
                        <div class="entity-tags">
                          <span v-for="(c, i) in clause.components" :key="i" class="entity-tag comp-tag">{{ c }}</span>
                        </div>
                      </div>

                      <!-- 语义三元组 -->
                      <div class="triplets-section" v-if="clause.triplets?.length">
                        <div class="triplet-label">📌 语义三元组</div>
                        <div v-for="(triplet, ti) in clause.triplets" :key="ti" class="triplet-row">
                          <span class="triplet-comp">{{ triplet.component || '—' }}</span>
                          <span class="triplet-arrow">—{{ triplet.requirement?.[0]?.toUpperCase() || 'M' }}→</span>
                          <span class="triplet-obj">{{ triplet.obj || '—' }}</span>
                          <span v-if="triplet.condition" class="triplet-cond">@ {{ triplet.condition }}</span>
                        </div>
                      </div>

                      <!-- 知识实体 -->
                      <div class="clause-entities-section" v-if="clause.topics?.length || clause.related_elements?.length || clause.images?.length || clause.referenced_tables?.length">
                        <!-- 多主题分组展示 -->
                        <template v-if="clause.topics?.length > 1">
                          <div class="entity-section-label">📝 主题分析</div>
                          <div v-for="(tp, ti) in clause.topics" :key="'topic-' + ti" class="topic-group">
                            <div class="topic-group-header">
                              <span class="topic-group-label">主题 {{ ti + 1 }}</span>
                              <span class="topic-group-title">{{ tp.topic }}</span>
                            </div>
                            <div class="topic-group-entities">
                              <div class="entity-item-row" v-for="(ent, ei) in tp.entities" :key="'t-ent-' + ti + '-' + ei">
                                <span class="entity-type-tag">noun</span>
                                <span class="entity-key">{{ ent }}</span>
                              </div>
                              <div v-if="!tp.entities?.length" class="topic-empty">（无实体）</div>
                            </div>
                          </div>
                        </template>
                        <!-- 单主题展示 -->
                        <template v-else-if="clause.topics?.length === 1">
                          <div v-if="clause.topics[0].topic" class="clause-topic-row collapsible" @click.stop="toggleEntityExpand(clause.clause_id)">
                            <span class="topic-toggle-icon">{{ expandedEntityClauseIds.has(clause.clause_id) ? '▼' : '▶' }}</span>
                            <span class="topic-label">📝 摘要</span>
                            <span class="topic-content">{{ clause.topics[0].topic }}</span>
                          </div>
                          <div v-show="expandedEntityClauseIds.has(clause.clause_id)" class="entity-expand-panel">
                            <div v-if="clause.topics[0].entities?.length" class="entity-section-label">📎 知识实体</div>
                            <div class="entity-item-row" v-for="(elem, ei) in clause.topics[0].entities" :key="'entity-' + ei">
                              <template v-if="typeof elem === 'string'">
                                <span class="entity-type-tag">noun</span>
                                <span class="entity-key">{{ elem }}</span>
                              </template>
                              <template v-else>
                                <span class="entity-type-tag">{{ elem.element_type || 'noun_entity' }}</span>
                                <span class="entity-key">{{ elem.key }}</span>
                                <span v-if="elem.value" class="entity-value">= {{ elem.value }}</span>
                                <span v-if="elem.unit" class="entity-unit">{{ elem.unit }}</span>
                              </template>
                            </div>
                          </div>
                        </template>
                        <!-- 图片及 VLM 分析结果 -->
                        <div v-if="clause.images?.length" class="clause-images-section">
                          <div class="entity-section-label">🖼️ 图片分析</div>
                          <div v-for="(img, ii) in clause.images" :key="'img-' + ii" class="clause-image-item">
                            <div class="media-type-badge media-type-image">图片</div>
                            <div v-if="img.caption" class="image-caption">{{ img.caption }}</div>
                            <div v-if="img.content" class="image-preview">
                              <img :src="img.content" :alt="img.caption || '图片'" style="max-width: 200px; max-height: 150px; object-fit: contain; border: 1px solid #ddd; border-radius: 4px;" />
                            </div>
                            <div v-if="img.img_vlm_content" class="vlm-content">
                              <span class="vlm-label">VLM 描述：</span>
                              <span class="vlm-text">{{ img.img_vlm_content }}</span>
                            </div>
                            <div v-else-if="img.vlm_status && img.vlm_status !== 'ok'" class="vlm-status-error">
                              <span class="vlm-label">VLM 状态：</span>
                              <span class="vlm-error">{{ img.vlm_status }}</span>
                            </div>
                          </div>
                        </div>
                        <!-- 引用表格图片 -->
                        <div v-if="clause.referenced_tables?.length" class="clause-tables-section">
                          <div class="entity-section-label">📊 引用表格</div>
                          <template v-for="(tb, ti2) in clause.referenced_tables" :key="'tb-' + ti2">
                            <div v-if="tb && typeof tb === 'object' && (tb.table_image_base64_content || tb.table_id || tb.caption)" class="clause-image-item">
                              <div class="media-type-badge media-type-table">表格</div>
                              <div v-if="tb.table_id || tb.caption" class="image-caption">
                                <span v-if="tb.table_id" class="table-id">{{ tb.table_id }}</span>
                                <span v-if="tb.caption"> {{ tb.caption }}</span>
                              </div>
                              <div v-if="tb.table_image_base64_content" class="image-preview">
                                <img :src="tb.table_image_base64_content" :alt="tb.table_id || tb.caption || '表格'" style="max-width: 240px; max-height: 180px; object-fit: contain; border: 1px solid #ddd; border-radius: 4px;" />
                              </div>
                            </div>
                          </template>
                        </div>
                      </div>
                    </div>

                    <!-- 子条款（挂载到容器下） -->
                    <div v-if="clause.is_clause_container && clause.child_clauses?.length" class="child-clauses">
                      <div v-for="childId in clause.child_clauses" :key="childId"
                           class="clause-item clause-child"
                           :class="{ 'clause-active': highlightedClauseId === childId }">
                        <div class="clause-row" @click.stop="handleClauseClick(getClauseById(chapter, childId))">
                          <span class="clause-id child-id">{{ getClauseById(chapter, childId)?.clause_id }}</span>
                          <span class="clause-title">{{ getClauseById(chapter, childId)?.clause_title || getClauseById(chapter, childId)?.content?.substring(0, 40) + '...' }}</span>
                        </div>
                        <!-- 子条款详情（简化版） -->
                        <div class="clause-detail child-detail" v-if="expandedClauseId === childId">
                          <div class="entity-row" v-if="getClauseById(chapter, childId)?.page">
                            <span class="entity-label" style="color:#6b7280">📍 位置</span>
                            <span class="entity-tag" style="background:#f3f4f6;color:#374151;border-color:#d1d5db">
                              第 {{ getClauseById(chapter, childId)?.page }} 页
                            </span>
                          </div>
                          <div v-if="getClauseById(chapter, childId)?.topics?.[0]?.topic" class="clause-topic-row collapsible child-topic" @click.stop="toggleEntityExpand(childId)">
                            <span class="topic-toggle-icon">{{ expandedEntityClauseIds.has(childId) ? '▼' : '▶' }}</span>
                            <span class="topic-label">📝 摘要</span>
                            <span class="topic-content">{{ getClauseById(chapter, childId)?.topics?.[0]?.topic }}</span>
                          </div>
                          <div v-show="expandedEntityClauseIds.has(childId)" class="entity-expand-panel">
                            <div v-if="getClauseById(chapter, childId)?.topics?.[0]?.entities?.length" class="entity-section-label">📎 知识实体</div>
                            <div class="entity-item-row" v-for="(elem, ei) in getClauseById(chapter, childId)?.topics?.[0]?.entities" :key="'child-entity-' + ei">
                              <span class="entity-type-tag">{{ elem.element_type || 'noun' }}</span>
                              <span class="entity-key">{{ typeof elem === 'string' ? elem : elem.key }}</span>
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </template>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
    </main>

    <!-- 解析方法选择模态窗口 -->
    <div v-if="showParseMethodModal" class="modal-overlay" @click.self="showParseMethodModal = false">
      <div class="modal-card">
        <div class="modal-header">
          <h3>选择解析方法</h3>
          <button class="modal-close" @click="showParseMethodModal = false">×</button>
        </div>
        <div class="modal-body">
          <p class="modal-desc">请选择 MinerU PDF 解析方法：</p>
          <div class="method-options">
            <label class="method-option" :class="{ active: selectedParseMethod === 'auto' }">
              <input type="radio" v-model="selectedParseMethod" value="auto" />
              <div class="method-content">
                <span class="method-name">Auto</span>
                <span class="method-desc">自动选择最佳解析方式</span>
              </div>
            </label>
            <label class="method-option" :class="{ active: selectedParseMethod === 'ocr' }">
              <input type="radio" v-model="selectedParseMethod" value="ocr" />
              <div class="method-content">
                <span class="method-name">OCR</span>
                <span class="method-desc">基于 OCR 的解析方式（默认）</span>
              </div>
            </label>
          </div>
        </div>
        <div class="modal-footer">
          <button class="modal-btn cancel" @click="showParseMethodModal = false">取消</button>
          <button class="modal-btn confirm" @click="confirmReAnnotate">确认</button>
        </div>
      </div>
    </div>

    <!-- 章节匹配模式选择弹窗 -->
    <template v-if="showChapterPatternModal">
      <div class="modal-backdrop" @click="showChapterPatternModal = false"></div>
      <div class="modal-wrapper" @keydown.esc="showChapterPatternModal = false" tabindex="-1">
        <div class="modal-card anchor-modal">
          <div class="modal-header">
            <h3>智能分析 - 章节模式选择</h3>
            <button class="modal-close" @click="showChapterPatternModal = false">×</button>
          </div>
          <div class="modal-body">
          <div v-if="anchorAutoRecommended" class="anchor-recommendation">
            <div class="anchor-rec-title">🎯 自动推荐结果</div>
            <div class="anchor-rec-body">
              章节 <strong>{{ chapterAnchor }}</strong>
              <span class="anchor-rec-sep">·</span>
              容器 <strong>{{ clauseContainer }}</strong>
            </div>
            <div v-if="anchorReason" class="anchor-rec-reason">{{ anchorReason }}</div>
            <div v-if="useMineruTitles" class="anchor-title-mode-hint">
              📄 检测到文档无标准条款层级（x.x.x），已自动切换为 <strong>MinerU Title 分段模式</strong>，将以文档标题作为章节切分。
            </div>
          </div>

          <div class="pattern-section">
            <div class="pattern-section-title">章节锚点（一级父节点）</div>
            <div class="pattern-options">
              <label class="pattern-option" :class="{ active: chapterAnchor === 'x' }">
                <input type="radio" v-model="chapterAnchor" value="x" />
                <span class="pattern-name">x</span>
                <span class="pattern-desc">2 术语</span>
              </label>
              <label class="pattern-option" :class="{ active: chapterAnchor === 'x.x' }">
                <input type="radio" v-model="chapterAnchor" value="x.x" />
                <span class="pattern-name">x.x</span>
                <span class="pattern-desc">2.1 配电</span>
              </label>
              <label class="pattern-option" :class="{ active: chapterAnchor === 'x.x.x' }">
                <input type="radio" v-model="chapterAnchor" value="x.x.x" />
                <span class="pattern-name">x.x.x</span>
                <span class="pattern-desc">2.1.1 导体</span>
              </label>
              <label class="pattern-option" :class="{ active: chapterAnchor === 'x.x.x.x' }">
                <input type="radio" v-model="chapterAnchor" value="x.x.x.x" />
                <span class="pattern-name">x.x.x.x</span>
                <span class="pattern-desc">2.1.1.1</span>
              </label>
            </div>
          </div>

          <div class="pattern-section">
            <div class="pattern-section-title">最小条款容器锚点（二级）</div>
            <div class="pattern-options">
              <label class="pattern-option" :class="{ active: clauseContainer === 'x.x', disabled: anchorDepth > 2 }">
                <input type="radio" v-model="clauseContainer" value="x.x" :disabled="anchorDepth > 2" />
                <span class="pattern-name">x.x</span>
                <span class="pattern-desc">2.1</span>
              </label>
              <label class="pattern-option" :class="{ active: clauseContainer === 'x.x.x', disabled: anchorDepth > 3 }">
                <input type="radio" v-model="clauseContainer" value="x.x.x" :disabled="anchorDepth > 3" />
                <span class="pattern-name">x.x.x</span>
                <span class="pattern-desc">2.1.1</span>
              </label>
              <label class="pattern-option" :class="{ active: clauseContainer === 'x.x.x.x', disabled: anchorDepth > 4 }">
                <input type="radio" v-model="clauseContainer" value="x.x.x.x" :disabled="anchorDepth > 4" />
                <span class="pattern-name">x.x.x.x</span>
                <span class="pattern-desc">2.1.1.1</span>
              </label>
              <label class="pattern-option" :class="{ active: clauseContainer === 'x.x.x.x.x', disabled: anchorDepth > 5 }">
                <input type="radio" v-model="clauseContainer" value="x.x.x.x.x" :disabled="anchorDepth > 5" />
                <span class="pattern-name">x.x.x.x.x</span>
                <span class="pattern-desc">2.1.1.1.1</span>
              </label>
            </div>
          </div>

          <div class="pattern-hint">
            章节锚点决定一级节点，容器锚点决定二级分组；无匹配容器时条款直接挂载到章节下。
          </div>
        </div>
        <div class="modal-footer">
          <button class="modal-btn cancel" @click="showChapterPatternModal = false">取消</button>
          <button class="modal-btn confirm" @click="confirmChapterPattern">确认并开始分析</button>
        </div>
      </div>
    </div>
    </template>

    <!-- 实时日志抽屉 -->
    <div class="log-drawer" :class="{ open: logDrawerOpen }">
      <div class="log-drawer-header" @click="logDrawerOpen = !logDrawerOpen">
        <span>实时日志</span>
        <span class="log-toggle">{{ logDrawerOpen ? '▼' : '▲' }}</span>
      </div>
      <div class="log-drawer-body" v-if="logDrawerOpen" ref="logScrollEl">
        <div v-for="(log, i) in realtimeLogs" :key="i" class="log-line" :class="logClass(log)">
          {{ log }}
        </div>
        <div v-if="!realtimeLogs.length" class="log-empty">暂无日志</div>
      </div>
    </div>

    <!-- 开始分析按钮（首次） -->
    <div v-if="showStartButton" class="start-overlay">
      <div class="start-card">
        <div class="start-icon">📊</div>
        <h3>智能分块标注分析</h3>
        <p>项目 <strong>{{ projectName }}</strong> 已上传完成，开始执行 LLM 智能分块与知识实体标注。</p>
        <div class="start-actions">
          <button class="start-btn" @click="handleStartChunking" :disabled="starting || analysisStatus === 'graph_chunking'">
            <span v-if="!(starting || analysisStatus === 'graph_chunking')">🚀 开始智能分析</span>
            <span v-else class="spinner-sm"></span>
          </button>
          <button class="reset-btn" @click="handleResetChunking" :disabled="starting || (analysisStatus === 'graph_chunking' && progressPercent < 100)">
            <span v-if="!starting">重置并重新分析</span>
            <span v-else class="spinner-sm"></span>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  generateOntology,
  getProject,
  getChunkAnalysis,
  getChunkProgress,
  startChunking,
  inferChunkAnchors,
  updateClauseEntity,
  getTaskStatus,
  getMineruChunks,
  getTaskEventsURL,
  reAnnotateMineru
} from '../api/graph'
import StepNavigator from '../components/StepNavigator.vue'
import { getPendingUpload, clearPendingUpload } from '../store/pendingUpload'

const props = defineProps({
  projectId: { type: String, required: true }
})

const router = useRouter()

// ============================================================================
// 状态
// ============================================================================

// 项目信息
const projectName = ref('')
const currentProjectId = ref('')
const analysisStatus = ref('')
const taskId = ref(null)

// PDF 渲染
const pdfUrl = ref('')
const pdfFileName = ref('')

const pdfjsLib = ref(null)
const viewerContainer = ref(null)
const pdfLoading = ref(false)
const totalPages = ref(0)
const pdfDoc = ref(null)
const pdfDocUrl = ref('')
const renderedPages = ref([])  // [{pageNum, pageWidth, pageHeight, canvasWidth, canvasHeight}]
const pageCanvasMap = ref({}) // pageNum -> canvas element

// BBox 标注
const allAnnotations = ref([])
const highlightedClauseId = ref(null)

// UI 状态
const expandedChapters = ref({})
const expandedClauseId = ref(null)
const expandedEntityClauseIds = ref(new Set())  // 控制摘要-知识实体的折叠展开
const allExpanded = ref(false)
const logDrawerOpen = ref(true)
const realtimeLogs = ref([])
const showStartButton = ref(false)
const starting = ref(false)
const reAnnotating = ref(false)
const waitingForReAnnotate = ref(false)
const progressPercent = ref(0)
const hasAutoExpanded = ref(false)
let pollInterval = null
let taskSource = null
const logScrollEl = ref(null)

// 重新标注模态窗口
const showParseMethodModal = ref(false)
const selectedParseMethod = ref('auto')

// 章节匹配模式选择弹窗
const showChapterPatternModal = ref(false)
const chapterAnchor = ref('x.x')  // 章节锚点（一级父节点）
const clauseContainer = ref('x.x.x')  // 最小条款容器锚点（二级）
const anchorAutoRecommended = ref(false)
const anchorReason = ref('')
const useMineruTitles = ref(false)  // 为 true 时切换为 MinerU title 分段模式
const isResetChunking = ref(false)  // 标记当前是首次分析(false)还是重置分析(true)

const anchorDepth = computed(() => {
  const depthMap = { 'x': 1, 'x.x': 2, 'x.x.x': 3, 'x.x.x.x': 4, 'x.x.x.x.x': 5 }
  return depthMap[chapterAnchor.value] || 1
})

// 当章节锚点变深时，自动修正最小条款容器为同级或更深的合法选项
watch(chapterAnchor, (newVal) => {
  const depthMap = { 'x': 1, 'x.x': 2, 'x.x.x': 3, 'x.x.x.x': 4, 'x.x.x.x.x': 5 }
  const containerDepth = depthMap[clauseContainer.value] || 5
  const anchorDepthValue = depthMap[newVal] || 1
  if (containerDepth < anchorDepthValue) {
    const next = Object.entries(depthMap).find(([k, v]) => v >= anchorDepthValue)
    clauseContainer.value = next ? next[0] : 'x.x.x.x.x'
  }
})

// 实体编辑
const editingClauseId = ref(null)
const editingField = ref('')
const editingValue = ref('')
const editingTerms = ref([])
const expandedTermDefs = ref(new Set())  // 展开的术语定义
const editingConditions = ref([])
const editingActions = ref([])

// MinerU 解析状态
const mineruMode = ref(false)
const mineruChunks = ref([])
const mineruSelectedChunk = ref(null)
const mineruSummary = ref(null)
const mineruchunkListRef = ref(null)

// 分析数据
const analysisData = ref(null)

// ============================================================================
// 计算属性
// ============================================================================

const statusLabel = computed(() => {
  const map = {
    'created': '待上传',
    'ontology_generation': '生成中',
    'ontology_generated': '已生成',
    'graph_chunking': '分析中',
    'graph_chunked': '已完成',
    'graph_building': '建图中',
    'graph_completed': '已完成',
    'failed': '失败'
  }
  return map[analysisStatus.value] || analysisStatus.value || '未知'
})

const statusDotColor = computed(() => {
  const colorMap = {
    'graph_chunking': '#FF5722',
    'graph_chunked': '#4CAF50',
    'graph_completed': '#4CAF50',
    'graph_building': '#FF5722',
    'failed': '#F44336',
    'ontology_generation': '#FF5722',
    'ontology_generated': '#4CAF50',
    'created': '#CCC'
  }
  return colorMap[analysisStatus.value] || '#CCC'
})

// 页码 -> 标注列表 的缓存（两种模式互斥）
const pageAnnotationsCache = computed(() => {
  const cache = {}
  if (mineruMode.value) {
    // MinerU 模式：使用 chunks.json 的 bbox_viewport
    if (mineruChunks.value.length) {
      for (const c of mineruChunks.value) {
        const bbox = c.bbox_viewport || c.bbox_pdf
        if (!bbox || bbox.length < 4) continue
        const pageNum = (c.page_idx || 0) + 1
        if (!cache[pageNum]) cache[pageNum] = []
        cache[pageNum].push({
          clauseId: c.chunk_id,
          page: pageNum,
          bbox,
          type: c.type || 'text',
          categoryId: c.category_id || 1,
          isMineru: true
        })
      }
    }
  } else {
    // 智能分析模式：使用 intelligent_chunks.json 的 clause bbox
    for (const ann of allAnnotations.value) {
      if (!cache[ann.page]) cache[ann.page] = []
      cache[ann.page].push(ann)
    }
  }
  return cache
})

function getPageAnnotations(pageNum) {
  return pageAnnotationsCache.value[pageNum] || []
}

// 设置 canvas ref（用于渲染时获取 canvas 元素）
function setCanvasRef(el, pageNum) {
  if (el) {
    pageCanvasMap.value[pageNum] = el
  }
}

// ============================================================================
// 生命周期
// ============================================================================

onMounted(async () => {
  await initPdfJs()

  if (props.projectId === 'new') {
    await handleNewProject()
  } else {
    currentProjectId.value = props.projectId
    await loadExistingProject()
  }
})

watch(() => props.projectId, async (newId) => {
  if (newId && newId !== 'new' && newId !== currentProjectId.value) {
    currentProjectId.value = newId
    resetState()
    await loadExistingProject()
  }
})

onUnmounted(() => {
  if (pollInterval) { clearInterval(pollInterval); pollInterval = null }
  if (taskSource) taskSource.close()
})

function resetState() {
  hasAutoExpanded.value = false
  analysisData.value = null
  allAnnotations.value = []
  expandedChapters.value = {}
  expandedClauseId.value = null
  highlightedClauseId.value = null
  pdfUrl.value = ''
  pdfFileName.value = ''
  pdfDoc.value = null
  pdfDocUrl.value = ''
  totalPages.value = 0
  renderedPages.value = []
  mineruMode.value = false
  mineruChunks.value = []
  mineruSummary.value = null
  mineruSelectedChunk.value = null
}

// ============================================================================
// 新建项目流程
// ============================================================================

async function handleNewProject() {
  const pending = getPendingUpload()
  if (!pending.files.length) {
    router.push('/')
    return
  }

  try {
    realtimeLogs.value.push('开始上传文档...')

    const formData = new FormData()
    pending.files.forEach(f => formData.append('files', f))
    formData.append('simulation_requirement', pending.simulationRequirement)

    // 默认项目名使用第一个上传文件的文件名（去掉扩展名）
    const firstFile = pending.files[0]
    if (firstFile && firstFile.name) {
      const baseName = firstFile.name.replace(/\.[^/.]+$/, '')
      formData.append('project_name', baseName)
    }

    const res = await generateOntology(formData)
    if (res.success) {
      clearPendingUpload()
      currentProjectId.value = res.data.project_id
      projectName.value = res.data.name || res.data.project_id
      taskId.value = res.data.task_id

      router.replace({ name: 'ChunkAnalysis', params: { projectId: currentProjectId.value } })
      realtimeLogs.value.push(`项目创建成功: ${currentProjectId.value}`)

      // 加载项目（会触发 MinerU 自动解析）
      await loadExistingProject()
    } else {
      realtimeLogs.value.push(`❌ 上传失败: ${res.error}`)
    }
  } catch (err) {
    realtimeLogs.value.push(`❌ 异常: ${err.message}`)
  }
}

async function loadExistingProject() {
  try {
    const res = await getProject(currentProjectId.value)
    if (!res.success) {
      realtimeLogs.value.push(`❌ 加载项目失败: ${res.error}`)
      return
    }

    projectName.value = res.data.name || res.data.project_id
    analysisStatus.value = res.data.status
    taskId.value = res.data.graph_build_task_id

    // 查找 PDF 文件
    const files = res.data.files || []
    for (const f of files) {
      const fname = f.saved_filename || f.filename || ''
      if (fname.toLowerCase().endsWith('.pdf')) {
        pdfFileName.value = fname
        break
      }
    }

    // 并行加载 PDF 和分析数据（MinerU + 智能分析）
    const tasks = []
    if (pdfFileName.value) {
      tasks.push(loadPdf())
    }
    tasks.push(loadMineruResults())

    if (res.data.status === 'graph_chunked' || res.data.status === 'graph_completed') {
      tasks.push(loadAnalysis())
    } else if (res.data.status === 'graph_chunking') {
      tasks.push(loadAnalysis())
      if (res.data.graph_build_task_id) {
        taskId.value = res.data.graph_build_task_id
        startTaskSSE()
        startProgressPolling()
      }
    } else if (res.data.status === 'ontology_generation' || res.data.status === 'ontology_generated' || res.data.status === 'created') {
      // ontology_generation 状态下 MinerU 正在解析，需要建立 SSE 接收日志
      if (res.data.ontology_task_id) {
        taskId.value = res.data.ontology_task_id
        startTaskSSE()
      }
      // MinerU 解析完成后由用户手动触发智能分析，不自动弹出 start-card
    }

    await Promise.allSettled(tasks)
  } catch (err) {
    realtimeLogs.value.push(`❌ 加载项目失败: ${err.message}`)
  }
}

// ============================================================================
// MinerU 解析
// ============================================================================

async function loadMineruResults() {
  if (!currentProjectId.value) return
  try {
    const res = await getMineruChunks(currentProjectId.value)
    if (res && Array.isArray(res.data?.chunks)) {
      mineruChunks.value = res.data.chunks
      mineruSummary.value = res.data.summary
      mineruMode.value = mineruChunks.value.length > 0
      realtimeLogs.value.push(`✅ MinerU 解析结果已加载: ${mineruChunks.value.length} 个布局块`)
    }
  } catch (e) {
    // 静默忽略
  }
}

async function toggleMineruMode() {
  const turningOn = !mineruMode.value
  if (turningOn && mineruChunks.value.length === 0) {
    await loadMineruResults()
  } else {
    mineruMode.value = !mineruMode.value
  }
  mineruSelectedChunk.value = null
}

function onMineruBboxClick(ann) {
  highlightedClauseId.value = ann.clauseId
  const chunk = mineruChunks.value.find(c => c.chunk_id === ann.clauseId)
  mineruSelectedChunk.value = chunk
  if (chunk) {
    scrollChunkListToItem(chunk.chunk_id)
  }
}

function onMineruChunkClick(chunk) {
  if (mineruSelectedChunk.value?.chunk_id === chunk.chunk_id) {
    mineruSelectedChunk.value = null
    highlightedClauseId.value = null
    return
  }
  mineruSelectedChunk.value = chunk
  highlightedClauseId.value = chunk.chunk_id
  // 滚动 PDF 到对应页面
  const pageNum = (chunk.page_idx || 0) + 1
  scrollToPage(pageNum)
}

function scrollChunkListToItem(chunkId) {
  nextTick(() => {
    const el = mineruchunkListRef.value?.querySelector(`[data-chunk-id="${chunkId}"]`)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }
  })
}

function categoryIdLabel(catId) {
  const labels = { 0: '标题', 1: '正文', 2: '表格', 3: '图片', 4: '表格', 5: '图片', 6: '公式' }
  return labels[catId] || '正文'
}

function categoryIdColor(catId) {
  const colors = { 0: '#ea580c', 1: '#2563eb', 2: '#16a34a', 3: '#9333ea', 4: '#16a34a', 5: '#9333ea', 6: '#ca8a04' }
  return colors[catId] || '#2563eb'
}

// ============================================================================
// PDF 渲染
// ============================================================================

async function initPdfJs() {
  if (window.pdfjsLib) {
    pdfjsLib.value = window.pdfjsLib
    return
  }
  return new Promise((resolve, reject) => {
    const script = document.createElement('script')
    script.src = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js'
    script.onload = () => {
      pdfjsLib.value = window['pdfjs-dist/build/pdf']
      pdfjsLib.value.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js'
      resolve()
    }
    script.onerror = reject
    document.head.appendChild(script)
  })
}

async function loadPdf() {
  if (!pdfFileName.value || !pdfjsLib.value) return
  pdfLoading.value = true
  renderedPages.value = []
  pageCanvasMap.value = {}
  try {
    const stableUrl = `${window.location.origin}/api/graph/project/${currentProjectId.value}/document/${encodeURIComponent(pdfFileName.value)}`
    pdfUrl.value = stableUrl + `?t=${Date.now()}`

    await nextTick()

    // 加载 PDF document
    let pdf
    if (pdfDoc.value && pdfDocUrl.value === stableUrl) {
      pdf = pdfDoc.value
    } else {
      const loadingTask = pdfjsLib.value.getDocument(stableUrl)
      pdf = await loadingTask.promise
      pdfDoc.value = pdf
      pdfDocUrl.value = stableUrl
      totalPages.value = pdf.numPages
    }

    // 渲染所有页面
    await renderAllPages(pdf)
  } catch (err) {
    console.error('loadPdf error:', err)
  } finally {
    pdfLoading.value = false
  }
}

async function renderAllPages(pdf) {
  const containerWidth = viewerContainer.value?.clientWidth || 600

  // 并行获取所有页面元数据（getPage 内部解析内容，较耗时）
  const pageMetas = await Promise.all(
    Array.from({ length: pdf.numPages }, async (_, i) => {
      const page = await pdf.getPage(i + 1)
      const unscaledViewport = page.getViewport({ scale: 1 })
      const scale = (containerWidth - 20) / unscaledViewport.width
      const viewport = page.getViewport({ scale })
      return {
        pageNum: i + 1,
        page,
        pageWidth: unscaledViewport.width,
        pageHeight: unscaledViewport.height,
        canvasWidth: viewport.width,
        canvasHeight: viewport.height,
        viewport
      }
    })
  )

  renderedPages.value = pageMetas

  // 等 canvas ref 绑定后再渲染
  await nextTick()

  // 串行渲染（PDF.js 渲染本身是 GPU 操作，并行反而可能冲突）
  for (const rp of pageMetas) {
    const canvas = pageCanvasMap.value[rp.pageNum]
    if (!canvas) continue
    canvas.height = rp.canvasHeight
    canvas.width = rp.canvasWidth
    const context = canvas.getContext('2d')
    await rp.page.render({ canvasContext: context, viewport: rp.viewport }).promise
  }
}

// ============================================================================
// 智能分析控制
// ============================================================================

async function openAnchorModal(reset) {
  starting.value = true
  isResetChunking.value = reset
  anchorAutoRecommended.value = false
  anchorReason.value = ''
  useMineruTitles.value = false

  try {
    const res = await inferChunkAnchors(currentProjectId.value)
    if (res.success) {
      chapterAnchor.value = res.data.chapter_anchor || 'x.x'
      clauseContainer.value = res.data.clause_container || 'x.x.x'
      anchorReason.value = res.data.reason || ''
      useMineruTitles.value = res.data.use_mineru_titles || false
      anchorAutoRecommended.value = true
    } else {
      chapterAnchor.value = 'x.x'
      clauseContainer.value = 'x.x.x'
    }
  } catch (err) {
    chapterAnchor.value = 'x.x'
    clauseContainer.value = 'x.x.x'
  } finally {
    starting.value = false
    showChapterPatternModal.value = true
  }
}

async function handleStartChunking() {
  if (starting.value) return
  await openAnchorModal(false)
}

async function handleResetChunking() {
  if (starting.value) return
  await openAnchorModal(true)
}

async function handleReAnnotate() {
  if (reAnnotating.value) return
  // 弹出解析方法选择模态窗口
  showParseMethodModal.value = true
}

function confirmReAnnotate() {
  showParseMethodModal.value = false
  doReAnnotate(selectedParseMethod.value)
}

async function doReAnnotate(parseMethod) {
  reAnnotating.value = true
  realtimeLogs.value.push(`🔄 开始重新标注 (方法: ${parseMethod})...`)

  try {
    const res = await reAnnotateMineru(currentProjectId.value, parseMethod)
    if (res.success) {
      taskId.value = res.data.task_id
      waitingForReAnnotate.value = true
      startTaskSSE()
    } else {
      realtimeLogs.value.push(`❌ 重新标注失败: ${res.error}`)
      reAnnotating.value = false
    }
  } catch (err) {
    realtimeLogs.value.push(`❌ 异常: ${err.message}`)
    reAnnotating.value = false
  }
}

async function confirmChapterPattern() {
  showChapterPatternModal.value = false
  starting.value = true
  showStartButton.value = false
  hasAutoExpanded.value = false

  const actionLabel = isResetChunking.value ? '重置并重新分析' : '开始智能分析'
  realtimeLogs.value.push(`${isResetChunking.value ? '🔄' : '🚀'} ${actionLabel} (章节锚点: ${chapterAnchor.value}, 条款容器: ${clauseContainer.value})...`)

  try {
    const res = await startChunking({
      project_id: currentProjectId.value,
      reset: isResetChunking.value,
      chapter_anchor: chapterAnchor.value,
      clause_container: clauseContainer.value,
      use_mineru_titles: useMineruTitles.value
    })
    if (res.success) {
      taskId.value = res.data.task_id
      analysisStatus.value = 'graph_chunking'
      if (isResetChunking.value) {
        analysisData.value = null
        allAnnotations.value = []
        expandedChapters.value = {}
        expandedClauseId.value = null
        highlightedClauseId.value = null
      }
      realtimeLogs.value.push(`任务已启动: ${res.data.message}`)
      startTaskSSE()
      startProgressPolling()
    } else {
      realtimeLogs.value.push(`❌ 启动失败: ${res.error}`)
      showStartButton.value = true
    }
  } catch (err) {
    realtimeLogs.value.push(`❌ 异常: ${err.message}`)
    showStartButton.value = true
  } finally {
    starting.value = false
  }
}

let pollCount = 0

// ============================================================================
// SSE 实时日志流
// ============================================================================

function startTaskSSE() {
  if (!taskId.value) return
  if (taskSource) taskSource.close()

  const url = getTaskEventsURL(taskId.value)
  taskSource = new EventSource(url)

  taskSource.onmessage = async (event) => {
    try {
      const { type: msgType, data } = JSON.parse(event.data)

      if (msgType === 'init') {
        // 初始化：直接替换已有日志，避免重复
        if (data.logs?.length) {
          realtimeLogs.value = data.logs.map(l => l.message)
        }
        return
      }

      if (msgType === 'update') {
        const payload = data
        if (payload.new_logs?.length) {
          payload.new_logs.forEach(l => {
            if (!realtimeLogs.value.find(existing => existing === l.message)) {
              realtimeLogs.value.push(l.message)
            }
          })
        }
        if (payload.status === 'completed' || payload.status === 'failed') {
          taskSource.close()
          taskSource = null
          // 清理轮询（防止轮询和 SSE 双重触发状态更新）
          if (pollInterval) {
            clearInterval(pollInterval)
            pollInterval = null
          }
          if (payload.status === 'completed') {
            analysisStatus.value = 'graph_chunked'
            realtimeLogs.value.push('✅ 分析完成！')
            await loadAnalysis()
          } else {
            analysisStatus.value = 'failed'
            realtimeLogs.value.push(`❌ 分析失败: ${payload.error || '未知错误'}`)
          }
          // re-annotate 任务完成时刷新 MinerU 结果
          if (waitingForReAnnotate.value) {
            waitingForReAnnotate.value = false
            reAnnotating.value = false
            if (payload.status === 'completed') {
              // 清除旧的选中状态
              mineruSelectedChunk.value = null
              highlightedClauseId.value = null
              // 重新加载 MinerU 结果
              await loadMineruResults()
              // 重新加载 PDF 视图以显示新的标注框
              await loadPdf()
              realtimeLogs.value.push(`✅ 重新标注完成`)
            } else {
              realtimeLogs.value.push(`❌ 重新标注失败: ${payload.error || '未知错误'}`)
            }
          }
        }
      }
    } catch (err) {
      console.error('SSE 解析错误:', err)
    }
  }

  taskSource.onerror = () => {
    taskSource.close()
    taskSource = null
    // REST fallback: 拉回历史日志和最终状态
    if (taskId.value) {
      getTaskStatus(taskId.value).then(res => {
        if (res.success) {
          const task = res.data
          if (task.logs?.length) {
            realtimeLogs.value = task.logs.map(l => l.message)
          }
          if (task.status === 'completed') {
            analysisStatus.value = 'graph_chunked'
            realtimeLogs.value.push('✅ 分析完成！')
            loadAnalysis()
          } else if (task.status === 'failed') {
            analysisStatus.value = 'failed'
            realtimeLogs.value.push(`❌ 分析失败: ${task.error || '未知错误'}`)
          }
        }
      }).catch(() => { /* ignore */ })
    }
  }
}

// ============================================================================
// 进度轮询
// ============================================================================

function startProgressPolling() {
  if (pollInterval) clearInterval(pollInterval)
  pollInterval = setInterval(async () => {
    try {
      // 优先使用 getChunkProgress 的章节级精度进度（智能分块阶段）
      const progRes = await getChunkProgress(currentProjectId.value)
      if (progRes.success && progRes.data) {
        const prog = progRes.data
        const prevPercent = progressPercent.value
        const newPercent = Math.round((prog.progress_ratio || 0) * 100)
        progressPercent.value = newPercent

        // 构建进度日志信息
        const progressInfo = []
        if (prog.total_chapters !== undefined && prog.total_chapters > 0) {
          progressInfo.push(`章节: ${prog.completed_chapters || 0}/${prog.total_chapters}`)
        }
        if (prog.completed_clauses_count !== undefined) {
          progressInfo.push(`条款: ${prog.completed_clauses_count}`)
        }
        if (prog.current_chapter) {
          progressInfo.push(`当前: ${prog.current_chapter.title || prog.current_chapter.chapter_number || '...'}`)
        }

        // 打印进度日志的条件：
        // 1. 进度变化了
        // 2. 或者每 10 次轮询且进度在 0-100% 之间（稳态时定期输出）
        if (newPercent !== prevPercent || (pollCount > 0 && pollCount % 10 === 0 && newPercent > 0 && newPercent < 100)) {
          const msg = progressInfo.length > 0
            ? `📊 智能分析 ${newPercent}% (${progressInfo.join(', ')})`
            : `📊 智能分析 ${newPercent}%`
          // 去重检查
          const exists = realtimeLogs.value.find(l => l.startsWith('📊 智能分析') && l.includes(`${newPercent}%`))
          if (!exists) {
            realtimeLogs.value.push(msg)
          }
        }
      } else if (pollCount <= 3) {
        // 前几次轮询如果 getChunkProgress 失败，可能还在初始化，显示等待消息
        const waitingMsg = '⏳ 等待分析任务初始化...'
        if (!realtimeLogs.value.find(l => l === waitingMsg)) {
          realtimeLogs.value.push(waitingMsg)
        }
      }

      // getTaskStatus 用于状态判断和日志显示
      if (taskId.value) {
        const res = await getTaskStatus(taskId.value)
        if (res.success) {
          const task = res.data
          // 显示任务消息（如果有）
          if (task.message && pollCount % 5 === 0) {
            const msgExists = realtimeLogs.value.find(l => l === task.message)
            if (!msgExists) {
              realtimeLogs.value.push(task.message)
            }
          }
          if (task.status === 'completed') {
            analysisStatus.value = 'graph_chunked'
            if (pollInterval) { clearInterval(pollInterval); pollInterval = null }
            realtimeLogs.value.push('✅ 分析完成！')
            await loadAnalysis()
          } else if (task.status === 'failed') {
            analysisStatus.value = 'failed'
            if (pollInterval) { clearInterval(pollInterval); pollInterval = null }
            realtimeLogs.value.push(`❌ 分析失败: ${task.error}`)
          }
        }
      }

      pollCount++
      if (pollCount % 2 === 0) {
        try { await loadAnalysis() } catch (e) { /* 忽略 */ }
      }
    } catch (err) {
      // 忽略轮询错误
    }
  }, 3000)
}

// ============================================================================
// 加载分析数据
// ============================================================================

async function loadAnalysis() {
  try {
    const res = await getChunkAnalysis(currentProjectId.value)
    if (res.success) {
      analysisData.value = res.data
      analysisStatus.value = res.data.status || 'graph_chunked'

      if (!pdfFileName.value && res.data.pdf_file) {
        pdfFileName.value = res.data.pdf_file
        await loadPdf()
      }

      buildAnnotations()

      if (!hasAutoExpanded.value && analysisData.value.chapter_tree?.length) {
        hasAutoExpanded.value = true
        const firstChapter = analysisData.value.chapter_tree[0].chapter.chapter_number
        expandedChapters.value[firstChapter] = true
      }

      if (res.data.status === 'graph_chunked') {
        realtimeLogs.value.push(`✅ 分析完成: ${analysisData.value.summary.total_clauses} 条文`)
      }
    }
  } catch (err) {
    // 404 或无数据时静默忽略
  }
}

function buildAnnotations() {
  const anns = []
  for (const c of analysisData.value?.clauses || []) {
    // 优先使用 bboxs（聚合多 bbox），其次使用单 bbox，最后回退到 pdf_location
    const bboxsList = c.bboxs || []
    const loc = c.pdf_location

    if (bboxsList.length > 0) {
      // 使用聚合的 bboxs [[page, x0,y0,x1,y1], ...]
      for (const item of bboxsList) {
        if (item.length >= 5) {
          anns.push({
            clauseId: c.clause_id,
            page: item[0],
            bbox: item.slice(1),
            type: 'clause'
          })
        }
      }
    } else {
      // 回退到单 bbox
      const page = loc?.page ?? c.page ?? ((c.page_idx != null) ? c.page_idx + 1 : null)
      const bbox = loc?.bbox ?? c.bbox
      if (page && bbox && bbox.length >= 4) {
        anns.push({
          clauseId: c.clause_id,
          page,
          bbox,
          type: 'clause'
        })
      }
    }
  }
  allAnnotations.value = anns
}

// ============================================================================
// 交互
// ============================================================================

function toggleChapter(chapterNum) {
  expandedChapters.value[chapterNum] = !expandedChapters.value[chapterNum]
}

function toggleAllChapters() {
  if (allExpanded.value) {
    expandedChapters.value = {}
  } else {
    const all = {}
    analysisData.value?.chapter_tree?.forEach(ch => {
      all[ch.chapter.chapter_number] = true
    })
    expandedChapters.value = all
  }
  allExpanded.value = !allExpanded.value
}

function toggleEntityExpand(clauseId) {
  const s = expandedEntityClauseIds.value
  if (s.has(clauseId)) {
    s.delete(clauseId)
  } else {
    s.add(clauseId)
  }
}

function getClauseById(chapter, clauseId) {
  if (!chapter.clauses) return null
  return chapter.clauses.find(c => c.clause_id === clauseId)
}

async function handleClauseClick(clause) {
  if (expandedClauseId.value === clause.clause_id) {
    expandedClauseId.value = null
    highlightedClauseId.value = null
    return
  }
  expandedClauseId.value = clause.clause_id
  highlightedClauseId.value = clause.clause_id

  // 优先使用 bboxs（多 bbox），回退到单 bbox 或 pdf_location
  const bboxs = clause.bboxs || []
  if (bboxs.length > 0) {
    scrollToPage(bboxs[0][0])
  } else {
    const loc = clause.pdf_location
    const page = loc?.page ?? clause.page ?? ((clause.page_idx != null) ? clause.page_idx + 1 : null)
    if (page) scrollToPage(page)
  }
}

function onBboxClick(ann) {
  highlightedClauseId.value = ann.clauseId
  const clause = analysisData.value?.clauses?.find(c => c.clause_id === ann.clauseId)
  if (clause) {
    expandedClauseId.value = clause.clause_id
  }
}

function scrollToPage(pageNum) {
  const wrapper = viewerContainer.value
  if (!wrapper) return
  const rp = renderedPages.value.find(p => p.pageNum === pageNum)
  if (!rp) return
  const pageEl = wrapper.querySelector(`[data-page="${pageNum}"]`)
  if (pageEl) {
    pageEl.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}

function handleEntityClick(entity, clauseId) {
  highlightedClauseId.value = clauseId
  const clause = analysisData.value?.clauses?.find(c => c.clause_id === clauseId)
  if (clause) {
    expandedClauseId.value = clauseId
  }
}

// ============================================================================
// 实体编辑
// ============================================================================

function startEditEntity(clause, field) {
  if (editingClauseId.value === clause.clause_id) {
    editingClauseId.value = null
    editingField.value = ''
    editingValue.value = ''
    return
  }

  editingClauseId.value = clause.clause_id
  editingField.value = field

  if (field === 'terms') {
    editingTerms.value = clause.terms || []
    editingValue.value = (clause.terms || []).map(t => typeof t === 'string' ? t : t.term_name).join('，')
  } else if (field === 'conditions') {
    editingConditions.value = clause.conditions || []
    editingValue.value = (clause.conditions || []).join('，')
  } else if (field === 'actions') {
    editingActions.value = clause.actions || []
    editingValue.value = (clause.actions || []).join('，')
  }
}

async function saveEntity(clauseId, field) {
  const value = editingValue.value.split('，').map(v => v.trim()).filter(v => v)
  try {
    const res = await updateClauseEntity(currentProjectId.value, { clause_id: clauseId, [field]: value })
    if (res.success) {
      const clause = analysisData.value?.clauses?.find(c => c.clause_id === clauseId)
      // 使用 API 返回的完整数据（含 definition 等完整字段）
      if (clause && res.data) {
        clause[field] = res.data[field]
      }
      editingClauseId.value = null
      editingField.value = ''
      editingValue.value = ''
      realtimeLogs.value.push(`✅ ${clauseId} ${field} 已更新`)
    } else {
      realtimeLogs.value.push(`❌ 更新失败: ${res.error}`)
    }
  } catch (err) {
    realtimeLogs.value.push(`❌ 保存失败: ${err.message}`)
  }
}

function toggleTermDef(termName) {
  if (expandedTermDefs.value.has(termName)) {
    expandedTermDefs.value.delete(termName)
  } else {
    expandedTermDefs.value.add(termName)
  }
  // 触发响应式更新
  expandedTermDefs.value = new Set(expandedTermDefs.value)
}

// ============================================================================
// 日志自动滚动
// ============================================================================

watch(() => realtimeLogs.value.length, () => {
  nextTick(() => {
    if (logScrollEl.value) {
      logScrollEl.value.scrollTop = logScrollEl.value.scrollHeight
    }
  })
})

// ============================================================================
// 日志 & 导航
// ============================================================================

function logClass(msg) {
  if (msg.startsWith('❌')) return 'log-error'
  if (msg.startsWith('✅')) return 'log-success'
  if (msg.startsWith('📊')) return 'log-info'
  return 'log-normal'
}

function goToGraphBuild() {
  router.push({ name: 'GraphBuild', params: { projectId: currentProjectId.value } })
}
</script>

<style scoped>
.chunk-analysis-view {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: #f8f9fa;
  color: #1a1a2e;
  overflow: hidden;
}

/* Header */
.header-center {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
}
.brand {
  font-family: 'JetBrains Mono', monospace;
  font-weight: 800;
  font-size: 18px;
  letter-spacing: 1px;
  cursor: pointer;
}
.view-switcher {
  display: flex;
  background: #F5F5F5;
  padding: 4px;
  border-radius: 6px;
  gap: 4px;
}
.switch-btn {
  border: none;
  background: transparent;
  padding: 6px 16px;
  font-size: 12px;
  font-weight: 600;
  color: #666;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.2s;
}
.switch-btn.active {
  background: #FFF;
  color: #000;
  box-shadow: 0 2px 4px rgba(0,0,0,0.05);
}
.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1;
  justify-content: flex-end;
}
.step-divider {
  width: 1px;
  height: 14px;
  background-color: #E0E0E0;
}
.status-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #666;
  font-weight: 500;
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #CCC;
}
.status-indicator .dot { }

header.ca-header {
  height: 60px;
  border-bottom: 1px solid #EAEAEA;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  background: #FFF;
  z-index: 100;
  position: relative;
  flex-shrink: 0;
}
.progress-wrapper { display: flex; align-items: center; gap: 8px; }
.progress-bar { width: 120px; height: 6px; background: #e5e7eb; border-radius: 3px; overflow: hidden; }
.progress-fill { height: 100%; background: linear-gradient(90deg, #2563eb, #3b82f6); border-radius: 3px; transition: width 0.5s ease; }
.progress-text { font-size: 12px; color: #6b7280; min-width: 36px; }

.goto-build-btn { background: #2563eb; color: white; border: none; padding: 6px 14px; border-radius: 6px; cursor: pointer; font-size: 13px; }
.goto-build-btn:hover { background: #1d4ed8; }

/* Main Layout */
.ca-main { display: flex; flex: 1; overflow: hidden; }

/* LEFT: PDF Panel */
.pdf-panel {
  width: 48%;
  display: flex;
  flex-direction: column;
  border-right: 1px solid #e0e0e0;
  background: #ffffff;
}
.pdf-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  background: #f9fafb;
  border-bottom: 1px solid #e0e0e0;
  flex-shrink: 0;
}
.pdf-toolbar-title { font-size: 13px; color: #6b7280; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 200px; }
.pdf-placeholder { color: #9ca3af; }
.pdf-toolbar-actions { display: flex; align-items: center; gap: 6px; }
.pdf-page-count { font-size: 12px; color: #6b7280; min-width: 40px; text-align: right; }

.pdf-body {
  flex: 1;
  overflow-y: auto;
  position: relative;
  padding: 10px;
}
.pdf-empty { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; gap: 12px; color: #9ca3af; }
.pdf-empty-icon { font-size: 48px; opacity: 0.4; }
.pdf-empty p { margin: 0; font-size: 14px; }
.pdf-empty-hint { font-size: 12px; color: #9ca3af; }

.pdf-scroll-container { display: flex; flex-direction: column; align-items: center; gap: 8px; }
.pdf-page-wrapper { position: relative; display: inline-block; }
.pdf-canvas { display: block; max-width: 100%; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }

.bbox-overlay { position: absolute; pointer-events: auto; overflow: visible; }
.bbox-rect { fill: transparent; stroke-width: 2; cursor: pointer; transition: all 0.2s; }
.bbox-clause { stroke: #ea580c; fill: transparent; }
.bbox-element { stroke: #2563eb; fill: transparent; }
.bbox-term { stroke: #16a34a; fill: transparent; }
.bbox-active { stroke-width: 2.5; stroke-dasharray: 6 3; fill: transparent; filter: drop-shadow(0 0 6px #eab308); }
.bbox-mineru { stroke-width: 1.5; fill-opacity: 0.15; }
.bbox-mineru:hover { fill-opacity: 0.35; stroke-width: 2.5; }

.pdf-loading {
  position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
  display: flex; flex-direction: column; align-items: center; gap: 8px; color: #6b7280;
}

.bbox-legend { display: flex; gap: 16px; padding: 6px 12px; background: #f9fafb; border-top: 1px solid #e0e0e0; font-size: 11px; }
.legend-item { display: flex; align-items: center; gap: 4px; color: #6b7280; }
.legend-dot { width: 10px; height: 10px; border-radius: 2px; border: 2px solid; }
.clause-dot { border-color: #ea580c; background: #fff7ed; }
.element-dot { border-color: #2563eb; background: #eff6ff; }
.term-dot { border-color: #16a34a; background: #f0fdf4; }
.legend-count { font-size: 10px; color: #9ca3af; margin-left: 8px; }

/* RIGHT: Analysis Panel */
.analysis-panel {
  width: 52%;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  background: #f8f9fa;
}

.analysis-content {
  display: flex;
  flex-direction: column;
  flex: 1;
}

/* 智能分析面板表头 */
.analysis-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  background: linear-gradient(135deg, #0d9488 0%, #0369a1 100%);
  color: white;
  border-bottom: 1px solid rgba(255,255,255,0.1);
}
.analysis-panel-title { display: flex; align-items: center; gap: 10px; font-size: 13px; font-weight: 600; }
.analysis-summary-badges { display: flex; gap: 6px; }
.re-analyse-btn { background: rgba(255,255,255,0.2); border: none; color: white; padding: 4px 12px; border-radius: 12px; cursor: pointer; font-size: 12px; font-weight: 500; }
.re-analyse-btn:hover { background: rgba(255,255,255,0.35); }
.re-analyse-btn:disabled { background: rgba(255,255,255,0.1); cursor: not-allowed; }

/* 内联进度条 */
.inline-progress {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  max-width: 200px;
}
.inline-progress-bar {
  flex: 1;
  height: 6px;
  background: rgba(255,255,255,0.3);
  border-radius: 3px;
  overflow: hidden;
}
.inline-progress-fill {
  height: 100%;
  background: #4ade80;
  border-radius: 3px;
  transition: width 0.3s ease;
}
.inline-progress-text {
  font-size: 11px;
  color: rgba(255,255,255,0.9);
  min-width: 32px;
  text-align: right;
}

/* MinerU 面板 */
.mineru-panel {
  border-bottom: 1px solid #e0e0e0;
  flex-shrink: 0;
}
.mineru-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
}
.mineru-panel-title { display: flex; align-items: center; gap: 10px; font-size: 13px; font-weight: 600; }
.mineru-summary-badges { display: flex; gap: 6px; }
.badge { padding: 2px 8px; border-radius: 10px; font-size: 10px; background: rgba(255,255,255,0.25); font-weight: 500; }
.close-mineru-btn { background: rgba(255,255,255,0.2); border: none; color: white; width: 22px; height: 22px; border-radius: 50%; cursor: pointer; font-size: 16px; line-height: 1; display: flex; align-items: center; justify-content: center; }
.close-mineru-btn:hover { background: rgba(255,255,255,0.35); }
.re-annotate-btn { background: #4f46e5; border: none; color: white; padding: 4px 12px; border-radius: 12px; cursor: pointer; font-size: 12px; font-weight: 500; }
.re-annotate-btn:hover { background: #4338ca; }
.re-annotate-btn:disabled { background: #a5b4fc; cursor: not-allowed; }

.mineru-layout-info { padding: 10px 16px; background: #f9fafb; }
.layout-info-row { display: flex; align-items: center; gap: 8px; padding: 3px 0; font-size: 12px; }
.info-label { color: #6b7280; min-width: 60px; }
.info-value { color: #1a1a2e; font-weight: 600; }

/* Chunks 列表 */
.mineru-chunk-list { max-height: 240px; overflow-y: auto; border-top: 1px solid #e0e0e0; }
.mineru-chunk-item {
  padding: 8px 16px;
  border-bottom: 1px solid #f0f0f0;
  cursor: pointer;
  transition: background 0.15s;
}
.mineru-chunk-item:hover { background: #f5f5ff; }
.chunk-active { background: #eff6ff; border-left: 3px solid #2563eb; }
.chunk-item-header { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }
.chunk-type-badge { padding: 1px 6px; border-radius: 4px; font-size: 10px; font-weight: 600; }
.chunk-block-type { padding: 1px 6px; border-radius: 4px; font-size: 10px; color: #6b7280; background: #f3f4f6; }
.chunk-page { font-size: 10px; color: #9ca3af; }
.chunk-id { font-size: 9px; color: #d1d5db; font-family: monospace; margin-left: auto; }
.chunk-item-content { font-size: 11px; color: #6b7280; line-height: 1.4; white-space: pre-wrap; word-break: break-all; }

/* 选中块详情 */
.mineru-chunk-detail { border-top: 1px solid #e0e0e0; padding: 10px 16px; background: #fafafa; overflow-y: auto; }
.detail-header { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.detail-type { padding: 2px 8px; background: #667eea22; color: #667eea; border-radius: 8px; font-size: 11px; font-weight: 600; }
.detail-block-type { padding: 2px 8px; background: #f3f4f6; color: #6b7280; border-radius: 8px; font-size: 11px; }
.detail-page { font-size: 11px; color: #9ca3af; }
.detail-content { font-size: 11px; color: #374151; line-height: 1.5; margin-bottom: 8px; white-space: pre-wrap; word-break: break-all; }
.detail-table-content { margin: 8px 0; }
.detail-table-label { font-size: 10px; color: #9ca3af; margin-bottom: 4px; font-weight: 500; }
.detail-table-markdown { background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 6px; padding: 10px; font-size: 11px; line-height: 1.6; color: #374151; overflow-x: auto; }
.detail-table-markdown table { width: auto; max-width: 100%; border-collapse: collapse; font-size: 11px; }
.detail-table-markdown td, .detail-table-markdown th { border: 1px solid #d1d5db; padding: 4px 8px; text-align: left; vertical-align: top; }
.detail-table-markdown th { background: #f3f4f6; font-weight: 600; }
.detail-table-markdown tr:nth-child(even) td { background: #f9fafb; }
.detail-table-markdown tr:hover td { background: #eff6ff; }
.detail-table-caption { font-size: 10px; color: #6b7280; margin-top: 6px; }
.detail-table-caption-label { color: #9ca3af; }
.detail-table-footnote { margin-top: 8px; }
.detail-table-footnote-text { font-size: 11px; color: #374151; background: #fffbeb; border: 1px solid #fde68a; border-radius: 6px; padding: 8px 10px; line-height: 1.6; white-space: pre-wrap; }
/* 图片块详情 */
.detail-image-section { margin-top: 8px; }
.detail-image-caption { font-size: 11px; color: #6b7280; margin-bottom: 6px; }
.detail-image-caption-label { color: #9ca3af; }
.detail-image-preview { margin: 6px 0; }
.detail-image-meta { font-size: 10px; color: #9ca3af; margin-top: 4px; }
.detail-image-meta-label { color: #9ca3af; }
.detail-image-meta-value { font-family: monospace; }
.detail-bbox { display: flex; flex-direction: column; gap: 2px; font-size: 10px; }
.detail-bbox-line { color: #6b7280; font-family: monospace; }

/* 章节树 */
.chapter-tree { border-bottom: 1px solid #e0e0e0; flex-shrink: 0; }
.tree-header {
  display: flex;
  align-items: center;
  padding: 10px 16px;
  background: #ffffff;
  font-size: 13px;
  font-weight: 600;
  color: #1a1a2e;
  border-bottom: 1px solid #e0e0e0;
}
.tree-header > span:first-child { flex-shrink: 0; }
.tree-stats {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  margin: 0 12px;
  font-size: 12px;
}
.stat-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px 3px 8px;
  border-radius: 16px;
  font-size: 12px;
  font-weight: 500;
  transition: transform 0.2s, box-shadow 0.2s;
}
.stat-badge:hover {
  transform: translateY(-1px);
  box-shadow: 0 2px 8px rgba(0,0,0,0.1);
}
.stat-badge.clauses {
  background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%);
  color: #92400e;
  border: 1px solid #fcd34d;
}
.stat-badge.terms {
  background: linear-gradient(135deg, #dbeafe 0%, #bfdbfe 100%);
  color: #1e40af;
  border: 1px solid #93c5fd;
}
.stat-badge.entities {
  background: linear-gradient(135deg, #d1fae5 0%, #a7f3d0 100%);
  color: #065f46;
  border: 1px solid #6ee7b7;
}
.stat-icon { font-size: 13px; }
.stat-num { font-weight: 700; font-size: 13px; }
.stat-label { opacity: 0.8; }
.tree-actions { display: flex; align-items: center; gap: 6px; }
.expand-all-btn { background: none; border: 1px solid #d0d7de; color: #6b7280; padding: 2px 8px; border-radius: 4px; cursor: pointer; font-size: 11px; }
.expand-all-btn:hover { background: #f0f0f0; color: #1a1a2e; }
.reset-chunks-btn { background: none; border: 1px solid #d97706; color: #d97706; padding: 2px 8px; border-radius: 4px; cursor: pointer; font-size: 11px; }
.reset-chunks-btn:hover:not(:disabled) { background: #fff7ed; }
.reset-chunks-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.tree-body { overflow-y: auto; }

.chapter-item { border-bottom: 1px solid #f0f0f0; }
.chapter-header { display: flex; align-items: center; gap: 8px; padding: 8px 16px; cursor: pointer; font-size: 13px; color: #1a1a2e; }
.chapter-header:hover { background: #f0f7ff; }
.chapter-toggle { color: #9ca3af; font-size: 10px; }
.chapter-title { font-weight: 600; }
.chapter-meta { color: #9ca3af; font-size: 11px; margin-left: auto; }

.clause-list { padding: 4px 0; }
.clause-item { padding: 4px 16px 4px 32px; cursor: pointer; border-left: 2px solid transparent; }
.clause-item:hover { background: #f9fafb; }
.clause-active { border-left-color: #2563eb; background: #eff6ff; }

.clause-row { display: flex; align-items: center; gap: 6px; padding: 2px 0; }
.clause-id { font-size: 11px; color: #2563eb; font-family: monospace; min-width: 40px; }
.clause-title { font-size: 12px; color: #6b7280; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }

.clause-detail { margin: 4px 0 6px; padding: 6px 0; border-top: 1px solid #f0f0f0; }

/* 条款容器样式 */
.clause-container { border-left-color: #9333ea; }
.clause-container:hover { background: #faf5ff; }
.container-id { color: #9333ea !important; font-weight: 600; }
.container-badge { font-size: 10px; padding: 1px 5px; background: #9333ea; color: #fff; border-radius: 8px; margin-left: auto; }

/* 子条款（容器下的条款） */
.child-clauses { margin-left: 16px; border-left: 2px dashed #e5e7eb; padding-left: 8px; }
.clause-child { padding-left: 16px !important; }
.clause-child:hover { background: #f9fafb; }
.child-id { color: #6b7280 !important; }
.child-detail { margin-left: 16px; border-left: 2px solid #e5e7eb; padding-left: 8px; }

.entity-row { display: flex; align-items: center; gap: 6px; margin: 3px 0; min-height: 22px; }
.entity-label { font-size: 11px; font-weight: 600; min-width: 48px; }
.term-label { color: #16a34a; }
.cond-label { color: #ca8a04; }
.action-label { color: #2563eb; }
.comp-label { color: #9333ea; }

.entity-tags { display: flex; flex-wrap: wrap; gap: 4px; flex: 1; align-items: center; }
.entity-tag { padding: 2px 7px; border-radius: 10px; font-size: 11px; cursor: pointer; transition: all 0.15s; }
.term-tag { background: #dcfce7; color: #16a34a; border: 1px solid #bbf7d0; }
.term-tag:hover { background: #bbf7d0; }
.term-tag.active { background: #16a34a; color: #fff; }
.cond-tag { background: #fef9c3; color: #ca8a04; border: 1px solid #fde68a; }
.cond-tag:hover { background: #fde68a; }
.action-tag { background: #dbeafe; color: #2563eb; border: 1px solid #bfdbfe; }
.action-tag:hover { background: #bfdbfe; }
.comp-tag { background: #f3e8ff; color: #9333ea; border: 1px solid #e9d5ff; }
.comp-tag:hover { background: #e9d5ff; }

.term-item { display: flex; flex-direction: column; gap: 2px; }
.term-def-arrow { font-size: 8px; color: #16a34a; text-align: center; line-height: 1; margin-top: -2px; }
.term-definition { font-size: 11px; color: #374151; background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 6px; padding: 4px 8px; margin-top: 2px; line-height: 1.4; }
.def-connector { color: #16a34a; font-weight: 600; margin-right: 4px; }

.edit-btn { background: none; border: 1px solid #d0d7de; color: #6b7280; padding: 1px 6px; border-radius: 4px; cursor: pointer; font-size: 10px; flex-shrink: 0; }
.edit-btn:hover { background: #f0f0f0; color: #1a1a2e; }
.entity-editor { display: flex; gap: 6px; margin: 4px 0; }
.entity-input { flex: 1; background: #ffffff; border: 1px solid #d0d7de; color: #1a1a2e; padding: 3px 8px; border-radius: 4px; font-size: 12px; }
.entity-input:focus { outline: none; border-color: #2563eb; }
.save-btn { background: #2563eb; border: none; color: white; padding: 3px 10px; border-radius: 4px; cursor: pointer; font-size: 11px; }
.save-btn:hover { background: #1d4ed8; }

.triplets-section { margin: 6px 0; }
.triplet-label { font-size: 11px; color: #6b7280; margin-bottom: 4px; }
.triplet-row { display: flex; align-items: center; gap: 6px; padding: 2px 0; font-size: 11px; flex-wrap: wrap; }
.triplet-comp { color: #9333ea; }
.triplet-arrow { color: #2563eb; }
.triplet-obj { color: #16a34a; }
.triplet-cond { color: #ca8a04; font-size: 10px; }

.clause-entities-section { margin: 6px 0; }
.clause-topic-row { display: flex; align-items: flex-start; gap: 8px; margin-bottom: 6px; padding: 6px 8px; background: #f0f9ff; border: 1px solid #e0f2fe; border-radius: 6px; }
.clause-topic-row.collapsible { cursor: pointer; user-select: none; transition: background 0.15s; }
.clause-topic-row.collapsible:hover { background: #e0f2fe; }
.clause-topic-row.collapsible .topic-toggle-icon { font-size: 10px; color: #0369a1; flex-shrink: 0; width: 14px; text-align: center; }
.topic-label { font-size: 11px; font-weight: 600; color: #0369a1; flex-shrink: 0; }
.topic-content { font-size: 12px; color: #1e40af; line-height: 1.4; }
.entity-section-label { font-size: 11px; color: #6b7280; margin-bottom: 4px; }
.entity-expand-panel { padding: 4px 8px 8px 28px; background: #fafafa; border: 1px solid #f0f0f0; border-top: none; border-radius: 0 0 6px 6px; }
.child-topic { margin-top: 4px; background: #f8fafc; border-color: #e2e8f0; }
.child-topic:hover { background: #f1f5f9; }

/* 多主题分组 */
.topic-group { margin-bottom: 8px; border: 1px solid #e5e7eb; border-radius: 6px; background: #fafafa; overflow: hidden; }
.topic-group-header { display: flex; align-items: center; gap: 6px; padding: 5px 8px; background: #f0f7ff; border-bottom: 1px solid #dbeafe; }
.topic-group-label { font-size: 10px; font-weight: 600; color: #4f46e5; background: #eef2ff; padding: 1px 6px; border-radius: 4px; }
.topic-group-title { font-size: 12px; font-weight: 600; color: #1e3a8a; }
.topic-group-entities { padding: 6px 8px; }
.topic-empty { font-size: 11px; color: #9ca3af; font-style: italic; padding: 2px 0; }

/* 媒体（图片/表格）展示 */
.clause-images-section, .clause-tables-section { margin-top: 8px; }
.clause-image-item { position: relative; margin: 6px 0 10px; padding: 6px 8px 8px; background: #fafafa; border: 1px solid #f0f0f0; border-radius: 6px; }
.media-type-badge { display: inline-block; font-size: 10px; font-weight: 600; padding: 1px 6px; border-radius: 4px; margin-bottom: 4px; }
.media-type-image { color: #0369a1; background: #e0f2fe; border: 1px solid #bae6fd; }
.media-type-table { color: #b45309; background: #fef3c7; border: 1px solid #fde68a; }
.image-caption { font-size: 11px; color: #4b5563; margin-bottom: 6px; line-height: 1.4; }
.image-caption .table-id { color: #b45309; font-weight: 600; margin-right: 4px; }
.image-preview { display: flex; align-items: flex-start; }
.vlm-content { font-size: 11px; color: #374151; margin-top: 4px; line-height: 1.5; }
.vlm-label { color: #6b7280; margin-right: 4px; }
.vlm-status-error { font-size: 11px; color: #b91c1c; margin-top: 4px; }
.vlm-error { color: #b91c1c; }
.entity-item-row { display: flex; align-items: center; gap: 6px; padding: 2px 0; font-size: 11px; }
.entity-type-tag { padding: 1px 6px; border-radius: 4px; font-size: 10px; background: #e5e7eb; color: #6b7280; font-weight: 600; }
.entity-key { color: #1a1a2e; font-weight: 500; }
.entity-value { color: #2563eb; }
.entity-unit { color: #9ca3af; font-size: 10px; }

/* 日志抽屉 */
.log-drawer { background: #ffffff; border-top: 1px solid #e0e0e0; flex-shrink: 0; }
.log-drawer-header { display: flex; align-items: center; justify-content: space-between; padding: 8px 16px; cursor: pointer; font-size: 12px; color: #6b7280; }
.log-drawer-header:hover { color: #1a1a2e; }
.log-toggle { font-size: 10px; }
.log-drawer-body { max-height: 400px; overflow-y: auto; padding: 0 16px 8px; font-family: 'Consolas', 'Monaco', monospace; font-size: 11px; background: #f9fafb; }
.log-line { padding: 1px 0; color: #6b7280; }
.log-error { color: #dc2626; }
.log-success { color: #16a34a; }
.log-info { color: #2563eb; }
.log-empty { color: #9ca3af; font-size: 11px; }

/* 开始分析弹窗 */
.start-overlay {
  position: fixed; top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0,0,0,0.4);
  display: flex; align-items: center; justify-content: center;
  z-index: 100;
}
.start-card {
  background: #ffffff; border: 1px solid #e0e0e0; border-radius: 12px;
  padding: 32px 40px; text-align: center; max-width: 440px;
  box-shadow: 0 8px 24px rgba(0,0,0,0.12);
}
.start-icon { font-size: 48px; margin-bottom: 12px; }
.start-card h3 { font-size: 18px; margin: 0 0 12px; color: #1a1a2e; }
.start-card p { font-size: 14px; color: #6b7280; margin: 0 0 24px; line-height: 1.6; }
.start-actions { display: flex; gap: 12px; justify-content: center; }
.start-btn { background: #2563eb; color: white; border: none; padding: 10px 24px; border-radius: 6px; cursor: pointer; font-size: 14px; font-weight: 600; }
.start-btn:hover:not(:disabled) { background: #1d4ed8; }
.start-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.reset-btn { background: none; border: 1px solid #d0d7de; color: #6b7280; padding: 10px 20px; border-radius: 6px; cursor: pointer; font-size: 14px; }
.reset-btn:hover:not(:disabled) { background: #f0f0f0; color: #1a1a2e; }

/* Spinner */
.spinner { width: 24px; height: 24px; border: 2px solid #e5e7eb; border-top-color: #2563eb; border-radius: 50%; animation: spin 0.7s linear infinite; margin: 0 auto; }
.spinner-sm { display: inline-block; width: 14px; height: 14px; border: 2px solid rgba(255,255,255,0.4); border-top-color: white; border-radius: 50%; animation: spin 0.7s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

/* MinerU toggle button */
.mineru-toggle { background: none; border: 1px solid #d0d7de; padding: 4px 10px; border-radius: 6px; cursor: pointer; font-size: 11px; color: #6b7280; }
.mineru-toggle:hover { background: #f0f0f0; }
.mineru-toggle.active { background: #667eea; color: white; border-color: #667eea; }

/* 解析方法选择模态窗口 */
.modal-overlay {
  position: fixed; top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0,0,0,0.5);
  display: flex; align-items: center; justify-content: center;
  z-index: 1000;
}
.modal-backdrop {
  position: fixed; top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0,0,0,0.5);
  z-index: 1000;
}
.modal-wrapper {
  position: fixed; top: 0; left: 0; right: 0; bottom: 0;
  display: flex; align-items: center; justify-content: center;
  z-index: 1001;
  pointer-events: none;
}
.modal-wrapper > .modal-card { pointer-events: auto; }
.modal-card {
  background: #ffffff; border-radius: 12px;
  padding: 0; min-width: 360px; max-width: 420px;
  box-shadow: 0 12px 32px rgba(0,0,0,0.2);
  overflow: hidden;
}
.modal-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 16px 20px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
}
.modal-header h3 { margin: 0; font-size: 16px; font-weight: 600; }
.modal-close { background: rgba(255,255,255,0.2); border: none; color: white; width: 24px; height: 24px; border-radius: 50%; cursor: pointer; font-size: 18px; line-height: 1; display: flex; align-items: center; justify-content: center; }
.modal-close:hover { background: rgba(255,255,255,0.3); }
.modal-body { padding: 20px; }
.modal-desc { margin: 0 0 16px; font-size: 14px; color: #6b7280; }
.method-options { display: flex; flex-direction: column; gap: 10px; }
.method-option {
  display: flex; align-items: center; gap: 12px;
  padding: 12px 16px;
  border: 2px solid #e5e7eb; border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s;
}
.method-option:hover { border-color: #667eea; background: #f9fafb; }
.method-option.active { border-color: #667eea; background: #eef2ff; }
.method-option input[type="radio"] { display: none; }
.method-content { display: flex; flex-direction: column; gap: 2px; }
.method-name { font-size: 14px; font-weight: 600; color: #1a1a2e; }
.method-desc { font-size: 12px; color: #6b7280; }
.modal-footer {
  display: flex; gap: 12px; justify-content: flex-end;
  padding: 16px 20px;
  background: #f9fafb;
  border-top: 1px solid #e5e7eb;
}
.modal-btn { padding: 8px 20px; border-radius: 6px; font-size: 14px; font-weight: 500; cursor: pointer; transition: all 0.2s; }
.modal-btn.cancel { background: none; border: 1px solid #d0d7de; color: #6b7280; }
.modal-btn.cancel:hover { background: #f0f0f0; }
.modal-btn.confirm { background: #667eea; border: none; color: white; }
.modal-btn.confirm:hover { background: #5a67e8; }
.anchor-modal { width: 420px; max-width: 92vw; }
.anchor-modal .modal-header { padding: 14px 16px; }
.anchor-modal .modal-body { padding: 14px 16px 16px; }
.anchor-modal .modal-footer { padding: 12px 16px; }
.anchor-recommendation {
  margin-bottom: 16px;
  padding: 12px 14px;
  background: linear-gradient(135deg, #e6f7ff 0%, #f0faff 100%);
  border: 1px solid #91d5ff;
  border-radius: 8px;
}
.anchor-rec-title { font-weight: 600; color: #096dd9; margin-bottom: 4px; font-size: 13px; }
.anchor-rec-body { color: #262626; font-size: 13px; }
.anchor-rec-body strong { color: #1890ff; font-weight: 700; }
.anchor-rec-sep { margin: 0 6px; color: #bfbfbf; }
.anchor-rec-reason { color: #595959; font-size: 11px; margin-top: 4px; line-height: 1.4; }
.anchor-title-mode-hint {
  background: #fffbe6;
  border: 1px solid #ffe58f;
  border-radius: 6px;
  padding: 8px 12px;
  font-size: 12px;
  color: #ad6800;
  margin-top: 8px;
  line-height: 1.5;
}
.anchor-title-mode-hint strong { color: #d46b08; }

.pattern-section { margin-bottom: 16px; }
.pattern-section-title { font-size: 12px; font-weight: 600; color: #4b5563; margin-bottom: 8px; }
.pattern-options { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
.pattern-option {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 2px;
  padding: 8px 4px;
  border: 1.5px solid #e5e7eb; border-radius: 8px;
  cursor: pointer;
  transition: all 0.15s;
  min-height: 56px;
}
.pattern-option:hover { border-color: #667eea; background: #f9fafb; }
.pattern-option.active { border-color: #667eea; background: #eef2ff; box-shadow: 0 0 0 2px rgba(102, 126, 234, 0.15); }
.pattern-option.disabled { opacity: 0.45; cursor: not-allowed; background: #f5f5f5; }
.pattern-option.disabled:hover { border-color: #e5e7eb; background: #f5f5f5; }
.pattern-option input[type="radio"] { display: none; }
.pattern-option .pattern-name { font-size: 14px; font-weight: 700; color: #1a1a2e; }
.pattern-option .pattern-desc { font-size: 10px; color: #6b7280; }
.pattern-hint {
  margin-top: 12px;
  padding: 10px 12px;
  background: #f6f7f9;
  border-radius: 6px;
  font-size: 11px;
  color: #6b7280;
  line-height: 1.5;
}
</style>
