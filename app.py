import csv
import io
import os
import uuid
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import Flask, Response, abort, flash, redirect, render_template, request, send_from_directory, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "pdf"}

app = Flask(__name__)
database_url = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'instance' / 'airost.db'}")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)
elif database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY", "dev-airost-change-me"),
    SQLALCHEMY_DATABASE_URI=database_url,
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    MAX_CONTENT_LENGTH=16 * 1024 * 1024,
    UPLOAD_FOLDER=str(BASE_DIR / "uploads"),
)
Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)
Path(BASE_DIR / "instance").mkdir(parents=True, exist_ok=True)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    club_id = db.Column(db.String(30), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(150), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="member")
    active = db.Column(db.Boolean, default=True)
    bank_name = db.Column(db.String(100), default="")
    bank_account_name = db.Column(db.String(100), default="")
    bank_account_number = db.Column(db.String(60), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self):
        return self.active


class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, default="")
    budget = db.Column(db.Float, nullable=False, default=0)
    start_date = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(20), default="active")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def paid_total(self):
        return sum(c.paid_amount or c.amount for c in self.claims if c.status == "paid")

    @property
    def committed_total(self):
        return sum(c.amount for c in self.claims if c.status in {"approved", "pending_payment"})

    @property
    def remaining(self):
        return self.budget - self.paid_total - self.committed_total


