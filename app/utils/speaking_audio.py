"""Chuẩn hoá bản ghi câu trả lời về Opus (docs/speaking-spec.md quyết định #10, §9).

Trình duyệt trả về mỗi máy một kiểu — Chrome cho webm/opus, Safari cho mp4/aac. Hai vấn
đề đi kèm: dung lượng (đĩa cloud chỉ còn 28GB, §9 bắt buộc phải tiết kiệm) và Gemini
không nhận `audio/webm` khi chấm. Nén một lần ngay lúc nhận giải quyết cả hai, và 16kbps
mono là đúng con số spec chốt.

ffmpeg hỏng thì GIỮ NGUYÊN file gốc: mất chất lượng nén còn hơn mất câu trả lời của học
viên. Lúc chấm sẽ gửi đúng mime của file gốc.
"""
import logging
import os
import tempfile
import subprocess

logger = logging.getLogger(__name__)

BITRATE = "16k"
TIMEOUT = 60


# Dưới ngưỡng này coi như KHÔNG có tiếng. Dùng ĐỈNH cho trường hợp im lặng số tuyệt đối
# (mic tắt hẳn), còn quyết định chính nằm ở `sustained_db` bên dưới.
SILENT_PEAK_DB = -50.0

# Mức âm thanh DUY TRÌ (phân vị 90 của các khung 30 ms) — đây mới là thứ phân biệt được im
# lặng thật với giọng nói. Số đo 20/09 trên bản ghi THẬT từ MacBook + Chrome của chủ dự án:
#   bấm ghi âm rồi ngồi im   -52,9 · -53,6 · -54,3 · -67,9 dB
#   đọc thật một từ          -21,0 dB          → cách nhau hơn 30 dB
#   (mẫu giọng tổng hợp bị hạ 30 dB: -42,8 … -48,1 dB — vẫn trên ngưỡng)
#
# TẠI SAO KHÔNG DÙNG ĐỈNH: bản ghi im lặng "dig" có đỉnh -25,8 dB chỉ vì một tiếng click
# lúc bấm nút, trong khi 90% thời lượng vẫn ở -52,9. Ngưỡng theo đỉnh (bản 19/09) vì thế
# cho im lặng lọt qua và AI chấm 95/100 — đúng lỗi chủ dự án báo.
SPEECH_LEVEL_DB = -50.0


def sustained_db(raw: bytes):
    """Mức âm thanh duy trì: phân vị 90 của RMS các khung 30 ms. None nếu quá ngắn.

    Một tiếng click hay tiếng gõ bàn phím chỉ chiếm một hai khung nên không kéo được con
    số này lên; phải có tiếng nói kéo dài thì nó mới cao.
    """
    levels = _frame_levels(raw)
    if levels is None or len(levels) < 10:
        return None
    return levels[len(levels) * 9 // 10]


def peak_db(raw: bytes):
    """Âm lượng ĐỈNH của bản ghi (dB, 0 = tối đa), đo bằng ffmpeg volumedetect.

    None nếu không đo được — người gọi phải coi None là "không biết", không phải "im
    lặng": ffmpeg trục trặc thì để AI nghe như cũ, đừng chặn oan học viên.
    """
    if not raw:
        return None
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", "pipe:0",
             "-af", "volumedetect", "-f", "null", "-"],
            input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT)
        for line in proc.stderr.decode(errors='ignore').splitlines():
            if 'max_volume:' in line:
                return float(line.split('max_volume:')[1].split('dB')[0].strip())
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as e:
        logger.warning("Could not measure audio volume: %s", e)
    return None


