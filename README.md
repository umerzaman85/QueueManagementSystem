# QMS — Queue Management System

A Django-based ticket queue management system for service environments such as universities, banks, and government offices. Provides real-time queue updates, speaker announcements, and role-based dashboards for staff and supervisors.

## Overview

- **Kiosk Interface** — self-service ticket generation for customers
- **Staff Dashboard** — real-time ticket claiming, serving, and transfer
- **Supervisor Portal** — analytics, reporting, and system oversight
- **Speaker Notifications** — real-time text-to-speech ticket announcements
- **Thermal Printing** — direct ticket printing via ESC/POS-compatible printers
- **Cross-Platform** — runs on Windows and Linux

## Key Features

**Ticket Management**
- Automatic ticket numbering with service-based prefixes
- Real-time status tracking (Waiting, Calling, Served, Skipped, Transferred)
- Service categorization and date-wise sequencing

**Staff Interface**
- Role-based access control (Staff / Supervisor / Administrator)
- Ticket claiming, recalling, serving, and skipping
- Counter assignment and ticket transfer between counters

**Supervisor Analytics**
- Daily, weekly, and monthly statistics
- Service and staff performance metrics
- CSV export of ticket flow data

**Speaker Notifications**
- Text-to-speech announcements via `pyttsx3`
- Real-time broadcast over WebSocket
- Multi-language voice support

**Thermal Printing**
- Direct integration with EPSON TM-T88V thermal printers
- ESC/POS command generation via `pywin32`

## Tech Stack

**Backend:** Django 5.2, Django Channels 4.3, Redis, SQLite (PostgreSQL/MySQL supported)
**Real-Time & Hardware:** WebSocket (Channels), pyttsx3 (TTS), pywin32 (printer control)
**Frontend:** Django templates, HTML/CSS/JavaScript, REST APIs for external integration

## Architecture

```
┌─────────────┐   ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
│    Kiosk    │   │    Staff    │   │ Supervisor  │   │   Speaker   │
│  Interface  │   │  Dashboard  │   │   Portal    │   │   Client    │
└──────┬──────┘   └──────┬──────┘   └──────┬──────┘   └──────┬──────┘
       │                 │                  │                 │
       └─────────────────┴────────┬─────────┴─────────────────┘
                                   ▼
                          ┌─────────────────┐
                          │ Django Channels │
                          └────────┬────────┘
                                   ▼
                          ┌─────────────────┐
                          │      Redis      │
                          └────────┬────────┘
                                   ▼
                          ┌─────────────────┐
                          │      SQLite     │
                          └─────────────────┘
```

## Getting Started

```bash
# Set up environment
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Configure (.env)
REDIS_URL=redis://localhost:6379/0
DJANGO_SECRET_KEY=your-secret-key

# Run migrations and create an admin user
python manage.py migrate
python manage.py createsuperuser

# Start the ASGI server (required for WebSocket support)
python -m daphne qms_project.asgi:application --port 8000
```

Services and counters are configured via the Django admin or shell (`Service` and `Counter` models).

## Usage

| Interface | Access | Notes |
|---|---|---|
| Kiosk | `/` | No authentication required; select a service to generate a ticket |
| Staff Dashboard | `/staff/` | Login required; claim, recall, serve, skip, or transfer tickets |
| Supervisor Portal | `/supervisor/` | Login required; real-time analytics and CSV export |

## API Endpoints

```http
POST /staff/action/              # Next / Prev / Recall / Done / Skip
GET  /staff/current-ticket/      # Poll current ticket
POST /staff/transfer_ticket/     # Transfer ticket to another counter
GET  /supervisor/stats/          # Queue statistics
GET  /supervisor/active-tickets/ # Active tickets feed
GET  /supervisor/export/         # CSV export
GET  /health/                    # System health check
```

## Deployment

Runs behind Gunicorn (WSGI) with Daphne handling the WebSocket connections, deployable via systemd or Docker. Logs are separated by concern (`queue.log`, `ticket.log`, `errors.log`) for production monitoring.

## Screenshots

| Kiosk | Staff Dashboard |
|---|---|
| ![Self-Service Kiosk](image.png) | ![Staff Dashboard](image-1.png) |

| Supervisor Dashboard | Supervisor Dashboard (cont.) |
|---|---|
| ![Supervisor Dashboard](image-2.png) | ![Supervisor Dashboard](image-3.png) |

Additionally screenshots are also available in `/qms_project/screenshots`.

## Tech Highlights

- Django 5, Django Channels, Redis-backed WebSocket messaging
- Role-based authentication and authorization
- Real-time queue algorithms with ticket state management
- Hardware integration: thermal printing and text-to-speech
- Production-oriented logging and health-check endpoints
