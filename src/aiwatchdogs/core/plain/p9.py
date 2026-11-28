"""Plain-language writer for P9: government exams delayed, postponed, moved, cancelled or re-run.

P9 findings come in two shapes. Bulk-collected ones carry a templated outcome sentence and a noisy
exam name scraped from a notice list, so the text is built from the dates, the delay and the
disruption type. Hand-researched ones carry a readable stage line and two readable summary
sentences, so the headline is built from the exam name and stage line and the brief from the
summaries. A few large families (vacancy tables, APAAR ID gaps, seat additions) get their own
sentence because "delayed by N days" would say more than those records show.
"""

from __future__ import annotations

import re
from typing import Any

from ..safety import scan_forbidden_terms
from ._common import clean, fmt_date, fmt_days, fmt_num, metrics, num, parse_date

_HEAD_SOFT = 100
_HEAD_HARD = 110
_BRIEF_SOFT = 250

_REASONS: dict[str, str] = {
    "judicial_or_policy_review": "court order or policy review",
    "technical_or_cbt_glitch": "technical or computer-based test glitch",
    "election_or_calendar_clash": "election schedule or calendar clash",
    "weather_or_disaster": "severe weather or natural disaster",
    "paper_integrity_compromise": "question paper security",
}

# Stage labels the bulk collector assigns; anything else is a hand-written stage line.
_GENERIC_STAGES: dict[str, str] = {
    "recruitment examination": "exam",
    "preliminary examination": "preliminary exam",
    "mains written examination": "mains exam",
    "written examination": "written exam",
    "interview / viva-voce": "interview",
    "physical measurement / screening test": "physical or screening test",
    "screening test": "screening test",
    "skill / typing test": "skill test",
    "computer-based recruitment test": "computer-based test",
    "subject knowledge test": "subject knowledge test",
    "document verification / scrutiny": "document verification",
    "scheduled cycle": "exam",
}
_GENERIC_STAGE_RE = re.compile(r"^(.+?)\s*(?:\((\d{4})\))?$")
_CYCLE_RE = re.compile(r"^((?:[A-Z][a-z]+ )?\d{4}(?:[–-]\d{2,4})?) Cycle\b(.*)$")

_EVENT_WORD = re.compile(
    r"cancel|postpon|reschedul|delay|defer|withheld|withhold|re-?conduct|re-?test|re-?exam|glitch|"
    r"corrigendum|extended|extension|revis|preponed|dropped|discontinu|suspen|swapped|change|shift|held in",
    re.IGNORECASE,
)

_OCR_FIXES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bIlaryana\b"), "Haryana"),
    (re.compile(r"\bInteview\b", re.IGNORECASE), "Interview"),
    (re.compile(r"\bcoltege\b", re.IGNORECASE), "college"),
    (re.compile(r"\bCORRIGEN\w?UM\b", re.IGNORECASE), "Corrigendum"),
    (re.compile(r"\b(\w+)( \1\b)+"), r"\1"),
)

_GARBAGE = re.compile(r"ADDENDUM_|Download|[%<>|]|__|\.pdf\b|\[\d+\]|\b(?:IAS|IPS|Secretary|Secy|Controller|Coniroller)\b")
_NOTICE_START = re.compile(
    r"^(?:public\s+)?(?:notice|notification|corrigendum|addendum|announcement|declaration|revised?|final|"
    r"syllabus|eligibility list|conducting|cancellation|supplementary|omr|schedule|result|important)\b",
    re.IGNORECASE,
)
_POST_OF = re.compile(r"(?:\bposts?\s+of|\(P[MS]T\)\s+(?:of|for))\s+(?:the\s+)?(.+)$", re.IGNORECASE)
_POST_TAIL = re.compile(
    r"\s*(?:\(Advt|\bagainst\b|\bvide\b|\bin compliance\b|\bunder\b|\balong\s?with\b|,\s*Cat\b|\bCat\.? No\b|"
    r"\bin the\b|\bin\s+[A-Z]|\bof\s+[A-Z][a-z]+\s+Department\b|\bfor\s+Rest\b|\bheld on\b|\bdated\b|\.\s).*$"
)
_ADVT_PREFIX = re.compile(r"^Advt\.?\s*No\.?\s*[\w/.-]+(?:\s+of\s+\d{4})?\s*[-–—:]\s*", re.IGNORECASE)
_TRAILING_NOTICE = re.compile(
    r"(?:\s*[-–,&]?\s*\(?\b(?:final|revised|supplementary|provisional|measurement)?\s*"
    r"(?:answer keys?(?: challenge)?|viva[ -]voce|results?|notifications?|corrigendum|addendum|notice|rankings?)\b\)?)+\s*$",
    re.IGNORECASE,
)
_ADVT_PAREN = re.compile(r"\s*\((?:Advt|Notif|GoAP)[^)]*\)?", re.IGNORECASE)
_PAREN = re.compile(r"\s*\([^()]*\)")
_BRACKET = re.compile(r"\[([^\[\]]{2,24})\]")
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_DOT_DATE = re.compile(r"\b\d{2}\.\d{2}\.\d{4}\b")
_SENTENCE_END = re.compile(
    r"(?<!\bDr)(?<!\bMr)(?<!\bMs)(?<!\bSt)(?<!\bNo)(?<!\bSmt)(?<!\bvs)(?<=[a-z0-9\)%])\.\s+(?=[A-Z])"
)

