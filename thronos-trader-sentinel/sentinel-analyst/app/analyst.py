"""
Claude-powered market analyst.  Receives the structured context text
from ContextManager and returns LLM-generated briefings / Q&A answers.
"""
import os

from anthropic import AsyncAnthropic

_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "512"))

SYSTEM_PROMPT = (
    "You are Thronos, an elite crypto market intelligence analyst. "
    "You receive real-time risk signals and market data. "
    "Be concise, precise, and actionable. "
    "Always reference the composite risk score and its main drivers. Detect repeatable market patterns (trend continuation, distribution, accumulation, volatility squeeze/breakout) only when supported by context. "
    "Never speculate beyond the provided data. "
    "Format responses in plain text — no markdown headers."
)


async def get_briefing(context: str) -> dict:
    """Generate a short trader briefing from the current market context."""
    msg = await _get_client().messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": (
                f"{context}\n\n"
                "Give a concise trading briefing (3-5 sentences): "
                "current risk level, the 2-3 main drivers, and a clear recommended action. "
                "End with 'Pattern Watch:' and one short line describing the strongest supported pattern."
            ),
        }],
    )
    return {
        "ok": True,
        "briefing": msg.content[0].text,
        "model": MODEL,
        "usage": {"input": msg.usage.input_tokens, "output": msg.usage.output_tokens},
    }


async def ask_analyst(context: str, question: str) -> dict:
    """Answer a specific question given the current market context."""
    msg = await _get_client().messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": (
                f"{context}\n\nQuestion: {question}\n"
                "Include one short Pattern Watch line if a clear pattern exists in the provided context."
            ),
        }],
    )
    return {
        "ok": True,
        "answer": msg.content[0].text,
        "model": MODEL,
        "usage": {"input": msg.usage.input_tokens, "output": msg.usage.output_tokens},
    }
