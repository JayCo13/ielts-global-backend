"""Chấm một bài Speaking (docs/speaking-spec.md §5).

Một lần gọi AI cho MỘT part, gửi kèm toàn bộ audio của part đó (quyết định #4): 3 lần gọi
cho cả bài thay vì hơn hai chục. Gemini vì nó nghe được audio thật — phát âm bắt buộc phải
lấy bằng chứng từ audio, model chỉ đọc chữ không làm được (quyết định #1, §5.3).

Phép tính band nằm ở đây chứ không để model tự cộng: §5.1 có hai bước làm tròn riêng và
chỗ này rất dễ sai. Model chỉ chấm 4 tiêu chí của từng part; mọi phép trung bình và làm
tròn do code làm.

Global port: all commentary the model writes is English (VN asks for Vietnamese), and
recordings are read from R2 through app/utils/speaking_storage.py instead of local disk.
"""
import logging
import math
import os
from typing import List, Optional

from app.utils import gemini_client

logger = logging.getLogger(__name__)

# Model đầy đủ chứ không phải Flash-Lite: bài chấm phải NGHE, không chỉ đọc.
MODEL = "gemini-flash-latest"
MAX_OUTPUT_TOKENS = 32768
# Trần dung lượng audio gửi kèm một lần gọi. Gemini nhận tối đa ~20MB cả request; opus
# 16kbps thì một part dài nhất cũng chỉ vài trăm KB, nên chạm trần nghĩa là có gì đó bất
# thường — cắt bớt còn hơn để cả lần chấm hỏng.
MAX_AUDIO_BYTES = 12 * 1024 * 1024

CRITERIA = ('pronunciation', 'fluency_coherence', 'lexical_resource', 'grammar')
CRITERION_VI = {
    'pronunciation': 'Pronunciation',
    'fluency_coherence': 'Fluency & Coherence',
    'lexical_resource': 'Lexical Resource',
    'grammar': 'Grammatical Range & Accuracy',
}
# §5.5 — khung chẩn đoán. Đây là thứ để model bám vào khi nêu bằng chứng, KHÔNG phải
# danh sách để chấm điểm từng mục.
SUBFACTORS = {
    'pronunciation': ("Individual Sounds, Word Stress, Sentence Stress, Rhythm, "
                      "Connected Speech, Intonation, Chunking, Intelligibility"),
    'fluency_coherence': ("Speech Flow, Speech Rate, Pausing, Hesitation, Repetition, "
                          "Self-correction, Continuity, Coherence, Logical Development, "
                          "Cohesion, Discourse Markers, Relevance, Directness, "
                          "Development, Naturalness"),
    'lexical_resource': ("Vocabulary Range, Precision, Word Choice, Collocations, "
                         "Idiomatic Language, Paraphrasing, Topic-specific Vocabulary, "
                         "Repetition, Word Formation, Accuracy, Flexibility, Natural Usage"),
    'grammar': ("Sentence Structure Range, Simple/Compound/Complex, Subordinate Clauses, "
                "Tenses, Conditionals, Relative Clauses, Passive, Articles/Determiners, "
                "Prepositions, Subject-Verb Agreement, Word Order, Verb Forms, "
                "Grammar Errors, Error Impact"),
}

# Nhóm câu thành 3 part để chấm. Câu nối của Part 2 chấm chung với phần nói dài — nó là
# một phần của Part 2 trong bài thi thật.
PART_GROUPS = (
    ('part1', 'Part 1', ('part1',)),
    ('part2', 'Part 2', ('part2', 'part2_followup')),
    ('part3', 'Part 3', ('part3',)),
)

MIME_BY_EXT = {'.ogg': 'audio/ogg', '.mp3': 'audio/mpeg', '.wav': 'audio/wav',
               '.m4a': 'audio/mp4', '.mp4': 'audio/mp4', '.aac': 'audio/aac',
               '.flac': 'audio/flac', '.webm': 'audio/webm'}

INSUFFICIENT = 'Insufficient evidence'


class GradeError(Exception):
    """Không chấm được part này."""


# ── Phép tính band (§5.1) ──────────────────────────────────────────────────────────────

def round_band(raw: float) -> float:
    """x.00–x.25 → x.0 · >x.25–x.75 → x.5 · >x.75 → (x+1).0

    Đúng bảng trong §5.1: 6.25→6.0, 6.26→6.5, 6.50→6.5, 6.75→6.5, 6.76→7.0.
    Làm tròn phần lẻ tới 6 chữ số trước khi so, nếu không 6.25 lưu dưới dạng
    6.249999… sẽ rơi nhầm nhánh.
    """
    whole = math.floor(raw)
    frac = round(raw - whole, 6)
    if frac <= 0.25:
        return float(whole)
    if frac <= 0.75:
        return whole + 0.5
    return float(whole + 1)


# ── Hiệu chỉnh thang điểm (tài liệu "Điều chỉnh lại chấm điểm", 15/09) ────────────────
#
# Hai thay đổi, cả hai đều nằm sau CÙNG một công tắc để bật/tắt được mà không phải revert
# code: đặt `SPEAKING_CALIBRATION=0` trong ielts-practice-backend/.env rồi khởi động lại
# backend là quay về ĐÚNG cách chấm cũ. Bài đã chấm rồi không đổi — điểm lưu trong DB, chỉ
# lần chấm sau mới theo luật mới.
#
#   (1) Điểm CUỐI của từng tiêu chí phải là SỐ NGUYÊN (RULE 1-2 của tài liệu). Nửa band chỉ
#       được tồn tại như "đánh giá nội bộ" để nói rằng bài nằm giữa hai band, và khi đó
#       lấy band CAO HƠN (RULE 4-8: 5.5→6, 6.5→7, 7.5→8, 8.5→9).
#   (2) Band nào là TRUNG BÌNH của các tiêu chí (Overall, Part Band, điểm một câu) thì làm
#       tròn theo đúng luật IELTS: .25 lên nửa band, .75 lên band nguyên kế tiếp. Luật cũ
#       ở §5.1 của docs/speaking-spec.md làm ngược — 6.25→6.0 và 6.75→6.5 — nên bài nào rơi
#       vào hai mốc đó cũng mất nửa band, đúng cái "chấm thấp hơn điểm thi thực tế khoảng
#       0.5 band" mà tài liệu mở đầu bằng. Writing đã chấm theo luật IELTS thật từ trước
#       (`student_actions._round_ielts_overall`), nên đây cũng là dọn cho hai kỹ năng nói
#       cùng một ngôn ngữ.
#
# Công tắc `SPEAKING_CALIBRATION` (ielts-practice-backend/.env, đổi xong phải
# `docker compose up -d --force-recreate backend` — restart không nạp lại env):
#   0 / off  → tắt hẳn, chấm như trước 15/09 (bảng §5.1 cũ, cho phép .5 ở tiêu chí)
#   v1       → tài liệu "Điều chỉnh lại chấm điểm" 15/09: model trả .5 khi nằm giữa hai
#              band, CODE đẩy lên band trên theo ngưỡng
#   v2       → tài liệu "Điều chỉnh điểm version 2" 19/09: MODEL tự chốt số nguyên theo
#              bằng chứng (CALIBRATION_V2), code chỉ chặn
#   v3 / 1 / để trống (mặc định) → tài liệu "Điều chỉnh điểm version 3" 21/09: như v2
#              nhưng mục 10 nở thành 17 mục con và các mốc chuyển band 23-26 viết lại —
#              xem CALIBRATION_V3
_raw_switch = os.getenv('SPEAKING_CALIBRATION', 'v3').strip().lower()
if _raw_switch in ('0', 'false', 'off', 'no'):
    CALIBRATION_VERSION = None
elif _raw_switch in ('v1', 'v2'):
    CALIBRATION_VERSION = _raw_switch
else:
    CALIBRATION_VERSION = 'v3'
CALIBRATION_ENABLED = CALIBRATION_VERSION is not None
# Các bản có khối luật riêng gắn vào prompt; bản nào cũng chốt điểm tiêu chí về SỐ NGUYÊN.
INTEGER_BAND_VERSIONS = ('v2', 'v3')


def calibrate_criterion(raw) -> Optional[float]:
    """Điểm cuối của MỘT TIÊU CHÍ: số nguyên, và nằm giữa hai band thì lấy band trên.

    Vì sao làm ở code chứ không phó mặc prompt: đây là ràng buộc cứng ("MUST be one of
    4,5,6,7,8,9"), mà model thì thỉnh thoảng vẫn trả 6.5 dù prompt cấm. Chặn ở đây thì
    không có đường nào lọt ra giao diện.

    Làm tròn nửa lên: 5.4→5, 5.5→6, 5.6→6. Model được phép trả .5 để nói "bài này thật sự
    nằm giữa hai band" (RULE 3), và luật ưu tiên band cao hơn biến nó thành band trên.
    Prompt cấm bịa ra .5 chỉ để đẩy điểm (RULE 9, 23) — đó là vế còn lại của cặp này.
    """
    if raw is None:
        return None
    try:
        band = float(raw)
    except (TypeError, ValueError):
        return None
    whole = math.floor(band)
    frac = round(band - whole, 6)
    result = whole + 1 if frac >= 0.5 else whole
    return float(max(0, min(9, result)))


