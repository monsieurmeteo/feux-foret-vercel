import asyncio
import os
from playwright.async_api import async_playwright

async def test_toggle_local():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        # Listen to page errors
        errors = []
        page.on("pageerror", lambda err: errors.append(err.message))
        
        local_path = os.path.abspath("index.html")
        url = f"file:///{local_path}"
        print(f"Opening local page: {url}")
        await page.goto(url)
        
        # Log in
        print("Logging in...")
        await page.fill("#username", "feux59")
        await page.fill("#password", "mto59")
        await page.click("button[type='submit']")
        await asyncio.sleep(2) # Give it time to load map
        
        # Wait for leaflet marker to appear
        try:
            await page.wait_for_selector(".leaflet-marker-icon", timeout=5000)
        except Exception:
            pass
            
        # Find all leaflet markers
        markers = await page.locator(".leaflet-marker-icon").all()
        print(f"Found {len(markers)} markers on the map.")
        
        for i, marker in enumerate(markers):
            print(f"Clicking marker {i}...")
            await marker.click()
            await asyncio.sleep(1)
            
            # Check if history toggle button is visible
            toggle_btn = page.locator(".history-toggle-btn")
            is_btn_visible = await toggle_btn.is_visible()
            if is_btn_visible:
                print("Found history toggle button! Clicking it...")
                await toggle_btn.click()
                await asyncio.sleep(1)
                
                # Check if we got any page errors
                if errors:
                    print("❌ JS ERRORS DETECTED:")
                    for err in errors:
                        print(f"  - {err}")
                else:
                    print("✅ Clicked toggle button successfully with 0 errors!")
                
                # Check if the container is visible
                container_id = await toggle_btn.evaluate("el => el.nextElementSibling.id")
                is_container_visible = await page.is_visible(f"#{container_id}")
                print(f"Container visible: {is_container_visible}")
                
                # Verify that it is indeed block
                display_style = await page.locator(f"#{container_id}").evaluate("el => el.style.display")
                print(f"Container display style: '{display_style}'")
                break
            else:
                # Close popup by clicking elsewhere on the map
                await page.click("#map", position={"x": 5, "y": 5})
                await asyncio.sleep(0.5)
                
        await browser.close()

if __name__ == "__main__":
    asyncio.run(test_toggle_local())
