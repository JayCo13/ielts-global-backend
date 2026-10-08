-- Same script as vn_port_schema_2026_10.sql, pinned to the app database so it
-- cannot run against the wrong schema (or with no database selected).
USE ielts_practice_db;

-- =====================================================================
-- VN → global schema port (alembic e5a1c7d2f9b4 → f2b1e0a7c3d9)
-- Generated with: alembic upgrade e5a1c7d2f9b4:head --sql
-- Verified on a throwaway TiDB v7.5.1 (243/243 statements OK).
--
-- RUN FROM FILE, never paste (see CLAUDE.md "TiDB ENUM corruption"):
--   mysql -h <tidb-host> -P 4000 -u <user> -p <db> < vn_port_schema_2026_10.sql
--
-- PRE-CHECKS — stop and report back if any of these differ:
--   SHOW COLUMNS FROM package_transactions LIKE 'payos_order_code'; -- expect: 1 row
--   SHOW TABLES LIKE 'centers';               -- expect: empty
--   SHOW COLUMNS FROM users LIKE 'center_id'; -- expect: empty
--
-- Note: one data statement — clears exam_sections.part_title on listening
-- rows where part_title is byte-identical to description (VN cleanup, b1f4c2d9a3e7).
-- =====================================================================

-- Running upgrade accf7c73e8f7 -> add_question_type_tags

ALTER TABLE exam_sections ADD COLUMN question_type_tags JSON;

INSERT INTO alembic_version (version_num) VALUES ('add_question_type_tags');

-- Running upgrade add_question_type_tags -> fdd7f49aface

ALTER TABLE writing_tasks ADD COLUMN question_type_tags JSON;

UPDATE alembic_version SET version_num='fdd7f49aface' WHERE alembic_version.version_num = 'add_question_type_tags';

-- Running upgrade fdd7f49aface -> b1f4c2d9a3e7

UPDATE exam_sections
        SET part_title = NULL
        WHERE section_type = 'listening'
          AND part_title IS NOT NULL
          AND description IS NOT NULL
          AND part_title = description;

UPDATE alembic_version SET version_num='b1f4c2d9a3e7' WHERE alembic_version.version_num = 'fdd7f49aface';

-- Running upgrade b1f4c2d9a3e7 -> c1a2b3d4e5f6

ALTER TABLE users MODIFY COLUMN role ENUM('admin','student','customer','center','teacher');

CREATE TABLE centers (
    center_id INTEGER NOT NULL AUTO_INCREMENT, 
    user_id INTEGER NOT NULL, 
    name VARCHAR(200) NOT NULL, 
    logo_url VARCHAR(255), 
    wallet_balance FLOAT NOT NULL DEFAULT '0', 
    wallet_deposited FLOAT NOT NULL DEFAULT '0', 
    wallet_used FLOAT NOT NULL DEFAULT '0', 
    vip_purchase_count INTEGER NOT NULL DEFAULT '0', 
    discount_rate FLOAT NOT NULL DEFAULT '0', 
    is_active BOOL, 
    created_at DATETIME, 
    PRIMARY KEY (center_id), 
    FOREIGN KEY(user_id) REFERENCES users (user_id), 
    UNIQUE (user_id)
);

CREATE INDEX ix_centers_center_id ON centers (center_id);

CREATE TABLE classrooms (
    class_id INTEGER NOT NULL AUTO_INCREMENT, 
    center_id INTEGER NOT NULL, 
    name VARCHAR(200) NOT NULL, 
    is_active BOOL, 
    created_at DATETIME, 
    PRIMARY KEY (class_id), 
    FOREIGN KEY(center_id) REFERENCES centers (center_id)
);

CREATE INDEX ix_classrooms_class_id ON classrooms (class_id);

CREATE TABLE center_memberships (
    membership_id INTEGER NOT NULL AUTO_INCREMENT, 
    center_id INTEGER NOT NULL, 
    user_id INTEGER NOT NULL, 
    member_type ENUM('teacher','student') NOT NULL, 
    is_paused BOOL, 
    is_disabled BOOL, 
    created_at DATETIME, 
    PRIMARY KEY (membership_id), 
    FOREIGN KEY(center_id) REFERENCES centers (center_id), 
    FOREIGN KEY(user_id) REFERENCES users (user_id)
)CHARSET=utf8mb4;

CREATE INDEX ix_center_memberships_membership_id ON center_memberships (membership_id);

CREATE TABLE class_members (
    id INTEGER NOT NULL AUTO_INCREMENT, 
    class_id INTEGER NOT NULL, 
    user_id INTEGER NOT NULL, 
    created_at DATETIME, 
    PRIMARY KEY (id), 
    FOREIGN KEY(class_id) REFERENCES classrooms (class_id) ON DELETE CASCADE, 
    FOREIGN KEY(user_id) REFERENCES users (user_id) ON DELETE CASCADE
);

CREATE INDEX ix_class_members_id ON class_members (id);

CREATE TABLE center_wallet_transactions (
    transaction_id INTEGER NOT NULL AUTO_INCREMENT, 
    center_id INTEGER NOT NULL, 
    type ENUM('deposit','vip_purchase') NOT NULL, 
    amount FLOAT NOT NULL, 
    method VARCHAR(50), 
    status ENUM('pending','completed','reject'), 
    target_user_id INTEGER, 
    package_id INTEGER, 
    discount_rate FLOAT, 
    payos_order_code BIGINT, 
    payos_checkout_url TEXT, 
    note TEXT, 
    created_at DATETIME, 
    PRIMARY KEY (transaction_id), 
    FOREIGN KEY(center_id) REFERENCES centers (center_id), 
    FOREIGN KEY(target_user_id) REFERENCES users (user_id), 
    FOREIGN KEY(package_id) REFERENCES vip_packages (package_id)
);

CREATE INDEX ix_center_wallet_transactions_transaction_id ON center_wallet_transactions (transaction_id);

CREATE UNIQUE INDEX ix_center_wallet_transactions_payos_order_code ON center_wallet_transactions (payos_order_code);

