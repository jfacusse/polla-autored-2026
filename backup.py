"""
Backup/restore de datos de la polla a GitHub Gist.
Requiere env var GITHUB_TOKEN con scope 'gist'.
"""
import json, os, time, threading, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime

BASE     = Path(__file__).parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE / "data"))
DATA_FILES = ["participants", "predictions", "results", "torneo_results", "config"]
GIST_ID_FILE = DATA_DIR / ".gist_id"
GIST_API = "https://api.github.com/gists"


def _token():
    return os.environ.get("GITHUB_TOKEN", "")


def _request(method, url, payload=None):
    token = _token()
    if not token:
        raise RuntimeError("GITHUB_TOKEN no configurado")
    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "PollaAutoRed/1.0",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _get_gist_id():
    if GIST_ID_FILE.exists():
        return GIST_ID_FILE.read_text().strip()
    gist_id = os.environ.get("GIST_ID", "")
    if gist_id:
        GIST_ID_FILE.write_text(gist_id)
    return gist_id


def backup():
    token = _token()
    if not token:
        print("[backup] Sin GITHUB_TOKEN — backup omitido")
        return

    files = {}
    for name in DATA_FILES:
        f = DATA_DIR / f"{name}.json"
        files[f"{name}.json"] = {"content": f.read_text() if f.exists() else "{}"}

    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    payload = {"description": f"Polla AutoRed — backup {ts}", "public": False, "files": files}

    gist_id = _get_gist_id()
    try:
        if gist_id:
            _request("PATCH", f"{GIST_API}/{gist_id}", payload)
            print(f"[backup] Gist actualizado: {gist_id} ({ts})")
        else:
            resp = _request("POST", GIST_API, payload)
            gist_id = resp["id"]
            GIST_ID_FILE.write_text(gist_id)
            print(f"[backup] Gist creado: {gist_id} ({ts})")
    except Exception as e:
        print(f"[backup] Error: {e}")


def restore():
    """Restaura desde Gist. Solo sobreescribe archivos donde el Gist tiene más datos."""
    token = _token()
    if not token:
        return

    gist_id = _get_gist_id()
    if not gist_id:
        print("[backup] Sin Gist — arranque limpio")
        return

    try:
        resp = _request("GET", f"{GIST_API}/{gist_id}")
        gist_files = resp.get("files", {})
        restored = 0
        for name in DATA_FILES:
            fname = f"{name}.json"
            if fname not in gist_files:
                continue
            remote_content = gist_files[fname].get("content", "{}")
            try:
                remote_data = json.loads(remote_content)
            except Exception:
                continue

            local_file = DATA_DIR / fname
            local_data = {}
            if local_file.exists():
                try:
                    local_data = json.loads(local_file.read_text())
                except Exception:
                    pass

            # Para predictions: merge profundo — Gist gana a nivel de pick individual
            if name == "predictions":
                merged = dict(local_data)
                for uid, gist_picks in remote_data.items():
                    if uid not in merged:
                        merged[uid] = gist_picks
                    elif isinstance(gist_picks, dict) and isinstance(merged[uid], dict):
                        # Gist aporta picks que no existen en local
                        base = dict(gist_picks)
                        base.update(merged[uid])  # local gana si el pick ya existe
                        merged[uid] = base
                if merged != local_data:
                    local_file.write_text(json.dumps(merged, indent=2, ensure_ascii=False))
                    restored += 1
            else:
                # Para el resto: el Gist gana si tiene más datos
                if len(str(remote_data)) > len(str(local_data)):
                    local_file.write_text(remote_content)
                    restored += 1

        print(f"[backup] Restaurados {restored} archivos desde Gist {gist_id}")
    except Exception as e:
        print(f"[backup] Error al restaurar: {e}")


def start_background(interval_minutes=15):
    def loop():
        # Primer backup inmediato al arrancar
        time.sleep(10)
        while True:
            try:
                backup()
            except Exception as e:
                print(f"[backup] error en loop: {e}")
            time.sleep(interval_minutes * 60)
    t = threading.Thread(target=loop, daemon=True)
    t.start()
