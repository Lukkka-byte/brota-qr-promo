import os
from datetime import date, timedelta, datetime
import string
import secrets
from zoneinfo import ZoneInfo
import csv
from io import StringIO

from flask import (
    Flask, render_template, request, abort,
    make_response, redirect, url_for, Response
)
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)

# 🔐 Clave secreta (cookies, seguridad)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key")

# 🗄️ Base de datos (Render / local)
db_url = os.environ.get("DATABASE_URL", "sqlite:///local.db")

# 🔧 Fix para Render (postgres:// → postgresql://)
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# 🔑 Admin
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "cambialo-porfavor")

# ⏰ Configuración horaria
TZ = ZoneInfo("Europe/Madrid")
MORNING_START_HOUR = 7
MORNING_END_HOUR = 12

class Subscriber(db.Model):
    __tablename__ = "subscribers"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)

    # Marketing consent (para emails)
    consent = db.Column(db.Boolean, nullable=False, default=True)

    # Premio
    reward_code = db.Column(db.String(32), unique=True, nullable=False)  # ✅ UNIQUE
    reward_name = db.Column(db.String(200), nullable=False)
    reward_terms = db.Column(db.String(700), nullable=False)

    # Vigencia
    valid_from = db.Column(db.Date, nullable=False)
    valid_to = db.Column(db.Date, nullable=False)

    # Control de canje / bloqueo
    redeemed_at = db.Column(db.DateTime, nullable=True)  # si está canjeado
    voided_at = db.Column(db.DateTime, nullable=True)    # si está bloqueado/anulado

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(TZ), nullable=False)


def generate_reward_code(length: int = 8) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def get_validity_range():
    hoy = date.today()
    inicio = hoy + timedelta(days=1)
    fin = inicio + timedelta(days=6)  # 7 días contando el inicio
    return inicio, fin


def is_morning_now() -> bool:
    now = datetime.now(TZ)
    return MORNING_START_HOUR <= now.hour < MORNING_END_HOUR


def pick_random_reward():
    rewards = []

    # Solo por la mañana
    if is_morning_now():
        rewards.append({
            "name": "🌱 Momento dulce: porción de tarta",
            "terms": "✅ Válido en horario de mañana con la compra de 1 café o bebida. ⚠️ Sujeto a disponibilidad."
        })

    rewards.extend([
        {
            "name": "🥟 Empanada a elegir (pollo o carne)",
            "terms": "✅ Válido con la compra de 1 consumición. ⚠️ Sujeto a disponibilidad."
        },
        {
            "name": "🍰 Postre gratis",
            "terms": "✅ Válido con la compra de 1 tapa, sándwich o menú. ⚠️ Sujeto a disponibilidad."
        },
        {
            "name": "🍺 Cerveza en botella",
            "terms": "✅ Válido con la compra de 1 consumición. ⚠️ Sujeto a disponibilidad."
        },
        {
            "name": "🍽️ 15% de descuento en Menú del día",
            "terms": "✅ Válido solo para Menú del día (no incluye extras). ⚠️ No acumulable con otras promociones."
        },
    ])

    return secrets.choice(rewards)


@app.route("/", methods=["GET", "POST"])
def reward():
    error = None
    already_claimed = request.cookies.get("reward_claimed") == "true"

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        consent = request.form.get("consent")  # "on" si está marcado

        if already_claimed:
            error = "Desde este dispositivo ya se ha utilizado una recompensa. Solo se permite una por dispositivo."
        elif not email:
            error = "Por favor, escribe tu email."
        elif consent is None:
            error = "Debes aceptar recibir promociones por email para activar el premio."
        else:
            existing = Subscriber.query.filter_by(email=email).first()
            if existing:
                error = "Este email ya ha utilizado una recompensa. Solo se permite una por persona."
            else:
                valid_from, valid_to = get_validity_range()
                chosen = pick_random_reward()

                # ✅ Evitar colisión (muy improbable, pero correcto)
                for _ in range(6):
                    code = generate_reward_code()
                    if not Subscriber.query.filter_by(reward_code=code).first():
                        break
                else:
                    error = "No se pudo generar un código. Intenta de nuevo."
                    return render_template("reward_form.html", error=error, already_claimed=already_claimed)

                subscriber = Subscriber(
                    email=email,
                    consent=True,
                    reward_code=code,
                    reward_name=chosen["name"],
                    reward_terms=chosen["terms"],
                    valid_from=valid_from,
                    valid_to=valid_to,
                )
                db.session.add(subscriber)
                db.session.commit()

                resp = make_response(
                    render_template(
                        "reward_success.html",
                        reward_code=code,
                        reward_name=subscriber.reward_name,
                        reward_terms=subscriber.reward_terms,
                        valid_from=valid_from,
                        valid_to=valid_to,
                    )
                )
                resp.set_cookie("reward_claimed", "true", max_age=60 * 60 * 24 * 365, path="/")
                return resp

    return render_template("reward_form.html", error=error, already_claimed=already_claimed)