def round_overall(raw: float) -> float:
    """Làm tròn một con số vốn là TRUNG BÌNH các tiêu chí, theo luật IELTS thật.

    x.00–<x.25 → x.0 · x.25–<x.75 → x.5 · ≥x.75 → (x+1).0
    VD: 5.75→6.0, 6.25→6.5. Giống hệt `student_actions._round_ielts_overall` của Writing.

    Khác `round_band` ở đúng hai mốc .25 và .75 — nhưng khi cả bốn tiêu chí đã là số
    nguyên thì trung bình của chúng CHỈ có thể rơi vào .0/.25/.5/.75, nên hai mốc đó là
    một nửa số bài. Đó là lý do phải sửa cùng lúc với (1), sửa mỗi (1) thì phần điểm vừa
    nâng lên ở tiêu chí lại bị luật làm tròn cũ ăn mất.
    """
    whole = math.floor(raw)
    frac = round(raw - whole, 6)
    if frac < 0.25:
        band = float(whole)
    elif frac < 0.75:
        band = whole + 0.5
    else:
        band = float(whole + 1)
    return min(9.0, band)


def band_for_average(raw: float) -> float:
    """Band của một con số là trung bình các tiêu chí: Overall, Part Band, điểm một câu.

    Một cửa duy nhất cho mọi chỗ tính loại band này, để bật/tắt hiệu chỉnh là cả hệ thống
    đổi cùng lúc. Trước đây mỗi nơi tự gọi `round_band`, sửa sót một chỗ là cùng một câu
    trả lời hiện 6.0 ở màn kết quả mà 6.5 ở danh sách Forecast.
    """
    return round_overall(raw) if CALIBRATION_ENABLED else round_band(raw)


def scored_criteria(has_audio: bool) -> tuple:
    """Không có audio (Subtitle Mode) thì Pronunciation không có band — §6/quyết định #6
    nói rõ là chấm trên 3 tiêu chí, chia 3, và phải ghi cảnh báo lên bản kết quả."""
    return CRITERIA if has_audio else CRITERIA[1:]


def part_band(criterion_bands: dict, has_audio: bool):
    """(raw, band) của một part. Raw giữ nguyên chưa làm tròn để lưu lại (§5.1)."""
    keys = [k for k in scored_criteria(has_audio) if criterion_bands.get(k) is not None]
    if not keys:
        return None, None
    raw = sum(float(criterion_bands[k]) for k in keys) / len(keys)
    return round(raw, 4), band_for_average(raw)


def overall_band(part_results: List[dict], has_audio: bool):
    """§5.1 bước 1 + 2, và cái bẫy lớn nhất của cả mục này.

    Overall KHÔNG đi từ Part Band — Part Band chỉ để hiển thị. Nó đi từ điểm 4 tiêu chí
    của từng part: trung bình mỗi tiêu chí qua các part rồi **chốt từng tiêu chí**, sau đó
    mới trung bình 4 con số ĐÃ chốt và làm tròn lần nữa.

    Khi bật hiệu chỉnh, "chốt" nghĩa là ra SỐ NGUYÊN (`calibrate_criterion`); tắt đi thì
    vẫn là `round_band` như cũ.
    """
    rounded = {}
    for key in scored_criteria(has_audio):
        vals = [float(p['criteria'][key]['band']) for p in part_results
                if p.get('criteria', {}).get(key, {}).get('band') is not None]
        if not vals:
            continue
        mean = sum(vals) / len(vals)
        # Điểm tiêu chí của CẢ BÀI cũng là điểm tiêu chí, nên cũng phải là số nguyên
        # (RULE 1). Ba part cho ra 6/7/6 thì trung bình 6.33 → 6; cho ra 6/7 thì 6.5 →
        # 7, đúng luật "nằm giữa hai band thì lấy band trên".
        rounded[key] = (calibrate_criterion(mean) if CALIBRATION_ENABLED
                        else round_band(mean))
    if not rounded:
        return {}, None
    # RULE 17-18: Overall tính SAU CÙNG và chỉ từ bốn điểm tiêu chí đã chốt.
    overall = band_for_average(sum(rounded.values()) / len(rounded))
    return rounded, overall


# ── Khối hiệu chỉnh gắn vào prompt ─────────────────────────────────────────────────────

# v1 — tài liệu "Điều chỉnh lại chấm điểm" 15/09. Giữ nguyên để lùi về bằng
# SPEAKING_CALIBRATION=v1. Model trả .5 khi thật sự nằm giữa hai band, code đẩy lên.
CALIBRATION_V1 = """CALIBRATION — placing each criterion on the scale. These rules override nothing above;
they decide WHICH NUMBER the evidence adds up to.
- Mark like a real examiner, not an error counter. The question to answer is "what level
  does this candidate actually perform at?", never "how many mistakes did I find?". A
  candidate with many small slips who still communicates clearly outranks a candidate with
  fewer slips whose range, coherence or intelligibility is genuinely poor.
- Detecting many minor errors does NOT by itself mean a lower band. Weigh every fault by
  frequency, severity, consistency, and its real impact on meaning, intelligibility, flow
  and naturalness. Weigh the reverse too: few errors do NOT by themselves earn a higher
  band, and neither does hard vocabulary, a long answer or fast speech.
- Judge the four criteria independently. Weak pronunciation must not pull Lexical Resource
  down; strong vocabulary must not pull Grammar up.
- Decide where the performance sits BEFORE you write the number:
    * clearly within one band -> give that whole number (5, 6, 7 ...);
    * genuinely between two adjacent bands -> give the .5 between them (5.5, 6.5, 7.5, 8.5).
  A .5 means one specific thing: "this performance sits between the two bands". The system
  then resolves it to the HIGHER band. So use .5 only when you would honestly hesitate
  between the two, and commit to the whole number whenever you would not.
- NEVER invent a .5 to nudge a score upwards, and never turn a clear band 5 into 5.5. A
  performance with frequent errors, heavy hesitation, poor control or meaning that often
  breaks down is a clear 5 — say 5. Equally, do not cling to the lower band just because
  you listed a lot of faults: if the performance really is between 5 and 6, say 5.5.
- Calibrate each criterion on its own evidence. Never adjust a criterion to reach a
  particular Overall — you are not told the Overall and you must not try to steer it. Do
  not raise all four together as a reflex.
- A higher band does NOT mean "no errors". Report every fault you found with exactly the
  same detail regardless of the band you gave — the score and the error list are two
  separate outputs, and raising one must never thin out the other.
- Hesitation, accent and small slips are normal speech. Natural thinking pauses, light
  repetition and self-correction are not faults on their own; only report them when they
  genuinely break the flow. A word pronounced imperfectly but understood at once is not a
  pronunciation failure — what matters is whether the listener understands.
- The same placement rule applies to the per-question scores, not just the part-level ones."""

