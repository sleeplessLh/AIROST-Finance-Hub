import io
import os
import shutil
from pathlib import Path
from unittest.mock import patch

os.environ["FLASK_ENV"] = "testing"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app import AuditLog, Event, User, app, db

TEST_UPLOADS = Path(__file__).parent / ".tmp_uploads"


def setup_module():
    TEST_UPLOADS.mkdir(parents=True, exist_ok=True)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, UPLOAD_FOLDER=str(TEST_UPLOADS))
    with app.app_context():
        db.create_all()
        member = User(club_id="M1", name="Member One", email="m@test.com", role="member",
                      bank_name="Bank", bank_account_name="Member One", bank_account_number="123")
        member.set_password("password")
        treasurer = User(club_id="T1", name="Treasurer", email="t@test.com", role="treasurer")
        treasurer.set_password("password")
        admin = User(club_id="A1", name="Admin", email="a@test.com", role="admin")
        admin.set_password("password")
        db.session.add_all([member, treasurer, admin, Event(name="Test Event", budget=1000, status="active")])
        db.session.commit()


def teardown_module():
    with app.app_context():
        db.session.remove()
        db.drop_all()
    shutil.rmtree(TEST_UPLOADS, ignore_errors=True)


def login(client, club_id):
    return client.post("/login", data={"club_id": club_id, "password": "password"}, follow_redirects=True)


def test_role_dashboards_and_access():
    client = app.test_client()
    assert login(client, "M1").status_code == 200
    assert client.get("/admin/users").status_code == 403
    client.get("/logout")
    assert login(client, "A1").status_code == 200
    assert client.get("/admin/users").status_code == 200


def test_complete_claim_workflow():
    client = app.test_client()
    login(client, "M1")
    response = client.post("/claims/new", data={
        "title": "Project materials", "amount": "120.50", "purchase_date": "2026-10-20",
        "merchant": "Campus Shop", "category": "Equipment", "event_id": "1",
        "description": "Materials for workshop", "action": "submitted",
        "item_photo": (io.BytesIO(b"image"), "item.jpg"),
        "supporting_document": (io.BytesIO(b"receipt"), "receipt.pdf"),
    }, content_type="multipart/form-data", follow_redirects=True)
    assert b"submitted and locked" in response.data
    assert client.get("/claims/1/edit").status_code == 403
    client.get("/logout")
    login(client, "T1")
    response = client.post("/claims/1/review", data={"decision": "approve"}, follow_redirects=True)
    assert b"Pending payment" in response.data
    response = client.post("/claims/1/pay", data={"paid_amount": "120.50", "paid_date": "2026-10-29",
                                                    "transaction_reference": "TXN-001"}, follow_redirects=True)
    assert b"Paid" in response.data


def test_admin_and_treasurer_can_adjust_event_budget():
    client = app.test_client()
    login(client, "T1")
    response = client.post("/events/1/budget", data={"budget": "1500"}, follow_redirects=True)
    assert b"budget updated to RM 1500.00" in response.data
    client.get("/logout")
    login(client, "A1")
    response = client.post("/events/1/budget", data={"budget": "1800"}, follow_redirects=True)
    assert b"budget updated to RM 1800.00" in response.data
    client.get("/logout")
    login(client, "M1")
    assert client.post("/events/1/budget", data={"budget": "2000"}).status_code == 403


def test_admin_can_create_edit_reset_and_deactivate_user():
    client = app.test_client()
    login(client, "A1")
    response = client.post("/admin/users", data={
        "club_id": "air-m002", "name": "New Member", "email": "new@airost.club",
        "role": "member", "password": "Initial123!",
    }, follow_redirects=True)
    assert b"Account created" in response.data
    with app.app_context():
        user = User.query.filter_by(club_id="AIR-M002").one()
        user_id = user.id
    edit_page = client.get(f"/admin/users/{user_id}/edit")
    assert edit_page.status_code == 200
    assert b"Edit New Member" in edit_page.data

    response = client.post(f"/admin/users/{user_id}/edit", data={
        "club_id": "AIR-T002", "name": "Updated Member", "email": "updated@airost.club",
        "role": "treasurer", "password": "Changed123!",
    }, follow_redirects=True)
    assert b"Account updated successfully" in response.data
    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.club_id == "AIR-T002"
        assert user.role == "treasurer"
        assert user.check_password("Changed123!")
        actions = {log.action for log in AuditLog.query.all()}
        assert {"user_created", "user_updated", "user_password_reset"}.issubset(actions)

    response = client.post(f"/admin/users/{user_id}/toggle", follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        assert db.session.get(User, user_id).active is False
    client.get("/logout")
    response = client.post("/login", data={"club_id": "AIR-T002", "password": "Changed123!"}, follow_redirects=True)
    assert b"Invalid AIROST ID or password" in response.data


def test_admin_account_validation_and_self_protection():
    client = app.test_client()
    login(client, "A1")
    response = client.post("/admin/users", data={
        "club_id": "M1", "name": "Duplicate", "email": "other@test.com",
        "role": "member", "password": "Password123!",
    }, follow_redirects=True)
    assert b"AIROST ID already exists" in response.data
    response = client.post("/admin/users/3/edit", data={
        "club_id": "A1", "name": "Admin", "email": "a@test.com",
        "role": "member", "password": "",
    }, follow_redirects=True)
    assert b"cannot remove your own administrator access" in response.data
    response = client.post("/admin/users/3/toggle", follow_redirects=True)
    assert b"cannot deactivate your own account" in response.data


def test_member_can_analyse_receipt_and_roles_are_enforced():
    extracted = {
        "merchant": "Campus Shop", "purchase_date": "2026-10-30", "amount": 48.9,
        "category": "Equipment", "title": "Workshop components",
        "description": "Electronic components for the AI workshop.",
        "receipt_number": "R-100", "confidence": 0.94,
    }
    client = app.test_client()
    login(client, "M1")
    with patch("app.extract_receipt_data", return_value=extracted):
        response = client.post("/ai/receipt/analyse", data={
            "receipt": (io.BytesIO(b"receipt image"), "receipt.jpg"),
        }, content_type="multipart/form-data")
    assert response.status_code == 200
    assert response.get_json() == extracted
    client.get("/logout")
    login(client, "T1")
    response = client.post("/ai/receipt/analyse", data={
        "receipt": (io.BytesIO(b"receipt image"), "receipt.jpg"),
    }, content_type="multipart/form-data")
    assert response.status_code == 403


def test_receipt_analysis_has_graceful_configuration_and_file_errors():
    client = app.test_client()
    login(client, "M1")
    response = client.post("/ai/receipt/analyse", data={}, content_type="multipart/form-data")
    assert response.status_code == 400
    assert b"Choose a receipt" in response.data
    with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
        response = client.post("/ai/receipt/analyse", data={
            "receipt": (io.BytesIO(b"receipt image"), "receipt.jpg"),
        }, content_type="multipart/form-data")
    assert response.status_code == 503
    assert b"not configured" in response.data

