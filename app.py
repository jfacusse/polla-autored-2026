#!/usr/bin/env python3
"""
Polla AutoRed — Mundial 2026
Corre con: python3 app.py
Acceso: http://TU_IP:5002
"""
import json, os, socket, calendar as _calendar
from pathlib import Path
import score_updater
import backup as bk

DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Offset de timezone de los horarios en fixtures.json respecto a UTC
# Chile en junio (invierno) = UTC-4  →  FIXTURE_TZ_OFFSET = -4
_tz_raw = os.environ.get("FIXTURE_TZ_OFFSET", "-4").strip()
FIXTURE_TZ_OFFSET = int(_tz_raw)
print(f"[init] FIXTURE_TZ_OFFSET={FIXTURE_TZ_OFFSET} (raw='{_tz_raw}')")
from functools import wraps
from datetime import datetime, timedelta
from flask import Flask, render_template, request, session, redirect, url_for, flash, jsonify

DIAS   = ["Lunes","Martes","Miércoles","Jueves","Viernes","Sábado","Domingo"]
MESES  = ["enero","febrero","marzo","abril","mayo","junio","julio",
          "agosto","septiembre","octubre","noviembre","diciembre"]

def torneo_is_open():
    cfg = _load("config")
    deadline_str = cfg.get("torneo_deadline", "2026-06-11 16:00")
    try:
        # deadline en hora Chile local → convertir a UTC para comparar
        deadline_local = datetime.strptime(deadline_str, "%Y-%m-%d %H:%M")
        deadline_utc = deadline_local - timedelta(hours=FIXTURE_TZ_OFFSET)
        return datetime.utcnow() < deadline_utc
    except Exception:
        return True

def parse_match_dt(date_str, time_str):
    """Devuelve (kickoff_utc datetime, kickoff_unix_ts int) o (None, 0) si falla."""
    try:
        local_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        # local → UTC: local - offset (offset=-4 para Chile → local + 4h = UTC)
        utc_dt = local_dt - timedelta(hours=FIXTURE_TZ_OFFSET)
        # calendar.timegm trata la tupla como UTC sin importar timezone del sistema
        ts = _calendar.timegm(utc_dt.timetuple())
        return utc_dt, ts
    except Exception as e:
        print(f"[lock] parse_match_dt error: {e}")
        return None, 0

def match_is_locked(date_str, time_str):
    """True si el partido está cerrado (≥5 min antes del kickoff en hora Chile)."""
    try:
        # Comparar todo en hora Chile local — sin conversiones UTC
        chile_now = datetime.utcnow() + timedelta(hours=FIXTURE_TZ_OFFSET)
        match_local = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        return chile_now >= match_local - timedelta(minutes=5)
    except Exception:
        return False

def date_pretty(date_str):
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return f"{DIAS[dt.weekday()]} {dt.day} de {MESES[dt.month-1]}"
    except Exception:
        return date_str

app = Flask(__name__)
app.secret_key = "polla_autored_2026_secret"
BASE = Path(__file__).parent

@app.template_filter("fecha")
def fecha_filter(date_str):
    return date_pretty(date_str)

# ── DATA HELPERS ────────────────────────────────────────────────────────────
def _load(name):
    f = DATA_DIR / f"{name}.json"
    return json.loads(f.read_text()) if f.exists() else {}

