"""
HierarchicalChunker - 多层级语义分块引擎

实现三级分块策略：
- Level-1: 章节级分割（正则）
- Level-2: 条文级解析（正则 + LLM补充）
- Level-3: 要素级提取（正则）

混合模式：正则识别编号和结构，LLM补充语义理解
"""

import re
import logging
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import asdict

from ..models.clause import (
    HierarchicalChunkResult,
    SectionSegment,
    ClauseSegment,
    ElementSegment,
    ChunkLevel,
    ElementType,
    RequirementType,
    SystemApplicability,
    CrossReference
)
from ..utils.file_parser import TextChunk

logger = logging.getLogger('mirofish.chunker')


# ============================================================================
# 正则模式定义
# ============================================================================

# 章节模式
CHAPTER_PATTERN = re.compile(
    r'(?:^|\n\n)(第[一二三四五六七八九十\d]+[章篇部])\s*[:：]?\s*([^\n]+)',
    re.MULTILINE
)

SECTION_PATTERN = re.compile(
    r'(?:^|\n\n)(\d+\.\d+)\s+([^\n]+)',
    re.MULTILINE
)

# 条文编号模式（核心）
CLAUSE_ID_PATTERN = re.compile(
    r'^(\d+\.\d+\.\d+)\s+(.+?)(?=\n\d+\.\d+\.\d+\s|\n\n[A-Z第]|\Z)',
    re.MULTILINE | re.DOTALL
)

# 款/项识别
PARAGRAPH_PATTERN = re.compile(
    r'^\d+\.\d+\.\d+\.(\d+)\s+(.+?)(?=\n\d+\.\d+\.\d+\.\d+\s|\n\d+\.\d+\.\d+\s|\Z)',
    re.MULTILINE | re.DOTALL
)

# 公式引用模式
FORMULA_REF_PATTERN = re.compile(
    r'(?:公式[\(（]?(\d+\.\d+(?:-\d+)?)[\)）]?|'
    r'\(([A-Z]\.\d+\.\d+(?:-\d+)?)\)|'
    r'式[\(（]?(\d+\.\d+(?:-\d+)?)[\)）]?)',
    re.IGNORECASE
)

# 表格引用模式
TABLE_REF_PATTERN = re.compile(
    r'(?:表[\s　]*(\d+\.\d+(?:\.\d+)?)|'
    r'见表\s*(\d+\.\d+(?:\.\d+)?))',
    re.IGNORECASE
)

# 适用系统模式
SYSTEM_PATTERN = re.compile(
    r'(?:适用于?|仅适用于?|适用)\s*'
    r'((?:TN|TT|IT)(?:-C|-S|-C-S)?)\s*系统',
    re.IGNORECASE
)

# 强制性标识
MANDATORY_PATTERN = re.compile(
    r'(?:必须|应(?:\s*不)?|严禁|不得|不应|必须不)',
    re.IGNORECASE
)

# 禁止性标识
PROHIBITED_PATTERN = re.compile(
    r'(?:严禁|不得|禁止|不应|必须不)',
    re.IGNORECASE
)

# 表格行提取（简单模式）
TABLE_ROW_PATTERN = re.compile(
    r'^\s*(\d+(?:\.\d+)?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*(?:\n|$)',
    re.MULTILINE
)

# 术语定义模式
TERM_PATTERN = re.compile(
    r'(?:^|\n)(2\.0\.\d+)\s+([^\n]+?)\s*[:：]\s*([^\n]+?)(?=\n\d|\n[A-Z]|\Z)',
    re.MULTILINE | re.DOTALL
)


