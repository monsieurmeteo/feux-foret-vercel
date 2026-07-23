import asyncio
import os
from playwright.async_api import async_playwright

async def test_local_html():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        # Listen to page errors
        def handle_error(err):
            print("❌ PAGE ERROR:", err.message)
            print("Name:", getattr(err, "name", "None"))
            print("Stack:", getattr(err, "stack", "None"))
            
        page.on("pageerror", handle_error)

        # Listen to console messages
        def handle_console(msg):
            print(f"CONSOLE [{msg.type}]: {msg.text}")
            if msg.location:
                print(f"  at {msg.location.get('url')}:{msg.location.get('lineNumber')}:{msg.location.get('columnNumber')}")

        page.on("console", handle_console)

        local_path = os.path.abspath("index.html")
        url = f"file:///{local_path}"
        await page.goto(url)
        await asyncio.sleep(2)
        await browser.close()

if __name__ == "__main__":
    asyncio.run(test_local_html())
