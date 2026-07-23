import asyncio
import os
from playwright.async_api import async_playwright

async def check():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        local_path = os.path.abspath("index.html")
        await page.goto(f"file:///{local_path}")
        await page.fill("#username", "feux59")
        await page.fill("#password", "mto59")
        await page.click("button[type='submit']")
        await asyncio.sleep(2)
        
        markers = await page.locator(".leaflet-marker-icon").all()
        for marker in markers:
            await marker.click()
            await asyncio.sleep(1)
            
            # Count elements matching ID hist-container-0
            count = await page.evaluate("document.querySelectorAll('#hist-container-0').length")
            print("Number of hist-container-0 elements in DOM:", count)
            
            # Let's print all elements with hist-container-0 and their HTML
            details = await page.evaluate("""() => {
                const els = document.querySelectorAll('#hist-container-0');
                return Array.from(els).map(el => ({
                    tagName: el.tagName,
                    className: el.className,
                    parentTagName: el.parentElement ? el.parentElement.tagName : 'null',
                    parentClassName: el.parentElement ? el.parentElement.className : 'null',
                    display: el.style.display
                }));
            }""")
            print("Details of matching elements:")
            for i, d in enumerate(details):
                print(f"[{i}]:", d)
            break
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(check())