def _save(name, data):
    (DATA_DIR / f"{name}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False))

DEFAULT_CONFIG = {
    "nombre_polla": "Polla AutoRed — Mundial 2026",
    "admin_pin": "autored26",
    "pts_exacto": 5,
    "pts_resultado": 3,
    "jokers_disponibles": 3,
    "pts_campeon": 25,
    "activa": True,
    "torneo_deadline": "2026-06-11 16:00"
}

def _migrate():
    # seed config with defaults for any missing keys
    cfg = _load("config")
    changed = False
    for key, val in DEFAULT_CONFIG.items():
        if key not in cfg:
            cfg[key] = val
            changed = True
    if changed:
        _save("config", cfg)

    parts = _load("participants")
    changed = False
    for uid, p in parts.items():
        if "activo" not in p:
            p["activo"] = True
            changed = True
    if changed:
        _save("participants", parts)

bk.restore()
_migrate()
score_updater.start_background(interval_hours=2)
bk.start_background(interval_minutes=15)

def teams_data():
    f = BASE / "static_data" / "teams.json"
    return json.loads(f.read_text())["teams"] if f.exists() else {}

def teams_list():
    f = BASE / "static_data" / "teams_list.json"
    return json.loads(f.read_text()) if f.exists() else []

def players_list():
    f = BASE / "static_data" / "players.json"
    return json.loads(f.read_text()) if f.exists() else []

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
    cfg          = _load("config")
    torneo_res   = _load("torneo_results")
    torneo_picks = preds.get(user_id, {}).get("torneo", {})
    torneo_pts_map = {
        "campeon":  cfg.get("pts_campeon", 25),
        "goleador": 15,
    }
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
        if not p.get("activo", True):
            continue
        if p.get("es_admin"):
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

    all_preds = _load("predictions")
    torneo_picks_all = {
        uid: all_preds.get(uid, {}).get("torneo", {})
        for uid in parts
        if not parts[uid].get("es_admin") and parts[uid].get("activo", True)
    }

    return render_template("index.html",
        cfg=cfg, tabla=tabla, upcoming=upcoming, finished=finished,
        today_matches=today_matches, recent=recent,
        user=user_id, user_data=user_data,
        jokers_usados=len(jokers_usados), jokers_max=jokers_max,
        torneo_res=torneo_res, total_jugados=len(res),
        torneo_picks_all=torneo_picks_all, parts=parts)


@app.route("/login", methods=["GET","POST"])
def login():
    cfg   = _load("config")
    parts = _load("participants")
    if request.method == "POST":
        nombre = request.form.get("nombre","").strip()
        pin    = request.form.get("pin","").strip()
        email = request.form.get("email","").strip().lower()
        # admin login (by PIN only) — solo activa sesión, no marca la cuenta
        if pin == cfg.get("admin_pin"):
            if not email.endswith("@autored.cl"):
                flash("Ingresa tu correo @autored.cl para acceder como admin.", "error")
                return redirect(url_for("login"))
            uid = email.split("@")[0]
            session["user"]     = uid
            session["nombre"]   = email
            session["is_admin"] = True
            if uid not in parts:
                parts[uid] = {"nombre": email, "email": email, "pin": "", "es_admin": False,
                              "jokers_usados": [], "activo": True}
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
    cfg    = {**DEFAULT_CONFIG, **_load("config")}
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
        action = request.form.get("action", "")

        if action == "torneo_picks":
            # guardar solo premios del torneo
            if torneo_is_open():
                if not isinstance(preds.get(uid), dict):
                    preds[uid] = {}
                torneo = preds[uid].get("torneo", {})
                for key in ("campeon", "subcampeon", "goleador", "asistente"):
                    val = request.form.get(f"torneo_{key}", "").strip()
                    if val:
                        torneo[key] = val
                preds[uid]["torneo"] = torneo
                _save("predictions", preds)
                flash("✅ Premios del torneo guardados.", "ok")
            return redirect(url_for("predict") + "#torneo")

        # guardar picks de partidos (solo los que aún no cerraron)
        locked_now = set()
        intentos_bloqueados = []
        for f in fixts:
            if f["status"] != "upcoming":
                continue
            if match_is_locked(f["date"], f["time"]):
                locked_now.add(f["id"])
                h = request.form.get(f"h_{f['id']}", "").strip()
                a = request.form.get(f"a_{f['id']}", "").strip()
                if h != "" and a != "":
                    intentos_bloqueados.append(f"{f['home']} vs {f['away']}")
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
                      and f["id"] not in locked_now
                      and request.form.get(f"joker_{f['id']}") == "1"]
        parts[uid]["jokers_usados"] = jokers_new[:jokers_max]

        # Re-leer predictions justo antes de guardar para no pisar datos de otros usuarios
        preds_fresh = _load("predictions")
        if not isinstance(preds_fresh.get(uid), dict):
            preds_fresh[uid] = {}
        torneo_saved = preds_fresh[uid].get("torneo", {})
        preds_fresh[uid].update(user_picks)
        if torneo_saved:
            preds_fresh[uid]["torneo"] = torneo_saved

        _save("predictions", preds_fresh)
        _save("participants", parts)
        if intentos_bloqueados:
            flash(f"⚠️ No se guardaron picks de partidos ya cerrados: {', '.join(intentos_bloqueados)}", "error")
        else:
            flash("✅ Predicciones guardadas correctamente.", "ok")
        return redirect(url_for("predict"))

    upcoming = sorted([f for f in fixts if f["status"] == "upcoming"],
                      key=lambda x: (x.get("date",""), x.get("time","")))
    chile_now = datetime.utcnow() + timedelta(hours=FIXTURE_TZ_OFFSET)
    print(f"[predict] chile_now={chile_now} utc_now={datetime.utcnow()} tz_offset={FIXTURE_TZ_OFFSET}")
    for f in upcoming:
        f["user_pick"] = user_picks.get(f["id"])
        f["is_joker"] = f["id"] in (parts[uid].get("jokers_usados", []))
        f["is_locked"] = match_is_locked(f["date"], f["time"])
        _, ts = parse_match_dt(f["date"], f["time"])
        f["kickoff_ts"] = ts
        print(f"[predict] {f['id']} {f['date']} {f['time']} locked={f['is_locked']} ts={ts}")
    already_locked = [fid for fid in user_picks if res.get(fid)]
    torneo_picks = preds.get(uid, {}).get("torneo", {})
    torneo_res   = _load("torneo_results")

    deadline_str = cfg.get("torneo_deadline", "2026-06-11 16:00")

    return render_template("predict.html",
        cfg=cfg, upcoming=upcoming, user=uid, nombre=session["nombre"],
        jokers_usados=parts[uid].get("jokers_usados",[]),
        jokers_max=jokers_max, already_locked=already_locked,
        torneo_picks=torneo_picks, torneo_res=torneo_res,
        torneo_abierto=torneo_is_open(),
        torneo_deadline=deadline_str,
        teams_list=teams_list(),
        players_list=players_list())


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
        if not isinstance(pick, dict) or "home" not in pick:
            continue
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
                    match_dt_utc, _ = parse_match_dt(target["date"], target["time"])
                    chile_now = datetime.utcnow() + timedelta(hours=FIXTURE_TZ_OFFSET)
                    try:
                        match_local = datetime.strptime(f"{target['date']} {target['time']}", "%Y-%m-%d %H:%M")
                        not_started = chile_now < match_local
                    except Exception:
                        not_started = False
                    if not_started:
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

        elif action == "toggle_pagado":
            t_uid = request.form.get("uid")
            if t_uid and t_uid in parts and not parts[t_uid].get("es_admin"):
                parts[t_uid]["pagado"] = not parts[t_uid].get("pagado", False)
                estado = "marcado como pagado" if parts[t_uid]["pagado"] else "marcado como pendiente"
                _save("participants", parts)
                flash(f"✅ {parts[t_uid]['nombre'].split('@')[0]} {estado}.", "ok")
            else:
                flash("No se puede modificar a ese participante.", "error")

        elif action == "torneo_resultado":
            torneo_res = _load("torneo_results")
            for key in ("campeon", "goleador"):
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

        elif action == "update_fixture_time":
            fid   = request.form.get("fid", "").strip()
            fecha = request.form.get("fecha", "").strip()
            hora  = request.form.get("hora", "").strip()
            if fid and fecha and hora:
                updated = False
                for f in fixts:
                    if f["id"] == fid:
                        f["date"] = fecha
                        f["time"] = hora
                        updated = True
                        break
                if updated:
                    fix_path = BASE / "static_data" / "fixtures.json"
                    fix_path.write_text(json.dumps(fixts, indent=2, ensure_ascii=False))
                    flash(f"✅ Horario de {fid} actualizado a {fecha} {hora}", "ok")
                else:
                    flash(f"❌ No se encontró el partido {fid}", "error")
            else:
                flash("❌ Faltan datos (fid, fecha, hora)", "error")

        return redirect(url_for("admin"))

    upcoming = [f for f in fixts if f["status"] == "upcoming"]
    upcoming.sort(key=lambda x: (x["date"], x["time"]))
    finished = [f for f in fixts if f["status"] == "finished"]
    chile_now = datetime.utcnow() + timedelta(hours=FIXTURE_TZ_OFFSET)
    for f in upcoming:
        try:
            match_local = datetime.strptime(f"{f['date']} {f['time']}", "%Y-%m-%d %H:%M")
            f["can_enter_result"] = chile_now >= match_local
            f["unlocks_at"] = match_local.strftime("%H:%M")
        except Exception:
            f["can_enter_result"] = True
            f["unlocks_at"] = f.get("time", "")
        f["date_pretty"] = date_pretty(f["date"])
    tabla      = tabla_general()
    torneo_res = _load("torneo_results")
    return render_template("admin.html",
        cfg=cfg, parts=parts, upcoming=upcoming, finished=finished,
        tabla=tabla, res=res, preds=preds, torneo_res=torneo_res)


