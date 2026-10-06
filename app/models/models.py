from sqlalchemy import Column, Integer, String, Float, DateTime, Date, Enum, JSON, ForeignKey, Boolean, Text, BigInteger, UniqueConstraint
from sqlalchemy.dialects.mysql import LONGBLOB, LONGTEXT
from sqlalchemy.orm import relationship, deferred
from app.database import Base
from datetime import datetime
from app.utils.datetime_utils import get_vietnam_time
from enum import Enum as PyEnum
class User(Base):
    __tablename__ = 'users'
    
    user_id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True)
    password = Column(Text)
    email = Column(String(100), unique=True, index=True)
    is_active = Column(Boolean, default=True)
    is_active_student = Column(Boolean, default=False)
    role = Column(Enum('admin', 'student', 'customer', 'center', 'teacher', name='role_types'))
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))
    image_url = Column(String(255), nullable=True)
    status = Column(Enum('online', 'offline', name='user_status'), default='offline')
    google_id = Column(String(255), nullable=True)
    last_active = Column(DateTime, nullable=True)
    is_vip = Column(Boolean, default=False)
    vip_expiry = Column(DateTime, nullable=True)
    account_activated_at = Column(DateTime, nullable=True)
    exam_results = relationship("ExamResult", back_populates="user")
    user_sessions = relationship("UserSession", back_populates="user")
    email_verified = Column(Boolean, default=False)  # verified via OTP after signup
    course_days = Column(Integer, nullable=True)  # per-student course window length; null = default 90
    # Dictation ("Chép chính tả") was students-only, which locked out centre members —
    # they are created with role='customer'. Admin can grant it per account instead.
    can_dictation = Column(Boolean, default=False, nullable=False, server_default='0')
    # Top Performer badge / Hall of Fame — recomputed daily by app.jobs.top_performers
    # over eligible (50–60 min) full-test attempts, all-time. Counts are PER SKILL
    # (badge tier is per-skill: 1 Top / 3 Elite / 10 Master / 25 Legend). The combined
    # top10_count = read+listen+write (total distinct Top-10 finishes); is_top_performer
    # = top10_count > 0. Hall of Fame is ranked per skill on the {skill}_top10_count /
    # {skill}_hof_attempts / {skill}_hof_score triples.
    is_top_performer = Column(Boolean, default=False, nullable=False, server_default='0')
    top10_count = Column(Integer, default=0, nullable=False, server_default='0')
    hof_attempts = Column(Integer, default=0, nullable=False, server_default='0')
    hof_score = Column(Integer, default=0, nullable=False, server_default='0')
    read_top10_count = Column(Integer, default=0, nullable=False, server_default='0')
    read_hof_attempts = Column(Integer, default=0, nullable=False, server_default='0')
    read_hof_score = Column(Integer, default=0, nullable=False, server_default='0')
    listen_top10_count = Column(Integer, default=0, nullable=False, server_default='0')
    listen_hof_attempts = Column(Integer, default=0, nullable=False, server_default='0')
    listen_hof_score = Column(Integer, default=0, nullable=False, server_default='0')
    write_top10_count = Column(Integer, default=0, nullable=False, server_default='0')
    write_hof_attempts = Column(Integer, default=0, nullable=False, server_default='0')
    write_hof_score = Column(Integer, default=0, nullable=False, server_default='0')
    # Affiliate / referral program (customers). referral_code is this user's own
    # share code; referred_by points at the affiliate who referred THIS user
    # (permanent). affiliate_balance is the commission wallet in "xu" (1 xu = 1 VND).
    referral_code = Column(String(16), unique=True, nullable=True, index=True)
    referred_by = Column(Integer, ForeignKey('users.user_id'), nullable=True)
    affiliate_balance = Column(BigInteger, default=0, nullable=False)
    # Number of visits landing on the site via this user's referral link (?ref=code).
    referral_clicks = Column(BigInteger, default=0, nullable=False)
    # Saved payout method for affiliate withdrawals (set once on the Payment page):
    # a QR image to scan and/or bank details. Snapshotted into each withdrawal.
    payout_qr_url = Column(String(255), nullable=True)
    payout_bank = Column(String(255), nullable=True)
    payout_account_number = Column(String(64), nullable=True)
    payout_account_holder = Column(String(255), nullable=True)

class UserSession(Base):
    __tablename__ = 'user_sessions'
    
    session_id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.user_id'))
    device_id = Column(String(255), nullable=False)  # Device fingerprint
    device_info = Column(JSON, nullable=True)  # Browser, OS, etc.
    ip_address = Column(String(45), nullable=True)  # IPv4 or IPv6
    login_time = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))
    logout_time = Column(DateTime, nullable=True)
    last_activity = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))
    is_active = Column(Boolean, default=True)
    session_token = Column(String(255), nullable=True)  # JWT token reference
    unique_session_id = Column(String(255), nullable=True, unique=True)  # Unique session identifier
    
    user = relationship("User", back_populates="user_sessions")

class DeviceViolation(Base):
    __tablename__ = 'device_violations'
    
    violation_id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.user_id'))
    device_id = Column(String(255), nullable=False)
    violation_type = Column(Enum('account_sharing', 'multiple_sessions', name='violation_types'))
    violation_count = Column(Integer, default=1)
    first_violation = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))
    last_violation = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))
    is_device_banned = Column(Boolean, default=False)
    ban_until = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))
    
    user = relationship("User")

class LoginCooldown(Base):
    __tablename__ = 'login_cooldowns'
    
    cooldown_id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.user_id'))
    device_id = Column(String(255), nullable=False)
    cooldown_start = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))
    cooldown_end = Column(DateTime, nullable=False)
    reason = Column(String(255), default='account_sharing_detected')
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))
    
    user = relationship("User")

class UserNotification(Base):
    __tablename__ = 'user_notifications'
    
    notification_id = Column(Integer, primary_key=True, index=True)
    image_url = Column(String(255), nullable=True)
    content = Column(Text, nullable=False)
    type = Column(String(50), nullable=False)  # Notification type
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))
    is_active = Column(Boolean, default=True)

class UpdateKey(Base):
    __tablename__ = 'update_keys'
    
    key_id = Column(Integer, primary_key=True, index=True)
    key = Column(String(255), nullable=False, unique=True)
    type = Column(String(50), nullable=False)  # Key type
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))
    is_active = Column(Boolean, default=True)


class VIPPackage(Base):
    __tablename__ = 'vip_packages'
    
    package_id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    duration_months = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    package_type = Column(String(200))
    skill_type = Column(String(200), nullable=True)
    ls_variant_id = Column(String(50), nullable=True)  # Lemon Squeezy variant ID
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))

class VIPSubscription(Base):
    __tablename__ = 'vip_subscriptions'
    
    subscription_id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.user_id'))
    package_id = Column(Integer, ForeignKey('vip_packages.package_id'))
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)
    payment_status = Column(Enum('pending', 'completed', 'reject', 'expired', name='payment_status_types'))
    ls_subscription_id = Column(String(100), nullable=True, index=True)  # Lemon Squeezy subscription ID
    ls_customer_id = Column(String(100), nullable=True)  # Lemon Squeezy customer ID
    is_auto_renew = Column(Boolean, default=True)  # Whether auto-renewal is active
    cancelled_at = Column(DateTime, nullable=True)  # When user cancelled (grace period)
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))

    user = relationship("User")
    package = relationship("VIPPackage")

class Feedback(Base):
    __tablename__ = 'feedback'
    
    feedback_id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=True)  # Add this line
    image_url = Column(Text, nullable=True)
    content = Column(Text)
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))
    user = relationship("User")

