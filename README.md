# 🍽️ EatALot

Full-stack food ordering platform built with:

- Flask (Python)
- PostgreSQL
- Database triggers
- Group & individual orders
- Manager dashboard analytics

## Features

- Individual and group orders
- Automatic order total recalculation via DB trigger
- Payment + delivery workflow
- Manager dashboard with KPIs
- Customer creation from UI
- Modern responsive UI

## Setup

```bash
pip install -r requirements.txt
python flaskapp.py
```

Open: http://127.0.0.1:5000

## Project Structure

```
EatALot/
│
├── templates/              # HTML templates (UI pages: dashboard, menu, manager, checkout, etc.)
├── scripts/                # SQL scripts / database setup files
├── __pycache__/            # Auto-generated Python cache files (not important for the project logic)
│
├── flaskapp.py             # Main Flask application (routes, business logic, order flow)
├── app.py                  # Alternative/legacy entry file (if used during development)
├── db.py                   # Database connection helper (PostgreSQL connection logic)
│
├── requirements.txt        # Python dependencies needed to run the project
├── .env                    # Environment variables (DB credentials, secrets – not for production push)
├── .gitignore              # Files/folders Git should ignore (venv, cache, env, etc.)
├── EatALot-Slide Deck.pdf  # Project presentation slides
└── README.md               # Project documentation
```

## Slide Deck
To see the complete presentation slides from Assignments 1 to 8 of this project, please navigate to the file EatALot-Slide Deck.pdf


## Special Thank You to Prof. Revoredo
Dear Professor Kate, as last words we wanted to apologize for the presentation day where the code was not running, we highly appreciate you letting us present without showing the application, even when we managed last minute. Hope everything runs smoothly once you are checking the assignment. Please let us know if you have any questions :)
