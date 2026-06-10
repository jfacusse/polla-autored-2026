import json, urllib.request, threading, time
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent
FIXTURES_FILE = BASE / "static_data" / "fixtures.json"
URL = "https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026/worldcup.json"

NAME_MAP = {
    "Czech Republic": "Czechia",
    "United States": "USA",
    "Cote d'Ivoire": "Ivory Coast",
    "Congo DR": "DR Congo",
    "Bosnia-Herzegovina": "Bosnia",
    "Korea Republic": "South Korea",
}

def _load(name):
    f = BASE / "data" / f"{name}.json"
    return json.loads(f.read_text()) if f.exists() else {}

def _save(name, data):
    (BASE / "data" / f"{name}.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False)
    )

def fetch_scores():
    try:
        req = urllib.request.Request(URL, headers={"User-Agent": "PollaAutoRed/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()).get("matches", [])
    except Exception as e:
        print(f"[updater] fetch error: {e}")
        return []

def normalize(name):
    return NAME_MAP.get(name, name)

def run_update():
    matches = fetch_scores()
    if not matches:
        return 0

    fixts = json.loads(FIXTURES_FILE.read_text())
    res = _load("results")
    updated = 0

    fix_index = {(f["home"], f["away"]): f for f in fixts}

    for m in matches:
        score = m.get("score")
        if not score:
            continue
        ft = score.get("ft")
        if not ft or len(ft) != 2:
            continue

        h = normalize(m.get("team1", ""))
        a = normalize(m.get("team2", ""))
        fix = fix_index.get((h, a))
        if not fix:
            continue

        if fix.get("score_home") == ft[0] and fix.get("score_away") == ft[1]:
            continue

        fix["score_home"] = ft[0]
        fix["score_away"] = ft[1]
        fix["status"] = "finished"
        res[fix["id"]] = {
            "score_home": ft[0], "score_away": ft[1],
            "home": h, "away": a
        }
        updated += 1
        print(f"[updater] {fix['id']} {h} {ft[0]}-{ft[1]} {a}")

    if updated:
        FIXTURES_FILE.write_text(json.dumps(fixts, indent=2, ensure_ascii=False))
        _save("results", res)
        print(f"[updater] {updated} resultados actualizados")

    return updated

def start_background(interval_hours=2):
    def loop():
        while True:
            try:
                run_update()
            except Exception as e:
                print(f"[updater] error: {e}")
            time.sleep(interval_hours * 3600)
    t = threading.Thread(target=loop, daemon=True)
    t.start()
