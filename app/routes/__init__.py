from fastapi import APIRouter
from .admin.auth import router as admin_router
from .admin.admin_actions import router as admin_actions_router
from .admin.reading_admin import router as reading_admin_router
from .admin.vip_management import router as admin_vip_router
from .admin.dictation_admin import router as dictation_admin_router
from .student.reading import router as student_reading_router
from .student.student_actions import router as student_actions_router
from .customer.vip_packages import router as customer_vip_router
from .customer.lemonsqueezy_webhook import router as lemonsqueezy_webhook_router
from .customer.payos_webhook import router as payos_webhook_router
from .AI.ai import router as ai_router
from .auth import router as auth_router
from .admin.multiple_actions import router as multiple_actions_router
from .student.multiple_actions import router as student_multiple_actions_router
from .student.vocabulary_routes import router as vocabulary_router
from .student.dictation_routes import router as student_dictation_router
from .student.translate_routes import router as student_translate_router
from .student.error_report_routes import router as error_report_router
from .student.exam_progress_routes import router as exam_progress_router
from .admin.error_report_admin import router as error_report_admin_router
from .admin.announcement_admin import router as announcement_admin_router
from .customer.announcements import router as announcements_router
from .public_seo import router as public_seo_router
from .admin.marketing import router as admin_marketing_router
from .admin.difficulty_admin import router as difficulty_admin_router
from .admin.forecast_auto_admin import router as forecast_auto_admin_router
from .admin.question_type_admin import router as question_type_admin_router
from .student.overview_routes import router as student_overview_router
from .student.leaderboard_routes import router as leaderboard_router
from .admin.top_performers_admin import router as top_performers_admin_router
from .admin.monthly_cup_admin import router as monthly_cup_admin_router

router = APIRouter()

# Include the admin routes
router.include_router(admin_router)
router.include_router(admin_actions_router, prefix="/admin", tags=["admin"])
router.include_router(reading_admin_router, prefix="/admin/reading", tags=["reading"])
router.include_router(admin_vip_router, prefix="/admin/vip", tags=["admin-vip"])
router.include_router(dictation_admin_router, prefix="/admin", tags=["admin-dictation"])
router.include_router(admin_marketing_router, prefix="/admin/marketing", tags=["admin-marketing"])
router.include_router(student_actions_router, prefix="/student", tags=["student"])
router.include_router(student_reading_router, prefix="/student/reading", tags=["student-reading"])
router.include_router(customer_vip_router, prefix="/customer/vip", tags=["customer-vip"])
router.include_router(lemonsqueezy_webhook_router, prefix="/customer/vip", tags=["lemonsqueezy-webhook"])
router.include_router(payos_webhook_router, prefix="/customer/vip", tags=["payos-webhook"])
router.include_router(student_multiple_actions_router, prefix="/student/action", tags=["student-actions"])
router.include_router(multiple_actions_router, prefix="/admin/action", tags=["admin-actions"])
router.include_router(ai_router, prefix="/ai", tags=["ai"])
router.include_router(auth_router, tags=["auth"])
router.include_router(vocabulary_router, prefix="/student", tags=["vocabulary"])
router.include_router(student_dictation_router, prefix="/student", tags=["student-dictation"])
router.include_router(student_translate_router, prefix="/student", tags=["student-translate"])
router.include_router(error_report_router, prefix="/student", tags=["error-report"])
# Exam-room heartbeat (/student/exam/heartbeat[/stop]) — feeds ExamResult.tab_switches.
router.include_router(exam_progress_router, prefix="/student", tags=["exam-progress"])
router.include_router(error_report_admin_router, prefix="/admin", tags=["admin-error-report"])
# Homepage announcements: admin CRUD (/admin/announcements) + public no-auth
# read (/announcements) for the student landing page.
router.include_router(announcement_admin_router, prefix="/admin", tags=["admin-announcement"])
router.include_router(announcements_router, tags=["announcements"])
router.include_router(public_seo_router, prefix="/public", tags=["public-seo"])
# VN F5 port: difficulty recompute trigger (/admin/difficulty/recompute), auto-forecast
# occurrence admin (/admin/forecast-auto/*), question typing (/admin/question-typing/*)
# and the student Results Overview (/student/results-overview).
router.include_router(difficulty_admin_router, prefix="/admin", tags=["admin-difficulty"])
router.include_router(forecast_auto_admin_router, prefix="/admin", tags=["admin-forecast-auto"])
router.include_router(question_type_admin_router, prefix="/admin", tags=["admin-question-typing"])
router.include_router(student_overview_router, prefix="/student", tags=["student-overview"])
# VN F4 port: per-test leaderboard, Monthly Cup, Hall of Fame (/student/leaderboard/{id},
# /student/monthly-cup, /student/hall-of-fame) + job triggers (no cron on Koyeb):
# /admin/top-performers/recompute, /admin/monthly-cup/snapshot, /admin/monthly-cup/backfill.
router.include_router(leaderboard_router, prefix="/student", tags=["leaderboard"])
router.include_router(top_performers_admin_router, prefix="/admin", tags=["admin-top-performers"])
router.include_router(monthly_cup_admin_router, prefix="/admin", tags=["admin-monthly-cup"])
