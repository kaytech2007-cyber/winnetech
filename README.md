# Winneba Technical Institute — Staff Management System

A Django application for managing programmes, HODs, Facilitators, attendance, timetables, and notifications at Winneba Technical Institute.

## 🌟 Features

### 1. **Welcome Page**
- Modern animated slideshow with school images
- Particle effects and smooth animations
- Four separate portals: Admin, Super Admin, Facilitator, and HOD
- Programme and HOD management, with programme-scoped HOD access
- Facilitator records grouped by programme and employment type
- Internship supervision history, extension requests, and expiry handling
- Clean, responsive UI design

### 2. **Facilitator Registration & Authentication**
- Admin-created Permanent and Internship Facilitator accounts
- Programme-scoped active Permanent Facilitator supervision for interns
- Generated Staff IDs and one-year default internship duration
- One-time email password setup; no initial password is emailed or stored
- Profile photo and certificate uploads, plus the existing approval workflow

### 3. **Admin Portal**
- **Dashboard**: Overview of attendance, pending approvals, today's timetable
- **Facilitator Management**: View, approve, reject, and deactivate facilitator accounts while retaining their records
- **Programme Management**: Maintain programmes and view assigned staff
- **HOD Records**: Create HOD accounts and assign programmes
- **Attendance System**: QR code-based attendance tracking
- **Timetable Management**: Create and manage weekly class schedules
- **Class Scheduling**: Evaluate candidates, request Facilitator confirmation, and route declined assignments to HOD-approved replacement review
- **Notifications**: Send announcements to facilitators
- **QR Code Generation**: Generate and print attendance QR codes

### 4. **Facilitator Portal**
- **Dashboard**: Personal attendance status, today's classes, notifications
- **QR Scanner**: Built-in camera scanner for attendance marking
- **Timetable View**: Full weekly timetable
- **Class Reminders**: Automatic 10-minute reminders before classes
- **Notification System**: Receive announcements with sound alerts

### 5. **QR Code Attendance System**
- Generate daily QR codes for attendance
- Facilitators scan QR code to check in
- Second scan marks lesson completion
- Attendance records are retained for facilitators who do not scan
- School hours: 7:00 AM – 3:30 PM

### 6. **Real-time Features**
- **Live Notifications**: User-scoped notifications and push notifications
- **Class Reminders**: 10-minute, 2-minute, and start-time reminders
- **Notification Polling**: Auto-refresh every 15 seconds

### 7. **Responsive Design**
- Mobile-first approach
- Works on all devices (desktop, tablet, mobile)
- Touch-friendly interface
- Optimized for classroom wall-mounted tablets

## 📋 Course Structure

### Electives
- Mathematics
- English Language
- Social Studies
- ICT
- Science
- EST (Entrepreneur Skills Training)

### Departmental (Vocational)
1. **Information Technology**
   - Computer System & Servicing
   - Workshop Practice & Power Management
   - Networking & Data Communication

2. **Plumbing**
   - Plumbing (General)
   - Plumbing Installation
   - Plumbing Maintenance

3. **Electricals**
   - Electrical Installation
   - Electrical Maintenance
   - Electrical Wiring

4. **Welding & Fabrication**
   - Welding & Fabrication
   - Metal Work

5. **Fashion**
   - Fashion Design
   - Garment Construction
   - Textile Studies

6. **Catering**
   - Catering (General)
   - Food & Beverage
   - Hospitality Management

7. **Mechanical Engineering**
   - Mechanical Engineering
   - Machine Maintenance

8. **Auto Mechanics**
   - Auto Mechanics
   - Auto Electrical

9. **Building & Construction**
   - Building & Construction
   - Masonry
   - Carpentry

## 🚀 Installation & Setup

### Prerequisites
- Python 3.11
- pip (Python package manager)

### 1. Create a local environment and install dependencies (Windows PowerShell)
```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:DEBUG = "true"
```

### 2. Check the existing local database before starting
```bash
python manage.py check
```

Do not run `setup_data.py`: it is a legacy bootstrap script that can replace timetable data and create a default account. Do not delete or recreate `db.sqlite3`. The Render build installs dependencies and collects static files only; it does not create or reset accounts and does not apply database migrations.

### Database migration and initial account setup

Migrations require an explicit deployment decision. Before applying any migration, confirm that the command targets the intended database, review the pending migration plan, and take and verify a restorable backup. For a local database, inspect the pending state with:

```powershell
python manage.py showmigrations
python manage.py migrate --plan
```

Only after approval, apply migrations manually to the intended environment:

```powershell
python manage.py migrate
```

For Render, use the service shell or a controlled release procedure only after the same review and backup checks. Do not add `migrate` to the build command or run it automatically during deployment.

