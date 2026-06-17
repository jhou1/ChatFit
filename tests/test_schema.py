from datetime import datetime
import pytest
from schema.training import Training
from schema.diet import Diet

def test_should_create_training_successfully():
    training = Training(
        date=datetime.now().date(),
        name="kettbell snatch",
        log="24kg, 5 otm x 25, 125 reps, 3000 kg. This is the heaviest session this week."
    )
    assert training.date is not None
    assert training.name == "kettbell snatch"
    assert "3000" in training.log

def test_should_failed_to_create_training_without_date():
    with pytest.raises(ValueError) as err:
        Training(
            name="kettbell snatch",
            log="24kg, 5 otm x 25, 125 reps, 3000 kg. This is the heaviest session this week."
        )
    assert "type=missing" in str(err)

def test_should_create_diet_successfully():
    diet = Diet(
        date=datetime.now().date(),
        log="breakfast: egg, milk. lunch: rice, fish. dinner: beef, vegetables."
    )
    assert diet.date is not None
    assert "lunch" in diet.log
