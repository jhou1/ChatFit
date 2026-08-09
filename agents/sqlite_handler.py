from datetime import date
import sqlite3
from contextlib import closing
from typing import Any

from agents.models import TrainingInputRecorder, MealInfo


def init_db(db_path):
    """Initialize the database tables."""

    with closing(sqlite3.connect(db_path)) as conn, conn:
        cursor = conn.cursor()

        cursor.execute("PRAGMA foreign_keys = ON")

        cursor.execute("DROP TABLE IF EXISTS training_sessions")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS write_operations (
                operation_id TEXT PRIMARY KEY,
                tool_name TEXT NOT NULL,
                result TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS practices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                type TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                active INTEGER NOT NULL DEFAULT 1
            )
            """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS training_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                practice_id INTEGER NOT NULL REFERENCES practices(id),
                date TEXT NOT NULL,
                warm_up TEXT,
                cool_down TEXT,
                rpe INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                note TEXT
            )
            """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS training_sets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                training_session_id INTEGER NOT NULL REFERENCES training_sessions(id) ON DELETE CASCADE,
                set_number INTEGER NOT NULL,
                weight REAL,
                reps INTEGER,
                distance REAL,
                duration REAL
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS meal_records (
               date TEXT NOT NULL,
               meal_type TEXT,
               items TEXT,
               note TEXT NOT NULL,
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)

        conn.commit()


def add_training_session(input_data: TrainingInputRecorder, db_path: str) -> str:
    """Save the TrainingInputRecorder to the 3-table training session schema.
    Returns a success string or an error string (Tool Rejection) if practices are missing.
    """

    with closing(sqlite3.connect(db_path)) as conn, conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS write_operations (
                operation_id TEXT PRIMARY KEY,
                tool_name TEXT NOT NULL,
                result TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)

        incoming_practice_names = list(
            set([s.practice_name.lower() for s in input_data.sessions])
        )

        cursor.execute("SELECT lower(name) FROM practices")
        existing_practices = [
            row[0] for row in cursor.fetchall() if row[0] in incoming_practice_names
        ]
        new_practices = [
            p for p in incoming_practice_names if p not in existing_practices
        ]

        if new_practices and not input_data.confirm_new_practices:
            return f"""Error: The following practices are not in the database: {new_practices}.
                    Ask the user if they want to create them. Do not proceed until they say yes.
                    If they say yes, call this tool again with confirm_new_practices=True.
                    """

        try:
            result = "Training log saved successfully!"
            if input_data.operation_id:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO write_operations (operation_id, tool_name, result)
                    VALUES (?, ?, ?)
                    """,
                    (input_data.operation_id, "log_training_session", result),
                )
                if cursor.rowcount == 0:
                    cursor.execute(
                        "SELECT result FROM write_operations WHERE operation_id = ?",
                        (input_data.operation_id,),
                    )
                    return cursor.fetchone()[0]

            for session in input_data.sessions:
                cursor.execute(
                    "SELECT id FROM practices WHERE lower(name)= ?",
                    (session.practice_name.lower(),),
                )
                row = cursor.fetchone()
                if not row:
                    cursor.execute(
                        "INSERT INTO practices (name, type, active) VALUES (?, ?, 1)",
                        (session.practice_name, session.practice_type),
                    )
                    practice_id = cursor.lastrowid
                else:
                    practice_id = row[0]

                cursor.execute(
                    "INSERT INTO training_sessions (practice_id, date, note, rpe, warm_up, cool_down) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        practice_id,
                        input_data.date.isoformat(),
                        session.note,
                        session.rpe,
                        session.warm_up,
                        session.cool_down,
                    ),
                )
                session_id = cursor.lastrowid

                for s in session.sets:
                    cursor.execute(
                        "INSERT INTO training_sets (training_session_id, set_number, weight, reps, distance, duration) VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            session_id,
                            s.set_number,
                            s.weight,
                            s.reps,
                            s.distance,
                            s.duration,
                        ),
                    )

            conn.commit()
            return result
        except Exception as e:
            conn.rollback()
            return f"Database error occurred: {str(e)}"


def get_training_sessions_of_last_n_days(n: int, db_path):
    """Get a list of training sessions of the last n days"""
    with closing(sqlite3.connect(db_path)) as conn, conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT *
            FROM training_sessions t, practices p, training_sets s
            WHERE t.practice_id = p.id AND t.id = s.training_session_id
            AND date(t.date) >= date('now', ?)
            ORDER BY t.date DESC
            """,
            (f"-{n} days",),
        )
        return cursor.fetchall()


