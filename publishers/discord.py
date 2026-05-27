import os
from discord_webhook import DiscordWebhook


def publish(report: str, username: str = "MarketIntelBot") -> None:
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        raise EnvironmentError("DISCORD_WEBHOOK_URL is not set")

    # Discord messages are capped at 2000 chars; split if needed
    chunks = [report[i:i + 1900] for i in range(0, len(report), 1900)]
    for chunk in chunks:
        webhook = DiscordWebhook(url=webhook_url, content=chunk, username=username)
        response = webhook.execute()
        response.raise_for_status()
