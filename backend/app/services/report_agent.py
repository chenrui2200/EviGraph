"""
Report Agent Service
Generate analytical reports based on GraphRAG knowledge base
"""

import os
import json
import time
import re
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..config import Config
from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger
from .graph_tools import (
    GraphToolsService,
    SearchResult,
    InsightForgeResult,
    PanoramaResult
)

logger = get_logger('mirofish.report_agent')


class ReportLogger:
    """Detailed logger for report generation"""
    def __init__(self, report_id: str):
        self.report_id = report_id
        self.log_file_path = os.path.join(
            Config.UPLOAD_FOLDER, 'reports', report_id, 'agent_log.jsonl'
        )
        self.start_time = datetime.now()
        self._ensure_log_file()

    def _ensure_log_file(self):
        os.makedirs(os.path.dirname(self.log_file_path), exist_ok=True)

    def _get_elapsed_time(self) -> float:
        return (datetime.now() - self.start_time).total_seconds()

    def log(self, action: str, stage: str, details: Dict[str, Any], section_title: str = None, section_index: int = None):
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "elapsed_seconds": round(self._get_elapsed_time(), 2),
            "report_id": self.report_id,
            "action": action,
            "stage": stage,
            "section_title": section_title,
            "section_index": section_index,
            "details": details
        }
        with open(self.log_file_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')

    def log_start(self, simulation_id: str, graph_id: str, simulation_requirement: str):
        self.log("report_start", "pending", {
            "simulation_id": simulation_id,
            "graph_id": graph_id,
            "simulation_requirement": simulation_requirement,
            "message": "Report generation task started"
        })

    def log_planning_start(self):
        self.log("planning_start", "planning", {"message": "Started planning report outline"})

    def log_planning_complete(self, outline_dict: Dict[str, Any]):
        self.log("planning_complete", "planning", {"message": "Outline planning completed", "outline": outline_dict})

    def log_section_start(self, section_title: str, section_index: int):
        self.log("section_start", "generating", {"message": f"Started generating section: {section_title}"}, section_title, section_index)

    def log_tool_call(self, section_title: str, section_index: int, tool_name: str, parameters: Dict[str, Any], iteration: int):
        self.log("tool_call", "generating", {"iteration": iteration, "tool_name": tool_name, "parameters": parameters}, section_title, section_index)

    def log_tool_result(self, section_title: str, section_index: int, tool_name: str, result: str, iteration: int):
        self.log("tool_result", "generating", {"iteration": iteration, "tool_name": tool_name, "result": result, "result_length": len(result)}, section_title, section_index)

    def log_llm_response(self, section_title: str, section_index: int, response: str, iteration: int, has_tool_calls: bool, has_final_answer: bool):
        self.log("llm_response", "generating", {"iteration": iteration, "response": response, "has_tool_calls": has_tool_calls, "has_final_answer": has_final_answer}, section_title, section_index)

    def log_section_content(self, section_title: str, section_index: int, content: str, tool_calls_count: int):
        self.log("section_content", "generating", {"content": content, "tool_calls_count": tool_calls_count}, section_title, section_index)

    def log_section_full_complete(self, section_title: str, section_index: int, full_content: str):
        self.log("section_complete", "generating", {"content": full_content}, section_title, section_index)

    def log_report_complete(self, total_sections: int, total_time_seconds: float):
        self.log("report_complete", "completed", {"total_sections": total_sections, "total_time_seconds": round(total_time_seconds, 2)})

    def log_error(self, error_message: str, stage: str, section_title: str = None):
        self.log("error", stage, {"error": error_message}, section_title)


class ReportConsoleLogger:
    """Console-style logger for report generation process"""
    def __init__(self, report_id: str):
        self.report_id = report_id
        self.log_file_path = os.path.join(Config.UPLOAD_FOLDER, 'reports', report_id, 'console_log.txt')
        os.makedirs(os.path.dirname(self.log_file_path), exist_ok=True)
        self._setup_file_handler()

    def _setup_file_handler(self):
        import logging
        self._file_handler = logging.FileHandler(self.log_file_path, mode='a', encoding='utf-8')
        self._file_handler.setLevel(logging.INFO)
        formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s', datefmt='%H:%M:%S')
        self._file_handler.setFormatter(formatter)
        for name in ['mirofish.report_agent', 'mirofish.graph_tools']:
            logging.getLogger(name).addHandler(self._file_handler)

    def close(self):
        import logging
        if hasattr(self, '_file_handler') and self._file_handler:
            for name in ['mirofish.report_agent', 'mirofish.graph_tools']:
                logging.getLogger(name).removeHandler(self._file_handler)
            self._file_handler.close()
            self._file_handler = None

    def __del__(self):
        self.close()


class ReportStatus(str, Enum):
    PENDING = "pending"
    PLANNING = "planning"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ReportSection:
    title: str
    content: str = ""
    def to_dict(self) -> Dict[str, Any]:
        return {"title": self.title, "content": self.content}
    def to_markdown(self, level: int = 2) -> str:
        md = f"{'#' * level} {self.title}\n\n"
        if self.content: md += f"{self.content}\n\n"
        return md


@dataclass
class ReportOutline:
    title: str
    summary: str
    sections: List[ReportSection]
    def to_dict(self) -> Dict[str, Any]:
        return {"title": self.title, "summary": self.summary, "sections": [s.to_dict() for s in self.sections]}
    def to_markdown(self) -> str:
        md = f"# {self.title}\n\n> {self.summary}\n\n"
        for section in self.sections: md += section.to_markdown()
        return md


@dataclass
class Report:
    report_id: str
    simulation_id: str
    graph_id: str
    simulation_requirement: str
    status: ReportStatus
    outline: Optional[ReportOutline] = None
    markdown_content: str = ""
    created_at: str = ""
    completed_at: str = ""
    error: Optional[str] = None
    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "simulation_id": self.simulation_id,
            "graph_id": self.graph_id,
            "simulation_requirement": self.simulation_requirement,
            "status": self.status.value,
            "outline": self.outline.to_dict() if self.outline else None,
            "markdown_content": self.markdown_content,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "error": self.error
        }


