from datetime import datetime
import pytest
from agents.models import TrainingInputRecorder, TrainingSession, TrainingSet, MealInfo


def test_should_create_training_session_successfully():
    input_data = TrainingInputRecorder(
        date=datetime.now().date(),
        sessions=[
            TrainingSession(
                practice_name="kettbell snatch",
                practice_type="weighted",
                sets=[TrainingSet(set_number=1, reps=125, weight=24)],
                note="24kg, 5 otm x 25, 125 reps, 3000 kg. This is the heaviest session this week.",
            )
        ],
    )
    assert input_data.date is not None
    assert input_data.sessions[0].practice_name == "kettbell snatch"
    assert "3000" in input_data.sessions[0].note


def test_should_failed_to_create_training_without_date():
    with pytest.raises(ValueError) as err:
        TrainingInputRecorder(
            sessions=[
                TrainingSession(
                    practice_name="kettbell snatch",
                    practice_type="weighted",
                    sets=[],
                    note="24kg, 5 otm x 25, 125 reps, 3000 kg. This is the heaviest session this week.",
                )
            ]
        )
    assert "type=missing" in str(err)


def test_should_create_meal_record_successfully():
    meal = MealInfo(
        date=datetime.now().date(),
        meal_type="breakfast",
        items="egg and milk",
        note="breakfast: egg, milk.",
    )
    assert meal.date is not None
    assert "egg" in meal.note

    meal = MealInfo(date=datetime.now().date(), note="lunch: rice.")
    assert "rice" in meal.note