class PackageTransaction(Base):
    __tablename__ = 'package_transactions'
    
    transaction_id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.user_id'))
    package_id = Column(Integer, ForeignKey('vip_packages.package_id'))
    subscription_id = Column(Integer, ForeignKey('vip_subscriptions.subscription_id'))
    amount = Column(Float, nullable=False)
    payment_method = Column(String(50))
    transaction_code= Column(String(500), nullable=True)
    bank_description= Column(Text, nullable=True)
    bank_transfer_image = Column(String(255), nullable=True)
    status = Column(Enum('pending', 'completed', 'reject', name='transaction_status_types'))
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))
    admin_note = Column(Text, nullable=True)
    ls_order_id = Column(String(100), nullable=True, index=True)  # Lemon Squeezy order ID
    payos_order_code = Column(BigInteger, nullable=True, unique=True, index=True)  # PayOS order code
    payos_checkout_url = Column(Text, nullable=True)  # PayOS hosted checkout URL
    user = relationship("User")
    package = relationship("VIPPackage")
    subscription = relationship("VIPSubscription")
    
class ExamAccessType(Base):
    __tablename__ = 'exam_access_types'
    
    exam_id = Column(Integer, ForeignKey('exams.exam_id'), primary_key=True)
    access_type = Column(Enum('no vip', 'vip', 'student', name='access_types'), primary_key=True)
    
    exam = relationship("Exam", back_populates="access_types")
class ExamResult(Base):
    __tablename__ = 'exam_results'
    
    result_id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.user_id'))
    exam_id = Column(Integer, ForeignKey('exams.exam_id'))
    total_score = Column(Float)
    completion_date = Column(DateTime)
    section_scores = Column(JSON)
    attempt_number = Column(Integer, nullable=False, default=1)
    is_forecast = Column(Boolean, default=False)  # True if this is a forecast (single part) result
    forecast_part = Column(Integer, nullable=True)  # Which part (1-4 for listening, 1-3 for reading) if forecast
    user = relationship("User", back_populates="exam_results")
    exam = relationship("Exam", back_populates="exam_results")
    answers = relationship("StudentAnswer", back_populates="exam_result")
    mode = Column(String(20), nullable=True)  # 'practice' (Luyện tập) | 'exam' (Thi thử); null for older attempts
    time_taken = Column(Integer, nullable=True)  # elapsed seconds spent on the attempt; null for older attempts
    tab_switches = Column(Integer, nullable=True)  # times the student left the exam tab; null for older attempts

class StudentAnswer(Base):
    __tablename__ = 'student_answers'
    
    answer_id = Column(Integer, primary_key=True, index=True)
    result_id = Column(Integer, ForeignKey('exam_results.result_id'))
    question_id = Column(Integer, ForeignKey('questions.question_id'))
    student_answer = Column(Text)
    score = Column(Float)
    
    exam_result = relationship("ExamResult", back_populates="answers")
    question = relationship("Question", back_populates="student_answers")

class ListeningAnswer(Base):
    __tablename__ = 'listening_answers'
    
    answer_id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.user_id'))
    exam_id = Column(Integer, ForeignKey('exams.exam_id'))
    result_id = Column(Integer, ForeignKey('exam_results.result_id'))
    question_id = Column(Integer, ForeignKey('questions.question_id'))
    student_answer = Column(Text)
    score = Column(Float)
    created_at = Column(DateTime)
    
    user = relationship("User")
    exam = relationship("Exam")
    exam_result = relationship("ExamResult")
    question = relationship("Question", back_populates="listening_answers")

class Exam(Base):
    __tablename__ = 'exams'
    
    exam_id = Column(Integer, primary_key=True, index=True)
    title = Column(String(100))
    created_at = Column(DateTime)
    description = Column(LONGTEXT, nullable=True)
    is_active = Column(Boolean, default=True)
    created_by = Column(Integer, ForeignKey('users.user_id'))
    access_types = relationship("ExamAccessType", back_populates="exam")
    exam_results = relationship("ExamResult", back_populates="exam")
    exam_sections = relationship("ExamSection", back_populates="exam")
   
class ExamSection(Base):
    __tablename__ = 'exam_sections'
    
    section_id = Column(Integer, primary_key=True, index=True)
    exam_id = Column(Integer, ForeignKey('exams.exam_id'))
    section_type = Column(String(255))
    duration = Column(Integer)
    total_marks = Column(Float)
    order_number = Column(Integer)
    description = Column(LONGTEXT, nullable=True)
    part_title = Column(LONGTEXT, nullable=True)  # Short display title for each part
    is_forecast = Column(Boolean, default=False)
    forecast_title = Column(String(200), nullable=True)
    is_recommended = Column(Boolean, default=False)  # Starred/recommended forecast
    question_types = Column(JSON, nullable=True)  # e.g. ["Multiple Choice", "Matching Headings"]
    exam = relationship("Exam", back_populates="exam_sections")
    questions = relationship("Question", back_populates="section")
    question_type_tags = Column(JSON, nullable=True)  # Admin-set tags for filtering, e.g. ["true_false_ng", "fill_blank"]
    # Difficulty classification (recomputed daily; see app/jobs/recompute_difficulty.py).
    # difficulty_score = average % correct over valid attempts (0-100). difficulty_label
    # = 'easy'|'medium'|'hard'|'very_hard', set only once the part is classified.
    difficulty_score = Column(Float, nullable=True)
    difficulty_label = Column(String(20), nullable=True)
    difficulty_valid_count = Column(Integer, nullable=True)
    difficulty_updated_at = Column(DateTime, nullable=True)
    # Auto-forecast (occurrence-based). occurrence_count ≥1 → part shows in forecast;
    # forecast_level 1-4 (1=Thấp … 4=Siêu trúng tủ) recomputed by percentile.
    occurrence_count = Column(Integer, nullable=False, default=0, server_default='0')
    forecast_level = Column(Integer, nullable=True)
    forecast_last_updated = Column(DateTime, nullable=True)
    skip_first_decay = Column(Boolean, nullable=False, default=False, server_default='0')

class QuestionGroup(Base):
    __tablename__ = 'question_groups'
    
    group_id = Column(Integer, primary_key=True, index=True)
    section_id = Column(Integer, ForeignKey('exam_sections.section_id'))
    instruction = Column(Text)
    question_range = Column(String(50))  # e.g., "1-6", "7-13"
    group_type = Column(String(50))  # e.g., "true_false_ng", "fill_blank"
    order_number = Column(Integer)
    
    # Add relationships
    section = relationship("ExamSection", backref="question_groups")
    questions = relationship("Question", back_populates="group")

class Question(Base):
    # Add these lines to your existing Question model
    group_id = Column(Integer, ForeignKey('question_groups.group_id'), nullable=True)
    question_number = Column(Integer)  # The question number within the test
    group = relationship("QuestionGroup", back_populates="questions")
    __tablename__ = 'questions'
    
    question_id = Column(Integer, primary_key=True, index=True)
    section_id = Column(Integer, ForeignKey('exam_sections.section_id'))
    group_id = Column(Integer, ForeignKey('question_groups.group_id'), nullable=True)
    question_type = Column(Text)
    question_text = Column(LONGTEXT)
    explanation = Column(LONGTEXT, nullable=True)
    locate = Column(LONGTEXT, nullable=True)
    correct_answer = Column(Text)
    marks = Column(Integer, default=1)
    media_url = Column(String(255))
    additional_data = Column(JSON)
    question_number = Column(Integer)  # Add this to track the question number within the test

    section = relationship("ExamSection", back_populates="questions")
    group = relationship("QuestionGroup", back_populates="questions")
    options = relationship("QuestionOption", back_populates="question")
    student_answers = relationship("StudentAnswer", back_populates="question")
    listening_answers = relationship("ListeningAnswer", back_populates="question")  # Add this line
    stats_category = Column(String(50), nullable=True)  # IELTS category for stats/difficulty (admin "gán dạng câu hỏi")
    
class QuestionOption(Base):
    __tablename__ = 'question_options'
    
    option_id = Column(Integer, primary_key=True, index=True)
    question_id = Column(Integer, ForeignKey('questions.question_id'))
    option_text = Column(Text)
    is_correct = Column(Boolean)

    question = relationship("Question", back_populates="options")


  



