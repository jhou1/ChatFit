import sqlite3
from schema.meal import MealRecord
from schema.training import TrainingSession

def init_db(db_path):
    """Initialize the database tables."""

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE training_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                practice_name TEXT NOT NULL,
                warm_up TEXT,
                cool_down TEXT,
                reps INTEGER,
                sets INTEGER,
                distance REAL,
                duration REAL,
                rpe INTEGER,
                note TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE meal_records (
               date TEXT NOT NULL,
               meal_type TEXT,
               items TEXT,
               note TEXT NOT NULL,
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.commit()

def add_training_session(session: TrainingSession, db_path: str) -> int:
    """Save the TrainingSession Pydantic model
    return the ID of the newly inserted row.
    """

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO training_sessions (
                date, practice_name, warm_up, cool_down, reps, sets, distance, duration, rpe, note
            ) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session.date.isoformat(),
                session.practice_name,
                session.warm_up,
                session.cool_down,
                session.reps,
                session.sets,
                session.distance,
                session.duration,
                session.rpe,
                session.note
            )
        )
        conn.commit()
        return cursor.lastrowid

def add_meal_record(meal: MealRecord, db_path: str) -> int:
    """Add the MealRecord pydantic model
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
