from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List, Dict

class UserBase(BaseModel):
    username: str
    email: str
    role: str

class ExamBase(BaseModel):
    title: str
    is_active: bool

class ExamSectionBase(BaseModel):
    section_type: str
    duration: int
    total_marks: float
    order_number: int

class QuestionBase(BaseModel):
    question_type: str
    question_text: str
    correct_answer: str
    marks: int
    media_url: Optional[str] = None
    additional_data: Optional[Dict] = None

class QuestionOptionBase(BaseModel):
    option_text: str
    is_correct: bool

class WritingTaskBase(BaseModel):
    task_type: str
    instructions: str
    image_url: Optional[str] = None
    essay_prompt: str
    word_limit: int
    total_marks: float

class ListeningMediaBase(BaseModel):
    audio_url: str
    transcript: str
    duration: int

class ReadingPassageBase(BaseModel):
    content: str
    title: str
    word_count: int