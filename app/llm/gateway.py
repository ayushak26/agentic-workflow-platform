async def chat_with_tools(
    self,
    *,
    model: str,
    system: str | None,
    messages: list[dict],
    tools: list[dict],
    temperature: float = 0.2,
) -> dict:
    """Returns: {"content": str|None, "tool_calls": [{"id", "name", "arguments"}]}"""
    # Implementation depends on provider. For Anthropic:
    response = await self._anthropic.messages.create(
        model=model,
        system=system or "",
        messages=messages,
        tools=[{"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]} for t in tools],
        max_tokens=4096,
        temperature=temperature,
    )
    tool_calls = []
    text_parts = []
    for block in response.content:
        if block.type == "tool_use":
            tool_calls.append({
                "id": block.id,
                "name": block.name,
                "arguments": block.input,
            })
        elif block.type == "text":
            text_parts.append(block.text)
    return {
        "content": "\n".join(text_parts) if text_parts else None,
        "tool_calls": tool_calls,
    }