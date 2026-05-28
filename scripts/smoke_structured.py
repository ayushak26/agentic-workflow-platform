"""Phase 3 smoke test — structured output via the LLM gateway.

The reranker uses complete_structured with a Pydantic model as the
response_format. This test exercises that path with a trivial schema.
"""
import asyncio
from pydantic import BaseModel
from app.llm import get_gateway


class Answer(BaseModel):
    number: int
    reasoning: str


async def main():
    gw, model = get_gateway("claude-haiku-4-5")
    r = await gw.complete_structured(
        model=model,
        system="Compute the answer and explain in one sentence.",
        user="What is 17 * 23?",
        response_model=Answer,
        max_tokens=400,
    )
    print(f"Model: {model}")
    print(f"Result type: {type(r).__name__}")
    print(f"Number: {r.number}")
    print(f"Reasoning: {r.reasoning}")


if __name__ == "__main__":
    asyncio.run(main())