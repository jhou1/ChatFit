import pytest
from pydantic import ValidationError
from agent.state import AgentState, UserProfile

def test_should_create_user_profile():
    user_profile = UserProfile(
        username="test user",
        diet_preference=["apple", "banana"],
        training_preference=["strength", "endurance", "swimming"]
    )
    assert user_profile.username == "test user"
    assert "apple" in user_profile.diet_preference
    assert len(user_profile.training_preference) == 3

def test_should_not_create_invalid_user_profile():
    with pytest.raises(ValidationError) as err:
        UserProfile(
            username="test user",
            interests=["reading"]
        )
    assert "interests" in str(err)
