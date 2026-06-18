import datetime
from typing import Optional
from pydantic import BaseModel, Field

class TrainingSession(BaseModel):
    """ Schema for extracting training information from user input """

    date: datetime.date = Field(description="Date of your training")
    practice_name: str = Field(description="Name of your practice")
    warm_up: Optional[str] = Field(None, description="Warm up activities")
    cool_down: Optional[str] = Field(None, description="Cool down activities")
    reps: Optional[int] = Field(None, description="Number of reps per set")
    sets: Optional[int] = Field(None, description="Number of sets")
    distance: Optional[float] = Field(None, description="Distance(km) accomplished")
    duration: Optional[float] = Field(None, description="Duration(min) of the training")
    rpe: Optional[int] = Field(None, description="Rate of perceived exertion (1-10)") 
    note: str = Field(description="The user's input as a whole, aka the training note. Let it be descriptive, record your warm up, weight, distance, duration, reps, sets, cool down, rest duration, RPE, gear, etc. Your future self will thank you.")
