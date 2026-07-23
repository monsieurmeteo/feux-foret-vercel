import asyncio
from playwright.async_api import async_playwright

async def test_toggle_public():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        # Listen to page errors and console
        errors = []
        page.on("pageerror", lambda err: errors.append(err.message))
        page.on("console", lambda msg: print(f"CONSOLE: [{msg.type}] {msg.text}"))
        
        url = "https://monsieurmeteo.github.io/feux-foret-vercel/?v=18"
        print(f"Opening public page: {url}")
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
        
        success = False
        for i, marker in enumerate(markers):
            print(f"Clicking marker {i}...")
            await marker.click()
            await asyncio.sleep(1.5)
            
            # Target the active button in the open leaflet popup
            active_btn = page.locator(".leaflet-popup .history-toggle-btn")
            is_btn_visible = await active_btn.is_visible()
            if is_btn_visible:
                print("Found active history toggle button! Clicking it physically...")
                await active_btn.click()
                await asyncio.sleep(1.5)
                
                # Check if we got any page errors
                if errors:
                    print("❌ JS ERRORS DETECTED:")
                    for err in errors:
                        print(f"  - {err}")
                else:
                    print("✅ Clicked toggle button successfully with 0 errors!")
                
                # Check if active button is STILL visible
                is_btn_still_visible = await active_btn.is_visible()
                print(f"Is active toggle button still visible? {is_btn_still_visible}")
                
                # Verify container display style inside evaluate to check actual state in browser!
                res = await page.evaluate('''() => {
                    const btn = document.querySelector('.leaflet-popup .history-toggle-btn');
                    const containerId = btn.getAttribute('data-container-id');
                    const container = document.getElementById(containerId);
                    return {
                        id: containerId,
                        display: container.style.display,
                        innerText: btn.innerText
                    };
                }''')
                print("Browser state after click:", res)
                success = True
                break
            else:
                # Close popup
                await page.click("#map", position={"x": 5, "y": 5})
                await asyncio.sleep(0.5)
                
        if not success:
            print("❌ Toggle test failed.")
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(test_toggle_public())