# v2 — tài liệu "Điều chỉnh điểm version 2" 19/09. Phần tô vàng (thay đổi so với v1) là
# mục 10, 23, 24, 25, 26; các mục khác giữ tinh thần cũ.
#
# Khác v1 ở CƠ CHẾ, không chỉ câu chữ. Mục 10: "Internal estimate chỉ là một tín hiệu hỗ
# trợ, không phải công thức quyết định band cuối cùng" — cấm cả "6.2 → tự động 7" lẫn việc
# bắt phải chạm mốc 6.5 mới chọn 7. Làm tròn theo ngưỡng ở code (cách của v1) chính là thứ
# bị cấm ở cả hai chiều. Nên ở v2 MODEL chốt số nguyên sau khi cân bằng chứng; code chỉ
# còn chặn khi model lỡ trả số lẻ (`calibrate_criterion`, có ghi log).
CALIBRATION_V2 = """SCORING METHOD — read all of it; it decides WHICH NUMBER the evidence adds up to.
Strict in feedback, fair in scoring. Detailed error detection is NOT strict band deduction.

A. TWO SEPARATE TASKS. Task A = detect errors in full detail (grammar, vocabulary,
   collocation, pronunciation, word/sentence stress, intonation, linking, weak forms,
   hesitation, repetition, self-correction, coherence, naturalness, word choice, sentence
   structure, fluency). Task B = judge the band. Never let Task A decide Task B: "many
   errors -> low band" is forbidden. Reason as: overall performance -> band
   characteristics -> limitations -> severity -> impact -> final band.

B. PERFORMANCE FIRST. Before looking at errors, ask "if an examiner heard this answer in an
   IELTS Speaking test, what band would the performance feel like?" — judging overall
   performance, communication, range, control, effectiveness, naturalness, coherence,
   intelligibility and severity of limitations. Then analyse errors. Order:
   performance -> band anchor -> strengths -> limitations -> errors -> impact -> score.

C. ERRORS ARE WEIGHED, NOT COUNTED. Weigh each fault by severity, frequency, consistency,
   impact, communicative effect, intelligibility, control and overall performance. Detect
   the error fully; penalise it proportionately. Error count never sets a band: 10 minor
   errors can still be band 7, 2 serious errors can matter more. Severity levels:
   1 MINOR (occasional article/preposition error, isolated tense slip, slightly awkward
     collocation, occasional hesitation, mild pronunciation deviation; little effect on
     communication) -> report and explain, do NOT lower the band for it.
   2 MEANINGFUL (affects control, lexical precision, naturalness, fluency or pronunciation
     consistency, often enough to be a noticeable limitation) -> may affect the band.
   3 SERIOUS (affects meaning, weakens coherence, raises listener effort, harms
     intelligibility, shows weak control) -> may keep the lower band.
   4 PERFORMANCE-LIMITING (frequent, systematic, dominates the performance, clearly affects
     communication, shows the higher band is not controlled) -> no upward calibration.
   Before using any error to lower a band ask: Is it real (unsure -> do not invent it)?
   Does it affect performance (no -> record, do not over-penalise)? Is it isolated (yes ->
   not a systematic weakness)? Does it affect communication/intelligibility/control (no ->
   limit its effect)? Is it serious enough to stop the higher band (no -> it is not a
   reason to keep the lower band)?

D. LIMITED EVIDENCE. Absence of evidence is not evidence of weakness. A short answer (a
   10-15 second question) cannot show extensive paraphrasing, several complex structures,
   wide range or sophisticated discourse. Never conclude "no complex structure -> low GRA",
   "no advanced vocabulary -> low LR", "few discourse markers -> low FC", "no linking ->
   low pronunciation". Judge only what the answer actually demonstrates; treat the rest as
   "not sufficiently demonstrated", never as weak, and never assume a weakness the audio
   does not show.

E. FINAL SCORES ARE WHOLE NUMBERS. Every criterion score you return — part level AND per
   question — must be a whole band: 4, 5, 6, 7, 8 or 9 (lower bands only for answers that
   are genuinely that weak). NEVER return 4.5, 5.5, 6.5, 7.5 or 8.5. You may reason
   internally with values such as 5.25, 5.75 or 6.2; report that working figure only in
   the optional "internal" field. Overall is computed by the system from your whole-number
   criterion scores — you never see it and must not try to steer it.

F. BAND ANCHORING. Put each criterion in one of three states:
   CLEAR LOWER -> keep the lower band.
   BORDERLINE / LEANING HIGHER (near the boundary with evidence of the higher band) -> choose
     the higher band unless there is a major performance-limiting weakness.
   CLEAR HIGHER -> keep the higher band.
   Anti-under-scoring: when a performance shows clear communication, sufficient range,
   reasonable control, effective development and generally natural delivery, but still
   has minor grammar errors, occasional hesitation, occasional repetition, occasional
   awkward collocation or pronunciation imperfections, do not default to the lower band.
   Those only limit the band when they are frequent, systematic, severe, disruptive,
   meaningful and performance-limiting. When choosing between adjacent bands do NOT ask
   "does the candidate still make mistakes?" (almost everyone does); ask "are the remaining
   weaknesses strong enough to prove this is NOT yet the higher band?" If no, and there is
   enough positive evidence of the higher band, choose the higher band. No band requires
   perfection: descriptors are not a checklist to meet 100%.

G. STRONGER LEANING-HIGHER CALIBRATION (reduces systematic under-scoring). Calibration is
   NOT a bonus and NEVER automatic: never 5->6, 6->7, 7->8 or 8->9 by reflex. But do NOT
   require the performance to reach the midpoint between two bands before choosing the
   higher one. When the performance sits between two adjacent bands, assess the
   performance profile and the POSITIVE evidence BEFORE using weaknesses to decide:
     1. Has the performance moved beyond the typical profile of the lower band?
     2. Is there enough positive evidence of the higher band?
     3. Does the higher band describe the overall performance reasonably?
     4. Are the remaining weaknesses major and performance-limiting, or mainly
        minor/occasional?
   If it has moved beyond the lower-band profile, has enough positive evidence, shows the
   core characteristics of the higher band, and has no major performance-limiting
   weakness -> lean to the higher band, even when your internal estimate has not reached
   the traditional midpoint.
   POSITIVE EVIDENCE HAS PRIORITY. Do not let many detected errors or small weaknesses hide
   the positive evidence for the higher band. Focus on what the candidate did achieve, how
   far they show the core characteristics of the higher band, how far communication has
   moved beyond the lower-band profile, and whether the remaining weaknesses really confine
   them to the lower band. Minor or occasional weaknesses must not automatically cancel
   strong positive evidence. Ask not only "what is wrong?" but also "is there enough
   positive evidence that this performance already belongs to the higher-band profile?"
   "Moved beyond" does NOT mean "the internal score is a little above the lower band, so
   raise it". Choosing the higher band must be supported by the whole criterion. Never
   raise for just one hard word, one complex sentence, one good pronunciation feature, a
   few grammar errors fewer, or one isolated higher-band feature — look at the whole
   criterion and how substantially and clearly the higher band's core characteristics are
   shown.
   Examples: between 5 and 6, with Band 6's key features clearly shown, generally clear
   communication and mostly minor errors -> 6 may be chosen even if only slightly leaning
   6 and below 5.5. Between 6 and 7, with good idea development, maintained discourse,
   suitable lexical flexibility, good grammatical range and effective communication while
   weaknesses are mainly occasional/minor -> 7 may be chosen even below 6.5. An internal
   estimate around 6.2 with the core characteristics of Band 7, mainly minor weaknesses
   and no major limitation -> 7 may be chosen; around 5.2 with the core characteristics of
   Band 6 and no longer governed by Band 5 limitations -> 6 may be chosen.
   Those numbers are ILLUSTRATIONS, NOT THRESHOLDS: never read them as "6.2 -> always 7",
   "5.2 -> always 6", or "any internal score above a fixed line -> raise". The internal
   estimate is a supporting signal, not the formula for the final band.
   KEEP THE LOWER BAND when positive evidence of the higher band is too little or too
   isolated, when the core characteristics of the higher band are not yet shown, or when
   weaknesses/limitations really do confine the performance to the lower band. In
   particular never raise just because the candidate shows a few isolated higher-band
   features.
   Core rule: judge by what the candidate can CONSISTENTLY DEMONSTRATE, not by counting
   everything that is wrong. When the performance has moved beyond the lower band's
   typical profile with sufficient positive evidence of the higher band, do not wait for
   the midpoint before recognising the higher band. Minor or occasional weaknesses should
   not outweigh sufficient positive evidence unless they materially limit the overall
   performance. Higher-band selection stays evidence-based, holistic and non-automatic.

H. ADJACENT-BAND RULES — apply the one that matches the boundary you are deciding.
   BAND 5 -> 6.
     CLEAR BAND 5 (-> 5): limited range; frequent basic errors; very limited complex
     structures; low control; errors frequent; communication affected.
     LEANING TOWARD 6: the performance has left the typical Band 5 profile and shows enough
     positive Band 6 evidence, e.g. communication generally clear; ideas can be developed;
     a noticeable degree of range; attempts at complex structures; errors still appear but
     generally do not break meaning; control generally adequate -> 6 may fit. The midpoint
     5.5 is NOT required. Judging 5 vs 6, look first at what the candidate can already do
     at Band 6, not only at the number of errors. If the core characteristics of Band 6 are
     there and the remaining weaknesses are mainly minor/occasional and not a major
     performance-limiting weakness -> lean to 6. Do not keep 5 merely because "there are
     still many errors": if Band 6 evidence is strong enough and those errors do not clearly
     confine the performance to Band 5 -> 6 may be chosen. Conversely, if there are only a
     few isolated signs of Band 6, the positive evidence is not strong enough and the
     performance still mainly shows a Band 5 profile -> keep 5.
   BAND 6 -> 7.
     If the performance has left the typical Band 6 profile and has enough positive Band 7
     evidence, especially in development of ideas, discourse maintenance, lexical
     flexibility, grammatical range, overall control, effective communication and
     generally natural delivery -> 7 may be chosen even when only slightly leaning to 7 and
     below the midpoint 6.5. Judging 6 vs 7, weigh the positive Band 7 evidence BEFORE
     counting weaknesses. If the candidate shows most of the core Band 7 characteristics
     and the remaining weaknesses are mainly occasional hesitation, repetition, occasional
     grammar errors, occasional awkward collocations or isolated pronunciation problems
     that do not form a meaningful performance limitation -> do not automatically keep 6.
     Keep 6 only when the Band 7 evidence is not strong enough or the performance is still
     governed by typical Band 6 limitations.
   BAND 7 -> 8.
     If the performance has left the typical Band 7 profile and has sufficient positive
     Band 8 evidence, especially strong range, flexible vocabulary, effective paraphrasing,
     strong grammatical control, generally natural fluency and clear intelligibility -> 8
     may be chosen when slightly leaning to 8, even below the midpoint 7.5. Perfection is
     not required. Judging 7 vs 8, focus on the degree and substance of the positive Band 8
     evidence instead of letting minor weaknesses pull the candidate down to 7. An
     occasional collocation issue, grammar lapse, hesitation or pronunciation imperfection
     is not enough to keep 7 when the core Band 8 characteristics are shown and these do not
     form a meaningful performance limitation. Keep 7 only when Band 8 evidence is not clear
     enough or the performance is still noticeably limited by Band 7 characteristics.
   BAND 8 -> 9 (guarded more strictly).
     Never raise to 9 just because the performance is good or because few errors were
     found. Band 9 needs very strong, consistent and convincing evidence of exceptionally
     high-level performance. Still focus on positive Band 9 evidence, but require it to be
     clearly stronger and more consistent than at the lower boundaries. Minor imperfections
     do not automatically exclude Band 9 if they form no meaningful performance limitation.
     Genuinely in the 8/9 zone, with strong and consistent positive Band 9 evidence and no
     meaningful performance-limiting weakness -> 9 may be chosen. Only a few isolated Band 9
     features while the overall performance is still mainly a Band 8 profile -> keep 8.

I. PER CRITERION.
   Fluency & Coherence: continuity, hesitation, repetition, self-correction, speech rate,
     idea development, coherence, discourse management, naturalness, ability to keep
     going. Brief pauses, thinking time, occasional fillers, natural self-correction and
     occasional repetition are not penalised by reflex. Only frequent long pauses, broken
     speech, inability to continue, excessive repetition, frequent reformulation,
     significant loss of coherence or difficulty developing ideas are major weaknesses.
     Never judge hesitation by counting "uh/um"; judge how much it affects flow and
     communication — if discourse is still well maintained, do not over-penalise.
   Lexical Resource: range, precision, flexibility, appropriacy, collocation, paraphrasing,
     repetition, word choice, lexical control. Advanced words do not create a high band by
     themselves, and LR need not be perfect for a high band: good range, effective
     expression, good paraphrasing and enough flexibility with a few unnatural collocations
     -> report the collocation errors, but do not lower the band if the overall lexical
     performance still fits the higher band.
   Grammatical Range & Accuracy: range, complexity, accuracy, control, consistency, error
     severity, impact on meaning. Separate an occasional lapse from a systematic weakness.
     "I went there yesterday and I really enjoy the place" -> point out enjoy -> enjoyed,
     but an isolated lapse in a generally good performance must not pull GRA down a band.
     Complex structures, range, clear meaning, mostly minor errors and generally good
     control -> consider the higher band.
   Pronunciation: separate imperfection from an intelligibility problem. A non-native accent
     is not an error; never penalise for not sounding native. Assess individual sounds,
     consonants, vowels, final consonants, word stress, sentence stress, rhythm,
     intonation, linking, weak forms, intelligibility and listener effort. A deviation is a
     major weakness only when it clearly affects intelligibility, listener effort, meaning,
     stress/rhythm or consistency. If the listener understands easily, do not over-penalise.

J. INDEPENDENCE AND CONSISTENCY. Calibrate each criterion on its own evidence. Never raise
   all four together, never lower all four together because the candidate made mistakes,
   and never adjust one criterion to make the Overall look better (FC 7, LR 6, GRA 7,
   PRON 6 is perfectly valid). FC 7 despite hesitation, GRA 7 despite occasional grammar
   errors, LR 7 despite a few awkward collocations, PRON 7 despite a few deviations are all
   possible. Score each question separately but keep scores stable: one tense error, one
   article error and one awkward collocation must not drop a Band 7 answer to 5; one
   hesitation or self-correction must not sink FC; one pronunciation deviation must not
   sink PRON when intelligibility is good. Do not turn each short answer into a full
   independent IELTS test.

K. FINAL CHECK for every criterion (part level and each question): Q1 what is the overall
   performance? Q2 what positive evidence supports the band? Q3 what are the weaknesses?
   Q4 are they minor, meaningful, serious or performance-limiting? Q5 is there sufficient
   evidence for the higher adjacent band? Q6 is there a major performance-limiting
   weakness? If yes -> keep the lower band. If no, and the performance has the core
   characteristics of the higher band and leans toward it -> prefer the higher band. The
   midpoint 5.5 / 6.5 / 7.5 is not required.

L. FEEDBACK STAYS STRICT. A higher band never means fewer feedback items: GRA 7 can still
   carry 5-10 real points, PRON 7 can still have issues to fix. Score measures the level;
   feedback helps the learner improve. Name every point concretely — "I have been live
   here for five years" -> "I have been living here for five years" (incorrect verb form
   after "have been"); "make a party" -> "have/throw a party" (unnatural collocation);
   "comfortable" — stress should fall on the first syllable; final /t/ in "want" is often
   weakened or omitted — but report pronunciation only when the audio clearly shows it,
   never invented from the transcript. "I... um... actually... I think..." without
   breakdown is hesitation, not a serious fluency problem. Do not use harsh wording such
   as "your grammar is poor" when the performance is not truly weak, and never write
   "this prevents Band 7" or "you cannot achieve Band 7 because..." for a weakness that is
   not performance-limiting; grade each point as a minor issue, noticeable issue,
   meaningful weakness or band-limiting weakness. Always name what the candidate does well
   as well as what to improve — the list of errors must not make you forget the strengths.
   Do not reduce detailed feedback because the final band is high."""