class WritingTask(Base):
    __tablename__ = 'writing_tasks'
    
    task_id = Column(Integer, primary_key=True, index=True)
    test_id = Column(Integer, ForeignKey('exams.exam_id'))
    part_number = Column(Integer)
    task_type = Column(Enum('essay', 'report', 'letter', name='task_types'))
    task1_type = Column(
        Enum('pie', 'map', 'process', 'table', 'line', 'bar', 'mixed', name='task1_question_types'),
        nullable=True,
    )
    task2_type = Column(
        Enum(
            'agree_disagree',
            'positive_negative',
            'advantages_disadvantages',
            'discussion',
            'solutions_effects',
            'two_part_mixed',
            name='task2_question_types',
        ),
        nullable=True,
    )
    title = Column(String(200), nullable=True)
    instructions = Column(LONGTEXT)  # Keep using LONGTEXT instead of Text
    word_limit = Column(Integer)
    total_marks = Column(Float)
    duration = Column(Integer)
    is_forecast = Column(Boolean, default=False)
    is_recommended = Column(Boolean, default=False)  # Starred/recommended forecast
    sample_essay = Column(LONGTEXT, nullable=True)

    exam = relationship("Exam", backref="writing_tasks")
    student_answers = relationship("WritingAnswer", back_populates="task")
    question_type_tags = Column(JSON, nullable=True)
    # Difficulty classification (daily). difficulty_score = average Overall Band over
    # valid attempts (0-9); difficulty_label set once classified.
    difficulty_score = Column(Float, nullable=True)
    difficulty_label = Column(String(20), nullable=True)
    difficulty_valid_count = Column(Integer, nullable=True)
    difficulty_updated_at = Column(DateTime, nullable=True)
    # Auto-forecast (occurrence-based).
    occurrence_count = Column(Integer, nullable=False, default=0, server_default='0')
    forecast_level = Column(Integer, nullable=True)
    forecast_last_updated = Column(DateTime, nullable=True)
    skip_first_decay = Column(Boolean, nullable=False, default=False, server_default='0')

class WritingAnswer(Base):
    __tablename__ = 'writing_answers'
    
    answer_id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey('writing_tasks.task_id'))
    user_id = Column(Integer, ForeignKey('users.user_id'))
    answer_text = Column(LONGTEXT, nullable=False)
    score = Column(Float, nullable=True)
    is_ai_evaluated = Column(Boolean, default=False)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)
    
    # Scores for each criterion
    task_achievement_score = Column(Float, nullable=True)
    coherence_cohesion_score = Column(Float, nullable=True)
    lexical_resource_score = Column(Float, nullable=True)
    grammatical_range_score = Column(Float, nullable=True)
    
    # New evaluation fields
    mistakes = Column(JSON, nullable=True)
    improvement_suggestions = Column(JSON, nullable=True)
    rewritten_essay = Column(LONGTEXT, nullable=True)

    task = relationship("WritingTask", back_populates="student_answers")
    user = relationship("User")
    # Persisted on-demand AI content (outline / sample essays / key language) so it
    # survives edits and page reloads ("lưu cứng"). Keyed by outline|sampleTarget|
    # sampleTop|keylang → the generator's full response object.
    ai_generated = Column(JSON, nullable=True)
    # Seconds the student spent writing this attempt (shown as "Thời gian đã làm").
    time_taken = Column(Integer, nullable=True)
    # Finalized: once the user ends review, the graded essay is locked into history —
    # no more AI grading/editing (to edit they must "Làm lại" → a new version).
    locked = Column(Boolean, nullable=False, default=False, server_default='0')


class ListeningMedia(Base):
    __tablename__ = "listening_media"

    media_id = Column(Integer, primary_key=True, index=True)
    section_id = Column(Integer, ForeignKey("exam_sections.section_id"))
    audio_file = Column(LONGBLOB, nullable=True)  # Legacy: kept for backward compat
    audio_filename = Column(String(255))
    audio_url = Column(String(500), nullable=True)  # R2 public URL
    transcript = Column(LONGTEXT)
    duration = Column(Integer)



class ReadingPassage(Base):
    __tablename__ = 'reading_passages'
    
    passage_id = Column(Integer, primary_key=True, index=True)
    section_id = Column(Integer, ForeignKey('exam_sections.section_id'))
    content = Column(Text)
    title = Column(String(100))
    word_count = Column(Integer)
class AdminNotificationRead(Base):
    __tablename__ = 'admin_notification_reads'
    
    read_id = Column(Integer, primary_key=True, index=True)
    admin_id = Column(Integer, ForeignKey('users.user_id'))
    notification_id = Column(String(255), nullable=False)  # Format: "type_id"
    notification_type = Column(String(50), nullable=False)  # e.g., "transaction", "writing", "speaking"
    item_id = Column(String(50), nullable=False)  # The actual ID of the item
    read_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))
    
    admin = relationship("User")


class SpeakingMaterial(Base):
    __tablename__ = 'speaking_materials'

    material_id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    part_type = Column(Enum('part1', 'part2_3', name='speaking_part_types'))
    pdf_url = Column(String(500), nullable=False)
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))
    
    access_types = relationship("SpeakingMaterialAccessType", back_populates="material", cascade="all, delete-orphan")


class SpeakingMaterialAccessType(Base):
    __tablename__ = 'speaking_material_access_types'
    
    material_id = Column(Integer, ForeignKey('speaking_materials.material_id', ondelete='CASCADE'), primary_key=True)
    access_type = Column(Enum('no vip', 'vip', 'student', name='access_types'), primary_key=True)
    
    material = relationship("SpeakingMaterial", back_populates="access_types")


class SavedVocabulary(Base):
    __tablename__ = 'saved_vocabulary'
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.user_id', ondelete='CASCADE'), nullable=False)
    word = Column(String(255), nullable=False)
    context = Column(Text, nullable=True)
    source_type = Column(Enum('listening', 'reading', 'writing', 'speaking', name='vocab_source_types'), nullable=False)
    source_exam_id = Column(Integer, nullable=True)
    source_exam_title = Column(String(255), nullable=True)
    is_important = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))
    
    user = relationship("User", backref="saved_vocabulary")


class DictationUnit(Base):
    __tablename__ = 'dictation_units'
    
    unit_id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))
    
    words = relationship("DictationWord", back_populates="unit", cascade="all, delete-orphan")


class DictationWord(Base):
    __tablename__ = 'dictation_words'
    
    word_id = Column(Integer, primary_key=True, index=True)
    unit_id = Column(Integer, ForeignKey('dictation_units.unit_id', ondelete='CASCADE'), nullable=False)
    word = Column(String(255), nullable=False)
    order_index = Column(Integer, default=0)
    is_important = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))
    
    unit = relationship("DictationUnit", back_populates="words")


class StudentImportantWord(Base):
    """Junction table to track which student marked which word as important."""
    __tablename__ = 'student_important_words'
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.user_id', ondelete='CASCADE'), nullable=False)
    word_id = Column(Integer, ForeignKey('dictation_words.word_id', ondelete='CASCADE'), nullable=False)
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))
    
    # Unique constraint: one user can only mark a word as important once
    __table_args__ = (
        {'mysql_charset': 'utf8mb4'},
    )


# ---------------------------------------------------------------------------
# Models ported from the Vietnam tree (ielts-main-nov), 2026-10. Schema comes
# from the VN alembic revisions merged at f1a0c0de2026.
# ---------------------------------------------------------------------------


class ResultAnswerSnapshot(Base):
    """Self-contained, gzipped snapshot of a result's reviewable answers (+ optional
    highlights/notes) captured at submit time. Lets "xem lại bài" keep working even
    after the exam's questions are later edited/replaced by admin — which hard-deletes
    the StudentAnswer/ListeningAnswer rows and used to leave review screens empty.
    Kept in a side table (not on exam_results) so history lists stay lean; loaded
    only when a result is opened for review."""
    __tablename__ = 'result_answer_snapshots'

    result_id = Column(Integer, ForeignKey('exam_results.result_id'), primary_key=True)
    answers_gz = Column(LONGBLOB, nullable=True)      # gzipped JSON: list of answer rows
    annotations_gz = Column(LONGBLOB, nullable=True)  # gzipped JSON: {highlights, notes, part_hashes}
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))


