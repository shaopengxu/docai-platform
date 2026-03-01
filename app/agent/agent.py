"""
DocAI Platform - Document Agent (Phase 4)
ReAct 风格 Agent：Thought → Action → Observation 循环
可自主规划和执行复杂文档查询
"""

from __future__ import annotations

import json
import time
from typing import Any, AsyncGenerator

import structlog

from app.agent.tools import (
    TOOL_DEFINITIONS,
    execute_tool,
    get_tools_description,
)
from app.core.llm_client import llm
from app.core.models import (
    AgentResponse,
    AgentStep,
    Citation,
    QueryResponse,
)

logger = structlog.get_logger()

# ═══════════════════════════════════════════════════════════════════════════
# Agent System Prompt
# ═══════════════════════════════════════════════════════════════════════════

AGENT_SYSTEM_PROMPT = """你是一个企业文档分析助手（DocAI Agent）。你可以使用以下工具来回答用户的问题。

{tools_description}

## 处理问题的原则

1. **先理解再行动**: 分析用户问题的类型、范围和复杂度
2. **事实性问题**: 直接检索后回答，1-2步搞定
3. **总结性问题**: 先看文档摘要了解全局，需要时再深入细节
4. **对比类问题**: 分别获取各方信息后综合分析
5. **版本相关问题**: 先查版本历史，再做针对性对比
6. **效率优先**: 每步操作后评估是否已有足够信息回答
7. **引用来源**: 答案必须标注信息来源（文档名、章节、页码）

## 回复格式

你必须严格按照以下 JSON 格式回复，不要输出任何其他文字:

当需要调用工具时:
```json
{{
  "thought": "你的思考过程...",
  "action": "工具名称",
  "action_input": {{
    "参数名": "参数值"
  }}
}}
```

当信息足够可以给出最终答案时:
```json
{{
  "thought": "总结分析...",
  "final_answer": "最终答案内容，包含引用来源标注"
}}
```

重要: 回复中 **只能** 包含一个 JSON 对象，不要有任何额外文字。
"""


# ═══════════════════════════════════════════════════════════════════════════
# DocumentAgent 核心类
# ═══════════════════════════════════════════════════════════════════════════