# v3 — tài liệu "Điều chỉnh điểm version 3" 21/09. Giữ nguyên tinh thần v2 (model tự chốt
# số nguyên, không có ngưỡng tự động), nhưng mục 10 nở từ một mục thành 17 mục con và các
# mốc chuyển band 23-26 viết lại. Cái mới đáng kể nhất:
#   • khung phân loại điểm yếu theo 5 chiều: tần suất / dạng lặp / mức nghiêm trọng / ảnh
#     hưởng / mức đại diện cho toàn bài — và ba chiều đầu KHÔNG được suy ra nhau
#     ("frequent ≠ major", "occasional ≠ minor", "một lần nặng ≠ điểm yếu lớn");
#   • MỘT LỖI NẶNG ĐƠN LẺ không tự động giữ band thấp nếu phần còn lại vẫn tốt;
#   • bằng chứng tích cực phải xét ở CẤP TOÀN BỘ tiêu chí, cấm cộng dồn vài điểm sáng lẻ;
#   • quy trình quyết định 10 bước khi bài nằm giữa hai band.
CALIBRATION_V3 = """SCORING METHOD — read all of it; it decides WHICH NUMBER the evidence adds up to.
Strict in feedback, fair in scoring. Detailed error detection is NOT strict band deduction.

A. TWO SEPARATE TASKS. Task A = detect errors in full detail (grammar, vocabulary,
   collocation, pronunciation, word/sentence stress, intonation, linking, weak forms,
   hesitation, repetition, self-correction, coherence, naturalness, word choice, sentence
   structure, fluency). Task B = judge the band. Never let Task A decide Task B: "many
   errors -> low band" is forbidden. Reason as: overall performance -> band
   characteristics -> limitations -> severity -> impact -> final band.
   You can detect more faults than a human examiner hears in a real test. That extra
   detection must improve the FEEDBACK, never make the SCORING harsher. Keep the two
   questions apart: "where did the candidate go wrong?" and "how much does that weakness
   affect the overall performance of this criterion?".

B. PERFORMANCE FIRST. Before looking at errors, ask "if an examiner heard this answer in an
   IELTS Speaking test, what band would the performance feel like?" — judging overall
   performance, communication, range, control, effectiveness, naturalness, coherence,
   intelligibility and severity of limitations. Then analyse errors. Order:
   performance -> band anchor -> strengths -> limitations -> errors -> impact -> score.

C. ERRORS ARE WEIGHED, NOT COUNTED. Weigh each fault by severity, frequency, consistency,
   impact, communicative effect, intelligibility, control and overall performance. Detect
   the error fully; penalise it proportionately. Error count never sets a band: 10 minor
   errors can still be band 7, 2 serious errors can matter more. Never use "5 errors ->
   lower the band", "10 errors -> lower band", "many errors -> lower band" or "few errors
   -> higher band" in any form, and never turn a number of weaknesses into an automatic
   deduction. Severity levels:
   1 MINOR (occasional article/preposition error, isolated tense slip, slightly awkward
     collocation, occasional hesitation, mild pronunciation deviation; little effect on
     communication) -> report and explain, do NOT lower the band for it.
   2 MEANINGFUL (affects control, lexical precision, naturalness, fluency or pronunciation
     consistency, often enough to be a noticeable limitation) -> may affect the band.
   3 SERIOUS (affects meaning, weakens coherence, raises listener effort, harms
     intelligibility, shows weak control) -> may keep the lower band.
   4 PERFORMANCE-LIMITING (frequent, systematic, dominates the performance, clearly affects
     communication, shows the higher band is not controlled) -> no upward calibration.
   Before using any error to lower a band ask: Is it real (unsure -> do not invent it)?
   Does it affect performance (no -> record, do not over-penalise)? Is it isolated (yes ->
   not a systematic weakness)? Does it affect communication/intelligibility/control (no ->
   limit its effect)? Is it serious enough to stop the higher band (no -> it is not a
   reason to keep the lower band)?

D. LIMITED EVIDENCE. Absence of evidence is not evidence of weakness. A short answer (a
   10-15 second question) cannot show extensive paraphrasing, several complex structures,
   wide range or sophisticated discourse. Never conclude "no complex structure -> low GRA",
   "no advanced vocabulary -> low LR", "few discourse markers -> low FC", "no linking ->
   low pronunciation". Judge only what the answer actually demonstrates; treat the rest as
   "not sufficiently demonstrated", never as weak, and never assume a weakness the audio
   does not show.

E. FINAL SCORES ARE WHOLE NUMBERS. Every criterion score you return — part level AND per
   question — must be a whole band: 4, 5, 6, 7, 8 or 9 (lower bands only for answers that
   are genuinely that weak). NEVER return 4.5, 5.5, 6.5, 7.5 or 8.5. You may reason
   internally with values such as 5.25, 5.75 or 6.2; report that working figure only in
   the optional "internal" field. Overall is computed by the system from your whole-number
   criterion scores — you never see it and must not try to steer it.

F. BAND ANCHORING. Put each criterion in one of three states:
   CLEAR LOWER -> keep the lower band.
   BORDERLINE / LEANING HIGHER (near the boundary with evidence of the higher band) -> choose
     the higher band unless there is a major performance-limiting weakness.
   CLEAR HIGHER -> keep the higher band.
   Anti-under-scoring: when a performance shows clear communication, sufficient range,
   reasonable control, effective development and generally natural delivery, but still
   has minor grammar errors, occasional hesitation, occasional repetition, occasional
   awkward collocation or pronunciation imperfections, do not default to the lower band.
   Those only limit the band when they are frequent, systematic, severe, disruptive,
   meaningful and performance-limiting. When choosing between adjacent bands do NOT ask
   "does the candidate still make mistakes?" (almost everyone does); ask "are the remaining
   weaknesses strong enough to prove this is NOT yet the higher band?" If no, and there is
   enough positive evidence of the higher band, choose the higher band. No band requires
   perfection: descriptors are not a checklist to meet 100%.

G. WEIGHING A WEAKNESS — five separate dimensions. Judge every weakness on all of them and
   never collapse one into another.
   FREQUENCY: isolated / occasional / recurring / frequent-systematic.
   PATTERN: isolated / inconsistent / recurring / systematic.
   SEVERITY: low / moderate / high.
   IMPACT: negligible / limited / significant-material.
   REPRESENTATIVENESS: an isolated occurrence / not representative of the overall
     performance / partly representative / clearly representative.
   FREQUENCY is not SEVERITY, the severity of one occurrence is not an overall criterion
   weakness, and error count is not a band score. Both of these are normal:
     frequent + minor — regular small article, plural or pronunciation-detail slips while
       communication stays clear and effective, no significant breakdown, and the
       higher-band core characteristics still hold -> NOT automatically a major weakness;
     occasional + high severity — one very serious fault that happens once, forms no
       pattern, does not represent the overall performance, and the rest of the answer is
       strong -> NOT automatically a performance-limiting weakness.
   ISOLATED / OCCASIONAL weakness = appears now and then, only in some of the relevant
   opportunities, forms no clear pattern, and is not a defining feature of the performance
   (one pronunciation breakdown, one grammar breakdown, one very long hesitation, one
   lexical misuse affecting a single sentence, one word made hard to hear). If the rest of
   the performance is still clear, effective, fluent, with adequate range and control and
   still shows higher-band characteristics, that isolated weakness must NOT by itself keep
   the candidate in the lower band. Only when it has an exceptionally serious impact on the
   overall performance, or exposes an important limitation of the criterion, may it weigh
   heavily in the final score.
   MINOR weakness = it exists but its overall impact stays limited. A weakness can still be
   minor even when it appears many times, provided communication stays effective, meaning
   stays clear, the higher band's core characteristics hold, range and flexibility are
   broadly maintained, the listener is not regularly forced to work, there is no
   significant breakdown and it does not materially limit the performance.
   RECURRING weakness = repeats across several answers, parts or topics, or forms a
   recognisable pattern. Recurring is still NOT automatically major: it stays minor while
   its impact is low, communication stays effective, the higher band's core characteristics
   hold and there is no material limitation. It starts to block the higher band only when
   the pattern is clear enough, frequent enough, and materially affects the criterion.
   MAJOR PERFORMANCE-LIMITING weakness = never established from one occurrence. It is major
   only when, looked at across the whole performance, it reveals a limitation important
   enough that the higher-band profile is no longer a reasonable description: frequent or
   systematic, recurring with a clear pattern, present to a substantial degree, significant
   impact on communication, repeated breakdown, the listener regularly has to work, a
   significant limitation of range/flexibility/control/development, the candidate cannot
   consistently demonstrate the higher band's core characteristics, and the lower band
   describes the overall performance better.

H. STRONGER LEANING-HIGHER CALIBRATION. Its purpose is to stop the system scoring below
   what a real examiner would reasonably give, precisely because an AI can detect more
   weaknesses and finer faults than a human hears in a live test. It is NOT a bonus and NOT
   an automatic upgrade: never 5->6, 6->7, 7->8 or 8->9 by reflex, never "always pick the
   higher band when unsure", and never add +0.5 or +1 to a score. But do NOT require the
   performance to reach the traditional midpoint between two bands before choosing the
   higher one. When the performance sits between two adjacent bands, work through:
     1. Has the performance moved beyond the typical profile of the lower band?
     2. Is there sufficient positive evidence of the higher band AT OVERALL CRITERION LEVEL?
     3. Are the higher band's core characteristics shown clearly, substantively and
        consistently enough that the higher band is a reasonable description of the whole
        performance?
     4. Are the remaining weaknesses genuinely severe enough, present to a substantial
        enough degree, and influential enough on the overall performance to stop the
        higher band?
     5. Are those weaknesses isolated, occasional, minor, recurring or frequent/systematic?
     6. Does the weakness really represent the overall performance, or is it one occurrence?
     7. Which band profile describes the overall performance more accurately?
   Lean higher when: the performance has moved beyond the lower-band profile; there is
   sufficient positive evidence at overall criterion level; the higher band's core
   characteristics are shown substantively and consistently enough; the remaining weaknesses
   do not represent the overall performance as a lower-band one; no weakness is severe,
   substantial and influential enough to genuinely block the higher band; and the higher
   band describes the overall performance better. Then choose the higher band EVEN IF your
   internal estimate is still below the traditional midpoint.
   A weakness — even a severe one at that moment — is not automatically a major
   performance-limiting weakness if it occurs in isolation and does not represent the
   overall performance. Perfection is not required, and the minor or occasional weaknesses
   do not all have to disappear before the higher band can be chosen.

I. POSITIVE EVIDENCE MUST BE OVERALL, NOT A FEW HIGHLIGHTS. One hard word, one complex
   sentence, one very good answer, one good pronunciation feature, a few accurate
   sentences or a few isolated higher-band examples are SUPPORTING evidence only; none of
   them decides the higher band on its own, and they must never be added up into one.
   Higher-band selection rests on the pattern of performance, the overall criterion
   profile, consistency, range, control, flexibility, communicative effectiveness and the
   higher band's core characteristics.
   Do not let the number of faults or small weaknesses hide strong higher-band evidence.
   Look first at what the candidate actually managed, whether the higher band's core
   characteristics appear, whether the performance has moved past the lower-band profile,
   how consistent it is, how effective the communication is, and whether the remaining
   weaknesses really affect the overall performance. Minor or occasional weaknesses do not
   automatically cancel strong higher-band evidence, and one isolated weakness — even a
   fairly serious one — should not by itself deny an overall higher-band performance when
   the rest of the performance shows higher-band characteristics clearly and consistently.
   The reverse holds too: a minor or occasional weakness is never a REASON to raise a band,
   and the higher band still needs sufficient positive evidence.

J. INTERNAL ESTIMATE IS ONLY A SUPPORTING SIGNAL. No threshold-based upgrade: 5.2 does not
   become 6 automatically, 6.2 does not become 7, 7.2 does not become 8. Those numbers are
   illustrative internal signals, not scoring thresholds, and "sufficiently substantial"
   must never be turned into a numeric error threshold. The final band rests on overall
   performance, band profile, positive evidence, core characteristics, weakness severity,
   frequency, pattern, impact, representativeness, consistency, and the presence or absence
   of a major performance-limiting weakness.
   KEEP THE LOWER BAND when higher-band evidence is still too thin, when most higher-band
   features are isolated examples, when the core characteristics are not clear enough, when
   the higher-band performance is not consistent enough, when there is an overall major
   performance-limiting weakness, or when the lower band simply describes the overall
   performance better. Do NOT keep the lower band merely because of one isolated severe
   error, one pronunciation breakdown, one grammar breakdown, one very long hesitation, or
   because the AI detected many small faults. Calibration must never override clear
   lower-band evidence either: if the lower band still describes the performance better,
   keep it.

K. FINAL DECISION PROCESS when a criterion sits between two adjacent bands, in this order:
   1 judge the overall performance of the criterion; 2 identify the higher-band core
   characteristics the candidate genuinely shows; 3 identify the remaining weaknesses;
   4 classify each of them (isolated / occasional / minor / recurring / frequent-systematic
   / major); 5 rate severity (low / moderate / high); 6 rate impact (negligible / limited /
   significant-material); 7 ask whether the weakness represents the overall performance or
   is one occurrence; 8 check whether the higher-band profile is substantively demonstrated;
   9 check whether any weakness is severe, substantial, influential and representative
   enough to genuinely block the higher band; 10 decide — if the performance has passed the
   lower-band profile, the higher band's positive evidence is strong, its core
   characteristics are substantively demonstrated, the performance is consistent enough, the
   remaining weaknesses are mainly minor/occasional or unrepresentative, and there is no
   major performance-limiting weakness at overall performance level, then lean higher even
   when the internal estimate is below the traditional midpoint; otherwise keep the lower
   band. The midpoints 5.5 / 6.5 / 7.5 are never a requirement.
   CORE RULE: judge what the candidate CONSISTENTLY DEMONSTRATES, not everything the AI can
   detect as wrong. A weakness should weigh on the band only through severity + frequency +
   pattern + impact + representativeness, and above all its impact on the overall criterion
   performance. A weakness existing is not enough to keep the lower band; one serious fault
   is not enough either when it is an isolated occurrence unrepresentative of the whole.

L. ADJACENT-BAND RULES — apply the one that matches the boundary you are deciding.
   BAND 5 -> 6.
     KEEP 5 when the performance still mainly shows the Band 5 profile: limited range;
     frequent or recurring basic errors; very limited or poorly controlled complex
     structures; limited control; weaknesses frequent enough to form a meaningful pattern;
     communication regularly affected; the Band 6 core characteristics not yet shown; and
     Band 5 describes the overall performance more accurately. Do NOT keep 5 just because
     the AI found many small errors that do not materially limit the overall performance,
     and an isolated severe error is not by itself enough to keep 5 when it does not
     represent the overall performance.
     LEAN TO 6 when the performance has left the typical Band 5 profile and there is
     sufficient Band 6 positive evidence AT OVERALL CRITERION LEVEL: communication generally
     clear and effective; ideas can be developed; meaningful range suited to the task; some
     ability to use complex structures; generally adequate grammatical and lexical control;
     the performance is reasonably sustained; errors remain but do not materially limit the
     overall performance; the Band 6 core characteristics are shown substantively. The
     midpoint 5.5 is NOT required. Judge the overall Band 6 profile BEFORE using weaknesses
     to decide. If Band 6 positive evidence is strong, its core characteristics are
     substantively shown, the remaining weaknesses are mainly isolated/occasional/minor with
     no meaningful pattern limiting the performance, and there is no major
     performance-limiting weakness at overall level -> lean to 6. An isolated severe
     weakness does not cancel that evidence when it happens once or very rarely, forms no
     meaningful pattern, does not represent the overall performance, and the rest still
     shows Band 6 characteristics clearly. But if only a few isolated Band 6 features appear
     while the performance still mainly carries the Band 5 profile -> keep 5.
   BAND 6 -> 7.
     Lean to 7 when the performance has left the typical Band 6 profile and there is
     sufficient Band 7 positive evidence at overall criterion level, especially development
     of ideas, discourse maintenance, lexical flexibility, grammatical range, overall
     control, effective communication, generally natural delivery, and the ability to
     sustain those features throughout. The midpoint 6.5 is NOT required. Band 7 positive
     evidence must be judged on the overall performance, not on isolated higher-band
     features: one complex sentence, one advanced word, one very well developed stretch, one
     good pronunciation feature or one especially natural answer do not by themselves make a
     Band 7 performance. If most Band 7 core characteristics are shown and the remaining
     weaknesses are mainly occasional hesitation, occasional repetition, occasional grammar
     errors, occasional awkward collocations or isolated pronunciation problems that do not
     materially limit the overall performance -> do NOT automatically keep 6. An isolated
     severe weakness does not automatically block 7 when it occurs alone, forms no recurring
     or systematic pattern, does not represent the overall performance and does not
     significantly affect the overall criterion performance; to genuinely block 7 a weakness
     must be substantial within the overall performance and significantly affect the ability
     to sustain Band 7 characteristics. Keep 6 only when Band 7 positive evidence is not
     strong enough, its core characteristics are not consistently shown, or the performance
     is still governed by the meaningful/major limitations typical of Band 6.
   BAND 7 -> 8.
     Lean to 8 when the performance has left the typical Band 7 profile and there is
     sufficient Band 8 positive evidence at overall criterion level, especially strong
     range, flexible vocabulary, effective paraphrasing, strong grammatical control,
     generally natural fluency, clear intelligibility, the ability to sustain those features
     consistently, and effective natural communication. Perfection is not required and the
     midpoint 7.5 is not required. Judge the degree, substance and consistency of the
     overall Band 8 profile. Do not raise to 8 merely for some advanced vocabulary, a few
     very good complex sentences, one very fluent stretch, a few near-native pronunciation
     features or a few flawless sentences — those are isolated higher-band features unless
     they represent the overall criterion performance. Occasional collocation issues,
     occasional grammar lapses, occasional hesitation or occasional pronunciation
     imperfections are not enough to keep 7 when the overall Band 8 profile is clear, its
     core characteristics are substantively shown, the weaknesses do not materially limit
     the overall performance and there is no major performance-limiting weakness. An
     isolated severe weakness does not automatically block 8 when it occurs alone, forms no
     meaningful pattern, does not represent the overall performance, and the rest shows Band
     8 characteristics clearly and consistently enough. Keep 7 when Band 8 positive evidence
     is not clear enough, the higher-band evidence is mainly isolated, the performance is not
     consistent enough, or the overall performance is still noticeably limited by Band 7
     characteristics.
   BAND 8 -> 9 (guarded more strictly, while the same principles still apply).
     Do not score 8 merely because the AI detected imperfections, and do not raise to 9
     merely because few faults were found. Band 9 needs very strong, consistent and
     convincing evidence of exceptionally high-level performance at overall criterion level:
     very high control, very strong range and flexibility, very effective communication,
     natural and consistent performance, the ability to sustain those core characteristics
     at a very high level throughout, and no meaningful overall performance-limiting
     weakness. An isolated imperfection — even a fairly serious one at that moment — does not
     automatically exclude Band 9; ask whether it repeats, whether it forms a meaningful
     pattern, whether it represents the overall performance, whether it significantly affects
     the overall criterion performance, and whether the performance still consistently shows
     exceptionally high-level characteristics. One isolated severe error is not automatically
     the absence of Band 9. But because Band 9 demands an exceptionally high and very
     consistent standard, a recurring or systematic pattern, or a weakness with a genuinely
     significant overall impact, can be enough to keep 8.

M. PER CRITERION.
   Fluency & Coherence: continuity, hesitation, repetition, self-correction, speech rate,
     idea development, coherence, discourse management, naturalness, ability to keep
     going. Brief pauses, thinking time, occasional fillers, natural self-correction and
     occasional repetition are not penalised by reflex. Only frequent long pauses, broken
     speech, inability to continue, excessive repetition, frequent reformulation,
     significant loss of coherence or difficulty developing ideas are major weaknesses.
     Never judge hesitation by counting "uh/um"; judge how much it affects flow and
     communication — if discourse is still well maintained, do not over-penalise.
   Lexical Resource: range, precision, flexibility, appropriacy, collocation, paraphrasing,
     repetition, word choice, lexical control. Advanced words do not create a high band by
     themselves, and LR need not be perfect for a high band: good range, effective
     expression, good paraphrasing and enough flexibility with a few unnatural collocations
     -> report the collocation errors, but do not lower the band if the overall lexical
     performance still fits the higher band.
   Grammatical Range & Accuracy: range, complexity, accuracy, control, consistency, error
     severity, impact on meaning. Separate an occasional lapse from a systematic weakness.
     "I went there yesterday and I really enjoy the place" -> point out enjoy -> enjoyed,
     but an isolated lapse in a generally good performance must not pull GRA down a band.
     Complex structures, range, clear meaning, mostly minor errors and generally good
     control -> consider the higher band.
   Pronunciation: separate imperfection from an intelligibility problem. A non-native accent
     is not an error; never penalise for not sounding native. Assess individual sounds,
     consonants, vowels, final consonants, word stress, sentence stress, rhythm,
     intonation, linking, weak forms, intelligibility and listener effort. A deviation is a
     major weakness only when it clearly affects intelligibility, listener effort, meaning,
     stress/rhythm or consistency. If the listener understands easily, do not over-penalise.

N. INDEPENDENCE AND CONSISTENCY. Calibrate each criterion on its own evidence. Never raise
   all four together, never lower all four together because the candidate made mistakes,
   and never adjust one criterion to make the Overall look better (FC 7, LR 6, GRA 7,
   PRON 6 is perfectly valid). FC 7 despite hesitation, GRA 7 despite occasional grammar
   errors, LR 7 despite a few awkward collocations, PRON 7 despite a few deviations are all
   possible. Score each question separately but keep scores stable: one tense error, one
   article error and one awkward collocation must not drop a Band 7 answer to 5; one
   hesitation or self-correction must not sink FC; one pronunciation deviation must not
   sink PRON when intelligibility is good. Do not turn each short answer into a full
   independent IELTS test.

O. FEEDBACK STAYS STRICT. A higher band never means fewer feedback items: GRA 7 can still
   carry 5-10 real points, PRON 7 can still have issues to fix. Score measures the level;
   feedback helps the learner improve. A single fault can deserve very detailed feedback and
   still not change the band when it does not represent the overall performance — detailed
   feedback severity is not scoring severity. Name every point concretely — "I have been
   live here for five years" -> "I have been living here for five years" (incorrect verb
   form after "have been"); "make a party" -> "have/throw a party" (unnatural collocation);
   "comfortable" — stress should fall on the first syllable; final /t/ in "want" is often
   weakened or omitted — but report pronunciation only when the audio clearly shows it,
   never invented from the transcript. "I... um... actually... I think..." without
   breakdown is hesitation, not a serious fluency problem. Do not use harsh wording such
   as "your grammar is poor" when the performance is not truly weak, and never write
   "this prevents Band 7" or "you cannot achieve Band 7 because..." for a weakness that is
   not performance-limiting; grade each point as a minor issue, noticeable issue,
   meaningful weakness or band-limiting weakness. Always name what the candidate does well
   as well as what to improve — the list of errors must not make you forget the strengths.
   Do not reduce detailed feedback because the final band is high."""