class StudentHistoryArchive(Base):
    """Snapshot of a student's exam/writing history captured when a center resets
    ('làm mới') the account so another student can reuse it. The live rows are
    deleted after archiving; this table keeps a gzipped JSON dump (exam results +
    per-question answers + writing) so nothing is permanently lost."""
    __tablename__ = 'student_history_archives'

    archive_id = Column(Integer, primary_key=True, index=True)
    center_id = Column(Integer, ForeignKey('centers.center_id'), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False, index=True)
    username = Column(String(50), nullable=True)      # login snapshot at reset time
    archived_by = Column(Integer, nullable=True)      # center-owner user_id who triggered it
    num_exams = Column(Integer, default=0)
    num_writing = Column(Integer, default=0)
    data_gz = Column(LONGBLOB, nullable=True)         # gzipped JSON dump of all archived rows
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))


class WritingAttempt(Base):
    """A snapshot of a past Writing attempt, so "Làm lại" keeps the previous result
    (a new version) instead of deleting it. The live answer stays in WritingAnswer;
    on retake we copy it here (with an incremented attempt_number) then reset the live
    one. Read-only history for the review screen + Lịch sử dropdown."""
    __tablename__ = 'writing_attempts'

    attempt_id = Column(Integer, primary_key=True, index=True)
    test_id = Column(Integer, index=True)                 # exam_id
    task_id = Column(Integer, ForeignKey('writing_tasks.task_id'), index=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), index=True)
    part_number = Column(Integer)
    attempt_number = Column(Integer)                       # per (test_id, user_id)
    answer_text = Column(LONGTEXT, nullable=True)
    score = Column(Float, nullable=True)
    task_achievement_score = Column(Float, nullable=True)
    coherence_cohesion_score = Column(Float, nullable=True)
    lexical_resource_score = Column(Float, nullable=True)
    grammatical_range_score = Column(Float, nullable=True)
    is_ai_evaluated = Column(Boolean, default=False)
    result = Column(JSON, nullable=True)                   # full grade blob
    ai_generated = Column(JSON, nullable=True)
    time_taken = Column(Integer, nullable=True)
    created_at = Column(DateTime)


class ListeningAlignment(Base):
    """Word-level timing for one listening part, from force-aligning its transcript to audio.

    Whisper transcribes what it HEARS; the authored transcript (exam_sections.description)
    is what was actually said, and it is the text that `Question.locate` and the answers
    are expressed in. Aligning the two word streams transfers Whisper's timings onto the
    authored words, so any position in the transcript can be turned into an audio offset.

    Measured on a 6-file pilot: sub-second where verifiable, and the timeline runs
    monotonically forward (<1.3% backward steps, none larger than 0.3s).

    data_gz holds gzipped JSON {"tokens": [...], "times": [float|null, ...]} — the two
    arrays are parallel, `null` meaning that word found no match in the audio.
    """
    __tablename__ = 'listening_alignments'

    section_id = Column(Integer, ForeignKey('exam_sections.section_id', ondelete='CASCADE'), primary_key=True)
    audio_duration = Column(Float, nullable=True)      # seconds, as reported by the model
    token_count = Column(Integer, nullable=True)       # words in the authored transcript
    aligned_count = Column(Integer, nullable=True)     # of those, how many got a timestamp
    coverage_pct = Column(Float, nullable=True)        # aligned_count / spoken span
    model = Column(String(64), nullable=True)
    # Fingerprint of what this alignment was built from (transcript text + audio byte
    # length). The nightly job re-aligns a part when this stops matching, so editing a
    # transcript or swapping the audio can't leave stale timestamps behind.
    source_fingerprint = Column(String(64), nullable=True)
    data_gz = Column(LONGBLOB, nullable=True)
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))


class ListeningCueOverride(Base):
    """A timestamp an admin set by hand for one listening question.

    Automatic cues come from matching the answer or the locate excerpt against the
    aligned transcript, which cannot work for multiple-choice questions — "B" is never
    spoken aloud. Those, and any case where the automatic guess landed wrong, are pinned
    here.

    An override always wins over the computed cue, and nothing in the nightly alignment
    job touches this table: re-aligning a part must never silently discard work someone
    did by ear.
    """
    __tablename__ = 'listening_cue_overrides'

    question_id = Column(Integer, ForeignKey('questions.question_id', ondelete='CASCADE'), primary_key=True)
    section_id = Column(Integer, ForeignKey('exam_sections.section_id', ondelete='CASCADE'), index=True)
    start_time = Column(Float, nullable=False)
    end_time = Column(Float, nullable=False)
    updated_by = Column(Integer, ForeignKey('users.user_id'), nullable=True)
    updated_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))


class SpeakingTopic(Base):
    """A Speaking topic: one Part 1 topic, or one Part 2 topic that Part 3 hangs off.

    Part 3 has no topic of its own — its questions belong to the Part 2 topic they were
    entered with, which is also how the exam pairs them ("We've been talking about
    <part 2 topic>..."). The forecast columns mirror reading/listening/writing so the
    existing ⭐ percentile formula can be reused, but the decay rule differs: a Speaking
    topic is live only while today falls inside its appear_from..appear_to window.
    """
    __tablename__ = 'speaking_topics'

    topic_id = Column(Integer, primary_key=True, index=True)
    part = Column(Enum('part1', 'part2', name='speaking_topic_parts'), nullable=False, index=True)
    title = Column(String(255), nullable=False)

    # Part 2 only — the six groups admins tick when adding a topic.
    category = Column(Enum('place', 'people', 'education', 'recreation', 'object', 'others',
                           name='speaking_topic_categories'), nullable=True)
    # Part 2 only — the full prompt plus cue card, kept verbatim as pasted.
    cue_card = Column(LONGTEXT, nullable=True)

    # Part 1 only. A student who says they are working must not be asked the Study topic
    # as their "important" topic, and vice versa.
    work_study = Column(Enum('work', 'study', 'neutral', name='speaking_work_study'),
                        default='neutral', nullable=False, server_default='neutral')
    is_important = Column(Boolean, default=False, nullable=False, server_default='0')

    # Forecast. occurrence_count is typed in by the admin; the nightly job only forces it
    # to 0 once the appearance window has passed.
    occurrence_count = Column(Integer, default=0, nullable=False, server_default='0')
    forecast_level = Column(Integer, nullable=True)          # 1..4 stars
    appear_from = Column(Date, nullable=True)                # entered as MM/YYYY
    appear_to = Column(Date, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False, server_default='1')

    # Độ khó — cùng thiết kế với Reading/Listening/Writing: `difficulty_score` là điểm
    # trung bình học viên đạt được ở chủ đề này (điểm càng cao thì chủ đề càng dễ), còn
    # `difficulty_label` do job xếp hạng phần trăm gán, chỉ khi đủ số lượt hợp lệ.
    difficulty_score = Column(Float, nullable=True)
    difficulty_label = Column(String(16), nullable=True)      # easy/medium/hard/very_hard
    difficulty_valid_count = Column(Integer, default=0, nullable=False, server_default='0')
    difficulty_updated_at = Column(DateTime, nullable=True)

    last_updated = Column(DateTime, nullable=True)
    created_by = Column(Integer, ForeignKey('users.user_id'), nullable=True)
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))

    questions = relationship("SpeakingQuestion", back_populates="topic",
                             cascade="all, delete-orphan")


