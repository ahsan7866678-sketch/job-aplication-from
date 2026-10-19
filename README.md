# Interview Application API

A FastAPI service that accepts an application and PDF CV, then emails the
application details and CV attachment to the configured company mailbox.
The API has no database dependency and does not retain submissions; application
data and CV bytes are processed in memory for email delivery only.

## Configure email

Set these SMTP settings in the project's ignored `.env` file, or configure
them as deployment environment variables:

```dotenv
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USERNAME=your_gmail_address
MAIL_PASSWORD=your_gmail_app_password
MAIL_FROM=your_gmail_address
MAIL_TO=recipient_email_address
MAIL_STARTTLS=true
MAIL_SSL_TLS=false
```

`MAIL_PASSWORD` must be a Google App Password, not the normal Google account
password. App Passwords require 2-Step Verification. `MAIL_FROM` should match
the authenticated Gmail account. Keep real credentials out of source control
and logs. The API uses STARTTLS and waits for Gmail to accept the message before
returning success.

Install dependencies if needed:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Run the API from the project directory:

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload --reload-include .env
```

Open <http://127.0.0.1:8000/docs>.

For deployment, install `requirements.txt`, provide the `MAIL_*` values via
the hosting platform's secret environment settings, and start with
`python -m uvicorn main:app --host 0.0.0.0 --port 8000` (use the platform's
required port if it supplies one). No database URL or service is needed.

## Submit an application

The `POST /applications` endpoint accepts `multipart/form-data`:

| Field | Required | Description |
| --- | --- | --- |
| `full_name` | Yes | Applicant name |
| `email` | Yes | Valid email address |
| `phone` | Yes | Applicant phone number |
| `position` | Yes | Position applied for |
| `cover_letter` | No | Cover letter |
| `cv` | Yes | Valid PDF file named with a `.pdf` extension, maximum 5 MiB |

PowerShell/cURL example:

```powershell
curl.exe -X POST "http://127.0.0.1:8000/applications" `
  -F "full_name=Jane Applicant" `
  -F "email=jane@example.com" `
  -F "phone=+1 555 010 1234" `
  -F "position=Software Engineer" `
  -F "cover_letter=I am interested in this role." `
  -F "cv=@C:\path\to\resume.pdf;type=application/pdf"
```

Each valid request sends a new email. Requests are not stored and retries are
not deduplicated; a retry after an uncertain email result may send a duplicate.

## Responses

Successful submission (HTTP `201`):

```json
{
  "success": true,
  "message": "Application received and emailed",
  "application_id": "a4f33e2a-5b10-4c25-a334-e7291735679a"
}
```

Invalid or malformed PDF (HTTP `400`):

```json
{
  "success": false,
  "message": "The uploaded file is not a valid PDF document."
}
```

CV larger than 5 MiB (HTTP `413`):

```json
{
  "success": false,
  "message": "The CV exceeds the 5 MB file size limit."
}
```

If Gmail rejects the credentials, the endpoint returns HTTP `502` with a
message to verify the Gmail address and App Password. Other SMTP connection,
timeout, sender/recipient, and message rejection errors also return actionable
HTTP `502` messages. Missing or invalid email configuration returns HTTP `503`.
These responses include an `application_id`; the application and CV are not
retained, so contact the company directly or submit again. Invalid form data
returns HTTP `422`.