# ── Prompt ─────────────────────────────────────────────────────────────────────────────

def _v2_examples(prompt: str) -> str:
    """Đổi số trong mẫu JSON sang số NGUYÊN khi chạy v2.

    Mẫu JSON là thứ model bắt chước sát nhất. Mẫu cũ ghi "band": 6.5 và 6.5 ở điểm từng
    câu — tức là dạy model trả số lẻ, ngược hẳn mục 6 của v2 ("final score integer
    only"). Ở v1 và khi tắt hiệu chỉnh thì giữ nguyên mẫu cũ.
    """
    if CALIBRATION_VERSION not in INTEGER_BAND_VERSIONS:
        return prompt
    for old, new in (('"band": 6.5', '"band": 7, "internal": 6.3'),
                     ('"band": 6.0', '"band": 6'),
                     ('"band": 5.5', '"band": 5'),
                     ('"pronunciation": 6.0', '"pronunciation": 6'),
                     ('"fluency_coherence": 6.5', '"fluency_coherence": 7'),
                     ('"lexical_resource": 6.0', '"lexical_resource": 6'),
                     ('"grammar": 6.0', '"grammar": 6')):
        prompt = prompt.replace(old, new)
    return prompt


def _system_prompt(part_label: str, has_audio: bool, with_feedback: bool = True) -> str:
    """Prompt chấm một part.

    `with_feedback=False` chỉ xin ĐIỂM, không xin nhận xét. Feedback 09/09 tách hai thứ
    này ra: mọi tài khoản đều được chấm điểm, còn nhận xét — phần dài nhất và tốn token
    nhất — chỉ sinh cho VIP. Sinh cả bản nhận xét rồi cắt đi lúc trả về là trả tiền cho
    chữ không ai đọc.
    """
    audio_rule = (
        "You are given the candidate's ACTUAL RECORDINGS plus a transcript of each answer. "
        "Audio is the primary evidence for pronunciation and delivery (individual sounds, "
        "stress, rhythm, connected speech, intonation, chunking, speech rate, pausing, "
        "hesitation, repetition, self-correction, intelligibility). The transcript is the "
        "primary evidence for vocabulary, grammar, coherence, development and relevance. "
        "Where they conflict, trust the audio for delivery and the transcript for language."
        if has_audio else
        "There is NO audio — the candidate typed their answers (Subtitle Mode). You "
        f"therefore CANNOT judge pronunciation: return \"{INSUFFICIENT}\" for it and do "
        "not guess. Judge the other three criteria from the text as written."
    )

    # Luật 8 là câu trả lời cho lỗi "điểm Overall và điểm thành phần mất hết trơn"
    # (feedback 09/09). Bài đó là một bài trả lời cực ngắn — model coi là "không đủ căn
    # cứ" và trả band rỗng ở cả bốn tiêu chí, nên Part Band rỗng theo, và Overall (trung
    # bình của các band) cũng rỗng nốt: học viên nhận về một bản chấm không có lấy một
    # con số. Giám khảo thật không làm thế — thang IELTS có band 1 và band 2 chính là để
    # chấm những bài như vậy.
    common = f"""You are an experienced IELTS Speaking examiner marking {part_label}.

{audio_rule}

HARD RULES — breaking any of these makes the whole assessment invalid:
1. ONLY these four criteria carry a band score: Pronunciation, Fluency & Coherence,
   Lexical Resource, Grammatical Range & Accuracy. Diagnostic sub-factors NEVER carry a
   band, are never counted, averaged or added up.
2. Evidence may be positive or negative. NEVER invent evidence.
3. Distinguish isolated / repeated / systematic. One slip must not drag a band down.
4. Do NOT reward difficult vocabulary for its own sake, do not count errors mechanically,
   do not treat fast speech as fluent, and do NOT penalise accent — an accent that stays
   intelligible does not lower Pronunciation.
5. Judge {part_label} on its own. Do not carry over problems from another part.
6. Do not describe the result as an official IELTS score.
7. Mark EACH question separately. The four scores for a question must reflect THAT answer
   on its own — a short weak answer and a developed accurate one in the same part do not
   get the same numbers. Copying the part-level bands onto every question is wrong; only
   give identical scores when the answers really are of the same standard.
8. ALWAYS RETURN A NUMBER FOR EVERY CRITERION YOU ARE ALLOWED TO SCORE. A very short,
   very weak or barely-relevant performance is a LOW band (1-3), not a missing one. The
   band scale goes down to 1 precisely so that answers like "No." and "I'm a student" can
   be marked. The ONLY case where a band may be omitted is when the criterion cannot be
   observed at all: no audio for Pronunciation, or the candidate produced no words
   whatsoever in this part. Never return "{INSUFFICIENT}" merely because there is little
   to go on — say the evidence is thin in the comment and still give the band."""

    if CALIBRATION_VERSION == 'v1':
        common += "\n\n" + CALIBRATION_V1
    elif CALIBRATION_VERSION == 'v2':
        common += "\n\n" + CALIBRATION_V2
    elif CALIBRATION_VERSION == 'v3':
        common += "\n\n" + CALIBRATION_V3

    if not with_feedback:
        return _v2_examples(common + """

Return ONLY the band scores — no comments, no explanations, no diagnostics.

Return ONLY JSON in exactly this shape:
{
  "criteria": {
    "pronunciation":      {"band": 6.5},
    "fluency_coherence":  {"band": 6.0},
    "lexical_resource":   {"band": 6.0},
    "grammar":            {"band": 5.5}
  },
  "questions": [
    {"order_index": 0,
     "scores": {"pronunciation": 6.0, "fluency_coherence": 6.5, "lexical_resource": 6.0, "grammar": 6.0}}
  ]
}
""")

    # §9–§10 (yêu cầu 11/09): phần nhận xét TỪNG CÂU phải chỉ đích danh chỗ sai — từ nào,
    # âm nào, câu nào, lặp mấy lần, giây thứ mấy. "Bạn phát âm chưa chuẩn" là vô dụng: học
    # viên không biết sửa cái gì. Đi kèm là luật chống bịa: không đủ căn cứ thì nói thẳng
    # là không đủ căn cứ, chứ không nặn ra một lỗi nghe cho có.
    timestamp_rule = (
        "Give a mm:ss timestamp whenever you can hear where the fault happens."
        if has_audio else
        "There is no audio, so NEVER write a timestamp and never report a pronunciation, "
        "pausing, hesitation or filler-word fault — you cannot hear any of them."
    )

    return _v2_examples(common + f"""
9. Never label a sub-factor "Good"/"Fair"/"Weak". Give concrete EVIDENCE (the actual word,
   phrase, sentence, sound or structure) and its IMPACT on communication.
10. NAME THE THING THAT IS WRONG. A fault report that does not identify its target is
   worthless — the student cannot fix what you did not point at. Always pin down whichever
   of these apply: the exact word or phrase, the exact sentence, the phoneme AND the word
   containing it AND its position in that word, the repeated item and how many times, the
   grammar structure by name. {timestamp_rule} Count occurrences when you can ("good" x4).
11. NEVER INVENT A FAULT. If you cannot pin one down, drop it, or write exactly
   "Not enough evidence to identify a specific error." An honest silence beats a plausible guess: the
   student will go and drill whatever you name, so a made-up fault wastes their time and
   teaches them something false.
12. A pause is only a fault if it actually breaks the flow. Natural thinking pauses are
   how fluent speakers talk; do not report them.

For each criterion return: band, a general comment, diagnostic findings, 1-3 priority
improvements and 1-3 strengths. If there are only one or two real points, give one or two
— never pad the list to three.

For each question, besides the four scores, give a PER-CRITERION breakdown of that single
answer: one short verdict per criterion, the concrete faults you can point at in THAT
answer, and 1-3 things to do about them. This is what the student clicks into when they
want to know why one particular answer scored what it did, so it must be about that answer
alone — never a restatement of the part-level comment. If a criterion was genuinely fine in
that answer, say so in one line and leave "issues" empty.

What to look for in a single answer, per criterion:
- pronunciation: wrong phonemes (name the phoneme, the word, its position), dropped or
  swapped final sounds, word stress, sentence stress, intonation, rhythm, linking, weak
  forms, intelligibility.
- fluency_coherence: hesitation and pauses that break the flow, speech rate, filler words
  (list them with counts), self-correction (quote it), repeated words or phrases (with
  counts), how the ideas connect and develop.
- lexical_resource: wrong word choice, unnatural collocations, over-simple vocabulary
  (prove it with counts such as "good" x4), repetition, word formation, paraphrasing,
  topic vocabulary. Give the natural alternative in "correct".
- grammar: tense, subject-verb agreement, articles, prepositions, pronouns, word form,
  singular/plural, sentence structure. Put the student's sentence in "incorrect" and the
  fixed one in "correct", and name the error type in "label". Also judge RANGE with
  evidence ("5 of the 6 sentences are simple, only 1 is complex").

"relevance" (§9.1) is about the CONTENT of the answer, not a fifth IELTS criterion: did it
answer the question, go off topic, miss a part, stay too short, ramble, repeat itself,
develop the idea, give a reason, give an example. Be concrete — how many words, which part
is missing, what exactly to add next time. "The answer is a bit short" on its own is not
acceptable.

"audio_quality" (§9.6) is advice about the recording only — low volume, distant mic,
background noise, echo, clipping — with the time range when you can give one. It must
NEVER affect the four IELTS bands.

Write all commentary in clear, simple English that an intermediate learner can follow.
Keep IELTS terminology, and quote the candidate's own words exactly.

Return ONLY JSON in exactly this shape:
{{
  "criteria": {{
    "pronunciation":      {{"band": 6.5, "general_comment": "...", "diagnostics": [{{"factor": "Word Stress", "evidence": "...", "impact": "..."}}], "priorities": [{{"problem": "...", "why": "...", "impact": "...", "how": "..."}}], "strengths": [{{"strength": "...", "evidence": "...", "keep_doing": "..."}}]}},
    "fluency_coherence":  {{...same shape...}},
    "lexical_resource":   {{...same shape...}},
    "grammar":            {{...same shape...}}
  }},
  "questions": [
    {{"order_index": 0,
      "scores": {{"pronunciation": 6.0, "fluency_coherence": 6.5, "lexical_resource": 6.0, "grammar": 6.0}},
      "criteria": {{
        "pronunciation": {{
          "verdict": "one or two sentences about this answer alone",
          "issues": [
            {{"label": "Wrong phoneme", "quote": "think", "target": "/θ/", "position": "initial sound",
              "timestamp": "00:12", "count": 2,
              "problem": "what is wrong", "fix": "how to fix it"}}
          ],
          "how_to_improve": ["a short, concrete action"]
        }},
        "fluency_coherence": {{"verdict": "...", "issues": [{{"label": "Filler words", "quote": "um", "count": 2, "timestamp": "00:08", "problem": "...", "fix": "..."}}], "how_to_improve": []}},
        "lexical_resource":  {{"verdict": "...", "issues": [{{"label": "Collocation", "incorrect": "make exercise", "correct": "do exercise", "problem": "...", "fix": "..."}}], "how_to_improve": []}},
        "grammar":           {{"verdict": "...", "issues": [{{"label": "Verb tense", "incorrect": "I go to the gym yesterday.", "correct": "I went to the gym yesterday.", "problem": "...", "fix": "..."}}], "how_to_improve": []}}
      }},
      "relevance": "...",
      "comment": "...",
      "grammar_errors": [{{"incorrect": "...", "correct": "...", "type": "...", "impact": "..."}}],
      "vocabulary_errors": [{{"incorrect": "...", "better": "...", "why": "..."}}],
      "audio_quality": "..."}}
  ]
}}
Every field inside an issue except "problem" is optional — fill in the ones you can back
up and leave the rest out. "quote" must be the candidate's own words, copied exactly.

Sub-factors you may draw on, per criterion:
- Pronunciation: {SUBFACTORS['pronunciation']}
- Fluency & Coherence: {SUBFACTORS['fluency_coherence']}
- Lexical Resource: {SUBFACTORS['lexical_resource']}
- Grammar: {SUBFACTORS['grammar']}
""")