class SpeakingQuestion(Base):
    """One spoken question. Part 2's own prompt lives on the topic's cue_card instead —
    what sits here for Part 2 is its follow-up question, asked right after the long turn.
    """
    __tablename__ = 'speaking_questions'

    question_id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey('speaking_topics.topic_id', ondelete='CASCADE'),
                      nullable=False, index=True)
    # 'part2' is the long turn itself — the cue card, copied here at save time. It gets a
    # row like every other question so generated content, and later the student's answer,
    # attach the same way instead of the long turn needing its own special case.
    part = Column(Enum('part1', 'part2', 'part2_followup', 'part3',
                       name='speaking_question_parts'),
                  nullable=False, index=True)
    order_index = Column(Integer, default=0, nullable=False)
    content = Column(LONGTEXT, nullable=False)

    # Generated content is produced by a background job, so each question carries its own
    # state and can be retried on its own.
    gen_status = Column(Enum('pending', 'running', 'done', 'failed',
                             name='speaking_gen_status'),
                        default='pending', nullable=False, server_default='pending')
    gen_error = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))

    topic = relationship("SpeakingTopic", back_populates="questions")


class SpeakingSuggestion(Base):
    """The "Gợi ý" for one question: an outline plus four band-level model answers.

    Stored as JSON rather than columns because the shape differs per part — Part 2 is
    four paragraphs mapped to four cue cards, Part 3 is exactly seven sentences.
    """
    __tablename__ = 'speaking_suggestions'

    question_id = Column(Integer, ForeignKey('speaking_questions.question_id', ondelete='CASCADE'),
                         primary_key=True)
    outline = Column(JSON, nullable=True)     # dàn bài
    samples = Column(JSON, nullable=True)     # {"4.5-5.5": "...", ...}
    model = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))


class SpeakingVocabulary(Base):
    """Topic vocabulary for one question — up to 6 items at each of 4 band levels."""
    __tablename__ = 'speaking_vocabularies'

    id = Column(Integer, primary_key=True, index=True)
    question_id = Column(Integer, ForeignKey('speaking_questions.question_id', ondelete='CASCADE'),
                         nullable=False, index=True)
    band_level = Column(Enum('4.5-5.5', '6.0-6.5', '7.0-7.5', '8.0-9.0',
                             name='speaking_band_levels'), nullable=False)
    order_index = Column(Integer, default=0, nullable=False)
    term = Column(String(255), nullable=False)
    meaning_vi = Column(String(500), nullable=True)
    example = Column(Text, nullable=True)


class SpeakingTts(Base):
    """Pre-generated examiner audio: one clip per line of speech, per voice.

    The browser never synthesises anything (docs/speaking-spec.md decision #3), so every
    question and every fixed examiner line is rendered here at authoring time.

    `cache_key` is a flat string — "question:12", "script:opening", "topic-intro:4" —
    rather than a nullable column per kind, so a new kind of clip needs no migration.
    `fingerprint` hashes the exact words plus the voice: edit a question's wording and its
    old clip stops matching and is regenerated, instead of students hearing the previous
    wording forever from a file that still exists.
    """
    __tablename__ = 'speaking_tts'

    id = Column(Integer, primary_key=True, index=True)
    cache_key = Column(String(64), nullable=False, index=True)
    voice = Column(String(32), nullable=False)
    fingerprint = Column(String(64), nullable=False)
    audio = Column(LONGBLOB, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))

    __table_args__ = (UniqueConstraint('cache_key', 'voice', name='uq_speaking_tts_key_voice'),)


class SpeakingAttempt(Base):
    """One sitting of a Speaking test (docs/speaking-spec.md §4).

    The assembled paper is frozen into `plan` at start time rather than re-derived on
    every request: the bank keeps changing underneath (admins edit wording, the decay job
    deactivates topics), and "Thi lại đúng bộ câu hỏi cũ" (§6.1) plus the marker's need to
    see exactly what was asked both depend on the paper not moving after the fact.

    Only a `completed` attempt that the student actually submitted may go to the AI
    (§4.4) — every other ending is kept as history and never scored.
    """
    __tablename__ = 'speaking_attempts'

    attempt_id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.user_id', ondelete='CASCADE'),
                     nullable=False, index=True)

    # ── Setup chosen before the test starts (§4.1–4.2) ──
    test_type = Column(Enum('full', 'part1', 'part2', 'part3', name='speaking_test_types'),
                       nullable=False)
    mode = Column(Enum('practice', 'mock', name='speaking_modes'), nullable=False)
    # Micro is always attempted first; a mic failure of any kind silently becomes
    # subtitle, which keeps the paper identical and only changes how answers arrive.
    input_method = Column(Enum('micro', 'subtitle', name='speaking_input_methods'),
                          default='micro', nullable=False, server_default='micro')
    occupation = Column(Enum('student', 'working', name='speaking_occupations'),
                        nullable=True)
    voice = Column(String(32), nullable=True)
    use_forecast = Column(Boolean, default=False, nullable=False, server_default='0')
    forecast_month = Column(Date, nullable=True)     # first day of the exam month
    exam_priority = Column(Enum('default', 'done', 'undone', name='speaking_exam_priority'),
                           default='default', nullable=False, server_default='default')

    plan = Column(JSON, nullable=True)               # the frozen paper, see speaking_assemble

    # ── Outcome (§4.4) ──
    status = Column(Enum('in_progress', 'completed', 'abandoned', 'terminated', 'interrupted',
                         name='speaking_attempt_status'),
                    default='in_progress', nullable=False, server_default='in_progress')
    end_reason = Column(String(255), nullable=True)
    submitted = Column(Boolean, default=False, nullable=False, server_default='0')

    # ── Scores, filled in by the grader; null until then ──
    # Grading is three AI calls over recorded audio, so it runs in the background and
    # carries its own state — the result screen has to be able to say "đang chấm".
    grade_status = Column(Enum('pending', 'running', 'done', 'failed',
                               name='speaking_grade_status'),
                          default='pending', nullable=False, server_default='pending')
    grade_error = Column(String(255), nullable=True)
    overall_band = Column(Float, nullable=True)
    criteria = Column(JSON, nullable=True)           # the four rounded criterion bands
    part_results = Column(JSON, nullable=True)       # per part: raw + band + feedback
    graded_at = Column(DateTime, nullable=True)

    started_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))
    ended_at = Column(DateTime, nullable=True)

    answers = relationship("SpeakingAttemptAnswer", back_populates="attempt",
                           cascade="all, delete-orphan")


class SpeakingAttemptAnswer(Base):
    """One question inside one attempt (§4.6).

    First and retry answers are both kept: Practice Mode lets a student answer again after
    a too-short reply, and §4.5 is explicit that the first attempt is still stored while
    only the final one is graded. `final_*` is therefore derived, not a third recording.

    Recordings live on disk (see routes/student/speaking_test.py) because static/ is
    served publicly and a student's voice must not be; only the path is stored here.
    """
    __tablename__ = 'speaking_attempt_answers'

    answer_id = Column(Integer, primary_key=True, index=True)
    attempt_id = Column(Integer, ForeignKey('speaking_attempts.attempt_id', ondelete='CASCADE'),
                        nullable=False, index=True)
    # Null is possible in principle for a line that is not from the bank; every question
    # asked today has a row, including the Part 2 long turn.
    question_id = Column(Integer, ForeignKey('speaking_questions.question_id', ondelete='SET NULL'),
                         nullable=True, index=True)
    topic_id = Column(Integer, ForeignKey('speaking_topics.topic_id', ondelete='SET NULL'),
                      nullable=True, index=True)
    part = Column(Enum('part1', 'part2', 'part2_followup', 'part3',
                       name='speaking_answer_parts'), nullable=False, index=True)
    order_index = Column(Integer, default=0, nullable=False)

    # Frozen copies — the bank may be edited after the test was taken.
    topic_title = Column(String(255), nullable=True)
    question_text = Column(LONGTEXT, nullable=True)
    # True for the one Part 3 question the AI improvised from what the student said, which
    # has no row in the bank and must not be reused as if it were forecast material.
    is_ai_followup = Column(Boolean, default=False, nullable=False, server_default='0')

    first_text = Column(LONGTEXT, nullable=True)     # transcript, or typed in Subtitle Mode
    retry_text = Column(LONGTEXT, nullable=True)
    first_audio = Column(String(255), nullable=True)
    retry_audio = Column(String(255), nullable=True)
    used_retry = Column(Boolean, default=False, nullable=False, server_default='0')

    answer_status = Column(Enum('answered', 'no_answer', 'auto_skipped', 'time_expired',
                                name='speaking_answer_status'),
                           default='no_answer', nullable=False, server_default='no_answer')
    duration_ms = Column(Integer, nullable=True)

    # Per-question marks (§6.2). Null until the attempt is graded.
    scores = Column(JSON, nullable=True)
    feedback = Column(JSON, nullable=True)

    # True once the recording file has been pruned (§9). The transcript, the four band
    # scores and the feedback stay forever — only the audio goes — so the room and the
    # analysis page need to tell "never recorded" apart from "recorded, then expired",
    # otherwise an old answer looks like the student never spoke.
    audio_expired = Column(Boolean, default=False, nullable=False, server_default='0')

    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))

    attempt = relationship("SpeakingAttempt", back_populates="answers")

    @property
    def final_text(self):
        """§4.5: retry wins when there is one; the first answer is still kept above."""
        return self.retry_text if self.used_retry and self.retry_text else self.first_text

    @property
    def final_audio(self):
        return self.retry_audio if self.used_retry and self.retry_audio else self.first_audio