CREATE TABLE chat_messages (
    message_id INTEGER NOT NULL AUTO_INCREMENT, 
    center_id INTEGER NOT NULL, 
    sender_id INTEGER NOT NULL, 
    scope ENUM('direct','class') NOT NULL, 
    class_id INTEGER, 
    recipient_id INTEGER, 
    content TEXT NOT NULL, 
    is_pinned BOOL, 
    created_at DATETIME, 
    PRIMARY KEY (message_id), 
    FOREIGN KEY(center_id) REFERENCES centers (center_id), 
    FOREIGN KEY(sender_id) REFERENCES users (user_id), 
    FOREIGN KEY(class_id) REFERENCES classrooms (class_id), 
    FOREIGN KEY(recipient_id) REFERENCES users (user_id)
);

CREATE INDEX ix_chat_messages_message_id ON chat_messages (message_id);

CREATE INDEX ix_chat_messages_center_id ON chat_messages (center_id);

CREATE INDEX ix_chat_messages_class_id ON chat_messages (class_id);

CREATE INDEX ix_chat_messages_created_at ON chat_messages (created_at);

CREATE TABLE exam_progress (
    progress_id INTEGER NOT NULL AUTO_INCREMENT, 
    user_id INTEGER NOT NULL, 
    center_id INTEGER, 
    exam_id INTEGER, 
    skill VARCHAR(30), 
    title VARCHAR(255), 
    questions_done INTEGER, 
    total_questions INTEGER, 
    last_question INTEGER, 
    is_active BOOL, 
    started_at DATETIME, 
    updated_at DATETIME, 
    PRIMARY KEY (progress_id), 
    FOREIGN KEY(user_id) REFERENCES users (user_id), 
    FOREIGN KEY(center_id) REFERENCES centers (center_id), 
    UNIQUE (user_id)
);

CREATE INDEX ix_exam_progress_progress_id ON exam_progress (progress_id);

CREATE UNIQUE INDEX ix_exam_progress_user_id ON exam_progress (user_id);

CREATE INDEX ix_exam_progress_center_id ON exam_progress (center_id);

UPDATE alembic_version SET version_num='c1a2b3d4e5f6' WHERE alembic_version.version_num = 'b1f4c2d9a3e7';

-- Running upgrade c1a2b3d4e5f6 -> d2e3f4a5b6c7

CREATE TABLE announcements (
    announcement_id INTEGER NOT NULL AUTO_INCREMENT, 
    icon VARCHAR(16), 
    content VARCHAR(500) NOT NULL, 
    link VARCHAR(500), 
    is_important BOOL, 
    display_order INTEGER, 
    is_active BOOL, 
    created_at DATETIME, 
    PRIMARY KEY (announcement_id)
);

CREATE INDEX ix_announcements_announcement_id ON announcements (announcement_id);

UPDATE alembic_version SET version_num='d2e3f4a5b6c7' WHERE alembic_version.version_num = 'c1a2b3d4e5f6';

-- Running upgrade d2e3f4a5b6c7 -> e3f4a5b6c7d8

ALTER TABLE announcements ADD COLUMN title VARCHAR(255);

ALTER TABLE announcements MODIFY content TEXT NULL;

UPDATE alembic_version SET version_num='e3f4a5b6c7d8' WHERE alembic_version.version_num = 'd2e3f4a5b6c7';

-- Running upgrade e3f4a5b6c7d8 -> f4a5b6c7d8e9

ALTER TABLE users ADD COLUMN referral_code VARCHAR(16);

ALTER TABLE users ADD COLUMN referred_by INTEGER;

ALTER TABLE users ADD COLUMN affiliate_balance BIGINT NOT NULL DEFAULT '0';

CREATE UNIQUE INDEX ix_users_referral_code ON users (referral_code);

ALTER TABLE users ADD CONSTRAINT fk_users_referred_by FOREIGN KEY(referred_by) REFERENCES users (user_id);

CREATE TABLE affiliate_wallet_tx (
    id INTEGER NOT NULL AUTO_INCREMENT, 
    user_id INTEGER NOT NULL, 
    type ENUM('commission','withdraw','withdraw_refund') NOT NULL, 
    amount BIGINT NOT NULL, 
    balance_after BIGINT NOT NULL, 
    description VARCHAR(255), 
    source_user_id INTEGER, 
    source_transaction_id INTEGER, 
    created_at DATETIME, 
    PRIMARY KEY (id), 
    FOREIGN KEY(user_id) REFERENCES users (user_id), 
    FOREIGN KEY(source_user_id) REFERENCES users (user_id)
);

CREATE INDEX ix_affiliate_wallet_tx_id ON affiliate_wallet_tx (id);

CREATE INDEX ix_affiliate_wallet_tx_user_id ON affiliate_wallet_tx (user_id);

CREATE UNIQUE INDEX ix_affiliate_wallet_tx_source_transaction_id ON affiliate_wallet_tx (source_transaction_id);

CREATE TABLE affiliate_withdrawals (
    withdrawal_id INTEGER NOT NULL AUTO_INCREMENT, 
    user_id INTEGER NOT NULL, 
    amount BIGINT NOT NULL, 
    account_holder VARCHAR(255) NOT NULL, 
    account_number VARCHAR(64) NOT NULL, 
    bank VARCHAR(255) NOT NULL, 
    status ENUM('pending','paid','rejected'), 
    admin_note TEXT, 
    created_at DATETIME, 
    processed_at DATETIME, 
    PRIMARY KEY (withdrawal_id), 
    FOREIGN KEY(user_id) REFERENCES users (user_id)
);

CREATE INDEX ix_affiliate_withdrawals_withdrawal_id ON affiliate_withdrawals (withdrawal_id);

CREATE INDEX ix_affiliate_withdrawals_user_id ON affiliate_withdrawals (user_id);

CREATE INDEX ix_affiliate_withdrawals_status ON affiliate_withdrawals (status);

UPDATE alembic_version SET version_num='f4a5b6c7d8e9' WHERE alembic_version.version_num = 'e3f4a5b6c7d8';

-- Running upgrade f4a5b6c7d8e9 -> a5b6c7d8e9f0

ALTER TABLE users ADD COLUMN payout_qr_url VARCHAR(255);

ALTER TABLE users ADD COLUMN payout_bank VARCHAR(255);

ALTER TABLE users ADD COLUMN payout_account_number VARCHAR(64);

ALTER TABLE users ADD COLUMN payout_account_holder VARCHAR(255);

ALTER TABLE affiliate_withdrawals ADD COLUMN qr_url VARCHAR(255);

ALTER TABLE affiliate_withdrawals MODIFY account_holder VARCHAR(255) NULL;

ALTER TABLE affiliate_withdrawals MODIFY account_number VARCHAR(64) NULL;