_SHORT_UPPER_WORDS_TEXT = (
    "male female post posts test list the and for of to in on at by no cat advt date exam held with from "
    "against group notice"
)
_SHORT_UPPER_WORDS = frozenset(_SHORT_UPPER_WORDS_TEXT.split())

# Ordinary words that hand-written stage lines put in Title Case. Words not listed keep their case,
# so place names and acronyms survive.
_COMMON_WORDS_TEXT = """absence access across action addendum addition additional advanced admission admissions admit admitted
    advertisement advertisements advisory affected affiliated affiliation against allocation allowed
    amendment and annexure announcement annual answer appeals appointment appointments approval approved
    attacks attending audit backlog bar barcode barred based before both bound cadre calendar cancellation
    cancelled candidate candidates cap card category centre centres certificate challenge change cities
    city clash code codes compliance conduct conducting confirmation consolidated constraints continuation
    continued controversy coordinated copies copying correction corrigendum counselling course cover
    cut cyber cycle data dated dates day deadline declaration deferment deferral deferred deficit delay
    delayed denied despite direct direction directives disaffiliated disclosure discontinued disruptions
    document documents down downgraded draft drive dropped edit eligible enquiry entry evaluation
    exam examination examinations exams exempting exemption expanding extended extending extension extra
    faculty fee filling final first flood for format four framework friction from further gap glitch
    grievance grievances guidelines hall halting heavy held historical hold hybrid implementation important
    in inclusion inconsistencies infrastructure initial inspection integration integrity interview judgment
    judicial jump jurisdiction key keys knowledge last lateral launched leak level levy linkage list lowering main
    mains mandating mandatory matrix measurement mid minute missing mode modified module month months multi
    multiple nationwide new no non notice notification of off official officials omission on online optional
    order ordered orders other over overhaul overhauled pan paper papers part peak pen pending per percentile
    personal petition petitioners phase physical placed policy portal post postponed postponement posts
    practical pre prelim preliminary prelims preparedness preponed procedure proceedings process processed
    promised proposal provisional provisionally put qualifying question quota rain rank re readvertisement
    reappearing recruitment reduction refund region regional registered registrants registration regular
    reintroduction release released relocation removal reopened reopening replacing reports representations
    requests requirement reschedule rescheduled rescheduling reservation reserve reserved residual restart
    result results retest review revised revision rolling round rule rules sanctioned scanned schedule
    scheduled schools screening scrutiny sealed seat seats second secondary section security select
    selected selection selections senior sequence session share shift shifts shortfall shortlist shortlisted
    single skill slot snowfall special specific sponsoring sports spot stage staged standing statutory still
    stray student subject supplementary suspension swapped syllabus system systemic table teaching technical
    tenure test testing the timeline to tracking transition two typing under unfilled update updates upload
    upto vacancies vacancy vacant verification week window winter wise with withheld without written year
    zero"""
_COMMON_WORDS = frozenset(_COMMON_WORDS_TEXT.split())

# A summary sentence carrying these words is not reused as a brief; the fixed sentence is used instead.
_CAUTION = re.compile(r"alleg|irregularit|arrest|malpractice|illegal|unfair means|cheat|copying|leak|accus", re.IGNORECASE)


def _text(value: Any) -> str:
    """Field as clean text. Lists, dicts and other containers count as missing."""
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        out = clean(value)
        for pattern, repl in _OCR_FIXES:
            out = pattern.sub(repl, out)
        return out
    return ""


def _days(value: Any) -> int | None:
    n = num(value)
    if n is None or n <= 0:
        return None
    return round(n)


