"""
LLM 客户端抽象层
支持 Anthropic Claude 和 OpenAI，统一接口
"""

from __future__ import annotations

import json
from typing import AsyncGenerator

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings

logger = structlog.get_logger()


class LLMClient:
    """统一的 LLM 调用接口"""

    def __init__(self, use_light_model: bool = False):
        """
        Args:
            use_light_model: True 时使用轻量级模型（更快更便宜，适合摘要/提取等简单任务）
        """
        self.use_light = use_light_model
        self._anthropic_client = None
        self._openai_client = None

    @property
    def provider(self) -> str:
        return settings.light_llm_provider if self.use_light else settings.llm_provider

    @property
    def model(self) -> str:
        if self.use_light:
            return settings.light_anthropic_model
        if self.provider == "anthropic":
            return settings.anthropic_model
        return settings.openai_model

    def _get_anthropic(self):
        if self._anthropic_client is None:
            from anthropic import AsyncAnthropic
            self._anthropic_client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        return self._anthropic_client

    def _get_openai(self):
        if self._openai_client is None:
            from openai import AsyncOpenAI
            self._openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
        return self._openai_client

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
    )
    async def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int | None = None,
        temperature: float = 0.1,
    ) -> str:
        """生成文本回复"""
        max_tokens = max_tokens or settings.anthropic_max_tokens

        if self.provider == "anthropic":
            client = self._get_anthropic()
            kwargs = {
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [{"role": "user", "content": prompt}],
            }
            if system_prompt:
                kwargs["system"] = system_prompt

            response = await client.messages.create(**kwargs)
            return response.content[0].text

        else:  # openai
            client = self._get_openai()
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            response = await client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return response.choices[0].message.content or ""

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
    )
    async def generate_json(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int | None = None,
    ) -> dict:
        """生成 JSON 格式回复（自动解析）"""
        full_prompt = prompt + "\n\n请只返回有效的 JSON，不要包含其他文字或 Markdown 代码块标记。"

        text = await self.generate(
            prompt=full_prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=0.0,
        )

        # 清理可能的 Markdown 代码块标记
        text = text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse LLM JSON response", error=str(e), raw_text=text[:500])
            raise ValueError(f"LLM returned invalid JSON: {e}") from e

    async def generate_stream(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int | None = None,
        temperature: float = 0.1,
    ) -> AsyncGenerator[str, None]:
        """流式生成文本（用于前端实时显示）"""
        max_tokens = max_tokens or settings.anthropic_max_tokens

        if self.provider == "anthropic":
            client = self._get_anthropic()
            kwargs = {
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [{"role": "user", "content": prompt}],
            }
            if system_prompt:
                kwargs["system"] = system_prompt

            async with client.messages.stream(**kwargs) as stream:
                async for text in stream.text_stream:
                    yield text

        else:  # openai
            client = self._get_openai()
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            stream = await client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
            )
            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content


# ── 预创建的客户端实例 ──

# 主力模型（复杂推理、最终答案生成）
llm = LLMClient(use_light_model=False)

# 轻量模型（摘要、实体提取、分类等简单任务）
llm_light = LLMClient(use_light_model=True)
