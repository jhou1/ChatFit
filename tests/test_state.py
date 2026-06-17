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

def test_should_create_agent_state():
    agent_state = AgentState(
        messages=["hello", "world"], 
        user_profile=UserProfile(
            username="hello user",
            diet_preference=["chocolate"],
            training_preference=["running"]

    ))
    assert "hello" in agent_state.messages
    assert agent_state.user_profile.username == "hello user"

def test_should_not_create_invalid_user_profile():
    with pytest.raises(ValueError) as err:
        AgentState(
            messages="hello", 
            user_profile=UserProfile(
                username="hello user",
                diet_preference=["chocolate"],
                training_preference=["running"]

        ))
    assert "list" in str(err)
