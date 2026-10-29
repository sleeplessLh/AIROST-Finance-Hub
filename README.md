# AIROST Finance Hub

AIROST Finance Hub is a responsive club budget and reimbursement web application. Members submit receipt-backed claims, treasurers review and release payments, and administrators manage accounts, events and budgets.

## Features

- One AIROST ID login with Member, Treasurer and Administrator roles
- Draft, submission lock, rejection/resubmission, approval and payment workflow
- Item photo and supporting-document uploads
- Complete payee banking details for authorised finance users
- Event budget allocation and live committed/remaining totals
- Search, filters, audit trail, responsive mobile layout and graceful empty/error states
- SQLite for local development and PostgreSQL-ready production configuration

## Quick start

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000`.

### Demo accounts

| Role | AIROST ID | Password |
|---|---|---|
| Member | `AIR-M001` | `Member123!` |
| Treasurer | `AIR-T001` | `Finance123!` |
| Administrator | `AIR-A001` | `Admin123!` |

The database and demo accounts are created automatically on first run.

## Tests

```powershell
pytest -q
```

## Production deployment

The repository includes `render.yaml` and a `Procfile` for deployment to Render. Create a Render Blueprint from this repository; it provisions the Flask service and PostgreSQL database. Set `SECRET_KEY` to a strong random value if not using the Blueprint-generated value.

Uploads are stored locally during development. For a production deployment that must retain uploaded files across service restarts, connect the upload layer to Supabase Storage or another persistent object store.

## Project structure

```text
app.py             Flask application, models and routes
templates/         Jinja pages for all roles
static/            Responsive styles and interactions
tests/             End-to-end Flask workflow tests
render.yaml        Render service/database definition
```

## Core workflow

```text
Draft → Submitted (locked) → Rejected → Edit and resubmit
                           ↘ Approved → Pending payment → Paid
```

Submitted claims cannot be edited. A rejected claim becomes editable again and locks on resubmission.
