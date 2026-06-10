#!/usr/bin/env python3
"""
Polla AutoRed — Mundial 2026
Corre con: python3 app.py
Acceso: http://TU_IP:5002
"""
import json, os, socket
from pathlib import Path
import score_updater

DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
from functools import wraps
from datetime import datetime, timedelta
from flask import Flask, render_template, request, session, redirect, url_for, flash, jsonify

DIAS   = ["Lunes","Martes","Miércoles","Jueves","Viernes","Sábado","Domingo"]
MESES  = ["enero","febrero","marzo","abril","mayo","junio","julio",
          "agosto","septiembre","octubre","noviembre","diciembre"]

def parse_match_dt(date_str, time_str):
    try:
        return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    except Exception:
        return None

def date_pretty(date_str):
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return f"{DIAS[dt.weekday()]} {dt.day} de {MESES[dt.month-1]}"
    except Exception:
        return date_str

app = Flask(__name__)
app.secret_key = "polla_autored_2026_secret"
BASE = Path(__file__).parent

# ── DATA HELPERS ────────────────────────────────────────────────────────────
def _load(name):
    f = DATA_DIR / f"{name}.json"
    return json.loads(f.read_text()) if f.exists() else {}

def _save(name, data):
    (DATA_DIR / f"{name}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False))

def _migrate():
    parts = _load("participants")
    changed = False
    for uid, p in parts.items():
        if "activo" not in p:
            p["activo"] = True
            changed = True
    if changed:
        _save("participants", parts)

_migrate()
score_updater.start_background(interval_hours=2)

def teams_data():
    f = BASE / "static_data" / "teams.json"
    return json.loads(f.read_text())["teams"] if f.exists() else {}

def fixtures():
    f = BASE / "static_data" / "fixtures.json"
    if not f.exists():
        return []
    return json.loads(f.read_text())

def predictions_data():
    f = BASE / "static_data" / "predictions.json"
    return json.loads(f.read_text()) if f.exists() else {}

# ── AUTH DECORATORS ─────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("is_admin"):
            flash("Necesitas acceso de administrador.", "error")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated

# ── SCORING ─────────────────────────────────────────────────────────────────
def calcular_puntos(user_id):
    cfg    = _load("config")
    preds  = _load("predictions")
    res    = _load("results")
    parts  = _load("participants")
    jokers = parts.get(user_id, {}).get("jokers_usados", [])
    user_picks = preds.get(user_id, {})

    pts_exacto   = cfg.get("pts_exacto", 5)
    pts_resultado = cfg.get("pts_resultado", 3)

    total = 0
    detalle = []
    for fid, result in res.items():
        if result["score_home"] is None:
            continue
        pick = user_picks.get(fid)
        if not pick:
            detalle.append({"fid": fid, **result, "pick": None, "pts": 0, "tipo": "sin_pick"})
            continue
        rh, ra = result["score_home"], result["score_away"]
        ph, pa = pick["home"], pick["away"]
        mult = 2 if fid in jokers else 1
        if ph == rh and pa == ra:
            pts = pts_exacto * mult
            tipo = "exacto"
        elif (ph > pa and rh > ra) or (ph < pa and rh < ra) or (ph == pa and rh == ra):
            pts = pts_resultado * mult
            tipo = "resultado"
        else:
            pts = 0
            tipo = "fallo"
        total += pts
        detalle.append({"fid": fid, **result, "pick": pick, "pts": pts, "tipo": tipo, "joker": fid in jokers})
    # torneo bonus
    torneo_res   = _load("torneo_results")
    torneo_picks = preds.get(user_id, {}).get("torneo", {})
    torneo_pts_map = {"goleador": 15, "arquero": 10, "mejor_jugador": 15}
    for key, bonus in torneo_pts_map.items():
        winner = torneo_res.get(key)
        pick   = torneo_picks.get(key, "").strip().lower()
        if winner and pick and pick == winner.strip().lower():
            total += bonus

    return total, detalle

def tabla_general():
    parts = _load("participants")
    preds = _load("predictions")
    tabla = []
    for uid, p in parts.items():
        if p.get("es_admin") or not p.get("activo", True):
            continue
        pts, detalle = calcular_puntos(uid)
        n_picks = len(preds.get(uid, {}))
        exactos    = sum(1 for d in detalle if d.get("tipo") == "exacto")
        resultados = sum(1 for d in detalle if d.get("tipo") == "resultado")
        tabla.append({"id": uid, "nombre": p["nombre"], "pts": pts, "picks": n_picks,
                      "exactos": exactos, "resultados": resultados,
                      "jokers_usados": len(p.get("jokers_usados", []))})
    return sorted(tabla, key=lambda x: (-x["pts"], -x["picks"]))

# ── ROUTES ──────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    cfg    = _load("config")
    tabla  = tabla_general()
    fixts  = fixtures()
    res    = _load("results")
    today  = datetime.now().strftime("%Y-%m-%d")

    upcoming = [f for f in fixts if f["status"] == "upcoming"]
    upcoming.sort(key=lambda x: x["date"] + x["time"])
    finished = [f for f in fixts if f["status"] == "finished"]
    finished.sort(key=lambda x: x["date"] + x["time"], reverse=True)

    today_matches = [f for f in fixts if f["date"] == today]
    for f in today_matches:
        r = res.get(f["id"], {})
        f["score_home"] = r.get("score_home")
        f["score_away"] = r.get("score_away")

    recent = [f for f in finished if res.get(f["id"])][:6]
    for f in recent:
        r = res.get(f["id"], {})
        f["score_home"] = r.get("score_home")
        f["score_away"] = r.get("score_away")

    user_id = session.get("user")
    user_picks = _load("predictions").get(user_id, {}) if user_id else {}
    parts = _load("participants")
    jokers_usados = parts.get(user_id, {}).get("jokers_usados", []) if user_id else []
    jokers_max = cfg.get("jokers_disponibles", 3)
    torneo_res = _load("torneo_results")

    user_data = None
    if user_id and tabla:
        for i, p in enumerate(tabla):
            if p["id"] == user_id:
                user_data = {**p, "pos": i + 1}
                break

    return render_template("index.html",
        cfg=cfg, tabla=tabla, upcoming=upcoming, finished=finished,
        today_matches=today_matches, recent=recent,
        user=user_id, user_data=user_data,
        jokers_usados=len(jokers_usados), jokers_max=jokers_max,
        torneo_res=torneo_res, total_jugados=len(res))


@app.route("/login", methods=["GET","POST"])
def login():
    cfg   = _load("config")
    parts = _load("participants")
    if request.method == "POST":
        nombre = request.form.get("nombre","").strip()
        pin    = request.form.get("pin","").strip()
        email = request.form.get("email","").strip().lower()
        # admin login (by PIN only)
        if pin == cfg.get("admin_pin"):
            if not email.endswith("@autored.cl"):
                flash("Ingresa tu correo @autored.cl para acceder como admin.", "error")
                return redirect(url_for("login"))
            uid = email.split("@")[0]
            session["user"]     = uid
            session["nombre"]   = email
            session["is_admin"] = True
            if uid not in parts:
                parts[uid] = {"nombre": email, "email": email, "pin": pin, "es_admin": True, "jokers_usados": []}
                _save("participants", parts)
            elif not parts[uid].get("es_admin"):
                parts[uid]["es_admin"] = True
                _save("participants", parts)
            flash("Bienvenido, Admin 🔑", "ok")
            return redirect(url_for("admin"))
        # validate AutoRed email
        if not email.endswith("@autored.cl"):
            flash("Debes usar tu correo @autored.cl para participar.", "error")
            return redirect(url_for("login"))
        uid = email.split("@")[0]
        # check existing participant
        for pid, p in parts.items():
            if p.get("email","") == email:
                if not p.get("activo", True):
                    flash("Tu cuenta está desactivada. Contacta al admin.", "error")
                    return redirect(url_for("login"))
                if p["pin"] != pin:
                    flash("PIN incorrecto para ese correo.", "error")
                    return redirect(url_for("login"))
                session["user"] = pid
                session["nombre"] = email
                flash(f"¡Bienvenido de vuelta! ⚽", "ok")
                return redirect(url_for("index"))
        # new participant
        if len(pin) < 4:
            flash("PIN muy corto (mínimo 4 dígitos).", "error")
            return redirect(url_for("login"))
        if uid in parts:
            uid = uid + "_" + pin[:3]
        parts[uid] = {"nombre": email, "email": email, "pin": pin, "es_admin": False, "jokers_usados": []}
        _save("participants", parts)
        session["user"] = uid
        session["nombre"] = email
        flash(f"¡Cuenta creada! Bienvenido ⚽", "ok")
        return redirect(url_for("predict"))
    return render_template("login.html", cfg=cfg)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/predecir", methods=["GET","POST"])
@login_required
def predict():
    cfg    = _load("config")
    parts  = _load("participants")
    fixts  = fixtures()
    model  = predictions_data()
    preds  = _load("predictions")
    res    = _load("results")
    uid    = session["user"]
    # ensure participant entry exists (e.g. admin logging in for first time)
    if uid not in parts:
        parts[uid] = {"nombre": session.get("nombre", uid), "pin": "", "es_admin": uid=="admin", "jokers_usados": []}
        _save("participants", parts)
    jokers = parts[uid].get("jokers_usados", [])
    jokers_max = cfg.get("jokers_disponibles", 3)
    user_picks = preds.get(uid, {})

    if request.method == "POST":
        for f in fixts:
            if f["status"] != "upcoming":
                continue
            fid = f["id"]
            h = request.form.get(f"h_{fid}", "").strip()
            a = request.form.get(f"a_{fid}", "").strip()
            if h != "" and a != "":
                try:
                    user_picks[fid] = {"home": int(h), "away": int(a)}
                except ValueError:
                    pass

        jokers_new = [f["id"] for f in fixts
                      if f["status"] == "upcoming"
                      and request.form.get(f"joker_{f['id']}") == "1"]
        parts[uid]["jokers_usados"] = jokers_new[:jokers_max]
        # torneo picks
        torneo = {}
        for key in ("goleador", "arquero", "mejor_jugador"):
            val = request.form.get(f"torneo_{key}", "").strip()
            if val:
                torneo[key] = val
        if not isinstance(preds.get(uid), dict):
            preds[uid] = {}
        preds[uid] = user_picks
        preds[uid]["torneo"] = torneo
        _save("predictions", preds)
        _save("participants", parts)
        flash("✅ Predicciones guardadas correctamente.", "ok")
        return redirect(url_for("predict"))

    upcoming = [f for f in fixts if f["status"] == "upcoming"]
    for f in upcoming:
        f["user_pick"] = user_picks.get(f["id"])
        f["is_joker"] = f["id"] in (parts[uid].get("jokers_usados", []))
    already_locked = [fid for fid in user_picks if res.get(fid)]
    torneo_picks = preds.get(uid, {}).get("torneo", {})
    torneo_res   = _load("torneo_results")

    return render_template("predict.html",
        cfg=cfg, upcoming=upcoming, user=uid, nombre=session["nombre"],
        jokers_usados=parts[uid].get("jokers_usados",[]),
        jokers_max=jokers_max, already_locked=already_locked,
        torneo_picks=torneo_picks, torneo_res=torneo_res)


@app.route("/mis-picks")
@login_required
def my_picks():
    uid    = session["user"]
    cfg    = _load("config")
    fixts  = fixtures()
    preds  = _load("predictions")
    res    = _load("results")
    parts  = _load("participants")
    pts, detalle = calcular_puntos(uid)
    jokers = parts.get(uid, {}).get("jokers_usados", [])
    fix_map = {f["id"]: f for f in fixts}

    rows = []
    for fid, pick in preds.get(uid, {}).items():
        f = fix_map.get(fid, {})
        r = res.get(fid, {})
        d = next((x for x in detalle if x["fid"] == fid), None)
        rows.append({
            "fid": fid, "home": f.get("home","?"), "away": f.get("away","?"),
            "date": f.get("date",""), "group": f.get("group",""),
            "pick_h": pick["home"], "pick_a": pick["away"],
            "real_h": r.get("score_home"), "real_a": r.get("score_away"),
            "pts": d["pts"] if d else None,
            "tipo": d["tipo"] if d else "pending",
            "joker": fid in jokers,
        })
    rows.sort(key=lambda x: x["date"])
    return render_template("mis_picks.html",
        cfg=cfg, rows=rows, pts_total=pts, nombre=session["nombre"],
        jokers_usados=len(jokers), jokers_max=cfg.get("jokers_disponibles",3))


@app.route("/tabla")
def tabla():
    cfg   = _load("config")
    tabla = tabla_general()
    parts = _load("participants")
    res   = _load("results")
    preds = _load("predictions")
    total_jugados = sum(1 for r in res.values() if r.get("score_home") is not None)
    user = session.get("user")
    return render_template("tabla.html",
        cfg=cfg, tabla=tabla, parts=parts, total_jugados=total_jugados,
        user=user, preds=preds)


@app.route("/admin", methods=["GET","POST"])
@admin_required
def admin():
    cfg   = _load("config")
    parts = _load("participants")
    fixts = fixtures()
    res   = _load("results")
    preds = _load("predictions")

    if request.method == "POST":
        action = request.form.get("action")

        if action == "resultado":
            fid = request.form.get("fid")
            h   = request.form.get("score_home","").strip()
            a   = request.form.get("score_away","").strip()
            if fid and h != "" and a != "":
                # server-side: only allow after match has started
                target = next((f for f in fixts if f["id"] == fid), None)
                if target:
                    match_dt = parse_match_dt(target["date"], target["time"])
                    if match_dt and datetime.now() < match_dt:
                        flash(f"⛔ Aún no puedes ingresar el resultado de {fid} — el partido empieza a las {target['time']}.", "error")
                        return redirect(url_for("admin"))
                for f in fixts:
                    if f["id"] == fid:
                        f["status"] = "finished"
                        f["score_home"] = int(h)
                        f["score_away"] = int(a)
                        break
                fix_path = BASE / "static_data" / "fixtures.json"
                fix_path.write_text(json.dumps(fixts, indent=2, ensure_ascii=False))
                res[fid] = {"score_home": int(h), "score_away": int(a),
                            "home": next((f["home"] for f in fixts if f["id"]==fid),"?"),
                            "away": next((f["away"] for f in fixts if f["id"]==fid),"?")}
                _save("results", res)
                flash(f"✅ Resultado {fid} guardado: {h}-{a}", "ok")

        elif action == "add_user":
            nombre = request.form.get("nombre","").strip()
            pin    = request.form.get("pin","").strip()
            if nombre and pin:
                uid = nombre.lower().replace(" ","_").replace("@","_").replace(".","_")
                if uid not in parts:
                    parts[uid] = {"nombre": nombre, "pin": pin, "es_admin": False, "activo": True, "jokers_usados": []}
                    _save("participants", parts)
                    flash(f"✅ Participante {nombre} agregado.", "ok")
                else:
                    flash("Ya existe ese participante.", "error")

        elif action == "toggle_activo":
            t_uid = request.form.get("uid")
            if t_uid and t_uid in parts and not parts[t_uid].get("es_admin"):
                parts[t_uid]["activo"] = not parts[t_uid].get("activo", True)
                estado = "activado" if parts[t_uid]["activo"] else "desactivado"
                _save("participants", parts)
                flash(f"✅ Participante {estado}.", "ok")
            else:
                flash("No se puede modificar a ese participante.", "error")

        elif action == "torneo_resultado":
            torneo_res = _load("torneo_results")
            for key in ("goleador", "arquero", "mejor_jugador"):
                val = request.form.get(key, "").strip()
                torneo_res[key] = val if val else None
            _save("torneo_results", torneo_res)
            flash("✅ Premios del torneo actualizados.", "ok")

        elif action == "del_user":
            del_uid = request.form.get("uid")
            if del_uid and del_uid in parts and not parts[del_uid].get("es_admin"):
                del parts[del_uid]
                _save("participants", parts)
                preds = _load("predictions")
                if del_uid in preds:
                    del preds[del_uid]
                    _save("predictions", preds)
                flash("✅ Participante eliminado.", "ok")
            else:
                flash("No se puede eliminar a ese participante.", "error")

        elif action == "update_config":
            cfg["nombre_polla"] = request.form.get("nombre_polla", cfg["nombre_polla"])
            cfg["pts_exacto"]   = int(request.form.get("pts_exacto", 5))
            cfg["pts_resultado"]= int(request.form.get("pts_resultado", 3))
            cfg["jokers_disponibles"] = int(request.form.get("jokers_disponibles", 3))
            cfg["admin_pin"]    = request.form.get("admin_pin", cfg["admin_pin"])
            _save("config", cfg)
            flash("✅ Configuración actualizada.", "ok")

        return redirect(url_for("admin"))

    upcoming = [f for f in fixts if f["status"] == "upcoming"]
    upcoming.sort(key=lambda x: (x["date"], x["time"]))
    finished = [f for f in fixts if f["status"] == "finished"]
    now = datetime.now()
    for f in upcoming:
        match_dt = parse_match_dt(f["date"], f["time"])
        f["can_enter_result"] = (match_dt is None) or (now >= match_dt)
        f["unlocks_at"] = match_dt.strftime("%H:%M") if match_dt else ""
        f["date_pretty"] = date_pretty(f["date"])
    tabla      = tabla_general()
    torneo_res = _load("torneo_results")
    return render_template("admin.html",
        cfg=cfg, parts=parts, upcoming=upcoming, finished=finished,
        tabla=tabla, res=res, preds=preds, torneo_res=torneo_res)


@app.route("/api/standings")
def api_standings():
    return jsonify(tabla_general())

@app.route("/api/refresh-scores")
@admin_required
def refresh_scores():
    updated = score_updater.run_update()
    return jsonify({"updated": updated})


@app.route("/api/picks/<user_id>")
@admin_required
def api_picks(user_id):
    preds = _load("predictions")
    parts = _load("participants")
    res   = _load("results")
    pts, detalle = calcular_puntos(user_id)
    return jsonify({"user": user_id,
                    "nombre": parts.get(user_id, {}).get("nombre","?"),
                    "pts": pts, "detalle": detalle})


if __name__ == "__main__":
    # show local IP for sharing
    try:
        ip = socket.gethostbyname(socket.gethostname())
    except:
        ip = "localhost"
    print("=" * 55)
    print("⚽  POLLA AUTORED — Mundial 2026")
    print("=" * 55)
    print(f"   Local:   http://localhost:5002")
    print(f"   Red:     http://{ip}:5002   ← comparte este link")
    print(f"   Admin:   PIN = {_load('config').get('admin_pin','autored26')}")
    print("=" * 55)
    port = int(os.environ.get("PORT", 5002))
    app.run(host="0.0.0.0", port=port, debug=False)