ALTER TABLE affiliate_withdrawals MODIFY bank VARCHAR(255) NULL;

UPDATE alembic_version SET version_num='a5b6c7d8e9f0' WHERE alembic_version.version_num = 'f4a5b6c7d8e9';

-- Running upgrade a5b6c7d8e9f0 -> b6c7d8e9f0a1

ALTER TABLE users ADD COLUMN referral_clicks BIGINT NOT NULL DEFAULT '0';

UPDATE alembic_version SET version_num='b6c7d8e9f0a1' WHERE alembic_version.version_num = 'a5b6c7d8e9f0';

-- Running upgrade b6c7d8e9f0a1 -> c7d8e9f0a1b2

CREATE TABLE homeworks (
    homework_id INTEGER NOT NULL AUTO_INCREMENT, 
    center_id INTEGER NOT NULL, 
    class_id INTEGER NOT NULL, 
    teacher_id INTEGER, 
    exam_id INTEGER NOT NULL, 
    skill VARCHAR(20), 
    title VARCHAR(255), 
    due_date DATETIME, 
    created_at DATETIME, 
    PRIMARY KEY (homework_id), 
    FOREIGN KEY(center_id) REFERENCES centers (center_id), 
    FOREIGN KEY(class_id) REFERENCES classrooms (class_id), 
    FOREIGN KEY(teacher_id) REFERENCES users (user_id), 
    FOREIGN KEY(exam_id) REFERENCES exams (exam_id)
);

CREATE INDEX ix_homeworks_class_id ON homeworks (class_id);

CREATE INDEX ix_homeworks_center_id ON homeworks (center_id);

UPDATE alembic_version SET version_num='c7d8e9f0a1b2' WHERE alembic_version.version_num = 'b6c7d8e9f0a1';

-- Running upgrade c7d8e9f0a1b2 -> d8e9f0a1b2c3

ALTER TABLE homeworks ADD COLUMN parts JSON;

UPDATE alembic_version SET version_num='d8e9f0a1b2c3' WHERE alembic_version.version_num = 'c7d8e9f0a1b2';

-- Running upgrade d8e9f0a1b2c3 -> e9f0a1b2c3d4

ALTER TABLE center_memberships ADD COLUMN is_principal BOOL NOT NULL DEFAULT false;

UPDATE alembic_version SET version_num='e9f0a1b2c3d4' WHERE alembic_version.version_num = 'd8e9f0a1b2c3';

-- Running upgrade e9f0a1b2c3d4 -> f0a1b2c3d4e5

ALTER TABLE exam_results ADD COLUMN mode VARCHAR(20);

UPDATE alembic_version SET version_num='f0a1b2c3d4e5' WHERE alembic_version.version_num = 'e9f0a1b2c3d4';

-- Running upgrade f0a1b2c3d4e5 -> a1b2c3d4e5f6

ALTER TABLE users ADD COLUMN email_verified BOOL NOT NULL DEFAULT false;

UPDATE alembic_version SET version_num='a1b2c3d4e5f6' WHERE alembic_version.version_num = 'f0a1b2c3d4e5';

-- Running upgrade a1b2c3d4e5f6 -> a7b8c9d0e1f2

CREATE TABLE result_answer_snapshots (
    result_id INTEGER NOT NULL, 
    answers_gz LONGBLOB, 
    annotations_gz LONGBLOB, 
    created_at DATETIME, 
    PRIMARY KEY (result_id), 
    FOREIGN KEY(result_id) REFERENCES exam_results (result_id)
);

UPDATE alembic_version SET version_num='a7b8c9d0e1f2' WHERE alembic_version.version_num = 'a1b2c3d4e5f6';

-- Running upgrade a7b8c9d0e1f2 -> b2c3d4e5f6a7

CREATE TABLE error_reports (
    report_id INTEGER NOT NULL AUTO_INCREMENT, 
    user_id INTEGER, 
    exam_id INTEGER, 
    result_id INTEGER, 
    skill VARCHAR(20), 
    exam_title VARCHAR(255), 
    error_types JSON, 
    wrong_answer_questions VARCHAR(255), 
    mis_graded_questions VARCHAR(255), 
    description TEXT, 
    is_viewed BOOL, 
    viewed_at DATETIME, 
    created_at DATETIME, 
    PRIMARY KEY (report_id), 
    FOREIGN KEY(user_id) REFERENCES users (user_id), 
    FOREIGN KEY(exam_id) REFERENCES exams (exam_id), 
    FOREIGN KEY(result_id) REFERENCES exam_results (result_id)
);

CREATE INDEX ix_error_reports_report_id ON error_reports (report_id);

CREATE INDEX ix_error_reports_user_id ON error_reports (user_id);

CREATE INDEX ix_error_reports_exam_id ON error_reports (exam_id);

CREATE INDEX ix_error_reports_is_viewed ON error_reports (is_viewed);

UPDATE alembic_version SET version_num='b2c3d4e5f6a7' WHERE alembic_version.version_num = 'a7b8c9d0e1f2';

-- Running upgrade b2c3d4e5f6a7 -> c3d4e5f6a7b8

ALTER TABLE exam_results ADD COLUMN time_taken INTEGER;

UPDATE alembic_version SET version_num='c3d4e5f6a7b8' WHERE alembic_version.version_num = 'b2c3d4e5f6a7';

-- Running upgrade c3d4e5f6a7b8 -> d4e5f6a7b8c9

ALTER TABLE exam_sections ADD COLUMN difficulty_score FLOAT;

ALTER TABLE exam_sections ADD COLUMN difficulty_label VARCHAR(20);

ALTER TABLE exam_sections ADD COLUMN difficulty_valid_count INTEGER;

ALTER TABLE exam_sections ADD COLUMN difficulty_updated_at DATETIME;

ALTER TABLE writing_tasks ADD COLUMN difficulty_score FLOAT;

ALTER TABLE writing_tasks ADD COLUMN difficulty_label VARCHAR(20);

ALTER TABLE writing_tasks ADD COLUMN difficulty_valid_count INTEGER;

ALTER TABLE writing_tasks ADD COLUMN difficulty_updated_at DATETIME;

UPDATE alembic_version SET version_num='d4e5f6a7b8c9' WHERE alembic_version.version_num = 'c3d4e5f6a7b8';

-- Running upgrade d4e5f6a7b8c9 -> e5f6a7b8c9d0