For a brand-new database only, create the first Super Admin interactively after the approved schema is ready:

```powershell
python manage.py createsuperuser
```

Run this command in the target environment (or with a verified connection to its database), protect the terminal/session, and choose a unique username, email address, and password when prompted. It does not reset any existing account. The application recognizes a Django superuser without an `AdminProfile` as a Super Admin. After signing in at `/superadmin-portal/`, create ordinary Admin accounts through the Super Admin portal. Do not use `setup_admins`; that legacy command is disabled and makes no database changes.

### Email and staff onboarding

Admin, HOD, and Facilitator accounts created by management require a unique email address. The system generates a Staff ID, keeps the account unable to authenticate until the recipient sets a password, and emails a single-use setup link. The token is carried in the link fragment so it is not sent in the HTTP request path, and only its SHA-256 digest is stored. Links expire after 48 hours by default; configure `STAFF_ONBOARDING_TIMEOUT` as a positive number of seconds to change this. If delivery fails, the account and a retryable onboarding record are retained, and an authorized manager can resend the link without creating a duplicate account.

Configure SMTP through the deployment environment or an approved secret manager,
outside source control. Set `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`,
`EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `EMAIL_USE_SSL`, and
`DEFAULT_FROM_EMAIL` there as appropriate for your mail provider. Do not put
credential values in source files, documentation, or committed environment files.

Without working email settings, account creation remains pending and displays a retry action; the application does not report that an email was sent.

Migrations `0017_alter_teacherprofile_staff_id_staffidregistry_and_more.py` and `0018_auth_user_email_case_insensitive_unique.py` are pending changes for durable generated Staff ID allocation, onboarding-token hashes, the longer generated Facilitator ID field, and case-insensitive uniqueness of non-empty account emails. Migration `0018` checks for duplicate emails and stops with an error if cleanup is needed; inspect the data and resolve any duplicates through an approved process before applying it. Review both migrations and back up the actual database before applying; this project does not apply them automatically.

### 3. Start the local server after the database schema is ready
```bash
python manage.py runserver
```

### 5. Access the Application
- **Welcome Page**: http://127.0.0.1:8000/
- **Admin Portal**: http://127.0.0.1:8000/admin-portal/
- **Facilitator Login**: http://127.0.0.1:8000/login/
- **Facilitator Registration**: http://127.0.0.1:8000/register/

The HOD login is `/hod-portal/`; the HOD dashboard is `/hod/dashboard/`.

### Internship expiry

Run expiry manually with:

```bash
python manage.py expire_internships
```

The command is idempotent and retains internship and supervision history. No scheduler is configured by this project. In production, schedule this command to run daily using the host's scheduler (for example, Windows Task Scheduler or cron) and configure it to use the deployment's Python environment and project directory.

## 📁 Project Structure

```
winnitech/
├── core/                      # Main application
│   ├── models.py             # Database models
│   ├── views.py              # View functions
│   ├── forms.py              # Form definitions
│   ├── urls.py               # URL routing
│   ├── consumers.py          # WebSocket consumers
│   ├── routing.py            # WebSocket routing
│   ├── admin.py              # Admin panel config
│   └── migrations/           # Database migrations
├── templates/                 # HTML templates
│   ├── admin/                # Admin portal templates
│   ├── teacher/              # Existing Facilitator portal templates (legacy directory name)
│   ├── auth/                 # Authentication templates
│   ├── qr/                   # QR scanner templates
│   ├── base.html             # Base template
│   └── welcome.html          # Landing page
├── static/                    # Static files
│   ├── css/
│   │   └── main.css          # Main stylesheet
│   ├── js/
│   │   └── main.js           # JavaScript functionality
│   └── images/               # School images
├── media/                     # User uploads
│   ├── profiles/             # Profile photos
│   ├── documents/            # CV, certificates, IDs
│   └── qrcodes/              # Generated QR codes
├── winnitech/                 # Project settings
│   ├── settings.py           # Django settings
│   ├── urls.py               # Root URL config
│   ├── asgi.py               # ASGI config (WebSockets)
│   └── wsgi.py               # WSGI config
├── manage.py                  # Django management script
├── setup_data.py             # Initial data setup
└── README.md                 # This file
```

## 🎨 Technology Stack

### Backend
- **Django 5.x**: Web framework
- **Channels**: WebSocket support
- **Daphne**: ASGI server
- **SQLite**: Database (can be upgraded to PostgreSQL)

### Frontend
- **Bootstrap 5**: UI framework
- **Font Awesome 6**: Icons
- **Poppins Font**: Typography
- **Vanilla JavaScript**: Interactivity
- **WebSocket API**: User-scoped live notifications

### Libraries
- **qrcode**: QR code generation
- **Pillow**: Image processing
- **html5-qrcode**: Browser-based QR scanner
- **django-crispy-forms**: Form rendering
- **django-widget-tweaks**: Form customization

## 🔧 Configuration

### School Hours
The scheduling decision engine permits classes Monday through Saturday, 08:00-15:30.
This policy is enforced by the scheduling service and form validation.

### Scheduling decisions and confirmation

The scheduling engine records each candidate evaluation as `ELIGIBLE`, `BLOCKED`,
or `UNVERIFIED`, with explanations and measured weekly workload. Workload is not
rejected against a maximum because no authorised limit is configured. Candidate
ranking compares eligible candidates by projected weekly teaching minutes and
never overrides a failed rule.

Browser GPS is not independent proof of physical attendance or location. The
project has no configured trusted attendance, availability, or subject
qualification verifier, so missing evidence remains `UNVERIFIED` and no class
confirmation request is sent. A server-side `SCHEDULING_VERIFICATION_PROVIDER`
may be configured in Django settings as a callable or dotted Python path only after an authorised,
independent verification source is available. It must not trust values supplied
directly by a browser.

A confirmation request is not a class assignment. A schedule is created only
after a currently eligible Facilitator accepts and passes a fresh evaluation.
Declines create a programme-scoped HOD replacement review; HOD approval sends a
new confirmation request but does not assign the replacement. The replacement
must accept before a schedule is created.

Migrations `0020_schedulingevaluation.py`,
`0021_classconfirmationrequest_classreplacementrequest.py`, and
`0022_classconfirmationrequest_unique_pending_confirmation_per_class_date.py`
add evaluation, confirmation, and replacement audit records plus pending-request
uniqueness. They have been prepared but not applied to any project database;
apply them only through the approved database change process.

### Time Zone
```python
TIME_ZONE = 'Africa/Accra'
```

### WebSocket Configuration
Uses in-memory channel layer (no Redis required for development).
For production, configure Redis in `settings.py`.

## 📱 Mobile Responsiveness

The system is fully responsive and works on:
- Desktop computers (1920px+)
- Laptops (1366px+)
- Tablets (768px+)
- Mobile phones (320px+)

## 🔔 Notification System

### Types
- **Attendance**: Check-in/lesson completion alerts
- **Schedule**: Class assignment notifications
- **Reminder**: 10-minute class reminders
- **General**: Announcements
- **Admin**: Administrative notices

### Sound Alerts
- Notification sound: Two-tone beep
- Reminder sound: Four-note chime
- Success sound: Three-note ascending

## 🎯 Key Workflows

### Facilitator Registration Flow
1. Facilitator fills multi-step registration form
2. Uploads documents (CV, certificates, ID)
3. Selects course type and preferred subject
4. Admin reviews application
5. Admin approves/rejects
6. Facilitator receives notification
7. Facilitator can log in

### Attendance Flow
1. Admin generates daily QR code
2. QR code displayed/printed in classroom
3. Facilitator scans QR code (check-in)
4. System records facilitator attendance
5. Admin receives notification
6. After lesson, facilitator scans again (lesson done)
7. System records completion time

### Class Scheduling Flow
1. Admin creates timetable entries
2. Admin evaluates a proposed facilitator and schedule
3. A confirmation request is sent only for an `ELIGIBLE` evaluation
4. Facilitator accepts or declines the request
5. On decline, the programme HOD reviews and approves any eligible replacement
6. A replacement receives a new confirmation request and must accept
7. An accepted request is re-evaluated before the class schedule is created
8. 10 minutes before class: First reminder
9. 2 minutes before class: Second reminder
10. At class time: Final reminder
7. Facilitator scans QR after teaching

## 🐛 Troubleshooting

### QR Scanner Not Working
- Ensure HTTPS or localhost
- Grant camera permissions
- Use modern browser (Chrome, Firefox, Safari)

### WebSocket Connection Failed
- Check if Daphne is running
- Verify ASGI configuration
- Check firewall settings

### Images Not Loading
- Run `python manage.py collectstatic`
- Check MEDIA_ROOT and MEDIA_URL settings
- Verify file permissions

## 📄 License

This project is developed for Winneba Technical Institute.

## 👨‍💻 Developer

Built with ❤️ for Winneba Technical Institute
Transforming and Empowering the Youth Through Vocational Skills

## 🆘 Support

For issues or questions:
1. Check the troubleshooting section
2. Review Django logs: `python manage.py runserver`
3. Check browser console for JavaScript errors
4. Verify database migrations are applied

---

**Winneba Technical Institute** — Est. Winneba, Ghana
