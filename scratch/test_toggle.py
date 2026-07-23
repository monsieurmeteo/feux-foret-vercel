import asyncio
import os
from playwright.async_api import async_playwright

async def test_toggle_local():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        # Listen to page errors and console
        errors = []
        page.on("pageerror", lambda err: errors.append(err.message))
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
        
        # Find all leaflet markers
        markers = await page.locator(".leaflet-marker-icon").all()
        print(f"Found {len(markers)} markers on the map.")
        
        success = False
        for i, marker in enumerate(markers):
            # Click marker to open popup
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
                
                if is_btn_still_visible:
                    # Target container by fetching its ID from the active button's attribute
                    container_id = await active_btn.evaluate("el => el.getAttribute('data-container-id')")
                    print(f"Active container ID: {container_id}")
                    
                    is_container_visible = await page.is_visible(f"#{container_id}")
                    print(f"Container visible: {is_container_visible}")
                    
                    display_style = await page.locator(f"#{container_id}").evaluate("el => el.style.display")
                    print(f"Container display style: '{display_style}'")
                    
                    btn_text = await active_btn.inner_text()
                    print(f"Button text after click: '{btn_text}'")
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
    asyncio.run(test_toggle_local())