def _audio_part(path: str):
    """(mime, bytes) của một bản ghi, hoặc None nếu file đã bị dọn (§9).

    Global: `path` is an R2 key (speaking_storage); read it through the S3 API."""
    if not path:
        return None
    from app.utils import speaking_storage
    data = speaking_storage.load(path)
    if not data:
        return None
    mime = MIME_BY_EXT.get(os.path.splitext(path)[1].lower(), 'audio/ogg')
    return mime, data


def _band_or_none(value):
    """Model có thể trả "Insufficient evidence" thay cho điểm — đó là câu trả lời hợp lệ
    theo §5.2, không phải lỗi."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return None
    try:
        band = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(9.0, band))


def _align_questions(questions: list, expected: List[int], part_label: str) -> list:
    """Gắn lại `order_index` TOÀN BÀI cho từng câu model trả về.

    Vì sao không tin số model đưa: mỗi part là một lần gọi riêng, và model hay đếm lại từ
    0 trong phạm vi part. Part 1 (câu 0–8) vô tình trùng nên đúng; Part 2 trả 0–1 và
    Part 3 trả 0–6 thì GHI ĐÈ lên câu của Part 1, còn câu thật của Part 2/3 không ai gán.
    Hậu quả trên prod (feedback 19/09, bài #198): câu "Do you live in a house or an
    apartment?" mang nhận xét "…yêu cầu của Part 3… ô nhiễm…", còn Part 2/3 mất sạch
    điểm từng câu. Thi lẻ một part thì đếm cục bộ trùng đếm toàn bài nên không lộ.

    Thử lần lượt, cái nào khớp trọn bộ thì dùng:
      1. số model trả đã là order_index toàn bài                → giữ nguyên
      2. đếm từ 0 trong part                                    → i → expected[i]
      3. chép nhãn "Question N" (= order_index + 1)             → trừ 1
      4. đếm từ 1 trong part                                    → i → expected[i-1]
      5. không cách nào khớp mà số mục bằng số câu              → ghép theo thứ tự
    Còn lại thì bỏ những mục không ghép được — thà thiếu điểm một câu còn hơn gắn điểm
    của câu này sang câu khác.
    """
    items = [q for q in questions if isinstance(q, dict)]
    if not items or not expected:
        return []
    want = set(expected)

    def idx(q):
        try:
            return int(q.get('order_index'))
        except (TypeError, ValueError):
            return None

    got = [idx(q) for q in items]
    n = len(expected)
    mappings = (
        lambda i: i if i in want else None,
        lambda i: expected[i] if 0 <= i < n else None,
        lambda i: i - 1 if (i - 1) in want else None,
        lambda i: expected[i - 1] if 1 <= i <= n else None,
    )
    for how in mappings:
        mapped = [how(i) if i is not None else None for i in got]
        if None not in mapped and len(set(mapped)) == len(mapped):
            return [{**q, 'order_index': m} for q, m in zip(items, mapped)]

    if len(items) == n:
        logger.warning("Speaking grading %s: unexpected order_index %s, matching by position", part_label, got)
        return [{**q, 'order_index': m} for q, m in zip(items, expected)]

    # Ghép được câu nào thì ghép câu đó, theo đúng mã toàn bài; phần còn lại bỏ.
    kept, used = [], set()
    for q, i in zip(items, got):
        if i in want and i not in used:
            kept.append({**q, 'order_index': i})
            used.add(i)
    logger.warning("Speaking grading %s: matched only %d/%d questions (model returned %s, expected %s)",
                   part_label, len(kept), n, got, expected)
    return kept


def grade_part(part_label: str, answers: List, has_audio: bool,
               with_feedback: bool = True) -> dict:
    """Chấm một part bằng một lần gọi. `answers` là các dòng SpeakingAttemptAnswer.

    `with_feedback=False` → chỉ điểm, không nhận xét (tài khoản thường, feedback 09/09).
    """
    media = []
    lines = []
    total_bytes = 0
    dropped = 0
    for a in answers:
        text = (a.final_text or '').strip()
        # Ghi THẲNG mã cần trả về vào tiêu đề. Bản cũ chỉ có nhãn "Question 10" (tức
        # order_index + 1) trong khi mẫu JSON lại ghi "order_index": 0 — model phải tự
        # đoán cách quy đổi, và ở Part 2/3 nó đếm lại từ 0. Xem `_align_questions`.
        lines.append(f"--- Question {a.order_index + 1} ({a.part}) "
                     f"[order_index: {a.order_index}] ---\n"
                     f"Topic: {a.topic_title or '-'}\n"
                     f"Question: {a.question_text or '-'}\n"
                     f"Status: {a.answer_status}\n"
                     f"Transcript: {text or '(no answer)'}\n"
                     f"Speaking time: {round((a.duration_ms or 0) / 1000)}s")
        if not has_audio:
            continue
        clip = _audio_part(a.final_audio)
        if not clip:
            continue
        if total_bytes + len(clip[1]) > MAX_AUDIO_BYTES:
            dropped += 1
            continue
        total_bytes += len(clip[1])
        # Nói rõ clip nào của câu nào, nếu không model không biết ghép.
        media.append(('text', f"Recording for Question {a.order_index + 1} "
                              f"[order_index: {a.order_index}]:"))
        media.append(clip)
    if dropped:
        logger.warning("Speaking grading: dropped %d clips over the size cap", dropped)

    # Chèn nhãn dạng text xen giữa các clip: gemini_client chỉ nhận (mime, bytes) nên
    # nhãn đi kèm được gói lại ở đây thành phần văn bản của prompt thay vì phần media.
    labelled = []
    prefix = []
    for mime, blob in media:
        if mime == 'text':
            prefix.append(blob)
        else:
            labelled.append((mime, blob))
    user_text = "\n\n".join(lines)
    user_text += ("\n\nIn \"questions\", return exactly one entry per question above, in the "
                  "same order, and set \"order_index\" to the number shown in its "
                  "[order_index: N] tag — copy it, do not renumber from 0.")
    if prefix:
        user_text += ("\n\nThe recordings are attached in this order:\n"
                      + "\n".join(prefix))

    # Chấm bài chạy ở nền, không ai ngồi đợi trong request — nên khi Gemini báo bận thì
    # cứ chờ lâu hơn hẳn mặc định. Mất một bài đã chấm vì bỏ cuộc sau 3 lần × 3 giây thì
    # tệ hơn nhiều so với việc học viên thấy "đang chấm" thêm một phút.
    data = gemini_client.generate_json(
        _system_prompt(part_label, has_audio, with_feedback), user_text,
        media=labelled, model=MODEL,
        # Bản chỉ-điểm ra vài dòng JSON; giữ nguyên trần 32k là thừa, mà trần rộng thì
        # model dễ lan man. Bản đầy đủ vẫn cần cả trần vì có nhận xét từng câu.
        max_output_tokens=MAX_OUTPUT_TOKENS if with_feedback else 4096,
        think_longer=True, temperature=0.2,
        retries=5, busy_backoff=8.0)

    criteria = {}
    raw_criteria = data.get('criteria') or {}
    for key in CRITERIA:
        block = raw_criteria.get(key) or {}
        band = _band_or_none(block.get('band')) if key in scored_criteria(has_audio) else None
        # Chốt số nguyên NGAY tại đây, trước khi con số đi bất cứ đâu. Mọi phép trung bình
        # phía sau (Part Band, điểm tiêu chí cả bài, Overall) đều chạy trên điểm ĐÃ chốt,
        # đúng thứ tự bắt buộc ở mục 30 của tài liệu.
        #
        # Ở v2 model đã tự chốt số nguyên theo bằng chứng; dòng này chỉ là chốt chặn khi nó
        # lỡ trả số lẻ. Không được dùng nó để "sửa" quyết định của model — một số nguyên
        # model đưa ra thì đi qua nguyên vẹn (6 → 6, 7 → 7).
        if CALIBRATION_ENABLED:
            if CALIBRATION_VERSION in INTEGER_BAND_VERSIONS and band is not None and band != int(band):
                logger.warning("Speaking grading %s: %s returned %s (not whole), clamped by "
                               "rounding half up", part_label, key, band)
            band = calibrate_criterion(band)
        entry = {'band': band}
        # Con số làm việc của model (v2 mục 6: "internal reasoning MAY use 5.25, 6.2…").
        # Chỉ lưu để đối chiếu xem model nghiêng lên ở đâu, không bao giờ dùng để tính điểm.
        if CALIBRATION_VERSION in INTEGER_BAND_VERSIONS:
            internal = _band_or_none(block.get('internal'))
            if internal is not None:
                entry['internal'] = internal
        if with_feedback:
            entry.update({
                'general_comment': block.get('general_comment') or (
                    INSUFFICIENT if band is None else ''),
                'diagnostics': block.get('diagnostics') or [],
                'priorities': block.get('priorities') or [],
                'strengths': block.get('strengths') or [],
            })
        criteria[key] = entry

    questions = _align_questions(data.get('questions') or [],
                                 [a.order_index for a in answers], part_label)
    if CALIBRATION_ENABLED:
        # Điểm bốn tiêu chí của TỪNG CÂU cũng là điểm tiêu chí — ảnh trong tài liệu khoanh
        # đỏ đúng hai ô "Trôi chảy & Mạch lạc 6.5" và "Từ vựng 6.5" của một câu trả lời.
        for q in questions:
            scores = q.get('scores')
            if not isinstance(scores, dict):
                continue
            for key, value in list(scores.items()):
                raw = _band_or_none(value)
                if CALIBRATION_VERSION in INTEGER_BAND_VERSIONS and raw is not None and raw != int(raw):
                    logger.warning("Speaking grading %s: question %s, %s = %s (not whole)",
                                   part_label, q.get('order_index'), key, raw)
                fixed = calibrate_criterion(raw)
                if fixed is None:
                    scores.pop(key)     # bỏ hẳn, chứ để None thì phép trung bình vỡ
                else:
                    scores[key] = fixed
    return {'criteria': criteria, 'questions': questions}