ALTER TABLE exam_sections ADD COLUMN occurrence_count INTEGER NOT NULL DEFAULT '0';

ALTER TABLE exam_sections ADD COLUMN forecast_level INTEGER;

ALTER TABLE exam_sections ADD COLUMN forecast_last_updated DATETIME;

ALTER TABLE exam_sections ADD COLUMN skip_first_decay BOOL NOT NULL DEFAULT '0';

ALTER TABLE writing_tasks ADD COLUMN occurrence_count INTEGER NOT NULL DEFAULT '0';

ALTER TABLE writing_tasks ADD COLUMN forecast_level INTEGER;

ALTER TABLE writing_tasks ADD COLUMN forecast_last_updated DATETIME;

ALTER TABLE writing_tasks ADD COLUMN skip_first_decay BOOL NOT NULL DEFAULT '0';

CREATE TABLE system_settings (
    setting_key VARCHAR(64) NOT NULL, 
    setting_value VARCHAR(255), 
    updated_at DATETIME, 
    PRIMARY KEY (setting_key)
);

UPDATE alembic_version SET version_num='e5f6a7b8c9d0' WHERE alembic_version.version_num = 'd4e5f6a7b8c9';

-- Running upgrade e5f6a7b8c9d0 -> f6a7b8c9d0e1

ALTER TABLE questions ADD COLUMN stats_category VARCHAR(50);

UPDATE alembic_version SET version_num='f6a7b8c9d0e1' WHERE alembic_version.version_num = 'e5f6a7b8c9d0';

-- Running upgrade f6a7b8c9d0e1 -> d1e2f3a4b5c6

ALTER TABLE users ADD COLUMN is_top_performer BOOL NOT NULL DEFAULT '0';

UPDATE alembic_version SET version_num='d1e2f3a4b5c6' WHERE alembic_version.version_num = 'f6a7b8c9d0e1';

-- Running upgrade d1e2f3a4b5c6 -> e2f3a4b5c6d7

ALTER TABLE users ADD COLUMN top10_count INTEGER NOT NULL DEFAULT '0';

ALTER TABLE users ADD COLUMN hof_attempts INTEGER NOT NULL DEFAULT '0';

ALTER TABLE users ADD COLUMN hof_score INTEGER NOT NULL DEFAULT '0';

UPDATE alembic_version SET version_num='e2f3a4b5c6d7' WHERE alembic_version.version_num = 'd1e2f3a4b5c6';

-- Running upgrade e2f3a4b5c6d7 -> f3a4b5c6d7e8

ALTER TABLE users ADD COLUMN read_top10_count INTEGER NOT NULL DEFAULT '0';

ALTER TABLE users ADD COLUMN read_hof_attempts INTEGER NOT NULL DEFAULT '0';

ALTER TABLE users ADD COLUMN read_hof_score INTEGER NOT NULL DEFAULT '0';

ALTER TABLE users ADD COLUMN listen_top10_count INTEGER NOT NULL DEFAULT '0';

ALTER TABLE users ADD COLUMN listen_hof_attempts INTEGER NOT NULL DEFAULT '0';

ALTER TABLE users ADD COLUMN listen_hof_score INTEGER NOT NULL DEFAULT '0';

ALTER TABLE users ADD COLUMN write_top10_count INTEGER NOT NULL DEFAULT '0';

ALTER TABLE users ADD COLUMN write_hof_attempts INTEGER NOT NULL DEFAULT '0';

ALTER TABLE users ADD COLUMN write_hof_score INTEGER NOT NULL DEFAULT '0';

UPDATE alembic_version SET version_num='f3a4b5c6d7e8' WHERE alembic_version.version_num = 'e2f3a4b5c6d7';

-- Running upgrade f3a4b5c6d7e8 -> vocab_ws_2026

ALTER TABLE saved_vocabulary MODIFY source_type ENUM('listening','reading','writing','speaking') NOT NULL;

UPDATE alembic_version SET version_num='vocab_ws_2026' WHERE alembic_version.version_num = 'f3a4b5c6d7e8';

-- Running upgrade vocab_ws_2026 -> ai_usage_2026

CREATE TABLE ai_score_usage (
    id INTEGER NOT NULL AUTO_INCREMENT, 
    user_id INTEGER NOT NULL, 
    kind VARCHAR(20) NOT NULL DEFAULT 'grade', 
    created_at DATETIME, 
    PRIMARY KEY (id), 
    FOREIGN KEY(user_id) REFERENCES users (user_id) ON DELETE CASCADE
);

CREATE INDEX ix_ai_score_usage_user_id ON ai_score_usage (user_id);

CREATE INDEX ix_ai_score_usage_created_at ON ai_score_usage (created_at);

UPDATE alembic_version SET version_num='ai_usage_2026' WHERE alembic_version.version_num = 'vocab_ws_2026';

-- Running upgrade ai_usage_2026 -> wa_gen_2026

ALTER TABLE writing_answers ADD COLUMN ai_generated JSON;

UPDATE alembic_version SET version_num='wa_gen_2026' WHERE alembic_version.version_num = 'ai_usage_2026';

-- Running upgrade wa_gen_2026 -> wt_time_2026

ALTER TABLE writing_answers ADD COLUMN time_taken INTEGER;

UPDATE alembic_version SET version_num='wt_time_2026' WHERE alembic_version.version_num = 'wa_gen_2026';

-- Running upgrade wt_time_2026 -> wa_attempt_2026

CREATE TABLE writing_attempts (
    attempt_id INTEGER NOT NULL AUTO_INCREMENT, 
    test_id INTEGER, 
    task_id INTEGER, 
    user_id INTEGER, 
    part_number INTEGER, 
    attempt_number INTEGER, 
    answer_text LONGTEXT, 
    score FLOAT, 
    task_achievement_score FLOAT, 
    coherence_cohesion_score FLOAT, 
    lexical_resource_score FLOAT, 
    grammatical_range_score FLOAT, 
    is_ai_evaluated BOOL DEFAULT '0', 
    result JSON, 
    ai_generated JSON, 
    time_taken INTEGER, 
    created_at DATETIME, 
    PRIMARY KEY (attempt_id), 
    FOREIGN KEY(task_id) REFERENCES writing_tasks (task_id), 
    FOREIGN KEY(user_id) REFERENCES users (user_id)
);

CREATE INDEX ix_writing_attempts_user_id ON writing_attempts (user_id);

