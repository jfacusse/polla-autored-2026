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

    gist_id_file = GIST_ID_FILE
    try:
        if gist_id_file.exists():
            gist_id = gist_id_file.read_text().strip()
            _request("PATCH", f"{GIST_API}/{gist_id}", payload)
            print(f"[backup] Gist actualizado: {gist_id} ({ts})")
        else:
            resp = _request("POST", GIST_API, payload)
            gist_id = resp["id"]
            gist_id_file.write_text(gist_id)
            print(f"[backup] Gist creado: {gist_id} ({ts})")
    except Exception as e:
        print(f"[backup] Error: {e}")


def restore_if_empty():
    """Al arrancar, si participants.json no existe o está vacío, restaurar desde Gist."""
    token = _token()
    if not token:
        return

    parts_file = DATA_DIR / "participants.json"
    if parts_file.exists():
        try:
            data = json.loads(parts_file.read_text())
            if data:
                return
        except Exception:
            pass

    gist_id_file = GIST_ID_FILE
    if not gist_id_file.exists():
        gist_id = os.environ.get("GIST_ID", "")
        if not gist_id:
            print("[backup] Sin Gist previo — arranque limpio")
            return
        gist_id_file.write_text(gist_id)
    else:
        gist_id = gist_id_file.read_text().strip()

    try:
        resp = _request("GET", f"{GIST_API}/{gist_id}")
        gist_files = resp.get("files", {})
        restored = 0
        for name in DATA_FILES:
            fname = f"{name}.json"
            if fname in gist_files:
                content = gist_files[fname].get("content", "{}")
                (DATA_DIR / fname).write_text(content)
                restored += 1
        print(f"[backup] Restaurados {restored} archivos desde Gist {gist_id}")
    except Exception as e:
        print(f"[backup] Error al restaurar: {e}")


def start_background(interval_hours=24):
    def loop():
        time.sleep(3600)  # espera 1h antes del primer backup
        while True:
            try:
                backup()
            except Exception as e:
                print(f"[backup] error en loop: {e}")
            time.sleep(interval_hours * 3600)
    t = threading.Thread(target=loop, daemon=True)
    t.start()