# ── Tool Descriptions ──

TOOL_DESC_INSIGHT_FORGE = "[InsightForge] Automatically decompose inquiry into sub-questions and perform multi-dimensional GraphRAG retrieval with source evidence."
TOOL_DESC_PANORAMA_SEARCH = "[PanoramaSearch] Get comprehensive view of entities and relations related to a topic with traceability information."
TOOL_DESC_QUICK_SEARCH = "[QuickSearch] Fast lookup for specific entities or facts."
TOOL_DESC_GET_PAGE_CONTEXT = "[get_page_context] Retrieve all raw text and extracted facts from a specific document page. Parameters: document_name (str), page_number (int). Use this when you find a relevant fact and want to see its surroundings."

PLAN_SYSTEM_PROMPT = """\
You are an expert in building and analyzing "GraphRAG Knowledge Bases". You have access to a structured knowledge graph built from user-uploaded PDF documents.

[Your Task]
Plan a comprehensive analytical report that answers the user's inquiry by exploring the entities, relations, and evidence stored in the graph.

[Report Positioning]
- ✅ Evidence-backed: Every finding must be linked to specific evidence from the source documents.
- ✅ Traceable: Highlighting where information came from (file names, page numbers).
- ✅ Structured: Clear logical flow from entity overview to complex relationship analysis.

Please output the report outline in JSON format as follows:
{
    "title": "Report Title",
    "summary": "Report Summary (one sentence summarizing core findings from the knowledge base)",
    "sections": [
        {
            "title": "Section Title",
            "description": "What aspects of the knowledge base this section will cover"
        }
    ]
}

Note: sections array must have at least 2 and at most 5 elements!"""

