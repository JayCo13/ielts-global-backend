class UserCreate(UserBase):
    password: str

class ExamCreate(ExamBase):
    created_by: int

class ExamSectionCreate(ExamSectionBase):
    exam_id: int

class QuestionCreate(QuestionBase):
    section_id: int

class QuestionOptionCreate(QuestionOptionBase):
    question_id: int

class WritingTaskCreate(WritingTaskBase):
    pass

class ListeningMediaCreate(ListeningMediaBase):
    section_id: int

class ReadingPassageCreate(ReadingPassageBase):
    section_id: int