class HierarchicalChunker:
    """
    多层级语义分块引擎

    使用混合模式：
    - 正则识别：条文编号、结构、引用关系
    - LLM补充：写入Neo4j后由SemanticEnricher补充语义
    """

    def __init__(self):
        self.logger = logging.getLogger('mirofish.chunker')

    def chunk(self, text_chunks: List[TextChunk]) -> HierarchicalChunkResult:
        """
        主入口：实现三级分块

        Args:
            text_chunks: 原始文本块列表

        Returns:
            HierarchicalChunkResult: 包含所有层级分块的结果
        """
        result = HierarchicalChunkResult()

        # 收集每个chunk的来源信息
        chunk_sources: Dict[int, Dict[str, Any]] = {}  # text_position -> {source, page}

        # 合并所有文本，同时记录每个字符的来源
        text_parts = []
        current_pos = 0
        for tc in text_chunks:
            if tc.text.strip():
                chunk_start = current_pos
                text_parts.append(tc.text)
                chunk_sources[chunk_start] = {
                    "source": tc.metadata.get("source") if tc.metadata else None,
                    "page": tc.metadata.get("page") if tc.metadata else None
                }
                current_pos += len(tc.text) + 1  # +1 for newline

        full_text = "\n".join(text_parts)

        self.logger.info(f"开始多层级分块，原始文本长度: {len(full_text)}")

        # Level-1: 章节分割
        sections = self._split_by_chapters(full_text)
        # 为每个section添加来源信息
        for section in sections:
            self._assign_source_info(section, chunk_sources)
        result.sections = sections
        self.logger.info(f"Level-1 章节级: {len(sections)} 个章节")

        # Level-2: 条文解析
        clauses = self._extract_clauses_from_text(full_text)
        # 为每个clause添加来源信息
        for clause in clauses:
            self._assign_source_info(clause, chunk_sources)
        result.clauses = clauses
        self.logger.info(f"Level-2 条文级: {len(clauses)} 个条文")

        # Level-3: 要素提取
        elements = self._extract_elements(full_text)
        # 为每个element添加来源信息
        for element in elements:
            self._assign_source_info(element, chunk_sources)
        result.elements = elements
        self.logger.info(f"Level-3 要素级: {len(elements)} 个要素")

        self.logger.info(f"分块完成，总计: {result.total_chunks} 个chunk")
        return result

    def _assign_source_info(self, chunk: 'HierarchicalChunk', chunk_sources: Dict[int, Dict[str, Any]]) -> None:
        """
        为chunk分配来源信息

        根据chunk内容在文本中的位置，找到对应的source和page
        """
        # 遍历所有已知的来源位置，找到包含chunk内容的那个
        if not chunk.content:
            return

        for pos, source_info in sorted(chunk_sources.items()):
            # 简单检查：如果chunk内容较短，可以尝试匹配
            # 这里使用第一个有source的chunk作为默认值
            if source_info.get("source"):
                if chunk.source is None:
                    chunk.source = source_info.get("source")
                if chunk.page is None and source_info.get("page"):
                    chunk.page = source_info.get("page")
            # 如果都找到了，可以提前退出
            if chunk.source and chunk.page is not None:
                break

    def chunk_single_text(self, text: str) -> HierarchicalChunkResult:
        """单文本分块入口"""
        fake_chunk = TextChunk(text=text, metadata={})
        return self.chunk([fake_chunk])

    def _split_by_chapters(self, text: str) -> List[SectionSegment]:
        """
        Level-1: 按章节结构分割

        识别模式：
        - 第X章 标题
        - X.Y 章节标题
        """
        sections = []
        lines = text.split('\n')

        current_chapter = None
        current_section = None
        current_content = []
        chapter_num = None

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # 检查是否是新章节
            chapter_match = re.match(r'^第([一二三四五六七八九十\d]+)[章篇部]\s*[:：]?\s*(.*)', line)
            section_match = re.match(r'^(\d+)\.(\d+)\s+(.+)', line)

            if chapter_match:
                # 保存当前章节
                if current_content and current_chapter:
                    sections.append(SectionSegment(
                        chapter_number=chapter_num,
                        title=current_chapter,
                        content='\n'.join(current_content)
                    ))

                chapter_num = self._chinese_to_int(chapter_match.group(1))
                current_chapter = chapter_match.group(2) or ""
                current_content = []
                current_section = None

            elif section_match:
                # 小节开始
                section_num = int(section_match.group(1))
                section_title = section_match.group(3)

                if current_section:
                    # 保存上一节
                    sections.append(SectionSegment(
                        chapter_number=chapter_num,
                        section_number=int(section_match.group(1)),
                        title=f"{section_match.group(1)} {section_title}",
                        content='\n'.join(current_content)
                    ))
                    current_content = []
                else:
                    # 第一小节且没有章标题时，创建虚拟章
                    if not current_chapter:
                        current_chapter = f"第{section_num}章"
                        chapter_num = section_num

                current_section = section_title

            # 累积内容
            if current_chapter:
                current_content.append(line)

        # 保存最后一个章节
        if current_content and current_chapter:
            sections.append(SectionSegment(
                chapter_number=chapter_num,
                title=current_chapter,
                content='\n'.join(current_content)
            ))

        return sections

    def _extract_clauses_from_text(self, text: str) -> List[ClauseSegment]:
        """
        Level-2: 从文本中提取条文

        使用正则识别条文编号和结构
        """
        clauses = []

        # 按条文编号模式查找所有条文
        matches = list(CLAUSE_ID_PATTERN.finditer(text))

        for i, match in enumerate(matches):
            clause_id = match.group(1)
            clause_content = match.group(2).strip()

            # 提取款/项
            paragraphs = self._extract_paragraphs(clause_content)

            # 提取公式引用
            formula_id = self._extract_formula_ref(clause_content)

            # 提取表格引用
            table_refs = self._extract_table_refs(clause_content)

            # 提取适用系统
            systems = self._extract_systems(clause_content)

            # 判断要求类型
            req_type = self._determine_requirement_type(clause_content)

            # 提取交叉引用
            cross_refs = self._extract_cross_refs(clause_content)

            # 清理内容（去除款/项部分）
            main_content = CLAUSE_ID_PATTERN.sub('', clause_content).strip()
            if not main_content:
                main_content = clause_content[:200]  # 保留学节内容前200字符

            clause = ClauseSegment(
                clause_id=clause_id,
                clause_title=self._extract_title(clause_content),
                content=main_content,
                paragraphs=paragraphs,
                formula_id=formula_id,
                table_refs=table_refs,
                applicable_systems=systems,
                requirement_type=req_type,
                cross_refs=cross_refs
            )
            clauses.append(clause)

        return clauses

    def _extract_paragraphs(self, clause_text: str) -> List[str]:
        """提取款/项内容"""
        paragraphs = []
        matches = PARAGRAPH_PATTERN.findall(clause_text)
        for item_num, content in matches:
            paragraphs.append(f"{item_num} {content.strip()}")
        return paragraphs

    def _extract_formula_ref(self, text: str) -> Optional[str]:
        """提取公式引用"""
        match = FORMULA_REF_PATTERN.search(text)
        if match:
            # 返回第一个非空匹配组
            for group in match.groups():
                if group:
                    return group
        return None

    def _extract_table_refs(self, text: str) -> List[str]:
        """提取表格引用"""
        refs = []
        matches = TABLE_REF_PATTERN.findall(text)
        for match in matches:
            for group in match:
                if group and group.strip():
                    refs.append(f"表{group.strip()}")
        return list(set(refs))  # 去重

    def _extract_systems(self, text: str) -> List[SystemApplicability]:
        """提取适用系统"""
        systems = []
        matches = SYSTEM_PATTERN.findall(text)
        for system_match in matches:
            system_type = system_match[0] if isinstance(system_match, tuple) else system_match
            if system_type.upper().startswith('TN'):
                sub_type = system_type[2:] if len(system_type) > 2 else None
                systems.append(SystemApplicability(
                    system_type='TN',
                    sub_type=sub_type
                ))
            else:
                systems.append(SystemApplicability(system_type=system_type))
        return systems

    def _determine_requirement_type(self, text: str) -> RequirementType:
        """判断要求类型"""
        if PROHIBITED_PATTERN.search(text):
            return RequirementType.PROHIBITED
        if MANDATORY_PATTERN.search(text):
            return RequirementType.MANDATORY
        return RequirementType.RECOMMENDED

    def _extract_cross_refs(self, text: str) -> List[CrossReference]:
        """提取交叉引用"""
        refs = []
        # 匹配条文引用如 5.2.9, 3.2.14
        ref_pattern = re.compile(r'(\d+\.\d+\.\d+)')
        matches = ref_pattern.findall(text)
        for ref in matches:
            if ref not in [cr.ref_id for cr in refs]:
                refs.append(CrossReference(ref_id=ref, ref_type="clause"))
        return refs

    def _extract_title(self, text: str) -> str:
        """提取条文标题（简化版）"""
        # 取第一行作为标题
        first_line = text.split('\n')[0].strip()
        if len(first_line) > 100:
            first_line = first_line[:100] + "..."
        return first_line

    def _extract_elements(self, text: str) -> List[ElementSegment]:
        """
        Level-3: 提取表格行、公式、术语等要素
        """
        elements = []

        # 提取表格行
        table_elements = self._extract_table_rows(text)
        elements.extend(table_elements)

        # 提取术语定义
        term_elements = self._extract_terms(text)
        elements.extend(term_elements)

        # 提取公式
        formula_elements = self._extract_formulas(text)
        elements.extend(formula_elements)

        return elements

    def _extract_table_rows(self, text: str) -> List[ElementSegment]:
        """提取表格行"""
        elements = []
        # 简单的markdown表格行提取
        lines = text.split('\n')
        current_table_id = None

        for line in lines:
            # 检测表格开始
            table_header_match = re.search(r'表\s*(\d+(?:\.\d+)?)', line, re.IGNORECASE)
            if table_header_match:
                current_table_id = f"表{table_header_match.group(1)}"
                continue

            # 检测表格分隔线
            if re.match(r'^\s*[-|:\s]+\s*$', line):
                continue

            # 提取表格行
            if current_table_id:
                row_match = re.match(r'^\s*(\d+(?:\.\d+)?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*(?:\||$)', line)
                if row_match:
                    row_idx, col1, col2 = row_match.groups()
                    elements.append(ElementSegment(
                        element_type=ElementType.TABLE_ROW,
                        source_id=current_table_id,
                        row_index=int(row_idx) if row_idx.isdigit() else None,
                        content=line.strip(),
                        key=col1.strip(),
                        value=col2.strip()
                    ))
                elif line.strip() and not line.strip().startswith('|'):
                    current_table_id = None  # 表格结束

        return elements

    def _extract_terms(self, text: str) -> List[ElementSegment]:
        """提取术语定义"""
        elements = []
        matches = TERM_PATTERN.findall(text)
        for term_id, term_name, definition in matches:
            elements.append(ElementSegment(
                element_type=ElementType.TERM,
                source_id=term_id,
                content=f"{term_id} {term_name}: {definition}",
                key=term_name.strip(),
                value=definition.strip()
            ))
        return elements

    def _extract_formulas(self, text: str) -> List[ElementSegment]:
        """提取公式"""
        elements = []
        # 识别公式块 (通常在行首或独占一行)
        formula_blocks = re.findall(
            r'(?:^|\n)\s*[\(（]?\s*([A-Z]?\.\d+\.\d+(?:-\d+)?)\s*[\)）]?\s*\n\s*([^\n]+?)(?=\n\n|\n[A-Z]|\Z)',
            text,
            re.MULTILINE
        )
        for formula_id, formula_expr in formula_blocks:
            elements.append(ElementSegment(
                element_type=ElementType.FORMULA,
                source_id=formula_id.strip(),
                content=formula_expr.strip(),
                key=formula_id.strip(),
                value=formula_expr.strip()
            ))
        return elements

    @staticmethod
    def _chinese_to_int(text: str) -> int:
        """将中文数字转换为整数"""
        chinese_map = {
            '一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
            '六': 6, '七': 7, '八': 8, '九': 9, '十': 10
        }
        result = 0
        for char in text:
            if char in chinese_map:
                result = result * 10 + chinese_map[char]
            elif char.isdigit():
                result = result * 10 + int(char)
        return result if result > 0 else int(text) if text.isdigit() else 0


# ============================================================================
# 便捷函数
# ============================================================================

def chunk_text(text: str) -> HierarchicalChunkResult:
    """
    便捷函数：对单个文本进行多层级分块

    Args:
        text: 输入文本

    Returns:
        HierarchicalChunkResult
    """
    chunker = HierarchicalChunker()
    return chunker.chunk_single_text(text)


def chunk_texts(text_chunks: List[TextChunk]) -> HierarchicalChunkResult:
    """
    便捷函数：对多个文本块进行多层级分块

    Args:
        text_chunks: TextChunk列表

    Returns:
        HierarchicalChunkResult
    """
    chunker = HierarchicalChunker()
    return chunker.chunk(text_chunks)
