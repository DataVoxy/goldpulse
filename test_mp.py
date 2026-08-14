import sys
sys.argv.append("--dry")
from woning_scraper import _scrape_marktplaats

results = _scrape_marktplaats()
print(f"Marktplaats (gefilterd): {len(results)} listings\n")
for r in sorted(results, key=lambda x: x["price"]):
    print(f"  {r['price']} | {r['title'][:55]} | {r['location']}")
    print(f"    {r['url']}")
    print()
