import json
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8000/api/streets") as r:
    d = json.load(r)
print("streets:", len(d))
for s in d[:6]:
    print(f"{s['name'][:34]:<35} best={s['best_score']} stack_pairs={s['stackable_pairs']}")
