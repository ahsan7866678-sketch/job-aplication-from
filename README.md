# Interview Application API

A FastAPI service that stores application details and CV PDF bytes in MySQL.
The PDF is also attached to the notification email.

## Configure MySQL and email

1. Create a MySQL database and a dedicated user with permission to create and
   write tables in that database. For example, run the following in MySQL after
   replacing the placeholders with your own values:

   ```sql
   CREATE DATABASE your_database_name
     CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   CREATE USER 'your_database_user'@'localhost' IDENTIFIED BY 'your_password';
   GRANT ALL PRIVILEGES ON your_database_name.* TO 'your_database_user'@'localhost';
   ```

2. Copy `.env.example` to `.env` and fill in the MySQL and SMTP settings. Use
   the database host, port, database name, and username supplied by your MySQL
   setup. Keep `.env` private; it is ignored by Git. Configure an SMTP mailbox
   and set `MAIL_TO` to the company email that should receive applications.
   Use an SMTP app password or provider-specific credential where required.

   `MAIL_STARTTLS` and `MAIL_SSL_TLS` are mutually exclusive for most providers.
   Use the values and port required by your SMTP provider.

3. Install dependencies in the project's active virtual environment if needed:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

The API creates the `applications` table when it starts. If it finds an older
table layout, it migrates the legacy name and position columns and adds fields
required by the current API while preserving existing application rows. PDFs
from older applications are verified and migrated from `uploads/cvs/` into
MySQL; their local copies are removed after a successful database migration.
New PDF uploads are stored directly in MySQL. The database itself must already
exist. Missing MySQL configuration prevents startup and is reported explicitly;
the application does not invent credentials.

## Run the API

From the project directory in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --reload
```

Open <http://127.0.0.1:8000/> to be redirected to the interactive Swagger
documentation, or go directly to <http://127.0.0.1:8000/docs>.

Uploaded PDF bytes are stored in the `applications.cv_data` MySQL `LONGBLOB`
column and attached to the notification email. Include the MySQL database in
your backups; CVs are no longer written to the local `uploads/cvs/` directory.

## Submit an application

The `POST /applications` endpoint accepts `multipart/form-data`:

| Field | Required | Description |
| --- | --- | --- |
| `full_name` | Yes | Applicant name |
| `email` | Yes | Valid email address |
| `phone` | Yes | Applicant phone number |
| `position` | Yes | Position applied for |
| `cover_letter` | No | Cover letter |
| `cv` | Yes | PDF file, maximum 5 MiB |

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

For retry-safe submissions, send an `Idempotency-Key` header and reuse it only
for retries of the exact same application and PDF. A successful email is not
sent again for that key. A failed email can be retried with the same key. Calls
without this optional header create a new application each time.

## Responses

Successful submission (HTTP `201`; an already completed idempotent retry uses
HTTP `200`):

```json
{
  "success": true,
  "message": "Application received",
  "application_id": 123
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

If the application is saved but SMTP delivery fails, the endpoint returns
HTTP `502` with `success: false` and the saved `application_id`. The database
record and CV are retained; if you submitted an idempotency key, retry with that
same key. Otherwise, contact the company with that ID. Database failures return
HTTP `503`. Invalid form data returns HTTP `422`.

Example email failure response (HTTP `502`):

```json
{
  "success": false,
  "message": "The application was saved, but its notification email could not be sent. If you submitted an Idempotency-Key, retry with that same key; otherwise contact the company with the application ID.",
  "application_id": 123
}
```
