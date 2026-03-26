class UserResponse(UserBase):
    user_id: int
    created_at: datetime

    class Config:
        orm_mode = True

class ExamResponse(ExamBase):
    exam_id: int
    created_at: datetime

    class Config:
        orm_mode = True

class ExamSectionResponse(ExamSectionBase):
    section_id: int
    exam_id: int

    class Config:
        orm_mode = True

class QuestionResponse(QuestionBase):
    question_id: int
    section_id: int

    class Config:
        orm_mode = True

class QuestionOptionResponse(QuestionOptionBase):
    option_id: int
    question_id: int

    class Config:
        orm_mode = True

class WritingTaskResponse(WritingTaskBase):
    task_id: int

    class Config:
        orm_mode = True

class ListeningMediaResponse(ListeningMediaBase):
    media_id: int
    section_id: int

    class Config:
        orm_mode = True

class ReadingPassageResponse(ReadingPassageBase):
    passage_id: int
    section_id: int

    class Config:
        orm_mode = True
