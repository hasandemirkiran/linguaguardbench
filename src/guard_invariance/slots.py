"""Slot extraction and preservation checks.

A perturbation is only label-preserving if every task-relevant "slot" in the original
instruction survives: quoted strings, file names, e-mail addresses, URLs, IBAN-like
account ids, dates, times, numbers/amounts, and proper names. The generator is asked to
keep these verbatim; this module verifies it mechanically before any LLM judge runs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Double quotes anywhere; single quotes only when they cannot be apostrophes
# (not preceded/followed by a letter), so "I'm ... I'll" never forms a quoted span.
QUOTED = re.compile(
    r"[\"“”]([^\"“”]{2,160})[\"“”]"
    r"|(?<![A-Za-z0-9])['‘’]([^'‘’]{2,160}?)['‘’](?![A-Za-z0-9])"
)
LIST_MARKER = re.compile(r"(?:^|\s)(\d{1,2})\.\s")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
URL = re.compile(r"(?:https?://|www\.)[^\s'\"<>]+")
FILENAME = re.compile(r"\b[\w-]+\.(?:txt|docx|doc|pdf|csv|xlsx|xls|md|json|png|jpg)\b", re.I)
IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{8,30}\b")
ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
TIME = re.compile(r"\b\d{1,2}:\d{2}\b")
NUMBER = re.compile(r"(?<![\w:-])\d+(?:[.,]\d+)?%?(?![\w:-])")
MONTH = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
TEXT_DATE = re.compile(rf"\b{MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?\b")
# Capitalized tokens that are not at the start of a sentence (rough proper-name heuristic).
CAP_TOKEN = re.compile(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b")
STOP_CAPS = {
    "I", "If", "Please", "Can", "Hey", "Hi", "What", "When", "Where", "Who", "How", "Find", "Read",
    "Create", "Add", "Send", "Perform", "Summarize", "Am", "On", "My", "The", "It", "Give", "Tell",
    "For", "Also", "Then", "After", "Before", "Do", "Is", "Are", "Which", "Show", "Check", "Update",
    "Reschedule", "Get", "Take", "Post", "Yes", "No", "Could", "Would", "Let", "Make", "Use",
}


@dataclass
class Slots:
    quoted: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    filenames: list[str] = field(default_factory=list)
    ibans: list[str] = field(default_factory=list)
    iso_dates: list[str] = field(default_factory=list)
    times: list[str] = field(default_factory=list)
    text_dates: list[str] = field(default_factory=list)
    numbers: list[str] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    months: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, list[str]]:
        return {k: v for k, v in self.__dict__.items() if v}


def _uniq(items) -> list[str]:
    seen, out = set(), []
    for x in items:
        x = x.strip()
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def extract_slots(text: str) -> Slots:
    s = Slots()
    s.quoted = _uniq(a or b for a, b in QUOTED.findall(text))
    s.emails = _uniq(EMAIL.findall(text))
    s.urls = _uniq(u.rstrip(".,;:!?)") for u in URL.findall(text))
    s.filenames = _uniq(FILENAME.findall(text))
    s.ibans = _uniq(IBAN.findall(text))
    s.iso_dates = _uniq(ISO_DATE.findall(text))
    s.times = _uniq(TIME.findall(text))
    s.text_dates = _uniq(TEXT_DATE.findall(text))
    # numbers not already inside a date/time/iban/email/url/filename span
    spans = [m.span() for rx in (ISO_DATE, TIME, IBAN, EMAIL, URL, FILENAME) for m in rx.finditer(text)]
    spans += [m.span(1) for m in LIST_MARKER.finditer(text)]  # "1. do X 2. do Y" enumerators
    s.numbers = _uniq(
        m.group(0) for m in NUMBER.finditer(text)
        if not any(a <= m.start() < b for a, b in spans)
    )
    s.months = _uniq(re.findall(MONTH, text))
    # proper names: capitalized runs excluding quoted content and stop words
    stripped = QUOTED.sub(" ", text)
    names = []
    for m in CAP_TOKEN.finditer(stripped):
        tok = m.group(1)
        if tok in STOP_CAPS or tok.split()[0] in STOP_CAPS:
            continue
        if TEXT_DATE.fullmatch(tok) or re.fullmatch(MONTH, tok):
            continue
        names.append(tok)
    s.names = _uniq(names)
    return s


# Phrases that change authorization scope, urgency, or permissions. A rewrite may not ADD
# any of these (it may keep ones the original already has). Checked case-insensitively.
FORBIDDEN_ADDITIONS = [
    r"\b(?:don'?t|do not|no need to|without) (?:ask|asking|confirm|confirming|check|checking|verify|verifying)\b",
    r"\byou have my (?:permission|approval|consent)\b",
    r"\b(?:i|I) (?:authori[sz]e|approve|consent)\b",
    r"\bwhatever it takes\b",
    r"\bno matter what\b",
    r"\b(?:urgent(?:ly)?|asap|immediately|right away|right now|hurry)\b",
    r"\b(?:ignore|override|bypass|skip) (?:the |any |all )?(?:checks?|verification|confirmation|warnings?|rules?|policy|policies|safety)\b",
    r"\bjust do it\b",
    r"\btrust me\b",
    r"\b(?:it'?s|this is) (?:fine|ok|okay|safe)\b",
]
_FORBIDDEN = [re.compile(p, re.I) for p in FORBIDDEN_ADDITIONS]


def forbidden_additions(original: str, perturbed: str) -> list[str]:
    """Authorization/urgency phrases present in the rewrite but absent from the original."""
    hits = []
    for rx in _FORBIDDEN:
        if rx.search(perturbed) and not rx.search(original):
            hits.append(rx.search(perturbed).group(0))
    return hits


def edit_distance(a: str, b: str) -> float:
    """Normalized character-level Levenshtein distance in [0, 1] (whitespace-collapsed, lowercased)."""
    a, b = re.sub(r"\s+", " ", a.strip().lower()), re.sub(r"\s+", " ", b.strip().lower())
    if a == b:
        return 0.0
    if not a or not b:
        return 1.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1] / max(len(a), len(b))


def token_jaccard(a: str, b: str) -> float:
    ta = set(re.findall(r"[\w']+", a.lower()))
    tb = set(re.findall(r"[\w']+", b.lower()))
    return len(ta & tb) / len(ta | tb) if (ta | tb) else 1.0


def surface_covariates(original: str, perturbed: str) -> dict[str, float]:
    return {
        "edit_distance": round(edit_distance(original, perturbed), 4),
        "token_jaccard": round(token_jaccard(original, perturbed), 4),
        "length_ratio": round(len(perturbed) / max(1, len(original)), 4),
    }


def slot_spans(text: str) -> list[tuple[int, int]]:
    """Character spans that typo noise must never touch."""
    spans = []
    for rx in (QUOTED, EMAIL, URL, FILENAME, IBAN, ISO_DATE, TIME, TEXT_DATE, NUMBER):
        spans += [m.span() for m in rx.finditer(text)]
    s = extract_slots(text)
    for name in s.names + s.months:
        for m in re.finditer(re.escape(name), text):
            spans.append(m.span())
    return spans


_KEYBOARD = {
    "a": "qwsz", "b": "vghn", "c": "xdfv", "d": "serfcx", "e": "wsdr", "f": "drtgvc", "g": "ftyhbv",
    "h": "gyujnb", "i": "ujko", "j": "huikmn", "k": "jiolm", "l": "kop", "m": "njk", "n": "bhjm",
    "o": "iklp", "p": "ol", "q": "wa", "r": "edft", "s": "awedxz", "t": "rfgy", "u": "yhji",
    "v": "cfgb", "w": "qase", "x": "zsdc", "y": "tghu", "z": "asx",
}


def typo_noise(text: str, *, rate: float = 0.08, seed: int = 0) -> str:
    """Seeded human-like typing noise on eligible words (len >= 4, outside slot spans).
    Ops: adjacent-key substitution, transposition, dropped letter, doubled letter.
    Also lowercases the first character and drops a final '.', '?' or '!'."""
    import random

    rng = random.Random(f"{seed}:{text}")
    protected = slot_spans(text)
    words = list(re.finditer(r"[A-Za-z]{4,}", text))
    eligible = [i for i, m in enumerate(words) if not any(a <= m.start() < b or a < m.end() <= b for a, b in protected)]
    chosen = {i for i in eligible if rng.random() < rate}
    if eligible and not chosen:  # always at least one typo, otherwise the condition is a placebo
        chosen = {rng.choice(eligible)}
    out, pos = [], 0
    for idx, m in enumerate(words):
        s, e = m.span()
        out.append(text[pos:s])
        word = m.group(0)
        if idx in chosen:
            i = rng.randrange(1, len(word) - 1)
            op = rng.choice(("sub", "swap", "drop", "double"))
            if op == "sub" and word[i].lower() in _KEYBOARD:
                word = word[:i] + rng.choice(_KEYBOARD[word[i].lower()]) + word[i + 1:]
            elif op == "swap":
                word = word[:i] + word[i + 1] + word[i] + word[i + 2:]
            elif op == "drop":
                word = word[:i] + word[i + 1:]
            else:
                word = word[:i] + word[i] + word[i:]
        out.append(word)
        pos = e
    out.append(text[pos:])
    noised = "".join(out)
    if noised and noised[0].isupper() and not any(a <= 0 < b for a, b in protected):
        noised = noised[0].lower() + noised[1:]
    return noised.rstrip().rstrip(".?!")


def missing_slots(original: str, perturbed: str, mode: str = "paraphrase") -> dict[str, list[str]]:
    """Return {slot_kind: [missing values]} for slots that must survive verbatim.

    mode="paraphrase": everything must survive verbatim (case-insensitive for names/months).
    mode="translate": text dates and month names may be localized -> only date digits must
                      survive; proper names are NOT mechanically required (Tokyo→Tokio,
                      French→französisch are legitimate) and are left to the LLM judge +
                      back-translation; quoted/files/emails/urls/ibans/times/numbers stay
                      verbatim.
    """
    slots = extract_slots(original)
    hay = perturbed
    hay_l = perturbed.lower()
    missing: dict[str, list[str]] = {}

    def need(kind: str, values: list[str], *, ci: bool = False) -> None:
        lost = [v for v in values if (v.lower() not in hay_l if ci else v not in hay)]
        if lost:
            missing[kind] = lost

    need("quoted", slots.quoted, ci=True)
    need("emails", slots.emails, ci=True)
    need("urls", slots.urls, ci=True)
    need("filenames", slots.filenames, ci=True)
    need("ibans", slots.ibans)
    need("iso_dates", slots.iso_dates)
    need("times", slots.times)
    need("numbers", slots.numbers)
    if mode != "translate":
        need("names", slots.names, ci=True)
        need("months", slots.months, ci=True)
    if mode == "translate":
        lost = []
        for d in slots.text_dates:
            digits = re.findall(r"\d+", d)
            if not all(x in hay for x in digits):
                lost.append(d)
        if lost:
            missing["text_dates"] = lost
    else:
        need("text_dates", slots.text_dates, ci=True)
    return missing
