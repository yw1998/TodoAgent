"""OpenAI 兼容 LLM 客户端封装，配置每次调用时从 settings 热读取。"""

from openai import AsyncOpenAI

from app.services import setting_service


async def build_client(session) -> tuple[AsyncOpenAI, str, float, int]:
    cfg = await setting_service.get_settings(session)
    client = AsyncOpenAI(
        base_url=cfg.get("llm_base_url") or None,
        api_key=cfg.get("llm_api_key") or "EMPTY",
        timeout=float(cfg.get("llm_timeout", 30)),
    )
    return client, cfg.get("llm_model", ""), float(cfg.get("llm_temperature", 0.1)), int(cfg.get("llm_timeout", 30))


async def chat(session, messages: list[dict], tools: list[dict]):
    client, model, temperature, _ = await build_client(session)
    return await client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tools or None,
        tool_choice="auto" if tools else None,
        temperature=temperature,
    )