class SpeakingQuestionProgress(Base):
    """Những gì thuộc về MỘT học viên với MỘT câu hỏi, sống lâu hơn từng bài thi (§6).

    Hai thứ ở đây không thể để trên dòng câu trả lời, vì một câu hỏi được trả lời nhiều
    lần qua nhiều bài thi khác nhau:

    * `viewed_at` — §6.1 đánh dấu "Chưa xem / Đã xem" khi học viên mở phần phân tích của
      câu đó. Trạng thái thuộc về câu hỏi, không thuộc lần trả lời nào.
    * `sample_answer_id` — §6.3: khi học viên tự chọn câu mẫu thì giữ nguyên lựa chọn đó,
      KHÔNG tự thay kể cả sau này có lần điểm cao hơn. Để trống thì hệ thống tự chọn lần
      điểm cao nhất (mới nhất nếu bằng điểm) — nên NULL ở đây mang nghĩa "chưa chọn tay",
      không phải "không có câu mẫu".
    """
    __tablename__ = 'speaking_question_progress'
    __table_args__ = (UniqueConstraint('user_id', 'question_id',
                                       name='uq_speaking_progress_user_question'),)

    progress_id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.user_id', ondelete='CASCADE'),
                     nullable=False, index=True)
    question_id = Column(Integer, ForeignKey('speaking_questions.question_id',
                                             ondelete='CASCADE'),
                         nullable=False, index=True)

    viewed_at = Column(DateTime, nullable=True)
    # Xoá bài thi thì câu mẫu trỏ vào đó biến mất, và §6.3 quay về tự chọn — đúng hơn là
    # giữ một con trỏ gãy.
    sample_answer_id = Column(Integer,
                              ForeignKey('speaking_attempt_answers.answer_id',
                                         ondelete='SET NULL'), nullable=True)
    # Câu mẫu do AI viết (từ "Cải thiện" hoặc "Tạo câu từ ý tưởng"). Tách khỏi
    # `sample_answer_id` vì đây KHÔNG phải bài học viên tự nói — giao diện phải ghi rõ như
    # vậy, không thể để lẫn rồi tưởng mình từng nói được câu đó. Một lúc chỉ có một trong
    # hai được đặt.
    sample_text = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None),
                        onupdate=lambda: get_vietnam_time().replace(tzinfo=None))


class SpeakingDailyUsage(Base):
    """Đếm lượt dùng theo NGÀY cho những tính năng Speaking bị giới hạn.

    Đặt trong DB chứ không đếm bằng Redis: `app/utils/redis_cache.py` cố ý trả về giá trị
    rỗng khi Redis chết thay vì ném lỗi, nên hạn mức đặt ở đó sẽ tự mở toang đúng lúc hạ
    tầng có chuyện — mà mỗi lượt ở đây là một lần gọi AI có trả tiền.

    `feature` để mở rộng: hôm nay chỉ có 'shadowing' (1 lần/ngày), mai có thêm thứ khác thì
    không phải dựng bảng mới.
    """
    __tablename__ = 'speaking_daily_usage'
    __table_args__ = (UniqueConstraint('user_id', 'day', 'feature',
                                       name='uq_speaking_usage_user_day_feature'),)

    usage_id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.user_id', ondelete='CASCADE'),
                     nullable=False, index=True)
    day = Column(Date, nullable=False, index=True)
    feature = Column(String(32), nullable=False)
    used = Column(Integer, default=0, nullable=False, server_default='0')
    updated_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None),
                        onupdate=lambda: get_vietnam_time().replace(tzinfo=None))


class AiScoreUsage(Base):
    """One row per AI Writing scoring action (grade / spell-check / on-demand gen).
    Backs server-side quota: free 2/day, VIP 1000/month, fair-usage 20 per rolling
    60 min → 30-min cooldown. created_at stored naive VN time."""
    __tablename__ = 'ai_score_usage'

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.user_id', ondelete='CASCADE'), nullable=False, index=True)
    kind = Column(String(20), nullable=False, default='grade')  # grade | spellcheck | sample | outline | keylang
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None), index=True)


class EmailBroadcast(Base):
    __tablename__ = 'email_broadcasts'

    id = Column(Integer, primary_key=True, autoincrement=True)
    subject = Column(String(500), nullable=False)
    body_html = Column(LONGTEXT, nullable=False)
    status = Column(Enum('pending', 'sending', 'completed', 'failed', name='broadcast_status'), default='pending')
    target_filter = Column(String(50), default='non_vip')  # non_vip, all, vip
    total_recipients = Column(Integer, default=0)
    sent_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)
    created_by = Column(Integer, ForeignKey('users.user_id'), nullable=True)
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))
    completed_at = Column(DateTime, nullable=True)


class Center(Base):
    __tablename__ = 'centers'

    center_id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False, unique=True)  # login account, role='center'
    name = Column(String(200), nullable=False)
    logo_url = Column(String(255), nullable=True)
    # Wallet (VND). balance = deposited - used (kept explicit for auditing).
    wallet_balance = Column(Float, default=0, nullable=False)
    wallet_deposited = Column(Float, default=0, nullable=False)   # cumulative topped up
    wallet_used = Column(Float, default=0, nullable=False)        # cumulative spent
    # Tiered VIP discount, derived from cumulative purchase count:
    #   count 1-5 -> 0%, 6-20 -> 5%, 21+ -> 10%
    vip_purchase_count = Column(Integer, default=0, nullable=False)
    discount_rate = Column(Float, default=0, nullable=False)      # current %, cached from count
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))

    user = relationship("User")


class Classroom(Base):
    __tablename__ = 'classrooms'

    class_id = Column(Integer, primary_key=True, index=True)
    center_id = Column(Integer, ForeignKey('centers.center_id'), nullable=False)
    name = Column(String(200), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))

    center = relationship("Center")


class CenterMembership(Base):
    """Links a teacher/student User to a Center. One row per user per center."""
    __tablename__ = 'center_memberships'

    membership_id = Column(Integer, primary_key=True, index=True)
    center_id = Column(Integer, ForeignKey('centers.center_id'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    member_type = Column(Enum('teacher', 'student', name='center_member_types'), nullable=False)
    is_paused = Column(Boolean, default=False)     # tạm dừng (temporarily suspended)
    is_disabled = Column(Boolean, default=False)   # vô hiệu hoá (deactivated)
    # A teacher marked hiệu trưởng (principal) can switch between their teacher
    # account and the center-management account without re-login. One per center.
    is_principal = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))

    center = relationship("Center")
    user = relationship("User")

    __table_args__ = (
        {'mysql_charset': 'utf8mb4'},
    )


