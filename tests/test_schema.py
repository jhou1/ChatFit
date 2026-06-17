from datetime import datetime
import pytest
from schema.training import TrainingLog
from schema.diet import Diet

def test_should_create_traininglog_successfully():
    training = TrainingLog(
        date=datetime.now().date(),
        practice_name="kettbell snatch",
        note="24kg, 5 otm x 25, 125 reps, 3000 kg. This is the heaviest session this week."
    )
    assert training.date is not None
    assert training.practice_name == "kettbell snatch"
    assert "3000" in training.note

def test_should_failed_to_create_training_without_date():
    with pytest.raises(ValueError) as err:
        TrainingLog(
            practice_name="kettbell snatch",
            note="24kg, 5 otm x 25, 125 reps, 3000 kg. This is the heaviest session this week."
        )
    assert "type=missing" in str(err)

def test_should_create_diet_successfully():
    diet = Diet(
        date=datetime.now().date(),
        log="breakfast: egg, milk. lunch: rice, fish. dinner: beef, vegetables."
    )
    assert diet.date is not None
    assert "lunch" in diet.log
