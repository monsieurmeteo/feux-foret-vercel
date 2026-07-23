import asyncio
from playwright.async_api import async_playwright

async def test_login():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        # Listen to page errors with full stack details
        page.on("pageerror", lambda err: print(f"❌ PAGE ERROR: {err.message}\nSTACK:\n{err.stack}\n"))
        
        url = "https://monsieurmeteo.github.io/feux-foret-vercel/?v=2"
        await page.goto(url)
        await asyncio.sleep(2)
        await browser.close()

if __name__ == "__main__":
    asyncio.run(test_login())
