"""Everything the examiner says that is not a question from the bank.

docs/speaking-spec.md §4.3. These lines are identical in every test, so they are
synthesised once per voice and reused; only the two that name a topic vary, and those are
keyed per topic rather than per test.

Two of the Part 2 lines need flagging. The spec says the examiner "đọc 2 đoạn thoại
chuẩn" before reading the topic but does not quote them, so PART2_INTRO_1 and
PART2_INTRO_2 below use the wording a real examiner says. Worth confirming against the
source documents before this ships to students.
"""

# ── Fixed lines, one clip each per voice ──
SCRIPTS = {
    'opening': (
        "This is the Speaking Test for International English Language Testing System. "
        "Now in the first part, I'd like to ask some questions about yourself."
    ),
    'part2_intro_1': (
        "Now I'm going to give you a topic, and I'd like you to talk about it "
        "for one to two minutes."
    ),
    'part2_intro_2': (
        "Before you talk, you'll have one minute to think about what you're going to say. "
        "You can make some notes if you wish. Do you understand?"
    ),
    'part2_start': (
        "All right? Remember, you have one to two minutes for this, so don't worry if I "
        "stop you. I'll tell you when the time is up. Can you start speaking now, please?"
    ),
    'part2_stop': "Thank you.",
    'closing': "Thank you very much. That is the end of the Speaking Test.",
}

# Countdown spoken between the note minute and the long turn (spec: "1 → 2 → 3").
COUNTDOWN = {f'countdown_{n}': str(n) for n in (1, 2, 3)}
SCRIPTS.update(COUNTDOWN)


# ── Lines that name a topic — one clip per topic ──
def topic_intro(title: str) -> str:
    """Said before the first question of each Part 1 topic."""
    return f"Let's talk about {title}."


def part3_intro(title: str) -> str:
    """Bridges the Part 2 long turn into Part 3, naming the topic just discussed."""
    return (f"We've been talking about {title}, and I'd like to discuss with you "
            f"one or two more general questions related to this. "
            f"Let's consider first of all…")


# ── Cache keys ──
# One flat string per clip keeps the storage table to a single unique index instead of a
# nullable-column-per-kind arrangement that has to grow every time a new kind appears.

def key_for_script(name: str) -> str:
    return f"script:{name}"


def key_for_question(question_id: int) -> str:
    return f"question:{question_id}"


def key_for_topic_intro(topic_id: int) -> str:
    return f"topic-intro:{topic_id}"


def key_for_part3_intro(topic_id: int) -> str:
    return f"part3-intro:{topic_id}"
