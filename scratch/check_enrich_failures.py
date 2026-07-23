import sys
import os

with open("fetch_feux.py", "r", encoding="utf-8") as f:
    code = f.read()

old_except = """        except Exception:
            pass"""

new_except = """        except Exception as e:
            import traceback
            print(f"❌ ENRICH ERROR for {f.get('commune')} ({f.get('url')}): {type(e).__name__}: {str(e)}")"""

if old_except in code:
    code = code.replace(old_except, new_except)
    print("Successfully patched code in memory!")
else:
    print("Could not find old_except in fetch_feux.py code!")

# Execute the code in memory with __file__ defined
namespace = {"__file__": os.path.abspath("fetch_feux.py")}
exec(code, namespace)

print("Running fetch_all_feux()...")
results, news = namespace["fetch_all_feux"]()
print("Total results fetched:", len(results))
valid_count = sum(1 for f in results if f.get("lat") and f.get("lon"))
print("Fires with valid coordinates:", valid_count)