class Claim(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(20), unique=True, nullable=False)
    title = db.Column(db.String(160), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    purchase_date = db.Column(db.Date, nullable=False)
    merchant = db.Column(db.String(150), nullable=False)
    category = db.Column(db.String(80), nullable=False)
    description = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(30), nullable=False, default="draft")
    rejection_reason = db.Column(db.Text, default="")
    paid_amount = db.Column(db.Float, nullable=True)
    paid_date = db.Column(db.Date, nullable=True)
    transaction_reference = db.Column(db.String(100), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    submitted_at = db.Column(db.DateTime, nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    paid_at = db.Column(db.DateTime, nullable=True)
    member_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    event_id = db.Column(db.Integer, db.ForeignKey("event.id"), nullable=False)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    member = db.relationship("User", foreign_keys=[member_id], backref="claims")
    reviewer = db.relationship("User", foreign_keys=[reviewed_by_id])
    event = db.relationship("Event", backref="claims")
    attachments = db.relationship("Attachment", backref="claim", cascade="all, delete-orphan")


class Attachment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(20), nullable=False)
    original_name = db.Column(db.String(255), nullable=False)
    stored_name = db.Column(db.String(255), nullable=False)
    claim_id = db.Column(db.Integer, db.ForeignKey("claim.id"), nullable=False)


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    action = db.Column(db.String(80), nullable=False)
    details = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    claim_id = db.Column(db.Integer, db.ForeignKey("claim.id"), nullable=True)
    user = db.relationship("User")


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def roles_required(*roles):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if current_user.role not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def audit(action, details="", claim=None):
    db.session.add(AuditLog(action=action, details=details, user_id=current_user.id, claim_id=claim.id if claim else None))


USER_ROLES = {"member", "treasurer", "admin"}


def normalise_account_form(form):
    club_id = form.get("club_id", "").strip().upper()
    name = form.get("name", "").strip()
    email = form.get("email", "").strip().lower()
    role = form.get("role", "").strip().lower()
    if not club_id or not name or not email:
        raise ValueError("AIROST ID, full name and email are required.")
    if role not in USER_ROLES:
        raise ValueError("Select a valid account role.")
    return club_id, name, email, role


def account_conflict(club_id, email, exclude_user_id=None):
    query = User.query
    if exclude_user_id is not None:
        query = query.filter(User.id != exclude_user_id)
    if query.filter(func.lower(User.club_id) == club_id.lower()).first():
        return "That AIROST ID already exists."
    if query.filter(func.lower(User.email) == email.lower()).first():
        return "That email address is already in use."
    return None


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def save_upload(file, kind, claim):
    if not file or not file.filename:
        return
    if not allowed_file(file.filename):
        raise ValueError("Only PNG, JPG, WEBP and PDF files are accepted.")
    original = secure_filename(file.filename)
    stored = f"{uuid.uuid4().hex}_{original}"
    file.save(Path(app.config["UPLOAD_FOLDER"]) / stored)
    db.session.add(Attachment(kind=kind, original_name=original, stored_name=stored, claim=claim))


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        user = User.query.filter(func.lower(User.club_id) == request.form["club_id"].strip().lower()).first()
        if user and user.active and user.check_password(request.form["password"]):
            login_user(user, remember=bool(request.form.get("remember")))
            return redirect(url_for("dashboard"))
        flash("Invalid AIROST ID or password.", "danger")
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    if current_user.role == "member":
        claims = Claim.query.filter_by(member_id=current_user.id).order_by(Claim.created_at.desc()).all()
        return render_template("member_dashboard.html", claims=claims)
    claims = Claim.query.order_by(Claim.created_at.desc()).all()
    events = Event.query.order_by(Event.created_at.desc()).all()
    return render_template("staff_dashboard.html", claims=claims, events=events)


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        current_user.name = request.form["name"].strip()
        current_user.email = request.form["email"].strip()
        current_user.bank_name = request.form["bank_name"].strip()
        current_user.bank_account_name = request.form["bank_account_name"].strip()
        current_user.bank_account_number = request.form["bank_account_number"].strip()
        audit("profile_updated", "Personal and banking information updated")
        db.session.commit()
        flash("Profile updated successfully.", "success")
        return redirect(url_for("profile"))
    return render_template("profile.html")


@app.route("/claims/new", methods=["GET", "POST"])
@roles_required("member")
def new_claim():
    events = Event.query.filter_by(status="active").all()
    if request.method == "POST":
        if not current_user.bank_account_number:
            flash("Complete your banking details before submitting a claim.", "warning")
            return redirect(url_for("profile"))
        try:
            claim = Claim(
                reference=f"AIR-{datetime.now():%y%m}-{uuid.uuid4().hex[:5].upper()}",
                title=request.form["title"].strip(), amount=float(request.form["amount"]),
                purchase_date=datetime.strptime(request.form["purchase_date"], "%Y-%m-%d").date(),
                merchant=request.form["merchant"].strip(), category=request.form["category"],
                description=request.form["description"].strip(), member_id=current_user.id,
                event_id=int(request.form["event_id"]), status=request.form.get("action", "draft"),
            )
            if claim.amount <= 0:
                raise ValueError("Amount must be greater than zero.")
            if claim.status == "submitted":
                photo = request.files.get("item_photo")
                document = request.files.get("supporting_document")
                if not photo or not photo.filename or not document or not document.filename:
                    raise ValueError("An item photo and a supporting document are required to submit.")
                claim.submitted_at = datetime.utcnow()
            db.session.add(claim)
            db.session.flush()
            save_upload(request.files.get("item_photo"), "item_photo", claim)
            save_upload(request.files.get("supporting_document"), "supporting_document", claim)
            audit("claim_created" if claim.status == "draft" else "claim_submitted", claim.reference, claim)
            db.session.commit()
            flash("Claim saved." if claim.status == "draft" else "Claim submitted and locked for review.", "success")
            return redirect(url_for("claim_detail", claim_id=claim.id))
        except (ValueError, KeyError) as exc:
            db.session.rollback()
            flash(str(exc), "danger")
    return render_template("claim_form.html", events=events, claim=None)


@app.route("/claims/<int:claim_id>/edit", methods=["GET", "POST"])
@roles_required("member")
def edit_claim(claim_id):
    claim = Claim.query.get_or_404(claim_id)
    if claim.member_id != current_user.id or claim.status not in {"draft", "rejected"}:
        abort(403)
    events = Event.query.filter_by(status="active").all()
    if request.method == "POST":
        try:
            claim.title = request.form["title"].strip()
            claim.amount = float(request.form["amount"])
            claim.purchase_date = datetime.strptime(request.form["purchase_date"], "%Y-%m-%d").date()
            claim.merchant = request.form["merchant"].strip()
            claim.category = request.form["category"]
            claim.event_id = int(request.form["event_id"])
            claim.description = request.form["description"].strip()
            save_upload(request.files.get("item_photo"), "item_photo", claim)
            save_upload(request.files.get("supporting_document"), "supporting_document", claim)
            action = request.form.get("action", "draft")
            if action == "submitted":
                kinds = {a.kind for a in claim.attachments}
                if not {"item_photo", "supporting_document"}.issubset(kinds):
                    raise ValueError("An item photo and a supporting document are required to submit.")
                claim.status = "submitted"
                claim.submitted_at = datetime.utcnow()
                claim.rejection_reason = ""
                audit("claim_resubmitted", claim.reference, claim)
            else:
                audit("claim_updated", claim.reference, claim)
            db.session.commit()
            flash("Claim submitted and locked." if action == "submitted" else "Draft updated.", "success")
            return redirect(url_for("claim_detail", claim_id=claim.id))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
    return render_template("claim_form.html", events=events, claim=claim)


@app.route("/claims/<int:claim_id>")
@login_required
def claim_detail(claim_id):
    claim = Claim.query.get_or_404(claim_id)
    if current_user.role == "member" and claim.member_id != current_user.id:
        abort(403)
    logs = AuditLog.query.filter_by(claim_id=claim.id).order_by(AuditLog.created_at.desc()).all()
    return render_template("claim_detail.html", claim=claim, logs=logs)


@app.route("/claims/<int:claim_id>/review", methods=["POST"])
@roles_required("treasurer")
def review_claim(claim_id):
    claim = Claim.query.get_or_404(claim_id)
    if claim.status != "submitted":
        abort(400)
    decision = request.form["decision"]
    if decision == "approve":
        claim.status = "pending_payment"
        claim.rejection_reason = ""
        audit("claim_approved", claim.reference, claim)
        flash("Claim approved and moved to pending payment.", "success")
    else:
        reason = request.form.get("reason", "").strip()
        if not reason:
            flash("A rejection reason is required.", "danger")
            return redirect(url_for("claim_detail", claim_id=claim.id))
        claim.status = "rejected"
        claim.rejection_reason = reason
        audit("claim_rejected", reason, claim)
        flash("Claim rejected. The member may now edit it.", "warning")
    claim.reviewed_by_id = current_user.id
    claim.reviewed_at = datetime.utcnow()
    db.session.commit()
    return redirect(url_for("claim_detail", claim_id=claim.id))


@app.route("/claims/<int:claim_id>/pay", methods=["POST"])
@roles_required("treasurer")
def pay_claim(claim_id):
    claim = Claim.query.get_or_404(claim_id)
    if claim.status != "pending_payment":
        abort(400)
    claim.paid_amount = float(request.form["paid_amount"])
    claim.paid_date = datetime.strptime(request.form["paid_date"], "%Y-%m-%d").date()
    claim.transaction_reference = request.form["transaction_reference"].strip()
    claim.status = "paid"
    claim.paid_at = datetime.utcnow()
    audit("claim_paid", f"RM {claim.paid_amount:.2f} · {claim.transaction_reference}", claim)
    db.session.commit()
    flash("Payment recorded successfully.", "success")
    return redirect(url_for("claim_detail", claim_id=claim.id))


@app.route("/admin/users", methods=["GET", "POST"])
@roles_required("admin")
def admin_users():
    if request.method == "POST":
        try:
            club_id, name, email, role = normalise_account_form(request.form)
            password = request.form.get("password", "")
            if len(password) < 8:
                raise ValueError("Temporary password must contain at least 8 characters.")
            conflict = account_conflict(club_id, email)
            if conflict:
                raise ValueError(conflict)
            user = User(club_id=club_id, name=name, email=email, role=role)
            user.set_password(password)
            db.session.add(user)
            audit("user_created", f"{user.club_id} · {user.role}")
            db.session.commit()
            flash("Account created.", "success")
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        return redirect(url_for("admin_users"))
    return render_template("admin_users.html", users=User.query.order_by(User.created_at.desc()).all())


@app.route("/admin/users/<int:user_id>/edit", methods=["GET", "POST"])
@roles_required("admin")
def edit_user(user_id):
    user = User.query.get_or_404(user_id)
    if request.method == "POST":
        try:
            club_id, name, email, role = normalise_account_form(request.form)
            if user.id == current_user.id and role != "admin":
                raise ValueError("You cannot remove your own administrator access.")
            conflict = account_conflict(club_id, email, exclude_user_id=user.id)
            if conflict:
                raise ValueError(conflict)
            old_summary = f"{user.club_id} · {user.name} · {user.email} · {user.role}"
            user.club_id, user.name, user.email, user.role = club_id, name, email, role
            password = request.form.get("password", "")
            if password:
                if len(password) < 8:
                    raise ValueError("New password must contain at least 8 characters.")
                user.set_password(password)
            audit("user_updated", f"{old_summary} → {user.club_id} · {user.name} · {user.email} · {user.role}")
            if password:
                audit("user_password_reset", user.club_id)
            db.session.commit()
            flash("Account updated successfully.", "success")
            return redirect(url_for("admin_users"))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
    return render_template("admin_user_edit.html", user=user)


@app.route("/admin/users/<int:user_id>/toggle", methods=["POST"])
@roles_required("admin")
def toggle_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash("You cannot deactivate your own account.", "danger")
    else:
        user.active = not user.active
        audit("user_status_changed", f"{user.club_id} · {'active' if user.active else 'inactive'}")
        db.session.commit()
    return redirect(url_for("admin_users"))


@app.route("/admin/events", methods=["GET", "POST"])
@roles_required("admin")
def admin_events():
    if request.method == "POST":
        event = Event(name=request.form["name"].strip(), description=request.form["description"].strip(),
                      budget=float(request.form["budget"]),
                      start_date=datetime.strptime(request.form["start_date"], "%Y-%m-%d").date(), status="active")
        db.session.add(event)
        audit("event_created", f"{event.name} · RM {event.budget:.2f}")
        db.session.commit()
        flash("Event budget created.", "success")
        return redirect(url_for("admin_events"))
    return render_template("admin_events.html", events=Event.query.order_by(Event.created_at.desc()).all())


@app.route("/events/<int:event_id>/budget", methods=["POST"])
@roles_required("admin", "treasurer")
def update_event_budget(event_id):
    event = Event.query.get_or_404(event_id)
    try:
        new_budget = float(request.form["budget"])
    except (KeyError, ValueError):
        flash("Enter a valid budget amount.", "danger")
        return redirect(request.referrer or url_for("dashboard"))
    committed = event.paid_total + event.committed_total
    if new_budget < committed:
        flash(f"Budget cannot be lower than RM {committed:.2f}, which is already committed or paid.", "danger")
        return redirect(request.referrer or url_for("dashboard"))
    old_budget = event.budget
    event.budget = new_budget
    audit("event_budget_adjusted", f"{event.name}: RM {old_budget:.2f} → RM {new_budget:.2f}")
    db.session.commit()
    flash(f"{event.name} budget updated to RM {new_budget:.2f}.", "success")
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/uploads/<path:filename>")
@login_required
def uploaded_file(filename):
    attachment = Attachment.query.filter_by(stored_name=filename).first_or_404()
    if current_user.role == "member" and attachment.claim.member_id != current_user.id:
        abort(403)
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


@app.route("/reports/claims.csv")
@roles_required("treasurer", "admin")
def export_claims():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Reference", "Member", "AIROST ID", "Event", "Category", "Amount", "Status", "Purchase date", "Merchant"])
    for claim in Claim.query.order_by(Claim.created_at.desc()).all():
        writer.writerow([claim.reference, claim.member.name, claim.member.club_id, claim.event.name, claim.category,
                         f"{claim.amount:.2f}", claim.status, claim.purchase_date.isoformat(), claim.merchant])
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=airost-claims.csv"})


@app.errorhandler(403)
def forbidden(_):
    return render_template("error.html", code=403, message="You do not have permission to view this page."), 403


@app.errorhandler(404)
def not_found(_):
    return render_template("error.html", code=404, message="The page you requested could not be found."), 404


@app.context_processor
def template_helpers():
    labels = {"draft": "Draft", "submitted": "Pending review", "rejected": "Rejected", "approved": "Approved",
              "pending_payment": "Pending payment", "paid": "Paid"}
    return {"status_labels": labels, "now": datetime.now}


def seed_demo_data():
    if User.query.first():
        return
    users = [
        ("AIR-M001", "Aina Rahman", "aina@airost.club", "member", "Member123!", "Maybank", "Aina Rahman", "112233445566"),
        ("AIR-T001", "Jason Lim", "treasurer@airost.club", "treasurer", "Finance123!", "", "", ""),
        ("AIR-A001", "Nur Izzati", "admin@airost.club", "admin", "Admin123!", "", "", ""),
    ]
    for club_id, name, email, role, password, bank, holder, number in users:
        user = User(club_id=club_id, name=name, email=email, role=role, bank_name=bank,
                    bank_account_name=holder, bank_account_number=number)
        user.set_password(password)
        db.session.add(user)
    db.session.add_all([
        Event(name="AI Innovation Workshop 2026", description="Hands-on AI workshop for university students.", budget=8500,
              start_date=datetime(2026, 11, 15).date(), status="active"),
        Event(name="AIROST Recruitment Drive", description="Annual membership and outreach campaign.", budget=3200,
              start_date=datetime(2026, 12, 2).date(), status="active"),
    ])
    db.session.commit()


with app.app_context():
    db.create_all()
    if os.getenv("FLASK_ENV") != "testing":
        seed_demo_data()


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)