class ClassMember(Base):
    """Many-to-many: which users (teachers or students) belong to a classroom.
    A teacher may belong to several classes; a center-student to 0 or 1 class
    (no row => 'khách lẻ')."""
    __tablename__ = 'class_members'

    id = Column(Integer, primary_key=True, index=True)
    class_id = Column(Integer, ForeignKey('classrooms.class_id', ondelete='CASCADE'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.user_id', ondelete='CASCADE'), nullable=False)
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))

    classroom = relationship("Classroom")
    user = relationship("User")


class CenterWalletTransaction(Base):
    __tablename__ = 'center_wallet_transactions'

    transaction_id = Column(Integer, primary_key=True, index=True)
    center_id = Column(Integer, ForeignKey('centers.center_id'), nullable=False)
    type = Column(Enum('deposit', 'vip_purchase', name='center_txn_types'), nullable=False)
    amount = Column(Float, nullable=False)                 # deposit: credited; vip_purchase: charged
    method = Column(String(50), nullable=True)            # 'payos' for deposits
    status = Column(Enum('pending', 'completed', 'reject', name='center_txn_status'), default='pending')
    target_user_id = Column(Integer, ForeignKey('users.user_id'), nullable=True)  # vip_purchase: who got VIP
    package_id = Column(Integer, ForeignKey('vip_packages.package_id'), nullable=True)
    discount_rate = Column(Float, nullable=True)          # discount applied on a vip_purchase
    payos_order_code = Column(BigInteger, nullable=True, unique=True, index=True)
    payos_checkout_url = Column(Text, nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))

    center = relationship("Center")


class ChatMessage(Base):
    """Teacher<->student direct messages and teacher->class messages.
    is_pinned marks an important/homework message that shouldn't scroll away."""
    __tablename__ = 'chat_messages'

    message_id = Column(Integer, primary_key=True, index=True)
    center_id = Column(Integer, ForeignKey('centers.center_id'), nullable=False, index=True)
    sender_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    scope = Column(Enum('direct', 'class', name='chat_scopes'), nullable=False)
    class_id = Column(Integer, ForeignKey('classrooms.class_id'), nullable=True, index=True)   # scope='class'
    recipient_id = Column(Integer, ForeignKey('users.user_id'), nullable=True)                 # scope='direct'
    content = Column(Text, nullable=False)
    is_pinned = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None), index=True)

    center = relationship("Center")
    sender = relationship("User", foreign_keys=[sender_id])
    recipient = relationship("User", foreign_keys=[recipient_id])
    classroom = relationship("Classroom")


class ExamProgress(Base):
    """Live exam-taking heartbeat for the teacher realtime dashboard. One row
    per user (upserted); Redis is the fast read path, this table is durability."""
    __tablename__ = 'exam_progress'

    progress_id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False, unique=True, index=True)
    center_id = Column(Integer, ForeignKey('centers.center_id'), nullable=True, index=True)
    exam_id = Column(Integer, nullable=True)
    skill = Column(String(30), nullable=True)             # listening/reading/writing/speaking
    title = Column(String(255), nullable=True)            # e.g. "Part 1: Chicken"
    questions_done = Column(Integer, default=0)
    total_questions = Column(Integer, nullable=True)
    last_question = Column(Integer, nullable=True)
    tab_switches = Column(Integer, default=0)             # times the student left the exam tab this attempt
    is_active = Column(Boolean, default=True)             # currently in an exam
    started_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))

    user = relationship("User")


class Announcement(Base):
    """Homepage "Thông tin mới" news items, authored from the admin dashboard
    and shown at the top of the public student landing page. `is_important`
    pins an item to the top of the list ("không bị trôi")."""
    __tablename__ = 'announcements'

    announcement_id = Column(Integer, primary_key=True, index=True)
    icon = Column(String(16), nullable=True)          # emoji, e.g. 🔥 / 🆕 / 📅
    title = Column(String(255), nullable=True)        # headline shown on the homepage list
    content = Column(Text, nullable=True)             # full body (rich HTML, may embed images)
    link = Column(String(500), nullable=True)         # optional external URL (used instead of the detail page)
    is_important = Column(Boolean, default=False)     # pinned at top of the list
    display_order = Column(Integer, default=0)        # manual ordering (asc)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))


class AffiliateWalletTx(Base):
    """Affiliate commission-wallet ledger (unit = xu, 1 xu = 1 VND). One row per
    balance change, shown as "Lịch sử ví". source_transaction_id is set for
    'commission' rows and is UNIQUE, so a given VIP PackageTransaction can credit
    commission at most once (idempotent across the PayOS webhook + admin-confirm
    paths). Withdrawals/refunds leave it NULL (MySQL allows many NULLs)."""
    __tablename__ = 'affiliate_wallet_tx'

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False, index=True)  # the affiliate
    type = Column(Enum('commission', 'withdraw', 'withdraw_refund', name='affiliate_tx_types'), nullable=False)
    amount = Column(BigInteger, nullable=False)        # signed xu (+commission, -withdraw)
    balance_after = Column(BigInteger, nullable=False)
    description = Column(String(255), nullable=True)
    source_user_id = Column(Integer, ForeignKey('users.user_id'), nullable=True)         # the buyer (commission)
    source_transaction_id = Column(Integer, unique=True, nullable=True, index=True)      # PackageTransaction id
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))


class AffiliateWithdrawal(Base):
    """A payout request against the affiliate wallet. Balance is deducted when the
    request is created (status 'pending'); admin manually bank-transfers then marks
    it 'paid', or 'rejected' (which refunds the balance)."""
    __tablename__ = 'affiliate_withdrawals'

    withdrawal_id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False, index=True)
    amount = Column(BigInteger, nullable=False)        # xu requested (== VND)
    account_holder = Column(String(255), nullable=True)
    account_number = Column(String(64), nullable=True)
    bank = Column(String(255), nullable=True)
    qr_url = Column(String(255), nullable=True)        # snapshot of the payout QR image
    status = Column(Enum('pending', 'paid', 'rejected', name='affiliate_withdrawal_status'), default='pending', index=True)
    admin_note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))
    processed_at = Column(DateTime, nullable=True)

    user = relationship("User", foreign_keys=[user_id])


class StudySession(Base):
    """A teacher-run live study session for one class: a Google Meet + an ordered
    list of existing tests (StudySessionTask), with a 'current task' pointer. The
    live 'who's online / which task' state lives in Redis (polled), like the exam
    realtime board; this table is the durable record so history can be reviewed."""
    __tablename__ = 'study_sessions'

    session_id = Column(Integer, primary_key=True, index=True)
    center_id = Column(Integer, ForeignKey('centers.center_id'), nullable=True, index=True)
    class_id = Column(Integer, ForeignKey('classrooms.class_id'), nullable=False, index=True)
    teacher_id = Column(Integer, ForeignKey('users.user_id'), nullable=True)
    teacher_name = Column(String(50), nullable=True)          # cached for the student banner
    meet_url = Column(String(500), nullable=True)
    status = Column(String(20), default='active', index=True)  # 'active' | 'ended'
    current_task_id = Column(Integer, nullable=True)          # -> study_session_tasks.task_id
    scheduled_start = Column(DateTime, nullable=True)
    scheduled_end = Column(DateTime, nullable=True)
    started_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))


class StudySessionTask(Base):
    """One test in a study session's ordered task list. Mirrors a Homework row
    ({exam_id, skill, parts}) so the student's 'Current Task' button reuses the
    homework deep-link (/{skill}_list?open=<exam_id>&part=<n>)."""
    __tablename__ = 'study_session_tasks'

    task_id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey('study_sessions.session_id'), nullable=False, index=True)
    skill = Column(String(20), nullable=True)                # listening/reading/writing/speaking
    exam_id = Column(Integer, nullable=True)
    # Speaking không có đề trong bảng `exams` — nó giao theo (chủ đề, Part), giống hệt bài
    # tập được giao (xem Homework.speaking_topic_id). Một task trỏ tới MỘT trong hai.
    speaking_topic_id = Column(Integer, nullable=True)
    speaking_section = Column(String(20), nullable=True)     # part1 | part2 | part3
    title = Column(String(255), nullable=True)               # cached exam title
    parts = Column(JSON, nullable=True)                      # [1] single part; null/[] = full test
    order_index = Column(Integer, default=0)
    status = Column(String(20), default='pending')           # pending | current | finished | cancelled
    started_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)