def _count(value: Any) -> str:
    """A count as the record prints it, regrouped the Indian way: '13,911' -> '13,911', '132905' -> '1,32,905'."""
    return fmt_num(str(value).replace(",", ""), 0)


def _plural(count_text: str, singular: str, plural: str) -> str:
    return f"{count_text} {singular if count_text == '1' else plural}"


def _fix_caps(text: str) -> str:
    """Turn a mostly upper-case name into title case, keeping short acronyms."""
    letters = [c for c in text if c.isalpha()]
    if len(text.split()) < 4 or sum(c.isupper() for c in letters) / len(letters) < 0.75:
        return text
    words = []
    for word in text.split(" "):
        core = word.strip("().,&/-")
        if not core.isupper():
            words.append(word)
        elif core.lower() in _SHORT_UPPER_WORDS:
            words.append(word.lower() if core.lower() not in {"group", "notice"} else word.capitalize())
        elif len(core) >= 5 and core.isalpha():
            words.append(word.capitalize())
        else:
            words.append(word)
    out = " ".join(words)
    return out[:1].upper() + out[1:]


def _sentence_case(text: str) -> str:
    """Lower-case ordinary Title Case words; leave names, acronyms and codes alone."""

    def lower(match: re.Match[str]) -> str:
        word = match.group(0)
        return word.lower() if word.lower() in _COMMON_WORDS else word

    return re.sub(r"\b[A-Z][a-z]+\b", lower, text)


def _tidy(text: str) -> str:
    """Remove separators the house style bans from a hand-written line."""
    out = re.sub(r"\s*\(\+\d+d\)", "", text).replace("->", "to").replace("→", "to")
    out = re.sub(r"\s+[—–]\s+", ", ", out).replace("—", ", ")
    out = out.replace(";", ",")
    out = re.sub(r"\s+,", ",", out)
    out = re.sub(r",\s*,", ",", out)
    return clean(out)


def _who(authority: str) -> str:
    """Short name of the conducting body for a headline."""
    name = re.split(r"\s+(?:&|/)\s+|,\s", authority)[0].strip()
    abbr_match = re.search(r"\(([A-Za-z][A-Za-z .-]{1,14})\)", name)
    abbr = abbr_match.group(1).split(" ")[0] if abbr_match else ""
    bare = clean(_PAREN.sub("", name))
    state_psc = re.match(r"^(.+?) Public Service Commission$", bare)
    if state_psc and state_psc.group(1) != "Union":
        return f"{state_psc.group(1)} PSC"
    state_ssc = re.match(r"^(.+?) Staff Selection Commission$", bare)
    if state_ssc:
        return f"{state_ssc.group(1)} SSC"
    if abbr and (len(bare) > 30 or abbr.isupper()):
        return abbr
    return bare if len(bare) <= 40 else ""


def _authority_phrase(authority: str) -> str:
    """'from the Haryana Public Service Commission' when the body is a single plainly named one."""
    if not authority or re.search(r"[&/,]", authority):
        return ""
    bare = clean(_PAREN.sub("", authority))
    if not bare or len(bare) > 50 or bare.split(" ")[0].isupper():
        return ""
    return f" from the {bare}"


