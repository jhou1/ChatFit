from datetime import datetime
import pytest
from schema.training import TrainingSession
from schema.meal import MealRecord

def test_should_create_training_session_successfully():
    training = TrainingSession(
        date=datetime.now().date(),
        practice_name="kettbell snatch",
        note="24kg, 5 otm x 25, 125 reps, 3000 kg. This is the heaviest session this week."
    )
    assert training.date is not None
    assert training.practice_name == "kettbell snatch"
    assert "3000" in training.note

def test_should_failed_to_create_training_without_date():
    with pytest.raises(ValueError) as err:
        TrainingSession(
            practice_name="kettbell snatch",
            note="24kg, 5 otm x 25, 125 reps, 3000 kg. This is the heaviest session this week."
        )
    assert "type=missing" in str(err)

def test_should_create_meal_record_successfully():
    meal = MealRecord(
        date=datetime.now().date(),
        meal_type="breakfast",
        items="egg and milk",
        note="breakfast: egg, milk."
    )
    assert meal.date is not None
    assert "egg" in meal.note

    meal = MealRecord(
        date=datetime.now().date(),
        note="lunch: rice."
    )
    assert "rice" in meal.note