@app.route("/admin/fix-time/<fid>/<fecha>/<hora>")
@admin_required
def admin_fix_time(fid, fecha, hora):
    fixts = fixtures()
    fix_path = BASE / "static_data" / "fixtures.json"
    updated = False
    for f in fixts:
        if f["id"].upper() == fid.upper():
            f["date"] = fecha
            f["time"] = hora.replace("-", ":")
            updated = True
            break
    if updated:
        fix_path.write_text(json.dumps(fixts, indent=2, ensure_ascii=False))
        flash(f"✅ {fid} actualizado a {fecha} {hora.replace('-',':')}", "ok")
    else:
        flash(f"❌ No se encontró {fid}", "error")
    return redirect(url_for("admin"))

@app.route("/admin/picks/<uid>")
@admin_required
def admin_picks(uid):
    parts  = _load("participants")
    preds  = _load("predictions")
    res    = _load("results")
    fixts  = fixtures()
    cfg    = _load("config")
    if uid not in parts:
        flash("Usuario no encontrado.", "error")
        return redirect(url_for("admin"))
    pts, detalle = calcular_puntos(uid)
    fix_map = {f["id"]: f for f in fixts}
    rows = []
    user_picks = preds.get(uid, {})
    for fid, pick in user_picks.items():
        if not isinstance(pick, dict) or "home" not in pick:
            continue
        f = fix_map.get(fid, {})
        r = res.get(fid, {})
        d = next((x for x in detalle if x["fid"] == fid), None)
        rows.append({
            "fid": fid, "date": f.get("date",""), "time": f.get("time",""),
            "home": f.get("home","?"), "away": f.get("away","?"),
            "group": f.get("group",""),
            "pick_h": pick["home"], "pick_a": pick["away"],
            "real_h": r.get("score_home"), "real_a": r.get("score_away"),
            "pts": d["pts"] if d else None,
            "tipo": d["tipo"] if d else ("pending" if not r else "sin_pick"),
            "joker": fid in parts.get(uid, {}).get("jokers_usados", []),
        })
    rows.sort(key=lambda x: (x["date"], x["time"]))
    torneo_picks = user_picks.get("torneo", {})
    return render_template("admin_picks.html",
        cfg=cfg, uid=uid, p=parts[uid], rows=rows, pts=pts,
        torneo_picks=torneo_picks, torneo_res=_load("torneo_results"))


