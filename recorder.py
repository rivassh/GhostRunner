import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

START_URL = "https://example.com"


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            record_har_path="session.har",
            record_har_content="embed",
        )
        page = context.new_page()

        print(f"Navigating to {START_URL}")
        page.goto(START_URL, wait_until="domcontentloaded")
        print("Browser is open. Perform login, 2FA, captcha, navigation, and submission now.")
        input("Press Enter when you have completed the target action...")

        print("Enter human-annotated inputs (optional), one per line. Leave empty to skip.")
        human_inputs = []
        while True:
            entry = input("> ")
            if not entry:
                break
            human_inputs.append(entry)

        context.storage_state(path="auth.json")

        page.close()
        context.close()
        browser.close()

        meta = {
            "start_url": START_URL,
            "human_inputs": human_inputs,
        }
        Path("raw_meta.json").write_text(json.dumps(meta, indent=2))
        print("Artifacts saved: auth.json, session.har, raw_meta.json")


if __name__ == "__main__":
    main()
