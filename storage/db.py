import sqlite3
from schema.training import TrainingLog

def init_db(db_path):
    """Initialize the database tables."""

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        # Create training log and diet log tables
        cursor.execute(
            """
            CREATE TABLE training_log (
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
            CREATE TABLE diet_log (
               date TEXT NOT NULL,
               note TEXT NOT NULL,
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.commit()

def add_training_log(log: TrainingLog, db_path: str) -> int:
    """Save the TrainingLog Pydantic model
    return the ID of the newly inserted row.
    """

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO training_log (
                date, practice_name, warm_up, cool_down, reps, sets, distance, duration, rpe, note
            ) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                log.date.isoformat(),  # Convert datetime.date to string for SQLite
                log.practice_name,
                log.warm_up,
                log.cool_down,
                log.reps,
                log.sets,
                log.distance,
                log.duraiton, # Assuming you haven't fixed the typo yet! Change to .duration if you do.
                log.rpe,
                log.note
            )
        )
        conn.commit()
        return cursor.lastrowid
