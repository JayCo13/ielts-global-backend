class ExamResultBase(BaseModel):
    user_id: int
    exam_id: int
    total_score: float
    completion_date: datetime
    section_scores: Dict[str, float]

class ExamResultCreate(ExamResultBase):
    pass

class ExamResultResponse(ExamResultBase):
    result_id: int

    class Config:
        orm_mode = True