CREATE INDEX ix_writing_attempts_task_id ON writing_attempts (task_id);

CREATE INDEX ix_writing_attempts_test_id ON writing_attempts (test_id);

UPDATE alembic_version SET version_num='wa_attempt_2026' WHERE alembic_version.version_num = 'wt_time_2026';

-- Running upgrade wa_attempt_2026 -> wa_lock_2026

ALTER TABLE writing_answers ADD COLUMN locked BOOL NOT NULL DEFAULT '0';

UPDATE alembic_version SET version_num='wa_lock_2026' WHERE alembic_version.version_num = 'wa_attempt_2026';

-- Running upgrade wa_lock_2026 -> mcup_2026

CREATE TABLE monthly_cup_winners (
    winner_id INTEGER NOT NULL AUTO_INCREMENT, 
    skill VARCHAR(20), 
    year INTEGER, 
    month INTEGER, 
    user_id INTEGER, 
    `rank` INTEGER, 
    top10_count INTEGER DEFAULT '0', 
    attempts INTEGER DEFAULT '0', 
    score INTEGER DEFAULT '0', 
    time_taken INTEGER, 
    created_at DATETIME, 
    PRIMARY KEY (winner_id), 
    FOREIGN KEY(user_id) REFERENCES users (user_id)
);

CREATE INDEX ix_monthly_cup_winners_month ON monthly_cup_winners (month);

CREATE INDEX ix_monthly_cup_winners_user_id ON monthly_cup_winners (user_id);

CREATE INDEX ix_monthly_cup_winners_skill ON monthly_cup_winners (skill);

CREATE INDEX ix_monthly_cup_winners_year ON monthly_cup_winners (year);

UPDATE alembic_version SET version_num='mcup_2026' WHERE alembic_version.version_num = 'wa_lock_2026';

-- Running upgrade mcup_2026 -> centerfb_2026

ALTER TABLE exam_results ADD COLUMN tab_switches INTEGER;

ALTER TABLE exam_progress ADD COLUMN tab_switches INTEGER DEFAULT '0';

CREATE TABLE student_history_archives (
    archive_id INTEGER NOT NULL AUTO_INCREMENT, 
    center_id INTEGER, 
    user_id INTEGER NOT NULL, 
    username VARCHAR(50), 
    archived_by INTEGER, 
    num_exams INTEGER DEFAULT '0', 
    num_writing INTEGER DEFAULT '0', 
    data_gz LONGBLOB, 
    created_at DATETIME, 
    PRIMARY KEY (archive_id), 
    FOREIGN KEY(center_id) REFERENCES centers (center_id), 
    FOREIGN KEY(user_id) REFERENCES users (user_id)
);

CREATE INDEX ix_student_history_archives_archive_id ON student_history_archives (archive_id);

CREATE INDEX ix_student_history_archives_user_id ON student_history_archives (user_id);

CREATE INDEX ix_student_history_archives_center_id ON student_history_archives (center_id);

UPDATE alembic_version SET version_num='centerfb_2026' WHERE alembic_version.version_num = 'mcup_2026';

-- Running upgrade centerfb_2026 -> studysess_2026

CREATE TABLE study_sessions (
    session_id INTEGER NOT NULL AUTO_INCREMENT, 
    center_id INTEGER, 
    class_id INTEGER NOT NULL, 
    teacher_id INTEGER, 
    teacher_name VARCHAR(50), 
    meet_url VARCHAR(500), 
    status VARCHAR(20) DEFAULT 'active', 
    current_task_id INTEGER, 
    scheduled_start DATETIME, 
    scheduled_end DATETIME, 
    started_at DATETIME, 
    ended_at DATETIME, 
    created_at DATETIME, 
    PRIMARY KEY (session_id), 
    FOREIGN KEY(center_id) REFERENCES centers (center_id), 
    FOREIGN KEY(class_id) REFERENCES classrooms (class_id), 
    FOREIGN KEY(teacher_id) REFERENCES users (user_id)
);

CREATE INDEX ix_study_sessions_status ON study_sessions (status);

CREATE INDEX ix_study_sessions_center_id ON study_sessions (center_id);

CREATE INDEX ix_study_sessions_session_id ON study_sessions (session_id);

CREATE INDEX ix_study_sessions_class_id ON study_sessions (class_id);

CREATE TABLE study_session_tasks (
    task_id INTEGER NOT NULL AUTO_INCREMENT, 
    session_id INTEGER NOT NULL, 
    skill VARCHAR(20), 
    exam_id INTEGER, 
    title VARCHAR(255), 
    parts JSON, 
    order_index INTEGER DEFAULT '0', 
    status VARCHAR(20) DEFAULT 'pending', 
    started_at DATETIME, 
    ended_at DATETIME, 
    PRIMARY KEY (task_id), 
    FOREIGN KEY(session_id) REFERENCES study_sessions (session_id)
);

CREATE INDEX ix_study_session_tasks_task_id ON study_session_tasks (task_id);

CREATE INDEX ix_study_session_tasks_session_id ON study_session_tasks (session_id);

UPDATE alembic_version SET version_num='studysess_2026' WHERE alembic_version.version_num = 'centerfb_2026';

-- Running upgrade studysess_2026 -> coursedays_2026

ALTER TABLE users ADD COLUMN course_days INTEGER;

UPDATE alembic_version SET version_num='coursedays_2026' WHERE alembic_version.version_num = 'studysess_2026';

-- Running upgrade coursedays_2026 -> lisalign_2026

CREATE TABLE listening_alignments (
    section_id INTEGER NOT NULL, 
    audio_duration FLOAT, 
    token_count INTEGER, 
    aligned_count INTEGER, 
    coverage_pct FLOAT, 
    model VARCHAR(64), 
    data_gz LONGBLOB, 
    created_at DATETIME, 
    PRIMARY KEY (section_id), 
    FOREIGN KEY(section_id) REFERENCES exam_sections (section_id) ON DELETE CASCADE
);

UPDATE alembic_version SET version_num='lisalign_2026' WHERE alembic_version.version_num = 'coursedays_2026';

-- Running upgrade lisalign_2026 -> candict_2026

ALTER TABLE users ADD COLUMN can_dictation BOOL NOT NULL DEFAULT '0';

UPDATE alembic_version SET version_num='candict_2026' WHERE alembic_version.version_num = 'lisalign_2026';

-- Running upgrade candict_2026 -> lisfp_2026