# Nhận ra ÂM ĐƠN (bíp, còi) — hai điều kiện phải CÙNG đúng, đo trên trung vị các khung có
# tiếng. Số đo 19/09 sau khi qua Opus như bản ghi thật:
#                                   độ trải phổ   entropy phổ
#   âm đơn 220/440/1000 Hz, bíp ngắt  155-439 Hz   0,034-0,043
#   giọng người bình thường          1.089-1.834   0,107-0,293
#   giọng qua mic kém/điện thoại        493-929    0,074-0,201
#   giọng vừa rè vừa rất nhỏ            542-774    0,007-0,013  ← entropy vô dụng khi nhỏ
# Chỉ trải phổ thì giọng mic kém (493) lọt xuống vùng âm đơn; chỉ entropy thì giọng nhỏ
# (0,007) lọt xuống. Ghép hai cái thì giọng người phải VỪA rè VỪA dải hẹp mới bị nhầm.
#
# Ngưỡng cố ý BẢO THỦ: chặn oan một học viên mic kém là họ không luyện được, còn để lọt
# một tiếng bíp chỉ xảy ra khi có người cố tình thử. Ca giọng người tệ nhất đã thử (từ "of",
# lọc 1.500 Hz, hạ 30 dB) = 542 Hz, còn cách ngưỡng 82 Hz. Mỗi lần chặn đều ghi log để
# theo dõi chặn nhầm trên prod.
TONE_SPREAD_HZ = 460.0
TONE_ENTROPY = 0.05
_VOICED_RMS_DB = -60.0      # khung nhỏ hơn mức này là khoảng lặng, không tính


def looks_like_tone(raw: bytes):
    """True nếu bản ghi chỉ là một âm đơn (bíp, còi), False nếu không, None nếu không đo
    được. Dùng ffmpeg `aspectralstats` vì máy prod không có numpy, và thêm thư viện thì
    phải build lại image backend 2GB.

    Vì sao cần phép đo này chứ không để AI nghe: thử 19/09, lượt nghe mù của Gemini vẫn
    khẳng định có người nói trong 3/4 lần nghe tiếng bíp ("why don't you look inside").
    """
    if not raw:
        return None
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", "pipe:0", "-ac", "1", "-ar", "16000",
             "-af", "aspectralstats=win_size=1024:measure=spread+entropy,"
                    "astats=metadata=1:reset=1:measure_overall=RMS_level:measure_perchannel=none,"
                    "ametadata=print:file=-",
             "-f", "null", "-"],
            input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("Could not measure audio spectrum: %s", e)
        return None
    spreads, entropies, cur = [], [], {}
    for line in proc.stdout.decode(errors='ignore').splitlines() + ['frame:']:
        if line.startswith('frame:'):
            if {'spread', 'entropy', 'rms'} <= cur.keys() and cur['rms'] > _VOICED_RMS_DB:
                spreads.append(cur['spread'])
                entropies.append(cur['entropy'])
            cur = {}
            continue
        key = ('spread' if '.spread=' in line else 'entropy' if '.entropy=' in line
               else 'rms' if 'RMS_level=' in line else None)
        if key:
            try:
                cur[key] = float(line.split('=', 1)[1])
            except ValueError:
                pass
    if len(spreads) < 5:
        return None             # quá ít khung có tiếng để kết luận gì
    median = lambda xs: sorted(xs)[len(xs) // 2]
    spread, entropy = median(spreads), median(entropies)
    tone = spread < TONE_SPREAD_HZ and entropy < TONE_ENTROPY
    if tone:
        logger.info("Recording detected as a pure tone: spread %.0f Hz, entropy %.3f, %d frames",
                    spread, entropy, len(spreads))
    return tone


# Âm thanh ĐỀU ĐỀU theo thời gian — tiếng ồn nền, quạt, xe, còi — không phải giọng người.
# Đo độ dao động âm lượng giữa các khung 30 ms (khoảng giữa phân vị 90 và 10). Số đo 19/09:
#   ồn trắng/hồng/nâu, bíp, sóng vuông     0,0-4,8 dB
#   giọng người tệ nhất (từ "of" rè, nhỏ)  10,7 dB · bình thường 17-42 dB
#   giọng người LẪN TRONG tiếng ồn          20-27 dB
# Giọng người lên xuống theo từng âm tiết nên luôn dao động mạnh; ngưỡng 7 dB nằm giữa hai
# nhóm. Cần phép đo này vì lượt nghe mù của Gemini thỉnh thoảng tưởng tượng ra chữ trong
# tiếng ồn ("dog"), và với một từ đứng riêng thì không so khớp chữ được (xem
# speaking_improve.speech_problem) — thử 19/09: ồn trắng + "green" = 95 điểm.
STEADY_SWING_DB = 7.0


def _frame_levels(raw: bytes):
    """RMS (dB) của từng khung 30 ms, đã sắp xếp tăng dần. None nếu không đo được.

    GIỮ CẢ khung im lặng (chỉ bỏ khung -inf, tức không có mẫu nào). Bản 19/09 bỏ mọi khung
    dưới -70 dB, và đó là lỗi: một bản ghi im lặng dài 1 giây có 102 khung thì 83 khung là
    im tuyệt đối, chỉ còn 19 khung của tiếng click — lọc xong thì mức duy trì tính ra
    -45,8 dB (nghe như có người nói) thay vì -67,9 dB (đúng bản chất im lặng).
    """
    if not raw:
        return None
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", "pipe:0", "-ac", "1", "-ar", "16000",
             "-af", "asetnsamples=n=480,"
                    "astats=metadata=1:reset=1:measure_overall=RMS_level:measure_perchannel=none,"
                    "ametadata=print:file=-",
             "-f", "null", "-"],
            input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("Could not measure per-frame volume: %s", e)
        return None
    levels = []
    for line in proc.stdout.decode(errors='ignore').splitlines():
        if 'RMS_level=' in line:
            try:
                v = float(line.split('=', 1)[1])
            except ValueError:
                continue
            if v > -200:            # bỏ -inf (khung rỗng), giữ mọi thứ còn lại
                levels.append(v)
    levels.sort()
    return levels