class DocumentAgent:
    """文档分析 Agent，使用 ReAct 模式自主规划和执行复杂查询"""

    MAX_STEPS = 8

    def __init__(self):
        self.system_prompt = AGENT_SYSTEM_PROMPT.format(
            tools_description=get_tools_description()
        )

    async def run(self, query: str, accessible_doc_ids: list[str] | None = None) -> AgentResponse:
        """
        执行完整的 Agent 循环（非流式）

        Returns:
            AgentResponse 包含答案、步骤记录、引用等
        """
        start_time = time.time()
        steps: list[AgentStep] = []
        messages = self._init_messages(query)

        for step_num in range(1, self.MAX_STEPS + 1):
            step_start = time.time()

            # 调用 LLM 获取下一步决策
            try:
                response_text = await llm.generate(
                    prompt=self._format_messages(messages),
                    system_prompt=self.system_prompt,
                    temperature=0.1,
                    max_tokens=2000,
                )
            except Exception as e:
                logger.error("Agent LLM call failed", step=step_num, error=str(e))
                return self._error_response(
                    f"Agent 推理失败: {str(e)}", steps, start_time
                )

            # 解析 LLM 响应
            parsed = self._parse_llm_response(response_text)

            if parsed is None:
                logger.warning("Agent response parse failed", raw=response_text[:300])
                # 将无法解析的响应作为最终结果
                return AgentResponse(
                    answer=response_text,
                    steps=steps,
                    total_steps=len(steps),
                    latency_ms=int((time.time() - start_time) * 1000),
                    route="agent",
                )

            thought = parsed.get("thought", "")

            # 检查是否有最终答案
            if "final_answer" in parsed:
                step = AgentStep(
                    step_number=step_num,
                    thought=thought,
                    action="final_answer",
                    duration_ms=int((time.time() - step_start) * 1000),
                )
                steps.append(step)

                return AgentResponse(
                    answer=parsed["final_answer"],
                    citations=self._extract_citations_from_steps(steps),
                    confidence=0.85,
                    latency_ms=int((time.time() - start_time) * 1000),
                    steps=steps,
                    total_steps=len(steps),
                    route="agent",
                )

            # 执行工具调用
            action = parsed.get("action", "")
            action_input = parsed.get("action_input", {})

            if not action:
                logger.warning("No action in parsed response", parsed=parsed)
                return self._error_response(
                    "Agent 未能决定下一步操作", steps, start_time
                )

            # 执行工具
            observation = await execute_tool(action, action_input, accessible_doc_ids=accessible_doc_ids)

            step = AgentStep(
                step_number=step_num,
                thought=thought,
                action=action,
                action_input=action_input,
                observation=observation[:1500],  # 限制存储大小
                duration_ms=int((time.time() - step_start) * 1000),
            )
            steps.append(step)

            logger.info(
                "Agent step completed",
                step=step_num,
                action=action,
                obs_len=len(observation),
            )

            # 将结果加入消息历史
            messages.append({
                "role": "assistant",
                "content": response_text,
            })
            messages.append({
                "role": "user",
                "content": f"工具返回结果 (Observation):\n{observation}\n\n请继续分析。如果信息已经足够回答用户问题，请给出 final_answer。",
            })

        # 超过最大步骤，强制生成答案
        return await self._force_final_answer(messages, steps, start_time)

    async def run_stream(self, query: str, accessible_doc_ids: list[str] | None = None) -> AsyncGenerator[dict, None]:
        """
        流式执行 Agent，通过 yield 逐步返回:
        - {"type": "agent_step", ...}  Agent 的思考和工具调用步骤
        - {"type": "token", "content": ...}  最终答案的流式 token
        - {"type": "sources", "citations": [...]}  引用来源
        - {"type": "done"}  完成
        """
        start_time = time.time()
        steps: list[AgentStep] = []
        messages = self._init_messages(query)

        for step_num in range(1, self.MAX_STEPS + 1):
            step_start = time.time()

            try:
                response_text = await llm.generate(
                    prompt=self._format_messages(messages),
                    system_prompt=self.system_prompt,
                    temperature=0.1,
                    max_tokens=2000,
                )
            except Exception as e:
                yield {"type": "error", "message": f"Agent 推理失败: {str(e)}"}
                return

            parsed = self._parse_llm_response(response_text)

            if parsed is None:
                # 无法解析时当最终答案直接输出
                yield {"type": "token", "content": response_text}
                yield {"type": "done"}
                return

            thought = parsed.get("thought", "")

            # 最终答案
            if "final_answer" in parsed:
                step = AgentStep(
                    step_number=step_num,
                    thought=thought,
                    action="final_answer",
                    duration_ms=int((time.time() - step_start) * 1000),
                )
                steps.append(step)

                yield {
                    "type": "agent_step",
                    "step_number": step_num,
                    "thought": thought,
                    "action": "生成最终答案",
                    "status": "complete",
                }

                # 发送引用
                citations = self._extract_citations_from_steps(steps)
                if citations:
                    yield {
                        "type": "sources",
                        "citations": [c.model_dump() for c in citations],
                    }

                # 流式输出最终答案
                answer = parsed["final_answer"]
                # 模拟分块输出，每次约 20 字
                chunk_size = 20
                for i in range(0, len(answer), chunk_size):
                    yield {"type": "token", "content": answer[i:i + chunk_size]}

                yield {"type": "done"}
                return

            # 工具调用步骤
            action = parsed.get("action", "")
            action_input = parsed.get("action_input", {})

            if not action:
                yield {"type": "error", "message": "Agent 未能决定下一步操作"}
                return

            # 发送步骤信息到前端
            yield {
                "type": "agent_step",
                "step_number": step_num,
                "thought": thought,
                "action": action,
                "action_input": action_input,
                "status": "executing",
            }

            # 执行工具
            observation = await execute_tool(action, action_input, accessible_doc_ids=accessible_doc_ids)

            step = AgentStep(
                step_number=step_num,
                thought=thought,
                action=action,
                action_input=action_input,
                observation=observation[:1500],
                duration_ms=int((time.time() - step_start) * 1000),
            )
            steps.append(step)

            # 通知前端该步骤完成
            yield {
                "type": "agent_step",
                "step_number": step_num,
                "thought": thought,
                "action": action,
                "observation_preview": observation[:200],
                "status": "done",
                "duration_ms": step.duration_ms,
            }

            messages.append({
                "role": "assistant",
                "content": response_text,
            })
            messages.append({
                "role": "user",
                "content": f"工具返回结果 (Observation):\n{observation}\n\n请继续分析。如果信息已经足够回答用户问题，请给出 final_answer。",
            })

        # 超过最大步骤，强制生成答案
        yield {
            "type": "agent_step",
            "step_number": self.MAX_STEPS + 1,
            "thought": "已达到最大推理步骤，正在综合已有信息给出答案。",
            "action": "force_final_answer",
            "status": "executing",
        }

        force_prompt = (
            "你已经完成了多步分析，现在请基于之前收集到的所有信息，"
            "直接给出最终答案。不要再调用工具，直接以文本形式回答用户问题。"
            "答案中请标注引用来源。"
        )
        messages.append({"role": "user", "content": force_prompt})

        try:
            async for token in llm.generate_stream(
                prompt=self._format_messages(messages),
                system_prompt="你是一个文档分析助手。请直接回答问题。",
                temperature=0.1,
            ):
                yield {"type": "token", "content": token}
        except Exception as e:
            yield {"type": "error", "message": f"生成最终答案失败: {str(e)}"}

        yield {"type": "done"}

    # ─── 内部方法 ─────────────────────────────────────────────────────────

    def _init_messages(self, query: str) -> list[dict[str, str]]:
        return [
            {"role": "user", "content": f"用户问题: {query}"},
        ]

    def _format_messages(self, messages: list[dict[str, str]]) -> str:
        """将消息列表格式化为单个 prompt 字符串"""
        parts = []
        for msg in messages:
            role = "用户" if msg["role"] == "user" else "助手"
            parts.append(f"[{role}]\n{msg['content']}")
        return "\n\n".join(parts)

    def _parse_llm_response(self, text: str) -> dict | None:
        """从 LLM 回复中解析 JSON"""
        text = text.strip()

        # 尝试直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 去掉 markdown 代码块
        if "```json" in text:
            start = text.index("```json") + 7
            end = text.index("```", start) if "```" in text[start:] else len(text)
            json_str = text[start:end].strip()
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass

        if "```" in text:
            start = text.index("```") + 3
            # Skip language identifier on first line
            if "\n" in text[start:start+20]:
                start = text.index("\n", start) + 1
            end = text.index("```", start) if "```" in text[start:] else len(text)
            json_str = text[start:end].strip()
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass

        # 尝试提取 JSON 对象（花括号匹配）
        brace_start = text.find("{")
        if brace_start >= 0:
            depth = 0
            for i in range(brace_start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        json_str = text[brace_start:i + 1]
                        try:
                            return json.loads(json_str)
                        except json.JSONDecodeError:
                            break

        return None

    def _extract_citations_from_steps(self, steps: list[AgentStep]) -> list[Citation]:
        """从 Agent 步骤中提取引用信息"""
        citations: list[Citation] = []
        seen_docs: set[str] = set()

        for step in steps:
            if step.action == "search_documents" and step.observation:
                # 从检索结果中提取引用
                lines = step.observation.split("\n")
                current_title = ""
                current_pages = ""
                for line in lines:
                    if line.startswith("[") and "》" in line:
                        # 解析类似 [1] 《XX文档》[章节] (第X页) 的行
                        try:
                            title_start = line.index("《") + 1
                            title_end = line.index("》")
                            current_title = line[title_start:title_end]

                            section = ""
                            if "[" in line[title_end:] and "]" in line[title_end:]:
                                s_start = line.index("[", title_end) + 1
                                s_end = line.index("]", s_start)
                                section = line[s_start:s_end]

                            page_nums: list[int] = []
                            if "(第" in line:
                                p_start = line.index("(第") + 2
                                p_end = line.index("页)", p_start)
                                page_str = line[p_start:p_end]
                                page_nums = [int(p.strip()) for p in page_str.split(",") if p.strip().isdigit()]

                            key = f"{current_title}|{section}"
                            if key not in seen_docs:
                                seen_docs.add(key)
                                citations.append(Citation(
                                    doc_id="",
                                    doc_title=current_title,
                                    section_path=section,
                                    page_numbers=page_nums,
                                ))
                        except (ValueError, IndexError):
                            continue

        return citations[:10]  # 最多10个引用

    def _error_response(
        self, message: str, steps: list[AgentStep], start_time: float
    ) -> AgentResponse:
        return AgentResponse(
            answer=message,
            steps=steps,
            total_steps=len(steps),
            latency_ms=int((time.time() - start_time) * 1000),
            route="agent",
        )

    async def _force_final_answer(
        self,
        messages: list[dict[str, str]],
        steps: list[AgentStep],
        start_time: float,
    ) -> AgentResponse:
        """超过最大步骤数，强制生成答案"""
        force_prompt = (
            "你已经完成了多步分析，现在请基于之前收集到的所有信息，"
            "直接给出最终答案。不要再调用工具。答案中请标注引用来源。"
        )
        messages.append({"role": "user", "content": force_prompt})

        try:
            answer = await llm.generate(
                prompt=self._format_messages(messages),
                system_prompt="你是一个文档分析助手。请直接回答问题。",
                temperature=0.1,
            )
        except Exception as e:
            answer = f"Agent 在达到最大步骤后无法生成最终答案: {str(e)}"

        return AgentResponse(
            answer=answer,
            citations=self._extract_citations_from_steps(steps),
            confidence=0.6,
            latency_ms=int((time.time() - start_time) * 1000),
            steps=steps,
            total_steps=len(steps),
            route="agent",
        )


# ── 全局 Agent 实例 ──
document_agent = DocumentAgent()