ALTER TABLE listening_alignments ADD COLUMN source_fingerprint VARCHAR(64);

UPDATE alembic_version SET version_num='lisfp_2026' WHERE alembic_version.version_num = 'candict_2026';

-- Running upgrade lisfp_2026 -> liscue_2026

CREATE TABLE listening_cue_overrides (
    question_id INTEGER NOT NULL, 
    section_id INTEGER, 
    start_time FLOAT NOT NULL, 
    end_time FLOAT NOT NULL, 
    updated_by INTEGER, 
    updated_at DATETIME, 
    PRIMARY KEY (question_id), 
    FOREIGN KEY(question_id) REFERENCES questions (question_id) ON DELETE CASCADE, 
    FOREIGN KEY(section_id) REFERENCES exam_sections (section_id) ON DELETE CASCADE, 
    FOREIGN KEY(updated_by) REFERENCES users (user_id)
);

CREATE INDEX ix_listening_cue_overrides_section_id ON listening_cue_overrides (section_id);

UPDATE alembic_version SET version_num='liscue_2026' WHERE alembic_version.version_num = 'lisfp_2026';

-- Running upgrade liscue_2026 -> spk1_2026

CREATE TABLE speaking_topics (
    topic_id INTEGER NOT NULL AUTO_INCREMENT, 
    part ENUM('part1','part2') NOT NULL, 
    title VARCHAR(255) NOT NULL, 
    category ENUM('place','people','education','recreation','object','others'), 
    cue_card LONGTEXT, 
    work_study ENUM('work','study','neutral') NOT NULL DEFAULT 'neutral', 
    is_important BOOL NOT NULL DEFAULT '0', 
    occurrence_count INTEGER NOT NULL DEFAULT '0', 
    forecast_level INTEGER, 
    appear_from DATE, 
    appear_to DATE, 
    is_active BOOL NOT NULL DEFAULT '1', 
    last_updated DATETIME, 
    created_by INTEGER, 
    created_at DATETIME, 
    PRIMARY KEY (topic_id), 
    FOREIGN KEY(created_by) REFERENCES users (user_id)
);

CREATE INDEX ix_speaking_topics_part ON speaking_topics (part);

CREATE TABLE speaking_questions (
    question_id INTEGER NOT NULL AUTO_INCREMENT, 
    topic_id INTEGER NOT NULL, 
    part ENUM('part1','part2_followup','part3') NOT NULL, 
    order_index INTEGER NOT NULL DEFAULT '0', 
    content LONGTEXT NOT NULL, 
    gen_status ENUM('pending','running','done','failed') NOT NULL DEFAULT 'pending', 
    gen_error VARCHAR(255), 
    created_at DATETIME, 
    PRIMARY KEY (question_id), 
    FOREIGN KEY(topic_id) REFERENCES speaking_topics (topic_id) ON DELETE CASCADE
);

CREATE INDEX ix_speaking_questions_topic_id ON speaking_questions (topic_id);

CREATE INDEX ix_speaking_questions_part ON speaking_questions (part);

CREATE TABLE speaking_suggestions (
    question_id INTEGER NOT NULL, 
    outline JSON, 
    samples JSON, 
    model VARCHAR(64), 
    created_at DATETIME, 
    PRIMARY KEY (question_id), 
    FOREIGN KEY(question_id) REFERENCES speaking_questions (question_id) ON DELETE CASCADE
);

CREATE TABLE speaking_vocabularies (
    id INTEGER NOT NULL AUTO_INCREMENT, 
    question_id INTEGER NOT NULL, 
    band_level ENUM('4.5-5.5','6.0-6.5','7.0-7.5','8.0-9.0') NOT NULL, 
    order_index INTEGER NOT NULL DEFAULT '0', 
    term VARCHAR(255) NOT NULL, 
    meaning_vi VARCHAR(500), 
    example TEXT, 
    PRIMARY KEY (id), 
    FOREIGN KEY(question_id) REFERENCES speaking_questions (question_id) ON DELETE CASCADE
);

CREATE INDEX ix_speaking_vocabularies_question_id ON speaking_vocabularies (question_id);

UPDATE alembic_version SET version_num='spk1_2026' WHERE alembic_version.version_num = 'liscue_2026';

-- Running upgrade spk1_2026 -> spk2_2026

ALTER TABLE speaking_questions MODIFY part ENUM('part1','part2','part2_followup','part3') NOT NULL;

UPDATE alembic_version SET version_num='spk2_2026' WHERE alembic_version.version_num = 'spk1_2026';

-- Running upgrade spk2_2026 -> spk3_2026

CREATE TABLE speaking_tts (
    id INTEGER NOT NULL AUTO_INCREMENT, 
    cache_key VARCHAR(64) NOT NULL, 
    voice VARCHAR(32) NOT NULL, 
    fingerprint VARCHAR(64) NOT NULL, 
    audio LONGBLOB, 
    duration_ms INTEGER, 
    created_at DATETIME, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_speaking_tts_key_voice UNIQUE (cache_key, voice)
);

CREATE INDEX ix_speaking_tts_cache_key ON speaking_tts (cache_key);

CREATE INDEX ix_speaking_tts_id ON speaking_tts (id);

UPDATE alembic_version SET version_num='spk3_2026' WHERE alembic_version.version_num = 'spk2_2026';

-- Running upgrade spk3_2026 -> spk4_2026

CREATE TABLE speaking_attempts (
    attempt_id INTEGER NOT NULL AUTO_INCREMENT, 
    user_id INTEGER NOT NULL, 
    test_type ENUM('full','part1','part2','part3') NOT NULL, 
    mode ENUM('practice','mock') NOT NULL, 
    input_method ENUM('micro','subtitle') NOT NULL DEFAULT 'micro', 
    occupation ENUM('student','working'), 
    voice VARCHAR(32), 
    use_forecast BOOL NOT NULL DEFAULT '0', 
    forecast_month DATE, 
    exam_priority ENUM('default','done','undone') NOT NULL DEFAULT 'default', 
    plan JSON, 
    status ENUM('in_progress','completed','abandoned','terminated','interrupted') NOT NULL DEFAULT 'in_progress', 
    end_reason VARCHAR(255), 
    submitted BOOL NOT NULL DEFAULT '0', 
    overall_band FLOAT, 
    criteria JSON, 
    part_results JSON, 
    graded_at DATETIME, 
    started_at DATETIME, 
    ended_at DATETIME, 
    PRIMARY KEY (attempt_id), 
    FOREIGN KEY(user_id) REFERENCES users (user_id) ON DELETE CASCADE
);

