import sqlite3
from datetime import datetime

from models import MealInfo
from models import RecordTrainingInput

def init_db(db_path):
    """Initialize the database tables."""

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        cursor.execute("PRAGMA foreign_keys = ON")

        cursor.execute("DROP TABLE IF EXISTS training_sessions")

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS practices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                type TEXT NOT NULL,
                created_at TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS training_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                practice_id INTEGER NOT NULL REFERENCES practices(id),
                warm_up TEXT,
                cool_down TEXT,
                rpe INTEGER,
                created_at TEXT NOT NULL,
                note TEXT
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS training_sets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                training_session_id INTEGER NOT NULL REFERENCES training_sessions(id) ON DELETE CASCADE,
                set_number INTEGER NOT NULL,
                weight REAL,
                reps INTEGER,
                distance REAL,
                duration REAL
            )
            """
        )


        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS meal_records (
               date TEXT NOT NULL,
               meal_type TEXT,
               items TEXT,
               note TEXT NOT NULL,
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.commit()

def add_training_session(input_data: RecordTrainingInput, db_path: str) -> str:
    """Save the RecordTrainingInput to the 3-table training session schema.
    Returns a success string or an error string (Tool Rejection) if practices are missing.
    """

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")

        incoming_practice_names = list(set([s.practice_name.lower() for s in input_data.sessions]))

        placeholders = ",".join(["?"] * len(incoming_practice_names))
        cursor.execute(f"SELECT lower(name) FROM practices WHERE lower(name) in ({placeholders})",
                       incoming_practice_names)
        existing_practices = [row[0] for row in cursor.fetchall()]
        new_practices = [p for p in incoming_practice_names if p not in existing_practices]

        if new_practices and not input_data.confirm_new_practices:
            return f"""Error: The following practices are not in the database: {new_practices}.
                    Ask the user if they want to create them. Do not proceed until they say yes.
                    If they say yes, call this tool again with confirm_new_practices=True.
                    """

        try:
            for session in input_data.sessions:
                cursor.execute("SELECT id FROM practices WHERE lower(name)= ?", (session.practice_name.lower(),))
                row = cursor.fetchone()
                if not row:
                    now_str = datetime.now().isoformat()
                    cursor.execute(
                        "INSERT INTO practices (name, type, created_at, active) VALUES (?, ?, ?, 1)",
                        (session.practice_name, session.practice_type, now_str)
                    )
                    practice_id = cursor.lastrowid
                else:
                    practice_id = row[0]

                cursor.execute(
                    "INSERT INTO training_sessions (practice_id, created_at, note, rpe, warm_up, cool_down) VALUES (?, ?, ?, ?, ?, ?)",
                    (practice_id, input_data.date.isoformat(), session.note, session.rpe, session.warm_up, session.cool_down)
                )
                session_id = cursor.lastrowid

                for s in session.sets:
                    cursor.execute(
                        "INSERT INTO training_sets (training_session_id, set_number, weight, reps, distance, duration) VALUES (?, ?, ?, ?, ?, ?)",
                        (session_id, s.set_number, s.weight, s.reps, s. distance, s.duration)
                    )

            conn.commit()
            return "Training log saved successfully!"
        except Exception as e:
            conn.rollback()
            return f"Database error occurred: {str(e)}"


def get_training_sessions_of_last_n_days(n: int, db_path):
    """Get a list of training sessions of the last n days"""
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT *
            FROM training_sessions t, practices p, training_sets s
            WHERE t.practice_id = p.id AND t.id = s.training_session_id
            AND date(t.created_at) >= date('now', '-{n} days')
            """
        )
        return cursor.fetchall()

def update_training_session():
    """Update the TrainingSessionInfo Pydantic model
    using the update_clause generated by the LLM.
    return the ID of the updated row.
    """

    pass

def add_meal_record(meal: MealInfo, db_path: str) -> int:
    """Add the MealInfo pydantic model
    return the ID of the newly inserted row.
    """
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO meal_records (
                date, meal_type, items, note
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                meal.date.isoformat(),
                meal.meal_type,
                meal.items,
                meal.note
            )
        )
        conn.commit()
        return cursor.lastrowid