PLAN_USER_PROMPT_TEMPLATE = """\
[User Inquiry]
{simulation_requirement}

[Knowledge Base Scale]
- Number of entities: {total_nodes}
- Number of relationships: {total_edges}
- Entity types: {entity_types}

[Sample Facts from Knowledge Base]
{related_facts_json}

Based on the inquiry and the knowledge base content, design the most appropriate report structure. Focus on answering the user's questions using the available structured data."""

SECTION_SYSTEM_PROMPT_TEMPLATE = """\
You are an expert in writing "Evidence-Based Knowledge Reports" based on GraphRAG retrieval results.

Report Title: {report_title}
Report Summary: {report_summary}
Primary Goal: {simulation_requirement}

Current Section to Write: {section_title}

═══════════════════════════════════════════════════════════════
[Most Important Rules - MUST FOLLOW]
═══════════════════════════════════════════════════════════════

1. [Traceability & Evidence]
   - Every fact you mention MUST include its source citation in brackets, e.g., "[Source: manual.pdf, Page: 12]".
   - The citations are provided by the retrieval tools in the facts list. DO NOT strip them.
   - If multiple sources support a point, list all of them.

2. [Must Call Tools]
   - You must call tools at least 3 times (maximum 5 times) to gather sufficient evidence from the knowledge graph.
   - Forbidden to use your own pre-trained knowledge to answer.

3. [Original Citations]
   - When quoting specific definitions or statements from the documents, use the blockquote format:
     > "Original text..." [Source: name.pdf, Page: X]

4. [Language Consistency]
   - If the inquiry and documents are in Chinese, the report must be in Chinese.
   - Translate English tool results to fluent Chinese while PRESERVING the source citations in their original bracketed format.

═══════════════════════════════════════════════════════════════
[⚠️ Format Specification - Extremely Important!]
═══════════════════════════════════════════════════════════════
- Each section is body text only.
- ❌ NO Markdown titles (#, ##, ###) allowed inside the section.
- ✅ Use **bold** for emphasis and sub-headings.
- ✅ Use lists and paragraphs for organization.
- Citations must be placed immediately after the relevant sentence or quote.

═══════════════════════════════════════════════════════════════
[Available Retrieval Tools] (call 3-5 times per section)
═══════════════════════════════════════════════════════════════

{tools_description}

═══════════════════════════════════════════════════════════════
[Workflow]
═══════════════════════════════════════════════════════════════

Each reply you can only do one of two things:

Option A - Call Tool:
Output your thinking, then call a tool using format:
<tool_call>
{{"name": "Tool Name", "parameters": {{"parameter_name": "parameter_value"}}}}
</tool_call>

Option B - Output Final Content:
When you have gathered enough information, start with "Final Answer:" and output section content.

[Section Content Requirements]
1. Content must be based on data retrieved by tools.
2. Heavily cite sources [Source: name.pdf, Page: X] to demonstrate traceability.
3. Use Markdown format (NO titles).
4. Quotes must be standalone paragraphs with blank lines before and after.
"""

SECTION_USER_PROMPT_TEMPLATE = """\
Completed Section Content:
{previous_content}

═══════════════════════════════════════════════════════════════
[Current Task] Write Section: {section_title}
═══════════════════════════════════════════════════════════════

[Important Reminders]
1. DO NOT repeat content from previous sections.
2. You must call tools to get evidence before writing.
3. Every claim MUST have a bracketed citation [Source: ..., Page: ...].

Please start by thinking (Thought) then calling a tool (Action). After gathering evidence, provide Final Answer.
"""

REACT_OBSERVATION_TEMPLATE = """\
Observation (Retrieval Result):

═══ Tool {tool_name} Returned ═══
{result}

═══════════════════════════════════════════════════════════════
Called tools {tool_calls_count}/{max_tool_calls}.
- Information sufficient? Start with "Final Answer:".
- Need more? Call another tool.
"""

