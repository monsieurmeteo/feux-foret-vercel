import asyncio
import os
from playwright.async_api import async_playwright

async def test_toggle():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        # Listen to page errors and console logs
        page.on("pageerror", lambda err: print(f"❌ PAGE ERROR: {err.message}\nSTACK:\n{err.stack}\n"))
        page.on("console", lambda msg: print(f"CONSOLE: [{msg.type}] {msg.text}"))
        
        local_path = os.path.abspath("index.html")
        url = f"file:///{local_path}"
        print(f"Opening local page: {url}")
        await page.goto(url)
        
        # Log in
        print("Logging in...")
        await page.fill("#username", "feux59")
        await page.fill("#password", "mto59")
        await page.click("button[type='submit']")
        await asyncio.sleep(2)
        
        # Click on the first fire marker to open the popup
        markers = await page.locator(".leaflet-marker-icon").all()
        print(f"Found {len(markers)} markers on the map.")
        if len(markers) > 0:
            print("Clicking the first marker...")
            await markers[0].click()
            await asyncio.sleep(1)
            
            # Wait for popup to be visible
            is_popup_visible = await page.is_visible(".leaflet-popup")
            print(f"Popup visible: {is_popup_visible}")
            
            # Find the history toggle button and click it
            toggle_btn = page.locator(".history-toggle-btn")
            is_btn_visible = await toggle_btn.is_visible()
            print(f"History toggle button visible: {is_btn_visible}")
            
            if is_btn_visible:
                print("Clicking history toggle button...")
                await toggle_btn.click()
                await asyncio.sleep(1)
                
                # Check if the container is visible
                container_id = await toggle_btn.evaluate("el => el.nextElementSibling.id")
                print(f"Container ID: {container_id}")
                
                is_container_visible = await page.is_visible(f"#{container_id}")
                print(f"Container visible after click: {is_container_visible}")
                
                container_html = await page.locator(f"#{container_id}").inner_html()
                print("Container HTML content:")
                print(container_html.strip()[:300])
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(test_toggle())