# ======= PRIVACIDAD =======
@app.route("/privacy", methods=["GET"])
def privacy():
    return render_template("privacy.html")


# ======= BAJA (consent=False) =======
@app.route("/unsubscribe", methods=["GET", "POST"])
def unsubscribe():
    msg = None
    error = None

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if not email:
            error = "Por favor, introduce tu email."
        else:
            sub = Subscriber.query.filter_by(email=email).first()
            if not sub:
                msg = "Si ese email estaba registrado, ya se ha procesado la baja."
            else:
                sub.consent = False
                db.session.commit()
                msg = "Listo. Se ha procesado tu baja de comunicaciones por email."

    return render_template("unsubscribe.html", msg=msg, error=error)


# ======= ADMIN =======
def require_admin():
    pwd = request.args.get("password")
    if pwd != ADMIN_PASSWORD:
        abort(401)
    return pwd


@app.route("/admin", methods=["GET"])
def admin():
    pwd = require_admin()

    q = request.args.get("q", "").strip().upper()
    if q:
        subscribers = Subscriber.query.filter(Subscriber.reward_code.ilike(f"%{q}%")).order_by(Subscriber.created_at.desc()).all()
    else:
        subscribers = Subscriber.query.order_by(Subscriber.created_at.desc()).all()

    return render_template("admin.html", subscribers=subscribers, q=q, pwd=pwd, today=date.today())


def is_valid_today(sub: Subscriber) -> bool:
    today = date.today()
    return sub.valid_from <= today <= sub.valid_to


@app.route("/admin/export", methods=["GET"])
def admin_export():
    pwd = require_admin()

    subscribers = (
        Subscriber.query
        .filter_by(consent=True)
        .order_by(Subscriber.created_at.asc())
        .all()
    )

    si = StringIO()
    writer = csv.writer(si)

    writer.writerow(["email", "fecha_alta"])

    for s in subscribers:
        writer.writerow([
            s.email,
            s.created_at.strftime("%Y-%m-%d %H:%M")
        ])

    output = si.getvalue()
    si.close()

    return Response(
        output,
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=brota_emails.csv"
        }
    )





@app.route("/admin/redeem/<int:sub_id>", methods=["POST"])
def admin_redeem(sub_id: int):
    pwd = require_admin()
    sub = Subscriber.query.get_or_404(sub_id)

    # ✅ reglas de canje
    if sub.voided_at is not None:
        return redirect(url_for("admin", password=pwd, q=sub.reward_code))

    if sub.redeemed_at is not None:
        return redirect(url_for("admin", password=pwd, q=sub.reward_code))

    if not is_valid_today(sub):
        return redirect(url_for("admin", password=pwd, q=sub.reward_code))

    sub.redeemed_at = datetime.now(TZ)
    db.session.commit()

    next_page = request.args.get("next")

    if next_page == "verify":
         return redirect(url_for("verify_code", password=pwd, code=sub.reward_code))

    return redirect(url_for("admin", password=pwd, q=sub.reward_code))



@app.route("/admin/void/<int:sub_id>", methods=["POST"])
def admin_void(sub_id: int):
    pwd = require_admin()
    sub = Subscriber.query.get_or_404(sub_id)

    sub.voided_at = datetime.now(TZ)
    db.session.commit()

    return redirect(url_for("admin", password=pwd, q=sub.reward_code))


@app.route("/admin/unvoid/<int:sub_id>", methods=["POST"])
def admin_unvoid(sub_id: int):
    pwd = require_admin()
    sub = Subscriber.query.get_or_404(sub_id)

    sub.voided_at = None
    db.session.commit()

    return redirect(url_for("admin", password=pwd, q=sub.reward_code))


# ✅ SOLO PARA PRUEBAS: resetear el bloqueo por dispositivo (cookie)
@app.route("/admin/reset-device", methods=["POST"])
def admin_reset_device():
    pwd = require_admin()
    resp = redirect(url_for("admin", password=pwd))
    resp.set_cookie("reward_claimed", "", expires=0, path="/")
    return resp
@app.route("/verify", methods=["GET", "POST"])
def verify_code():
    pwd = require_admin()
    result = None

    code = (request.args.get("code") or "").strip().upper()

    if request.method == "POST":
        code = request.form.get("code", "").strip().upper()

    if code:
        sub = Subscriber.query.filter_by(reward_code=code).first()

        if not sub:
            result = {"status": "NOT_FOUND"}
        elif sub.voided_at:
            result = {"status": "BLOCKED"}
        elif sub.redeemed_at:
            result = {"status": "REDEEMED", "when": sub.redeemed_at}
        elif date.today() < sub.valid_from:
            result = {"status": "NOT_YET", "from": sub.valid_from}
        elif date.today() > sub.valid_to:
            result = {"status": "EXPIRED"}
        else:
            result = {
                "status": "OK",
                "sub_id": sub.id,
                "reward": sub.reward_name,
                "terms": sub.reward_terms,
            }

    return render_template("verify.html", pwd=pwd, result=result, code=code)


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=5000)