@app.route("/api/standings")
def api_standings():
    return jsonify(tabla_general())

@app.route("/api/refresh-scores")
@admin_required
def refresh_scores():
    updated = score_updater.run_update()
    return jsonify({"updated": updated})

@app.route("/api/backup-now")
@admin_required
def backup_now():
    try:
        bk.backup()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


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


@app.route("/debug/lock")
@admin_required
def debug_lock():
    """Muestra el estado de lock de todos los partidos upcoming — diagnóstico."""
    fixts = fixtures()
    utc_now = datetime.utcnow()
    chile_now = utc_now + timedelta(hours=FIXTURE_TZ_OFFSET)
    upcoming = [f for f in fixts if f["status"] == "upcoming"]
    rows = []
    for f in upcoming:
        locked = match_is_locked(f["date"], f["time"])
        _, ts = parse_match_dt(f["date"], f["time"])
        rows.append({
            "id": f["id"], "match": f"{f['home']} vs {f['away']}",
            "date": f["date"], "time": f["time"],
            "is_locked": locked, "kickoff_ts": ts
        })
    return jsonify({
        "server_utc": utc_now.strftime("%Y-%m-%d %H:%M:%S"),
        "chile_now": chile_now.strftime("%Y-%m-%d %H:%M:%S"),
        "FIXTURE_TZ_OFFSET": FIXTURE_TZ_OFFSET,
        "matches": rows
    })


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