# ReACT loop messages
REACT_INSUFFICIENT_TOOLS_MSG = "[Notice] Only {tool_calls_count} tools used, need at least {min_tool_calls}. Call more tools for evidence."
REACT_TOOL_LIMIT_MSG = "Tool limit reached ({tool_calls_count}/{max_tool_calls}). Provide Final Answer based on evidence."
REACT_FORCE_FINAL_MSG = "Tool limit reached, please directly output Final Answer:"


class ReportAgent:
    """Agent for generating traceable knowledge reports from GraphRAG"""

    MAX_TOOL_CALLS_PER_SECTION = 5

    def __init__(self, graph_id: str, simulation_id: str, simulation_requirement: str, llm_client: Optional[LLMClient] = None, graph_tools: Optional[GraphToolsService] = None):
        self.graph_id = graph_id
        self.simulation_id = simulation_id
        self.simulation_requirement = simulation_requirement
        self.llm = llm_client or LLMClient()
        if not graph_tools: raise ValueError("graph_tools required")
        self.graph_tools = graph_tools
        self.tools = self._define_tools()
        self.report_logger = None
        self.console_logger = None

    def _define_tools(self):
        return {
            "insight_forge": {"name": "insight_forge", "description": TOOL_DESC_INSIGHT_FORGE, "parameters": {"query": "Search query"}},
            "panorama_search": {"name": "panorama_search", "description": TOOL_DESC_PANORAMA_SEARCH, "parameters": {"query": "Topic"}},
            "quick_search": {"name": "quick_search", "description": TOOL_DESC_QUICK_SEARCH, "parameters": {"query": "Query"}},
            "get_page_context": {"name": "get_page_context", "description": TOOL_DESC_GET_PAGE_CONTEXT, "parameters": {"document_name": "Filename", "page_number": "Integer"}}
        }

    def _execute_tool(self, tool_name: str, parameters: Dict[str, Any], report_context: str = "") -> str:
        logger.info(f"Executing tool: {tool_name}, parameters: {parameters}")
        try:
            if tool_name == "insight_forge":
                query = parameters.get("query", "")
                ctx = parameters.get("report_context", "") or report_context
                result = self.graph_tools.insight_forge(
                    graph_id=self.graph_id,
                    query=query,
                    simulation_requirement=self.simulation_requirement,
                    report_context=ctx
                )
                return result.to_text()
            elif tool_name == "panorama_search":
                query = parameters.get("query", "")
                result = self.graph_tools.panorama_search(
                    graph_id=self.graph_id,
                    query=query
                )
                return result.to_text()
            elif tool_name == "quick_search":
                query = parameters.get("query", "")
                result = self.graph_tools.quick_search(
                    graph_id=self.graph_id,
                    query=query
                )
                return result.to_text()
            elif tool_name == "get_page_context":
                doc = parameters.get("document_name", "")
                page = parameters.get("page_number")
                if isinstance(page, str) and page.isdigit():
                    page = int(page)
                result = self.graph_tools.get_page_context(
                    graph_id=self.graph_id,
                    document_name=doc,
                    page_number=page
                )
                return result
            return f"Unknown tool: {tool_name}"
        except Exception as e:
            return f"Error: {str(e)}"

    def _parse_tool_calls(self, response: str) -> List[Dict[str, Any]]:
        calls = []
        xml_pattern = r'<tool_call>\s*(\{.*?\})\s*</tool_call>'
        for match in re.finditer(xml_pattern, response, re.DOTALL):
            try: calls.append(json.loads(match.group(1)))
            except: pass
        if not calls:
            try:
                stripped = response.strip()
                if stripped.startswith('{') and stripped.endswith('}'):
                    data = json.loads(stripped)
                    if data.get("name") in ["insight_forge", "panorama_search", "quick_search", "get_page_context"]:
                        calls.append(data)
            except: pass
        return calls

    def _get_tools_description(self) -> str:
        parts = ["Available Tools:"]
        for name, tool in self.tools.items():
            parts.append(f"- {name}: {tool['description']}")
        return "\n".join(parts)

    def plan_outline(self, progress_callback=None) -> ReportOutline:
        if progress_callback: progress_callback("planning", 10, "Planning outline...")
        context = self.graph_tools.get_simulation_context(self.graph_id, self.simulation_requirement)
        sys_prompt = PLAN_SYSTEM_PROMPT
        user_prompt = PLAN_USER_PROMPT_TEMPLATE.format(
            simulation_requirement=self.simulation_requirement,
            total_nodes=context.get('graph_statistics', {}).get('total_nodes', 0),
            total_edges=context.get('graph_statistics', {}).get('total_edges', 0),
            entity_types=list(context.get('graph_statistics', {}).get('entity_types', {}).keys()),
            related_facts_json=json.dumps(context.get('related_facts', [])[:5], ensure_ascii=False)
        )
        try:
            res = self.llm.chat_json([{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_prompt}], temperature=0.3)
            sections = [ReportSection(s.get("title", "")) for s in res.get("sections", [])]
            return ReportOutline(res.get("title", "Analysis Report"), res.get("summary", ""), sections)
        except:
            return ReportOutline("Analysis Report", "Knowledge base summary", [ReportSection("Overview"), ReportSection("Detail Analysis")])

    def _generate_section_react(self, section, outline, previous_sections, progress_callback=None, section_index=0) -> str:
        if self.report_logger: self.report_logger.log_section_start(section.title, section_index)
        sys_prompt = SECTION_SYSTEM_PROMPT_TEMPLATE.format(
            report_title=outline.title, report_summary=outline.summary,
            simulation_requirement=self.simulation_requirement, section_title=section.title,
            tools_description=self._get_tools_description()
        )
        prev_content = "\n\n".join(previous_sections) if previous_sections else "(First section)"
        user_prompt = SECTION_USER_PROMPT_TEMPLATE.format(previous_content=prev_content, section_title=section.title)
        messages = [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_prompt}]

        tool_calls_count = 0
        min_tool_calls = 3

        for iteration in range(5):
            response = self.llm.chat(messages, temperature=0.5)
            if not response: break

            tool_calls = self._parse_tool_calls(response)
            has_tool_calls = bool(tool_calls)
            has_final_answer = "Final Answer:" in response

            if self.report_logger:
                self.report_logger.log_llm_response(section.title, section_index, response, iteration+1, has_tool_calls, has_final_answer)

            if has_final_answer and tool_calls_count >= min_tool_calls:
                answer = response.split("Final Answer:")[-1].strip()
                return answer

            if has_tool_calls and tool_calls_count < self.MAX_TOOL_CALLS_PER_SECTION:
                call = tool_calls[0]
                if self.report_logger: self.report_logger.log_tool_call(section.title, section_index, call["name"], call.get("parameters", {}), iteration+1)
                result = self._execute_tool(call["name"], call.get("parameters", {}))
                if self.report_logger: self.report_logger.log_tool_result(section.title, section_index, call["name"], result, iteration+1)
                tool_calls_count += 1
                messages.append({"role": "assistant", "content": response})
                messages.append({"role": "user", "content": REACT_OBSERVATION_TEMPLATE.format(tool_name=call["name"], result=result, tool_calls_count=tool_calls_count, max_tool_calls=self.MAX_TOOL_CALLS_PER_SECTION)})
                continue

            if has_final_answer: # Insufficient tools but LLM tried to end
                messages.append({"role": "assistant", "content": response})
                messages.append({"role": "user", "content": REACT_INSUFFICIENT_TOOLS_MSG.format(tool_calls_count=tool_calls_count, min_tool_calls=min_tool_calls)})
                continue

            break # Fallback

        return response.split("Final Answer:")[-1].strip() if response and "Final Answer:" in response else (response or "")

    def generate_report(self, progress_callback=None, report_id=None) -> Report:
        import uuid
        if not report_id: report_id = f"report_{uuid.uuid4().hex[:12]}"
        self.report_logger = ReportLogger(report_id)
        self.console_logger = ReportConsoleLogger(report_id)
        report = Report(report_id, self.simulation_id, self.graph_id, self.simulation_requirement, ReportStatus.PENDING, created_at=datetime.now().isoformat())

        try:
            ReportManager._ensure_report_folder(report_id)
            outline = self.plan_outline(progress_callback)
            report.outline = outline
            report.status = ReportStatus.GENERATING
            generated = []
            for i, section in enumerate(outline.sections):
                content = self._generate_section_react(section, outline, generated, progress_callback, i+1)
                section.content = content
                generated.append(f"## {section.title}\n\n{content}")
                ReportManager.save_section(report_id, i+1, section)

            report.markdown_content = ReportManager.assemble_full_report(report_id, outline)
            report.status = ReportStatus.COMPLETED
            report.completed_at = datetime.now().isoformat()
            ReportManager.save_report(report)
            return report
        except Exception as e:
            report.status = ReportStatus.FAILED
            report.error = str(e)
            return report


