"""Test manuel des formats Discord via un webhook fourni en environnement."""

import os

import requests


def _send(webhook_url: str, label: str, payload: dict) -> bool:
    print(f"Test: {label}")
    response = requests.post(webhook_url, json=payload, timeout=15)
    print(f"Status: {response.status_code}")
    if response.status_code == 204:
        return True
    print(response.text[:500])
    return False


def test_webhook_basic(webhook_url: str) -> bool:
    return _send(webhook_url, "message simple", {"content": "Test webhook Moddy"})


def test_webhook_embed(webhook_url: str) -> bool:
    return _send(
        webhook_url,
        "embed",
        {
            "content": "Message avec embed :",
            "embeds": [{
                "title": "Bot Moddy",
                "description": "Interface de modération moderne",
                "color": 0x5865F2,
                "fields": [
                    {"name": "Status", "value": "Opérationnel", "inline": True},
                    {"name": "Version", "value": "2.0", "inline": True},
                ],
                "footer": {"text": "Moddy Bot"},
            }],
        },
    )


def test_new_components(webhook_url: str) -> bool:
    return _send(
        webhook_url,
        "components V2",
        {
            "content": "Test nouveaux components :",
            "flags": 32768,
            "components": [{
                "type": 17,
                "components": [
                    {"type": 10, "content": "## Nouveaux Components Discord"},
                    {"type": 14},
                    {"type": 10, "content": "Layout flexible"},
                ],
            }],
        },
    )


def main() -> None:
    webhook_url = os.environ.get("MODDY_TEST_WEBHOOK_URL", "").strip()
    if not webhook_url:
        raise SystemExit("MODDY_TEST_WEBHOOK_URL n'est pas configurée")
    if test_webhook_basic(webhook_url) and test_webhook_embed(webhook_url):
        test_new_components(webhook_url)


if __name__ == "__main__":
    main()
