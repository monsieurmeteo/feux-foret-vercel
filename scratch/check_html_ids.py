import re

with open("index.html", "r", encoding="utf-8") as f:
    content = f.read()

# Let's search for hist-container- inside the file
matches = re.findall(r'id=["\']hist-container-([^"\']+)["\']', content)
print("hist-container- IDs found in index.html:", matches[:10])
print("Total matches:", len(matches))