CREATE INDEX ix_speaking_attempts_user_id ON speaking_attempts (user_id);

CREATE TABLE speaking_attempt_answers (
    answer_id INTEGER NOT NULL AUTO_INCREMENT, 
    attempt_id INTEGER NOT NULL, 
    question_id INTEGER, 
    topic_id INTEGER, 
    part ENUM('part1','part2','part2_followup','part3') NOT NULL, 
    order_index INTEGER NOT NULL DEFAULT '0', 
    topic_title VARCHAR(255), 
    question_text LONGTEXT, 
    is_ai_followup BOOL NOT NULL DEFAULT '0', 
    first_text LONGTEXT, 
    retry_text LONGTEXT, 
    first_audio VARCHAR(255), 
    retry_audio VARCHAR(255), 
    used_retry BOOL NOT NULL DEFAULT '0', 
    answer_status ENUM('answered','no_answer','auto_skipped','time_expired') NOT NULL DEFAULT 'no_answer', 
    duration_ms INTEGER, 
    scores JSON, 
    feedback JSON, 
    created_at DATETIME, 
    PRIMARY KEY (answer_id), 
    FOREIGN KEY(attempt_id) REFERENCES speaking_attempts (attempt_id) ON DELETE CASCADE, 
    FOREIGN KEY(question_id) REFERENCES speaking_questions (question_id) ON DELETE SET NULL, 
    FOREIGN KEY(topic_id) REFERENCES speaking_topics (topic_id) ON DELETE SET NULL
);

CREATE INDEX ix_speaking_attempt_answers_attempt_id ON speaking_attempt_answers (attempt_id);

CREATE INDEX ix_speaking_attempt_answers_question_id ON speaking_attempt_answers (question_id);

CREATE INDEX ix_speaking_attempt_answers_topic_id ON speaking_attempt_answers (topic_id);

CREATE INDEX ix_speaking_attempt_answers_part ON speaking_attempt_answers (part);

UPDATE alembic_version SET version_num='spk4_2026' WHERE alembic_version.version_num = 'spk3_2026';

-- Running upgrade spk4_2026 -> spk5_2026

ALTER TABLE speaking_attempts ADD COLUMN grade_status ENUM('pending','running','done','failed') NOT NULL DEFAULT 'pending';

ALTER TABLE speaking_attempts ADD COLUMN grade_error VARCHAR(255);

UPDATE alembic_version SET version_num='spk5_2026' WHERE alembic_version.version_num = 'spk4_2026';

-- Running upgrade spk5_2026 -> spk6_2026

ALTER TABLE speaking_attempt_answers ADD COLUMN audio_expired BOOL NOT NULL DEFAULT '0';

UPDATE alembic_version SET version_num='spk6_2026' WHERE alembic_version.version_num = 'spk5_2026';

-- Running upgrade spk6_2026 -> spk7_2026

CREATE TABLE speaking_question_progress (
    progress_id INTEGER NOT NULL AUTO_INCREMENT, 
    user_id INTEGER NOT NULL, 
    question_id INTEGER NOT NULL, 
    viewed_at DATETIME, 
    sample_answer_id INTEGER, 
    updated_at DATETIME, 
    PRIMARY KEY (progress_id), 
    FOREIGN KEY(user_id) REFERENCES users (user_id) ON DELETE CASCADE, 
    FOREIGN KEY(question_id) REFERENCES speaking_questions (question_id) ON DELETE CASCADE, 
    FOREIGN KEY(sample_answer_id) REFERENCES speaking_attempt_answers (answer_id) ON DELETE SET NULL, 
    CONSTRAINT uq_speaking_progress_user_question UNIQUE (user_id, question_id)
);

CREATE INDEX ix_speaking_question_progress_progress_id ON speaking_question_progress (progress_id);

CREATE INDEX ix_speaking_question_progress_user_id ON speaking_question_progress (user_id);

CREATE INDEX ix_speaking_question_progress_question_id ON speaking_question_progress (question_id);

UPDATE alembic_version SET version_num='spk7_2026' WHERE alembic_version.version_num = 'spk6_2026';

-- Running upgrade spk7_2026 -> spk8_2026

ALTER TABLE speaking_question_progress ADD COLUMN sample_text TEXT;

UPDATE alembic_version SET version_num='spk8_2026' WHERE alembic_version.version_num = 'spk7_2026';

-- Running upgrade spk8_2026 -> spk9_2026

CREATE TABLE speaking_daily_usage (
    usage_id INTEGER NOT NULL AUTO_INCREMENT, 
    user_id INTEGER NOT NULL, 
    day DATE NOT NULL, 
    feature VARCHAR(32) NOT NULL, 
    used INTEGER NOT NULL DEFAULT '0', 
    updated_at DATETIME, 
    PRIMARY KEY (usage_id), 
    FOREIGN KEY(user_id) REFERENCES users (user_id) ON DELETE CASCADE, 
    CONSTRAINT uq_speaking_usage_user_day_feature UNIQUE (user_id, day, feature)
);

CREATE INDEX ix_speaking_daily_usage_usage_id ON speaking_daily_usage (usage_id);

CREATE INDEX ix_speaking_daily_usage_user_id ON speaking_daily_usage (user_id);

CREATE INDEX ix_speaking_daily_usage_day ON speaking_daily_usage (day);

UPDATE alembic_version SET version_num='spk9_2026' WHERE alembic_version.version_num = 'spk8_2026';

-- Running upgrade spk9_2026 -> spk10_2026

ALTER TABLE speaking_topics ADD COLUMN difficulty_score FLOAT;

ALTER TABLE speaking_topics ADD COLUMN difficulty_label VARCHAR(16);

ALTER TABLE speaking_topics ADD COLUMN difficulty_valid_count INTEGER NOT NULL DEFAULT '0';

ALTER TABLE speaking_topics ADD COLUMN difficulty_updated_at DATETIME;

UPDATE alembic_version SET version_num='spk10_2026' WHERE alembic_version.version_num = 'spk9_2026';

-- Running upgrade spk10_2026 -> spk11_2026