def is_steady(raw: bytes):
    """True nếu âm lượng đều đều suốt bản ghi (tiếng ồn/âm đơn, không có người nói), False
    nếu có dao động kiểu lời nói, None nếu không đo được hoặc quá ngắn."""
    levels = _frame_levels(raw)
    if levels is None or len(levels) < 10:
        return None
    swing = levels[len(levels) * 9 // 10] - levels[len(levels) // 10]
    if swing < STEADY_SWING_DB:
        logger.info("Recording has a steady level (swing %.1f dB, %d frames)", swing, len(levels))
        return True
    return False


# ── Chẩn đoán cửa kiểm (TẠM — gỡ khi đã chỉnh xong ngưỡng trên bản ghi thật) ───────────
#
# 19/09: ngưỡng ở trên chỉnh bằng âm thanh TỔNG HỢP; im lặng thật qua micro trình duyệt
# (tự tăng âm lượng, khử ồn, tiếng click lúc bấm nút) vẫn lọt và được chấm 95/100. Muốn
# chỉnh đúng thì phải có số đo của bản ghi thật, nên tạm thời ghi số đo của MỌI lượt vào
# log (mức WARNING, vì uvicorn prod chạy --log-level warning), và lưu nguyên bản ghi của
# ba tài khoản test — tức tài khoản của chính chủ dự án, không phải của học viên.
# Global: kept in R2 under speaking/_gate_debug/ (app/utils/speaking_storage.py), never on
# the ephemeral Koyeb disk. Name retained for reference only.
GATE_DEBUG_DIR = "speaking/_gate_debug"


def gate_metrics(raw: bytes) -> dict:
    """Mọi số đo mà cửa kiểm dùng, gom một chỗ để ghi log."""
    out = {'bytes': len(raw or b''), 'duration_ms': probe_duration_ms(raw),
           'peak_db': peak_db(raw), 'tone': looks_like_tone(raw), 'steady': is_steady(raw)}
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", "pipe:0", "-ac", "1", "-ar", "16000",
             "-af", "asetnsamples=n=480,"
                    "astats=metadata=1:reset=1:measure_overall=RMS_level:measure_perchannel=none,"
                    "ametadata=print:file=-",
             "-f", "null", "-"],
            input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT)
        lv = []
        for line in proc.stdout.decode(errors='ignore').splitlines():
            if 'RMS_level=' in line:
                try:
                    lv.append(float(line.split('=', 1)[1]))
                except ValueError:
                    pass
        lv = [x for x in lv if x > -200]
        out['frames'] = len(lv)
        for cut in (-70, -60, -50, -40, -30):
            out[f'fr>{cut}'] = sum(1 for x in lv if x > cut)
        if lv:
            srt = sorted(lv)
            out['rms_p50'] = round(srt[len(srt) // 2], 1)
            out['rms_p90'] = round(srt[len(srt) * 9 // 10], 1)
            out['rms_max'] = round(srt[-1], 1)
    except (OSError, subprocess.SubprocessError) as e:
        out['err'] = str(e)[:80]
    return out


def keep_for_debug(raw: bytes, tag: str) -> None:
    """Lưu một bản ghi của tài khoản test để đo lại ngưỡng. Hỏng thì bỏ qua.

    Global: uploads to R2 (private key) instead of writing to local disk."""
    import re, time
    try:
        from app.utils import speaking_storage
        safe = re.sub(r'[^a-zA-Z0-9_-]+', '_', tag)[:60]
        speaking_storage.save_gate_debug(raw, f"{time.strftime('%m%d-%H%M%S')}_{safe}.ogg")
    except Exception as e:
        logger.warning("Could not store gate debug recording: %s", e)


def probe_duration_ms(raw: bytes):
    """Thời lượng thật của một đoạn audio, tính bằng ffprobe. None nếu không đọc được.

    Trước đây thời lượng clip giám khảo được ĐOÁN từ kích thước file (`len * 8 / 16`, tức
    giả định 16 kbps cố định). Opus là mã hoá biến thiên nên con số đó lệch rất xa — đo
    lại ngày 06/09 thấy sai tới 30-60%. Nó không chỉ là con số hiển thị: phòng thi dùng
    thời lượng này làm đồng hồ chặn cho trường hợp thẻ <audio> không bao giờ bắn `ended`,
    nên sai thời lượng nghĩa là cắt lời giám khảo giữa chừng hoặc bắt thí sinh ngồi đợi.
    """
    if not raw:
        return None
    # PHẢI ghi ra file tạm, không đưa qua đường ống: ffprobe cần tua lại để đọc thời lượng,
    # mà pipe thì không tua được — nó trả về rỗng và ta lại rơi về đúng phép ước lượng sai
    # đang muốn bỏ. Đây chính là cái bẫy làm phép đo đầu tiên ngày 06/09 ra y hệt số cũ.
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as fh:
            fh.write(raw)
            tmp = fh.name
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", tmp],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT)
        return int(float(proc.stdout.decode().strip()) * 1000)
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError) as e:
        logger.warning("Could not measure audio duration: %s", e)
        return None
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


def to_opus(raw: bytes):
    """(bytes, đuôi file). Trả về (raw, None) nếu không nén được."""
    if not raw:
        return raw, None
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error",
             "-i", "pipe:0",
             "-ac", "1", "-c:a", "libopus", "-b:a", BITRATE,
             "-f", "ogg", "pipe:1"],
            input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("Could not compress Speaking recording: %s", e)
        return raw, None
    if proc.returncode != 0 or not proc.stdout:
        logger.warning("ffmpeg failed compressing recording: %s",
                       proc.stderr.decode("utf-8", "replace")[:200])
        return raw, None
    return proc.stdout, ".ogg"