class StudySessionEvent(Base):
    """Dòng thời gian của một buổi học — "Session Replay" trong spec.

    Ghi bằng BẢN SAO (task_title) chứ không chỉ khoá ngoại: buổi học xem lại sau nhiều
    tháng, lúc đó đề có thể đã bị đổi tên hoặc gỡ, mà bản ghi lịch sử thì phải kể đúng
    những gì đã diễn ra hôm ấy.
    """
    __tablename__ = 'study_session_events'

    event_id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey('study_sessions.session_id'),
                        nullable=False, index=True)
    # session_started | task_started | task_finished | task_cancelled | session_ended
    kind = Column(String(24), nullable=False)
    task_id = Column(Integer, nullable=True)
    task_title = Column(String(255), nullable=True)
    note = Column(String(255), nullable=True)
    at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None), index=True)


class StudySessionAttendance(Base):
    """Điểm danh một học viên trong một buổi học.

    Không có nút "điểm danh": học viên nào có mặt là do CHÍNH HỌ hỏi máy chủ "lớp mình có
    buổi học nào đang chạy không" (`/student/study-sessions/active`, màn hình hỏi lại theo
    chu kỳ). Mỗi lần hỏi là một lần chạm, nên `online_seconds` cộng dồn từ khoảng cách giữa
    hai lần chạm — bỏ qua khoảng nghỉ quá dài để học viên đóng máy giữa chừng không bị tính
    là vẫn đang online.
    """
    __tablename__ = 'study_session_attendance'

    attendance_id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey('study_sessions.session_id'),
                        nullable=False, index=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False, index=True)
    joined_at = Column(DateTime, nullable=True)
    last_seen_at = Column(DateTime, nullable=True)
    online_seconds = Column(Integer, default=0)


class Homework(Base):
    """A teacher's homework assignment: one exam assigned to a whole class. A student
    in that class is "done" when they have a submitted result for the exam. Delete +
    re-create to change assignments."""
    __tablename__ = 'homeworks'

    homework_id = Column(Integer, primary_key=True, index=True)
    center_id = Column(Integer, ForeignKey('centers.center_id'), nullable=False, index=True)
    class_id = Column(Integer, ForeignKey('classrooms.class_id'), nullable=False, index=True)
    teacher_id = Column(Integer, ForeignKey('users.user_id'), nullable=True)
    # Nullable từ 01/10: bài tập Speaking KHÔNG có đề trong bảng `exams` — Speaking ghép
    # bài từ speaking_topics, nên một dòng homework hoặc trỏ tới `exam_id` (L/R/W) hoặc
    # trỏ tới `speaking_topic_id` + `speaking_section`, không bao giờ cả hai.
    exam_id = Column(Integer, ForeignKey('exams.exam_id'), nullable=True)
    speaking_topic_id = Column(Integer, ForeignKey('speaking_topics.topic_id'), nullable=True)
    speaking_section = Column(String(20), nullable=True)   # part1 | part2 | part3
    skill = Column(String(20), nullable=True)           # listening/reading/writing/speaking
    title = Column(String(255), nullable=True)          # cached exam title
    parts = Column(JSON, nullable=True)                 # [1,2,..] specific parts; null/[] = full test
    due_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))


class ErrorReport(Base):
    """A customer-submitted error report filed from the exam Review screen.
    error_types is a JSON array of selected type keys (see the frontend for the
    canonical list): 'wrong_answer', 'mis_graded', 'spelling', 'audio', 'ui', 'other'.
    wrong_answer_questions / mis_graded_questions hold the free-text question numbers
    the user typed for the two type-specific fields. Admins mark reports as viewed
    (is_viewed) so a large queue stays manageable."""
    __tablename__ = 'error_reports'

    report_id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=True, index=True)
    exam_id = Column(Integer, ForeignKey('exams.exam_id'), nullable=True, index=True)
    result_id = Column(Integer, ForeignKey('exam_results.result_id'), nullable=True)
    skill = Column(String(20), nullable=True)                 # reading/listening/writing/speaking
    exam_title = Column(String(255), nullable=True)           # cached title for the admin list
    error_types = Column(JSON, nullable=True)                 # ["wrong_answer", ...]
    wrong_answer_questions = Column(String(255), nullable=True)
    mis_graded_questions = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    is_viewed = Column(Boolean, default=False, index=True)
    viewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))

    user = relationship("User")
    exam = relationship("Exam")


class SystemSetting(Base):
    """Simple key/value store for admin-tunable settings (e.g. forecast decay days)."""
    __tablename__ = 'system_settings'

    setting_key = Column(String(64), primary_key=True)
    setting_value = Column(String(255), nullable=True)
    updated_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))


class MonthlyCupWinner(Base):
    """Frozen Top-3 of a month's "Cúp tháng" (per skill), snapshotted at month end.
    The new Hall of Fame is aggregated from these rows (# Top-3 finishes → total
    score → total time)."""
    __tablename__ = "monthly_cup_winners"

    winner_id = Column(Integer, primary_key=True, index=True)
    skill = Column(String(20), index=True)          # reading / listening / writing
    year = Column(Integer, index=True)
    month = Column(Integer, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id"), index=True)
    rank = Column(Integer)                           # 1 / 2 / 3
    top10_count = Column(Integer, default=0)         # # tests they were Top-10 of that month
    attempts = Column(Integer, default=0)
    score = Column(Integer, default=0)               # cup score metric (→ HoF total score)
    time_taken = Column(Integer, nullable=True)      # total time that month (→ HoF tie-break)
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))


class SpeakingPronUnit(Base):
    """Một bài học phát âm. Tiêu đề và lý thuyết do admin nhập tay."""
    __tablename__ = 'speaking_pron_units'

    unit_id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)          # VD: "Unit 1: /ɪ/ and /iː/"
    theory = Column(Text, nullable=True)                 # HTML/markdown do admin nhập
    order_index = Column(Integer, default=0, nullable=False)
    # Chưa sinh xong phần luyện tập thì đừng cho học viên thấy — bài học không có gì để
    # luyện là một trang cụt.
    is_published = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))
    updated_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None),
                        onupdate=lambda: get_vietnam_time().replace(tzinfo=None))


class SpeakingPronItem(Base):
    """Một từ hoặc một câu để luyện, sinh bởi AI từ lý thuyết của Unit.

    `user_id` NULL = bộ MẶC ĐỊNH của Unit, do admin sinh, ai vào cũng thấy.
    `user_id` có giá trị = bộ riêng của học viên đó sau khi bấm "Làm mới nội dung luyện
    tập" (quyền VIP). Làm vậy để một người bấm làm mới không đổi nội dung của cả lớp —
    nếu ghi đè bộ mặc định thì mỗi lần ai đó làm mới, mọi học viên khác lại thấy bài khác.

    `kind` quyết định cách luyện: 'word' → chấm phát âm, 'sentence' → shadowing. Đúng
    phân công trong feedback, và cả hai đều dùng lại cơ chế đã có.
    """
    __tablename__ = 'speaking_pron_items'

    item_id = Column(Integer, primary_key=True, index=True)
    unit_id = Column(Integer, ForeignKey('speaking_pron_units.unit_id', ondelete='CASCADE'),
                     nullable=False, index=True)
    user_id = Column(Integer, ForeignKey('users.user_id', ondelete='CASCADE'),
                     nullable=True, index=True)
    kind = Column(Enum('word', 'sentence', name='speaking_pron_item_kinds'),
                  nullable=False, default='word')
    content = Column(String(500), nullable=False)
    # Một dòng nhắc bằng tiếng Việt: âm nào cần chú ý, trọng âm rơi vào đâu.
    note = Column(String(500), nullable=True)
    order_index = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=lambda: get_vietnam_time().replace(tzinfo=None))