def update_training_session():
    """Update the TrainingSessionInfo Pydantic model
    using the update_clause generated by the LLM.
    return the ID of the updated row.
    """

    pass


def add_meal_log(meal: MealInfo, db_path: str) -> int:
    """Add the MealInfo pydantic model
    return the ID of the newly inserted row.
    """
    with closing(sqlite3.connect(db_path)) as conn, conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO meal_records (
                date, meal_type, items, note
            )
            VALUES (?, ?, ?, ?)
            """,
            (meal.date.isoformat(), meal.meal_type, meal.items, meal.note),
        )
        conn.commit()
        return cursor.lastrowid or 0


def get_aggregated_training_data(n: int, db_path: str):
    """Aggregate training volume, sets, and RPE grouped by date and practice type."""
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT 
                date(t.date) as training_date,
                p.type as practice_type,
                SUM(CASE WHEN p.type = 'weighted' THEN s.weight * s.reps ELSE 0 END) as total_weight_volume,
                SUM(CASE WHEN p.type = 'bodyweight' THEN s.reps ELSE 0 END) as total_reps,
                SUM(s.distance) as total_distance,
                SUM(s.duration) as total_duration,
                COUNT(s.id) as total_sets,
                AVG(t.rpe) as avg_rpe
            FROM training_sessions t
            JOIN practices p ON t.practice_id = p.id
            JOIN training_sets s ON t.id = s.training_session_id
            WHERE date(t.date) >= date('now', ?)
            GROUP BY date(t.date), p.type
            ORDER BY date(t.date) ASC
            """,
            (f"-{n} days",),
        )
        return [dict(row) for row in cursor.fetchall()]


def get_training_records_for_date(
    target_date: date, db_path: str
) -> list[dict[str, Any]]:
    """Return one recap row per training session on a calendar date."""
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT date(t.date) AS training_date,
                   p.name AS practice_name,
                   t.rpe,
                   t.note,
                   COUNT(s.id) AS total_sets
            FROM training_sessions AS t
            JOIN practices AS p ON p.id = t.practice_id
            LEFT JOIN training_sets AS s ON s.training_session_id = t.id
            WHERE date(t.date) = date(?)
            GROUP BY t.id, date(t.date), p.name, t.rpe, t.note
            ORDER BY t.id ASC
            """,
            (target_date.isoformat(),),
        ).fetchall()
        return [dict(row) for row in rows]


def get_aggregated_training_between(
    start_date: date, end_date: date, db_path: str
) -> list[dict[str, Any]]:
    """Aggregate training data in an inclusive calendar-date range."""
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                date(t.date) AS training_date,
                p.type AS practice_type,
                SUM(CASE WHEN p.type = 'weighted' THEN s.weight * s.reps ELSE 0 END) AS total_weight_volume,
                SUM(CASE WHEN p.type = 'bodyweight' THEN s.reps ELSE 0 END) AS total_reps,
                SUM(s.distance) AS total_distance,
                SUM(s.duration) AS total_duration,
                COUNT(s.id) AS total_sets,
                AVG(t.rpe) AS avg_rpe
            FROM training_sessions AS t
            JOIN practices AS p ON t.practice_id = p.id
            JOIN training_sets AS s ON t.id = s.training_session_id
            WHERE date(t.date) BETWEEN date(?) AND date(?)
            GROUP BY date(t.date), p.type
            ORDER BY date(t.date) ASC
            """,
            (start_date.isoformat(), end_date.isoformat()),
        ).fetchall()
        return [dict(row) for row in rows]


def get_meal_records_of_last_n_days(n: int, db_path: str):
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM meal_records 
            WHERE date(date) >= date('now', ?)
            ORDER BY date ASC
            LIMIT 50
            """,
            (f"-{n} days",),
        )
        return [dict(row) for row in cursor.fetchall()]


def get_meal_records_between(
    start_date: date, end_date: date, db_path: str
) -> list[dict[str, Any]]:
    """Return meals recorded in the inclusive calendar-date range."""
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT * FROM meal_records
            WHERE date(date) BETWEEN date(?) AND date(?)
            ORDER BY date ASC
            """,
            (start_date.isoformat(), end_date.isoformat()),
        ).fetchall()
        return [dict(row) for row in rows]


def get_meal_records_for_date(target_date: date, db_path: str) -> list[dict[str, Any]]:
    """Return meals recorded on one calendar date."""
    return get_meal_records_between(target_date, target_date, db_path)
