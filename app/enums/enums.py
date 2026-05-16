from enum import Enum
class SectionNameEnum(str, Enum):
    PART1 = 'Part 1'
    PART2 = 'Part 2'
    PART3 = 'Part 3'
    PART4 = 'Part 4'

class ContentTypeEnum(str, Enum):
    AUDIO = "audio"
    QUESTION = "question" 
    TOPIC = "topic"
    SCRIPT = "script"
    IMAGE = "image"

class QuestionTypeEnum(str, Enum):
    MULTIPLE_CHOICE = "multiple_choice"
    TRUE_FALSE = "true_false"
    SHORT_ANSWER = "short_answer"
    ESSAY = "essay"

class NotificationTypeEnum(str, Enum):
    UPDATE = "update"
    ANNOUNCEMENT = "announcement"
    MAINTENANCE = "maintenance"

class KeyTypeEnum(str, Enum):
    READING = "reading"
    LISTENING = "listening"
    SPEAKING = "speaking"
    WRITING = "writing"

class Task1QuestionTypeEnum(str, Enum):
    PIE = "pie"
    MAP = "map"
    PROCESS = "process"
    TABLE = "table"
    LINE = "line"
    BAR = "bar"
    MIXED = "mixed"

TASK1_QUESTION_TYPE_ORDER = ["pie", "map", "process", "table", "line", "bar", "mixed"]
