import os
import anthropic


_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def _build_prompt(data: dict) -> str:
    lines = ["You are a market intelligence analyst. Summarize the following market data concisely.\n"]

    if data.get("quotes"):
        lines.append("## Price Quotes")
        for symbol, q in data["quotes"].items():
            lines.append(
                f"- {symbol}: ${q['price']}  ({q['change_percent']}) on {q['latest_trading_day']}"
            )
        lines.append("")

    if data.get("news"):
        lines.append("## Recent News")
        for topic, articles in data["news"].items():
            lines.append(f"\n### {topic}")
            for a in articles[:5]:
                lines.append(f"- [{a['title']}]({a['url']}) — {a['source']}")

    lines.append(
        "\nWrite a brief market intelligence report (3–5 bullet points) covering key price movements, "
        "notable news, and any risks or opportunities. Be direct and factual."
    )
    return "\n".join(lines)


def analyze(data: dict, model: str = "claude-sonnet-4-6") -> str:
    client = _get_client()
    prompt = _build_prompt(data)

    message = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text
