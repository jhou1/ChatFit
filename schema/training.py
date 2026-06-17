import datetime
from pydantic import BaseModel, Field

class Training(BaseModel):
    date: datetime.date = Field(description="Date of your training")
    name: str = Field(description="Name of your practice")
    log: str = Field(description="The training log, be descriptive, record your warm up, weight, distance, duration, reps, sets, cool down, rest duration, RPE, gear, etc. Your future self will thank you.")