class ReportManager:
    REPORTS_DIR = os.path.join(Config.UPLOAD_FOLDER, 'reports')
    @classmethod
    def _get_report_folder(cls, report_id: str) -> str: return os.path.join(cls.REPORTS_DIR, report_id)
    @classmethod
    def _ensure_report_folder(cls, report_id: str): os.makedirs(cls._get_report_folder(report_id), exist_ok=True)
    @classmethod
    def _get_report_path(cls, report_id: str): return os.path.join(cls._get_report_folder(report_id), "meta.json")
    @classmethod
    def save_section(cls, report_id, index, section):
        cls._ensure_report_folder(report_id)
        path = os.path.join(cls._get_report_folder(report_id), f"section_{index:02d}.md")
        with open(path, 'w', encoding='utf-8') as f: f.write(f"## {section.title}\n\n{section.content}")
    @classmethod
    def assemble_full_report(cls, report_id, outline):
        full = f"# {outline.title}\n\n> {outline.summary}\n\n"
        folder = cls._get_report_folder(report_id)
        for f in sorted(os.listdir(folder)):
            if f.startswith("section_") and f.endswith(".md"):
                with open(os.path.join(folder, f), 'r', encoding='utf-8') as sf: full += sf.read() + "\n\n"
        return full
    @classmethod
    def save_report(cls, report):
        cls._ensure_report_folder(report.report_id)
        with open(cls._get_report_path(report.report_id), 'w', encoding='utf-8') as f: json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
    @classmethod
    def get_report(cls, report_id: str) -> Optional[Report]:
        path = cls._get_report_path(report_id)
        if not os.path.exists(path):
            return None
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # Rebuild report object logic
        return Report(
            report_id=data['report_id'],
            simulation_id=data['simulation_id'],
            graph_id=data['graph_id'],
            simulation_requirement=data['simulation_requirement'],
            status=ReportStatus(data['status']),
            markdown_content=data.get('markdown_content', ''),
            created_at=data.get('created_at', ''),
            completed_at=data.get('completed_at', ''),
            error=data.get('error')
        )

    @classmethod
    def get_report_by_simulation(cls, simulation_id: str) -> Optional[Report]:
        cls._ensure_reports_dir()
        for item in os.listdir(cls.REPORTS_DIR):
            folder_path = os.path.join(cls.REPORTS_DIR, item)
            if os.path.isdir(folder_path):
                report = cls.get_report(item)
                if report and report.simulation_id == simulation_id:
                    return report
        return None

    @classmethod
    def update_progress(cls, report_id: str, status: str, progress: int, message: str, **kwargs):
        path = os.path.join(cls._get_report_folder(report_id), 'progress.json')
        data = {"status": status, "progress": progress, "message": message, "updated_at": datetime.now().isoformat(), **kwargs}
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @classmethod
    def _ensure_reports_dir(cls):
        os.makedirs(cls.REPORTS_DIR, exist_ok=True)