CREATE TABLE speaking_pron_units (
    unit_id INTEGER NOT NULL AUTO_INCREMENT, 
    title VARCHAR(255) NOT NULL, 
    theory TEXT, 
    order_index INTEGER NOT NULL DEFAULT '0', 
    is_published BOOL NOT NULL DEFAULT '0', 
    created_at DATETIME, 
    updated_at DATETIME, 
    PRIMARY KEY (unit_id)
);

CREATE INDEX ix_speaking_pron_units_unit_id ON speaking_pron_units (unit_id);

CREATE TABLE speaking_pron_items (
    item_id INTEGER NOT NULL AUTO_INCREMENT, 
    unit_id INTEGER NOT NULL, 
    user_id INTEGER, 
    kind ENUM('word','sentence') NOT NULL DEFAULT 'word', 
    content VARCHAR(500) NOT NULL, 
    note VARCHAR(500), 
    order_index INTEGER NOT NULL DEFAULT '0', 
    created_at DATETIME, 
    PRIMARY KEY (item_id), 
    FOREIGN KEY(unit_id) REFERENCES speaking_pron_units (unit_id) ON DELETE CASCADE, 
    FOREIGN KEY(user_id) REFERENCES users (user_id) ON DELETE CASCADE
);

CREATE INDEX ix_speaking_pron_items_item_id ON speaking_pron_items (item_id);

CREATE INDEX ix_speaking_pron_items_unit_id ON speaking_pron_items (unit_id);

CREATE INDEX ix_speaking_pron_items_user_id ON speaking_pron_items (user_id);

UPDATE alembic_version SET version_num='spk11_2026' WHERE alembic_version.version_num = 'spk10_2026';

-- Running upgrade spk11_2026 -> hwspk_2026

ALTER TABLE homeworks MODIFY exam_id INTEGER NULL;

ALTER TABLE homeworks ADD COLUMN speaking_topic_id INTEGER;

ALTER TABLE homeworks ADD COLUMN speaking_section VARCHAR(20);

ALTER TABLE homeworks ADD CONSTRAINT fk_homeworks_speaking_topic FOREIGN KEY(speaking_topic_id) REFERENCES speaking_topics (topic_id);

CREATE INDEX ix_homeworks_speaking_topic_id ON homeworks (speaking_topic_id);

UPDATE alembic_version SET version_num='hwspk_2026' WHERE alembic_version.version_num = 'spk11_2026';

-- Running upgrade hwspk_2026 -> sspha2_2026

CREATE TABLE study_session_events (
    event_id INTEGER NOT NULL AUTO_INCREMENT, 
    session_id INTEGER NOT NULL, 
    kind VARCHAR(24) NOT NULL, 
    task_id INTEGER, 
    task_title VARCHAR(255), 
    note VARCHAR(255), 
    at DATETIME, 
    PRIMARY KEY (event_id), 
    FOREIGN KEY(session_id) REFERENCES study_sessions (session_id)
);

CREATE INDEX ix_study_session_events_session_id ON study_session_events (session_id);

CREATE INDEX ix_study_session_events_event_id ON study_session_events (event_id);

CREATE INDEX ix_study_session_events_at ON study_session_events (at);

CREATE TABLE study_session_attendance (
    attendance_id INTEGER NOT NULL AUTO_INCREMENT, 
    session_id INTEGER NOT NULL, 
    user_id INTEGER NOT NULL, 
    joined_at DATETIME, 
    last_seen_at DATETIME, 
    online_seconds INTEGER DEFAULT '0', 
    PRIMARY KEY (attendance_id), 
    FOREIGN KEY(session_id) REFERENCES study_sessions (session_id), 
    FOREIGN KEY(user_id) REFERENCES users (user_id)
);

CREATE INDEX ix_study_session_attendance_user_id ON study_session_attendance (user_id);

CREATE INDEX ix_study_session_attendance_session_id ON study_session_attendance (session_id);

CREATE INDEX ix_study_session_attendance_attendance_id ON study_session_attendance (attendance_id);

ALTER TABLE study_session_attendance ADD CONSTRAINT uq_ss_attendance_session_user UNIQUE (session_id, user_id);

UPDATE alembic_version SET version_num='sspha2_2026' WHERE alembic_version.version_num = 'hwspk_2026';

-- Running upgrade sspha2_2026 -> sstaskspk_2026

ALTER TABLE study_session_tasks ADD COLUMN speaking_topic_id INTEGER;

ALTER TABLE study_session_tasks ADD COLUMN speaking_section VARCHAR(20);

UPDATE alembic_version SET version_num='sstaskspk_2026' WHERE alembic_version.version_num = 'sspha2_2026';

-- Running upgrade e5a1c7d2f9b4, sstaskspk_2026 -> f1a0c0de2026

DELETE FROM alembic_version WHERE alembic_version.version_num = 'e5a1c7d2f9b4';

UPDATE alembic_version SET version_num='f1a0c0de2026' WHERE alembic_version.version_num = 'sstaskspk_2026';

-- Running upgrade f1a0c0de2026 -> f2b1e0a7c3d9

CREATE TABLE email_broadcasts (
    id INTEGER NOT NULL AUTO_INCREMENT, 
    subject VARCHAR(500) NOT NULL, 
    body_html LONGTEXT NOT NULL, 
    status ENUM('pending','sending','completed','failed'), 
    target_filter VARCHAR(50), 
    total_recipients INTEGER, 
    sent_count INTEGER, 
    failed_count INTEGER, 
    created_by INTEGER, 
    created_at DATETIME, 
    completed_at DATETIME, 
    PRIMARY KEY (id), 
    FOREIGN KEY(created_by) REFERENCES users (user_id)
);

UPDATE alembic_version SET version_num='f2b1e0a7c3d9' WHERE alembic_version.version_num = 'f1a0c0de2026';


-- Normalise alembic_version to the single new head, whatever it held before
-- (prod schema is confirmed at e5a1c7d2f9b4: payos_order_code exists).
DELETE FROM alembic_version WHERE version_num <> 'f2b1e0a7c3d9';
INSERT INTO alembic_version (version_num)
  SELECT 'f2b1e0a7c3d9' FROM DUAL
  WHERE NOT EXISTS (SELECT 1 FROM alembic_version WHERE version_num = 'f2b1e0a7c3d9');

-- Post-checks (expect: 1 row, 1 row, f2b1e0a7c3d9)
SHOW TABLES LIKE 'announcements';
SHOW COLUMNS FROM users LIKE 'email_verified';
SELECT version_num FROM alembic_version;
