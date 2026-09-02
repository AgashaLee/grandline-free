import urllib.request
import re

html = urllib.request.urlopen("https://onepiecetopdecks.com/").read().decode("utf-8")
images = set(re.findall(r'src="(https://onepiecetopdecks\.com/wp-content/uploads/[^"]+\.(?:jpg|png|webp))"', html))

for img in images:
    if "op" in img.lower() or "st" in img.lower():
        print(img)