def _label(exam_name: str, authority: str, who: str) -> str:
    """A short, readable name for the exam, or '' when the stored name is not usable."""
    name = exam_name.strip(" |>\"'")
    if not name or _GARBAGE.search(name):
        return ""
    invited = re.search(r"had invited applications .*? in (.+)$", name)
    if invited:
        name = invited.group(1)
    bracket = _BRACKET.search(name)
    if bracket:
        return bracket.group(1)
    name = _ADVT_PREFIX.sub("", name)
    post = _POST_OF.search(name)
    if post and (_NOTICE_START.search(name) or len(name) > 60):
        name = _POST_TAIL.sub("", post.group(1))
    name = _ADVT_PAREN.sub("", name)
    name = re.sub(r"\s+(?:notified|is given|postponed|uploaded on .*|against Advt.*)$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"^(?:final\s+)?result of (?:the\s+)?", "", name, flags=re.IGNORECASE)
    name = re.sub(r"_\S*$", "", name)
    name = _TRAILING_NOTICE.sub("", _balanced(name))
    name = _fix_caps(_tidy(name))
    # Long bracketed expansions ("UGC-NET (University Grants Commission ...)") add nothing a reader needs.
    name = re.sub(r"\s*\([^()]{26,}\)", "", name)
    name = clean(name)
    if len(name) > 55:
        head = re.split(r"\s+in\s+(?=(?:the\s+)?[A-Z])", name)[0]
        if len(head) >= 10 and not re.search(r"\bin the subject of\b", name):
            name = head
    if re.match(r"^[IVX]{1,4}\b", name) or ("/" in name and not re.search(r"[a-z]", name)):
        return ""
    if not name or len(name) > 70 or _NOTICE_START.search(name):
        return ""
    if " " not in name and len(name) > 14:
        return ""
    auth_bare = clean(_PAREN.sub("", authority))
    if auth_bare and name.lower().startswith(auth_bare.lower()):
        rest = name[len(auth_bare) :]
        if not re.sub(r"\(?\d{4}\)?|examination|exam|\s", "", rest, flags=re.IGNORECASE):
            return ""
    abbr = re.search(r"\(([A-Z]{2,10})\b[^)]*\)", authority)
    if abbr and abbr.group(1) != who and re.match(rf"^{re.escape(abbr.group(1))}(?:'s)?\s", name):
        name = name.split(" ", 1)[1]
    if len(re.sub(r"[^A-Za-z]", "", name)) < 3:
        return ""
    return name


def _needs_who(label: str, who: str, authority: str) -> bool:
    if not who:
        return False
    if re.search(rf"\b{re.escape(who)}\b", label, re.IGNORECASE):
        return False
    abbr = re.search(r"\(([A-Z]{2,10})\b", authority)
    if abbr and re.search(rf"\b{re.escape(abbr.group(1))}\b", label):
        return False
    return not label.startswith(who.split(" ")[0] + " ")


def _balanced(text: str) -> str:
    """Drop a trailing bracket that never closes (names cut short at the source)."""
    if text.count("(") > text.count(")"):
        text = text[: text.rfind("(")]
    return clean(text)


def _shorter(desc: str) -> list[str]:
    """The stage line, then shorter cuts of it at natural breaks."""
    out = [desc, clean(_PAREN.sub("", desc))]
    for sep in (" per ", ", ", " & "):
        head = out[-1].split(sep)[0]
        if len(head) >= 18:
            out.append(head)
    seen: list[str] = []
    for item in out:
        if item and item not in seen:
            seen.append(item)
    return seen


def _fresh(desc: str, label: str) -> str:
    """Drop leading parts of the stage line that only repeat the exam name."""
    parts = desc.split(", ")
    plain = _PAREN.sub("", label).lower() + " " + label.lower()
    while len(parts) > 1 and _PAREN.sub("", parts[0]).strip().lower() in plain:
        parts.pop(0)
    return ", ".join(parts)


def _join(*parts: str) -> str:
    return " ".join(p for p in parts if p)


def _fit(candidates: list[str]) -> str:
    """First candidate that fits the soft limit, else the first within the hard limit."""
    usable = [_balanced(clean(c)) for c in candidates if clean(c)]
    for limit in (_HEAD_SOFT, _HEAD_HARD):
        for cand in usable:
            if len(cand) <= limit:
                return cand
    return ""


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _verb_headline(who: str, core: str, named: bool, dtype: str, days: int | None, templated: bool) -> str:
    article = "the" if named else "a"
    span = f" by {fmt_days(days)}" if days else ""
    subject = _join(who, core)
    if dtype == "delayed":
        if days:
            return f"{subject} delayed{span}"
        if who:
            return f"{who} issued a revision notice for {article} {core}"
        return f"{core}: delay or revision on record"
    if dtype == "postponed":
        return f"{subject} postponed{span}"
    if dtype == "rescheduled":
        return f"{subject} rescheduled{span}"
    if dtype == "cancelled_before_conduct":
        return f"{subject} cancelled before being held"
    if dtype == "cancelled_after_conduct":
        return f"{subject} cancelled after candidates had sat it"
    if dtype == "partial_reexam_or_glitch":
        if templated and who:
            return f"{who} notified a partial re-test or special chance for {article} {core}"
        return f"{subject} had a partial re-exam or glitch"
    return f"{subject}: change on record"


def _source(m: dict[str, Any]) -> tuple[str, str]:
    """Kind of record ('A Lok Sabha reply') and the date printed in its reference, when there is one."""
    ref = _text(m.get("outcome_document_ref")) or _text(m.get("schedule_document_ref"))
    low = ref.lower()
    if "lok sabha" in low:
        kind = "A Lok Sabha reply"
    elif "rajya sabha" in low:
        kind = "A Rajya Sabha reply"
    elif "press release" in low:
        kind = "A press release"
    elif "cancellation notice" in low:
        kind = "A cancellation notice"
    elif "corrigendum" in low:
        kind = "A corrigendum"
    elif "public notice" in low:
        kind = "A public notice"
    elif "write-up" in low:
        kind = "A result notice"
    else:
        kind = "An official notice"
    found = re.search(r"\b(\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2})\b", ref)
    when = fmt_date(found.group(1)) if found else ""
    if not when:
        spelled = re.search(r"dated (\d{1,2}) ([A-Z][a-z]{2})[a-z]* (\d{4})", ref)
        if spelled:
            when = f"{int(spelled.group(1))} {spelled.group(2)} {spelled.group(3)}"
    return kind, when


def _dated(kind: str, when: str) -> str:
    return f"{kind} dated {when}" if when else kind


def _special(m: dict[str, Any], exam: str, stage: str, dtype: str) -> tuple[str, str] | None:
    """Record families where the stage line holds the figure that matters."""
    kind, when = _source(m)
    src = _dated(kind, when)
    inst = clean(_PAREN.sub("", re.split(r"\s+—\s+(?=AYUSH|Standing|NORCET|\d{4})", exam)[0].split(" (")[0]))
    inst = _tidy(inst)
    period_match = re.match(r"^(\d{4}(?:-\d{2})?)\b", stage)
    in_period = f" in {period_match.group(1)}" if period_match else ""

    vacant = re.search(r"\(([\d,]+) Vacant of ([\d,]+) Sanctioned Posts\)", stage)
    if vacant and inst:
        sort = "non-faculty " if re.search(r"Group B/C|Non-Faculty", stage + exam) else "faculty " if "Faculty" in stage else ""
        n, total = _count(vacant.group(1)), _count(vacant.group(2))
        return (
            f"{inst} had {n} of {total} {sort}posts vacant{in_period}",
            (f"{src} lists {n} of {total} sanctioned {sort}posts at {inst} as vacant{in_period}. "
            "The audit files this under delayed recruitment."),
        )
    both = re.search(r"\(([\d,]+) Faculty & ([\d,]+) Non-Faculty Still Vacant\)", stage)
    if both and inst:
        fac, non = _count(both.group(1)), _count(both.group(2))
        return (
            f"{inst} had {fac} faculty and {non} non-faculty posts vacant{in_period}",
            (f"{src} lists {fac} faculty and {non} non-faculty posts at {inst} as vacant{in_period}. "
            "The audit files this under delayed recruitment."),
        )
    jump = re.search(r"\(([\d,]+) Vacant: ([\d,]+) Faculty & ([\d,]+) Non-Faculty\)", stage)
    if jump and inst:
        n, fac, non = (_count(jump.group(i)) for i in (1, 2, 3))
        return (
            f"{inst} had {n} posts vacant{in_period}",
            (f"{src} lists {n} vacant posts at {inst}{in_period}, {fac} faculty and {non} non-faculty. "
            "The audit files this under delayed recruitment."),
        )
    pending = re.search(r"\(([\d,]+) Posts? Pending Advertisements", stage)
    if pending and inst:
        posts = _plural(_count(pending.group(1)), "post", "posts")
        return (
            f"{inst} had {posts} awaiting recruitment{in_period}",
            (f"{src} lists {_plural(_count(pending.group(1)), 'vacant post', 'vacant posts')} at {inst} "
            "at various stages of recruitment. The audit files this under delayed recruitment."),
        )

    region = clean(exam.split(" — ")[-1]) if " — " in exam else ""
    apaar = re.search(r"\(([\d,]+) Secondary Pending\)", stage)
    if apaar and region and "APAAR" in exam:
        n = _count(apaar.group(1))
        students = _plural(n, "student", "students")
        head = f"{n} government school {'student' if n == '1' else 'students'} in {region} had no APAAR ID yet"
        if len(head) > _HEAD_SOFT:
            head = f"{region}: {students} had no APAAR ID yet"
        tail = ""
        if "not excluding" in _text(m.get("outcome_summary")):
            tail = " The ministry said no student lacking the ID would be kept out of board exams."
        return (
            head,
            (f"{src} shows APAAR IDs were pending for {n} secondary government school "
            f"{'student' if n == '1' else 'students'} in {region}.{tail}"),
        )
    seats = re.search(r"Addition of ([\d,]+) PG Seats? \(([\d,]+) Govt, ([\d,]+) Pvt\)", stage)
    round_name = re.match(r"^(NEET-PG \d{4}-\d{2})\b", exam)
    if seats and region and round_name:
        n, govt, pvt = (_count(seats.group(i)) for i in (1, 2, 3))
        return (
            f"{region} got {_plural(n, 'new PG medical seat', 'new PG medical seats')} during {round_name.group(1)} counselling",
            (f"{src} shows {_plural(n, 'PG medical seat was', 'PG medical seats were')} added in {region} "
            f"while {round_name.group(1)} counselling was under way, {govt} government and {pvt} private."),
        )
    registered = re.search(r"\((\d{1,2} [A-Z][a-z]+ \d{4}) Notice\) — ([\d,]+) Registered", stage)
    cuet = re.match(r"^(CUET \(UG\) \d{4}) Hybrid-Mode Examination — (.+)$", exam)
    if registered and cuet:
        n = _count(registered.group(2))
        people = _plural(n, "candidate", "candidates")
        head = f"{cuet.group(1)} moved to hybrid pen-and-paper mode for {people} in {cuet.group(2)}"
        if len(head) > _HEAD_SOFT:
            head = f"{cuet.group(2)}: {cuet.group(1)} format changed for {people}"
        return (
            head,
            (f"{src} lists {n} registered {cuet.group(1)} {'candidate' if n == '1' else 'candidates'} in "
            f"{cuet.group(2)}. A notice of {registered.group(1)} had moved the exam to a hybrid pen-and-paper format."),
        )
    centres = re.search(r"Zero In-Jurisdiction CBT Centres Despite ([\d,]+) Registered Candidates?", stage)
    imu = re.match(r"^(IMU-CET \d{4}) Examination — (.+?) Centre Deficit$", exam)
    if centres and imu:
        n = _count(centres.group(1))
        people = _plural(n, "registered candidate", "registered candidates")
        head = f"{imu.group(1)} had no test centre in {imu.group(2)} for {people}"
        if len(head) > _HEAD_SOFT:
            head = f"{imu.group(2)}: no {imu.group(1)} test centre for {people}"
        return (
            head,
            (f"{src} shows {_plural(n, 'candidate', 'candidates')} from {imu.group(2)} registered for "
            f"{imu.group(1)}, with no computer-based test centre inside the state or union territory."),
        )

    if exam.startswith("IBPS Common Recruitment Process") and stage.startswith("IBPS Official Corrigendum"):
        crp = re.search(r"\bCRP[ _-]?(RRBs?|PO[ _/-]?MT|CSA|SPL|Clerks?)?[ _-]?(X[IVX]*)\b", exam)
        if crp:
            stream = (crp.group(1) or "").replace("_", "/").replace(" ", "/")
            round_label = _join("CRP", f"{stream}-{crp.group(2)}" if stream else crp.group(2))
            head = f"IBPS revised timelines for its {round_label} recruitment round"
        else:
            head = "IBPS issued a corrigendum to a Common Recruitment Process notice"
        return (
            head,
            ("An IBPS corrigendum or extension notice revised online registration or recruitment timelines "
            "for this round. The record does not give the number of candidates affected."),
        )
    post_code = re.match(r"^(Phase-[IVXL]+/\d{4}) — Post Codes? [\w–-]+ \((.+)\)$", stage)
    if post_code and dtype == "cancelled_before_conduct":
        return (
            f"SSC cancelled Selection Posts {post_code.group(1)} recruitment for {post_code.group(2)}",
            (f"{src} cancelled or withdrew this post from the Selection Posts {post_code.group(1)} recruitment. "
            "The record does not give the number of candidates affected."),
        )
    action = re.match(r"^CBSE Board Examination Affiliation Action — (.+)$", exam)
    if action:
        descs = _shorter(_sentence_case(_tidy(stage)))
        full = f"CBSE affiliation action in {action.group(1)}: {descs[0]}"
        head = full if len(full) <= _HEAD_HARD else _fit([f"CBSE affiliation action in {action.group(1)}: {d}" for d in descs])
        if head:
            return head, ""
    return None


def _summary_brief(m: dict[str, Any]) -> str:
    """Brief built from the hand-written schedule and outcome sentences, when they are short and plain."""

    def sentence(key: str) -> str:
        text = _text(m.get(key))
        text = re.sub(r";\s+(\w)", lambda mm: ". " + mm.group(1).upper(), text)
        parts = _SENTENCE_END.split(text)
        first = parts[0] if parts else ""
        first = _ISO_DATE.sub(lambda mm: fmt_date(mm.group(0)) or mm.group(0), first)
        first = _DOT_DATE.sub(lambda mm: fmt_date(mm.group(0)) or mm.group(0), first)
        first = _tidy(first.replace("Hon'ble ", "").replace("Hon’ble ", ""))
        if not first or _CAUTION.search(first) or scan_forbidden_terms(first) or "!" in first or "?" in first:
            return ""
        return first.rstrip(".") + "."

    before, after = sentence("schedule_summary"), sentence("outcome_summary")
    for cand in (_join(before, after), after, before):
        if cand and len(cand) <= _BRIEF_SOFT:
            return cand
    for long in (after, before):
        if not long:
            continue
        cut = long[:_BRIEF_SOFT]
        stop = max(cut.rfind(", "), cut.rfind(" while "), cut.rfind(" before "))
        if stop >= 110 and cut[:stop].count("(") == cut[:stop].count(")"):
            return cut[:stop].rstrip(" ,") + "."
    return ""


def _template_brief(
    m: dict[str, Any], dtype: str, days: int | None, authority: str, corrigendum: bool
) -> str:
    sched = fmt_date(m.get("scheduled_date"))
    event = fmt_date(m.get("actual_or_event_date"))
    sched_d, event_d = parse_date(m.get("scheduled_date")), parse_date(m.get("actual_or_event_date"))
    differ = bool(sched and event and sched != event)
    # "N days later" only when the stored delay is the gap between the two stored dates.
    gap_matches = bool(days and sched_d and event_d and (event_d - sched_d).days == days)
    notice = f"An official notice{_authority_phrase(authority)}"
    dated = f"{notice} dated {event}" if event else notice
    listed = f"The official schedule listed it for {sched}." if sched else ""
    span = fmt_days(days) if days else ""
    extra = f"The record puts the delay at {span}." if span else ""

    if dtype == "delayed":
        if corrigendum:
            # The stored dates for these are the original notification's, not the corrigendum's.
            issuer = _authority_phrase(authority)
            parts = [f"An official corrigendum{issuer} changed the notified exam or recruitment terms or timelines."]
        elif gap_matches:
            later = f"{dated}, {span} later, records a delay or revision such as a revised result or corrigendum."
            parts = [listed, later]
        else:
            body = f"{dated} records a delay or revision for this exam, such as a revised result or corrigendum."
            parts = [listed if differ else "", body, extra]
    elif dtype == "postponed":
        parts = [listed, f"{dated} postponed or deferred it.", extra]
    elif dtype == "rescheduled":
        if differ:
            shift = f", a shift of {span}" if span else ""
            parts = [f"{notice} changed the exam date or schedule from {sched} to {event}{shift}."]
        else:
            parts = [f"{dated} revised the exam date or schedule."]
    elif dtype == "cancelled_before_conduct":
        parts = [f"{dated} cancelled or withdrew the recruitment or exam before it was held.", listed if differ else ""]
    elif dtype == "cancelled_after_conduct":
        record = dated.replace("An official notice", "An official record", 1)
        held = f"It had been scheduled for {sched}." if differ else ""
        parts = [f"{record} shows the exam was cancelled after candidates had taken it.", held, extra]
    elif dtype == "partial_reexam_or_glitch":
        body = f"{dated} records a partial re-exam, a re-conduct for a centre or shift, or a special chance for candidates."
        parts = [body, f"The original date was {sched}." if differ else ""]
    else:
        parts = [f"{dated} records a change to this exam." if event else "", listed if differ else ""]

    brief = _join(*parts)
    reason = _REASONS.get(_text(m.get("disruption_reason_category")))
    if brief and reason:
        with_reason = f"{brief} The record puts the reason under {reason}."
        if len(with_reason) <= _BRIEF_SOFT:
            brief = with_reason
    return brief


def render(finding: dict[str, Any]) -> tuple[str, str]:
    """Return (headline, brief) for one P9 finding, or ('', '') when the fields say too little."""
    if not isinstance(finding, dict):
        return "", ""
    m = metrics(finding)
    authority = _text(m.get("authority")) or _text(finding.get("entity_name"))
    exam = _text(m.get("exam_name"))
    stage = _text(m.get("cycle_or_stage"))
    dtype = _text(m.get("disruption_type")) or _text(m.get("signal_category"))
    if not dtype:
        rule = _text(finding.get("rule_triggered")).upper()
        dtype = rule[3:].lower().replace("-", "_") if rule.startswith("P9-") else ""
    days = _days(m.get("delay_days"))
    if not dtype or not (authority or exam):
        return "", ""

    outcome = _text(m.get("outcome_summary"))
    templated = not outcome or outcome.startswith("Official ")
    corrigendum = "modified notified examination/recruitment terms" in outcome

    brief = "" if templated else _summary_brief(m)
    if not brief:
        brief = _template_brief(m, dtype, days, authority, corrigendum)

    special = _special(m, exam, stage, dtype)
    if special and special[0]:
        return _cap(special[0]).rstrip("."), special[1] or brief

    who = _who(authority)
    label = _label(exam, authority, who)
    lead = who if _needs_who(label, who, authority) else ""
    bare_label = clean(_PAREN.sub("", label))

    generic = _GENERIC_STAGE_RE.match(stage)
    noun = _GENERIC_STAGES.get(generic.group(1).lower()) if generic else None
    year_match = re.search(r"\b(20\d{2})\b", stage)
    year = year_match.group(1) if year_match else _text(m.get("exam_year"))
    if not re.fullmatch(r"20\d{2}", year):
        year = ""

    def core_of(name: str) -> str:
        """Exam name plus the stage word, without saying 'exam' or 'interview' twice."""
        stage_noun = noun if noun is not None else ("exam" if templated else "")
        plain = _PAREN.sub("", name).lower()
        repeats_stage = bool(stage_noun) and stage_noun.split(" ")[-1] in plain.split()
        already_exam = stage_noun == "exam" and bool(
            re.search(r"\b(?:examinations?|exams|tests?|recruitment|interview)\b", plain)
        )
        if repeats_stage or already_exam:
            stage_noun = ""
        elif stage_noun and stage_noun != "exam":
            name = re.sub(r",?\s*\b(?:Examination|Exam)\b(?=[^A-Za-z]*$)", "", name).strip() or name
        return _join(name, stage_noun)

    generic_core = _join(year, "recruitment exam" if noun in (None, "exam") else noun)
    cores = [core_of(label), core_of(bare_label)] if label else [generic_core]
    verb_forms = [_verb_headline(lead, c, bool(label), dtype, days, templated) for c in cores]
    verb_forms += [_verb_headline("", c, bool(label), dtype, days, templated) for c in cores if lead and not templated]
    verb_forms.append(_verb_headline(who, generic_core, False, dtype, days, templated))

    candidates: list[str] = []
    if noun is None and stage:
        cycle = _CYCLE_RE.match(stage)
        heads = [h for h in (re.split(r", | & ", bare_label)[0],) if len(h) >= 12 and h != bare_label]
        names = [label, bare_label, *heads]
        subjects = [_join(lead, n) for n in names] + names if label else [who]
        subjects = list(dict.fromkeys(x for x in subjects if x))
        if cycle:
            rest = cycle.group(2).strip(" —–-")
            if rest.startswith("(") and rest.endswith(")"):
                rest = rest[1:-1]
            descs = _shorter(_sentence_case(_fresh(_tidy(rest), label))) if rest else []
            cycle_name = "" if cycle.group(1).split(" ")[-1][:4] in label else cycle.group(1)
            span = f" by {fmt_days(days)}" if days else ""
            verb = {
                "delayed": f"delayed{span}" if days else "",
                "postponed": f"postponed{span}",
                "rescheduled": f"rescheduled{span}",
                "cancelled_before_conduct": "cancelled before being held",
                "cancelled_after_conduct": "cancelled after candidates had sat it",
            }.get(dtype, "")
            for subj in subjects:
                named = _join(subj, cycle_name)
                for desc in descs:
                    if _EVENT_WORD.search(desc) or not verb:
                        candidates.append(f"{named}: {desc}")
                    else:
                        candidates.append(f"{named} {verb}: {desc}")
            if label:
                with_cycle = [_join(c, cycle_name) for c in cores]
                verb_forms = [_verb_headline(lead, c, True, dtype, days, templated) for c in with_cycle] + verb_forms
        else:
            descs = _shorter(_sentence_case(_fresh(_tidy(stage), label)))
            candidates += [f"{subj}: {desc}" for subj in subjects for desc in descs]

    headline = _cap(_fit(candidates) or _fit(verb_forms))
    if scan_forbidden_terms(headline) or _CAUTION.search(headline):
        headline = _cap(_fit(verb_forms))
        if scan_forbidden_terms(headline) or _CAUTION.search(headline):
            headline = _cap(_verb_headline(who, generic_core, False, dtype, days, templated))
    if not headline:
        return "", ""
    return headline.rstrip("."), brief
