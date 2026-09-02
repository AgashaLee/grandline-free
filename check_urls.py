import urllib.request

urls = [
    "https://en.onepiece-cardgame.com/images/products/boosters/op01/box.png",
    "https://en.onepiece-cardgame.com/images/products/boosters/op02/box.png",
    "https://en.onepiece-cardgame.com/images/products/decks/st01/box.png"
]

for url in urls:
    try:
        urllib.request.urlopen(url)
        print("OK: " + url)
    except Exception as e:
        print("FAIL: " + url + " - " + str(e))
