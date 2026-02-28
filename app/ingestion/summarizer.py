import json
import structlog
from app.core.llm_client import llm_light
from app.core.models import Chunk, ChunkType
from config.settings import settings

logger = structlog.get_logger()

async def generate_section_summary(title: str, section_path: str, content: str) -> tuple[str, list[str]]:
    """生成章节摘要及要点 (返回 summary_text, key_points)"""
    prompt = f"""请对以下内容（属于文档《{title}》的【{section_path}】部分）生成约100-200字的简洁摘要，并提取3-5个关键要点。
返回 JSON 格式：
{{
  "summary": "...",
  "key_points": ["要点1", "要点2"]
}}

内容如下：
{content[:8000]}
"""
    try:
        res = await llm_light.generate_json(prompt)
        return res.get("summary", ""), res.get("key_points", [])
    except Exception as e:
        logger.error("section_summary_failed", error=str(e))
        return "", []

async def generate_doc_summary_and_entities(title: str, section_summaries_text: str) -> tuple[str, dict]:
    """生成文档的整体摘要和关键实体"""
    prompt = f"""基于以下文档《{title}》各章节的摘要，生成一份整体的文档总结（约300字），并提取文档中的关键实体。
返回 JSON 格式：
{{
  "doc_summary": "...",
  "key_entities": {{
    "organizations": ["...", "..."],
    "people": ["..."],
    "dates": ["..."],
    "amounts": ["..."]
  }}
}}

各章节摘要：
{section_summaries_text[:12000]}
"""
    try:
        res = await llm_light.generate_json(prompt)
        return res.get("doc_summary", ""), res.get("key_entities", {})
    except Exception as e:
        logger.error("doc_summary_failed", error=str(e))
        return "", {}

async def generate_contextual_description(doc_title: str, doc_summary: str, section_path: str, chunk_content: str) -> str:
    """生成用于 Contextual Retrieval 的 chunk 描述"""
    prompt = f"""<document_title>{doc_title}</document_title>
<document_summary>{doc_summary}</document_summary>
<section_path>{section_path}</section_path>
<chunk_content>{chunk_content[:2000]}</chunk_content>

请用简短的1-3句话（约50字），描述这个文本块在上方文档中所处的上下文背景或核心作用。只需返回这段描述文字。
"""
    try:
        return await llm_light.generate(prompt, temperature=0.1)
    except Exception as e:
        return ""
