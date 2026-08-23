"""Phase 3 smoke test — LLM gateway end-to-end with fallback routing."""
import asyncio
from app.llm import get_gateway


async def main():
    """Compute the main."""
    gw, model = get_gateway("claude-haiku-4-5")

    r = await gw.complete(
        model=model,
        system="You are a calculator. Return only the number.",
        user="What is 17 * 23?",
        max_tokens=200,
    )

    print(f"Asked for claude-haiku-4-5, ran on {model}, got {r.text!r}")
    print(f"Tokens: in={r.input_tokens} out={r.output_tokens}")


if __name__ == "__main__":
    asyncio.run(main())