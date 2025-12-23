import os
from datetime import date, timedelta, datetime
import string
import secrets
from PIL import Image, ImageDraw, ImageFont
import segno
from zoneinfo import ZoneInfo
import csv
from io import StringIO, BytesIO

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

    @app.route("/coupon/<code>.png", methods=["GET"])
def coupon_png(code):
    code = (code or "").strip().upper()
    sub = Subscriber.query.filter_by(reward_code=code).first_or_404()

    # ---- QR con el CÓDIGO (no metemos password ni nada) ----
    qr = segno.make(sub.reward_code, error='m')
    qr_buf = BytesIO()
    qr.save(qr_buf, kind="png", scale=8, border=2)
    qr_buf.seek(0)
    qr_img = Image.open(qr_buf).convert("RGBA")

    # ---- Cupón "pro" 1080x1350 ----
    W, H = 1080, 1350
    CREAM = (251, 246, 239)
    CREAM2 = (243, 241, 238)
    BROWN = (176, 96, 48)
    OLIVE = (112, 112, 64)
    TEXT = (17, 17, 17)
    MUTED = (90, 90, 90)
    WHITE = (255, 255, 255)

    img = Image.new("RGB", (W, H), CREAM)
    draw = ImageDraw.Draw(img)
    for y in range(H):
        t = y / (H - 1)
        r = int(CREAM[0] * (1 - t) + CREAM2[0] * t)
        g = int(CREAM[1] * (1 - t) + CREAM2[1] * t)
        b = int(CREAM[2] * (1 - t) + CREAM2[2] * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b))

    # Fuentes (si no encuentra, usa default)
    try:
        f_title = ImageFont.truetype("DejaVuSans-Bold.ttf", 56)
        f_reward = ImageFont.truetype("DejaVuSans-Bold.ttf", 48)
        f_terms = ImageFont.truetype("DejaVuSans.ttf", 38)
        f_code  = ImageFont.truetype("DejaVuSans-Bold.ttf", 84)
        f_small = ImageFont.truetype("DejaVuSans.ttf", 30)
    except Exception:
        f_title = f_reward = f_terms = f_code = f_small = ImageFont.load_default()

    # Helper wrap
    def wrap(text, font, max_w):
        words = (text or "").split()
        lines, line = [], ""
        for w in words:
            test = (line + " " + w).strip()
            if draw.textlength(test, font=font) <= max_w:
                line = test
            else:
                if line:
                    lines.append(line)
                line = w
        if line:
            lines.append(line)
        return lines

    # Card
    pad = 70
    x1, y1 = pad, 210
    x2, y2 = W - pad, H - 180
    draw.rounded_rectangle((x1, y1, x2, y2), radius=40, fill=WHITE, outline=(220, 205, 190), width=4)

    y = y1 + 50

    # Logo si existe
    try:
        logo_path = os.path.join(app.root_path, "static", "img", "brota_logo.png")
        logo = Image.open(logo_path).convert("RGBA")
        maxw = 520
        ratio = maxw / logo.size[0]
        logo = logo.resize((int(logo.size[0] * ratio), int(logo.size[1] * ratio)))
        img.paste(logo, (int((W - logo.size[0]) / 2), y), logo)
        y += logo.size[1] + 28
    except Exception:
        y += 20

    draw.text((W//2, y), "🌱 TU PREMIO", fill=TEXT, font=f_title, anchor="mm")
    y += 70

    # Premio
    max_w = (x2 - x1) - 110
    for line in wrap(sub.reward_name, f_reward, max_w)[:3]:
        draw.text((W//2, y), line, fill=BROWN, font=f_reward, anchor="mm")
        y += 60
    y += 10

    # Términos
    for line in wrap(sub.reward_terms, f_terms, max_w)[:4]:
        draw.text((W//2, y), line, fill=MUTED, font=f_terms, anchor="mm")
        y += 48

    y += 25

    # Código
    draw.text((W//2, y), sub.reward_code, fill=BROWN, font=f_code, anchor="mm")
    y += 120

    # QR (lo pegamos centrado)
    qr_size = 360
    qr_img = qr_img.resize((qr_size, qr_size))
    img.paste(qr_img, (W//2 - qr_size//2, y), qr_img)
    y += qr_size + 30

    # Vigencia
    vf = sub.valid_from.strftime("%d/%m/%Y")
    vt = sub.valid_to.strftime("%d/%m/%Y")
    draw.text((W//2, y), f"Válido: {vf} → {vt}", fill=OLIVE, font=f_terms, anchor="mm")
    y += 55

    draw.text((W//2, y), "Mostrá este cupón al camarero ✅", fill=TEXT, font=f_small, anchor="mm")

    # Entrega PNG
    out = BytesIO()
    img.save(out, format="PNG")
    out.seek(0)

    resp = Response(out.getvalue(), mimetype="image/png")
    resp.headers["Cache-Control"] = "no-store"
    return resp



with app.app_context():
    db.create_all()
