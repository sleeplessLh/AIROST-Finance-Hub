import io
import os
from pathlib import Path

TEST_DB = Path(__file__).parent / "test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["FLASK_ENV"] = "testing"
import os

os.environ["FLASK_ENV"] = "testing"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app import Event, User, app, db


def setup_module():
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
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

