"""P7: public tenders with one bid, a repeated winner name, or a thin bid record (engines/only_one_bid.py).

Each finding is about one target tender. What the engine fields mean, and so what the text may say:

* ``effective_bids`` is the qualified-bid count when the record has one, else bids received. It is None on
  award-of-contract sources, where the engine refuses to read the portal's envelope count as a bid count. None
  therefore means "this data does not show it", never "the body did not record it".
* ``winner_total_wins`` and the other cohort fields count awards under the same normalised winner *name* inside
  the same buyer chain. Names can collide, so the text says a winner name is listed on N contracts and never
  that one person or firm won them all.
* The buyer chain is the whole ``A||B||C`` hierarchy. When the readable name is a parent of the office the
  cohort covers, shares are attributed to "the same office", not to the parent.
* ``bid_window_days`` carries portal placeholders (90, 180, 365, 999), so only a window under the engine's
  14-day benchmark is printed, and only when the publish and close dates do not contradict it.
* ``procurement_method`` defaults to "open" when the source is blank, so the text never calls a tender open.
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple

from ._common import clean, fmt_date, fmt_num, fmt_pct, metrics, num, parse_date, place
from ._common import fmt_inr as _common_fmt_inr

_HEADLINE_FIT = 95
_BRIEF_FIT = 240
_SHORT_WINDOW_DAYS = 14  # the engine's own benchmark, the only case where the window is the finding
_SHARE_WORTH_SAYING_PCT = 10.0
_MIN_PLAUSIBLE_INR = 1_000.0
_MAX_PLAUSIBLE_INR = 1e13
_MAX_PLAUSIBLE_COUNT = 1_000_000
_NAME_MAX = 80

_STATE_ABBREVIATIONS: dict[str, str] = {
    "himachal pradesh": "HP",
    "madhya pradesh": "MP",
    "andhra pradesh": "AP",
    "uttar pradesh": "UP",
}
_OLD_STATE_NAMES: dict[str, str] = {"odisha": "Orissa"}
_NULL_NAMES = {"nan", "none", "null", "na", "n/a", "unknown", "unspecified", "true", "false", "inf"}
_PLACE_OK = re.compile(r"[A-Za-z][A-Za-z .&'-]{1,59}")

# Portal codes that say nothing to a reader, mapped to what the office is.
_AUTHORITY_ALIASES: dict[str, str] = {
    "ce rw i": "Rural Works chief engineer's office",
    "home b": "Home B Department",
    "pr. chief conservator of forest and head of forest force": "Principal Chief Conservator of Forest office",
    "welfare of plain tribes and backward classes": "Welfare of Plain Tribes and Backward Classes Department",
}
_UMBRELLA_SEGMENTS = {"municipal bodies"}
_CODE_LEADS = {"home b"}
_CRYPTIC_LEAD = re.compile(r"^(CE|SE|EE)\b", re.IGNORECASE)
_OFFICER_PREFIX = re.compile(r"^(?:S\.?E\.?|E\.?E\.?|C\.?E\.?|Director)\s*[-.]?\s+", re.IGNORECASE)
_OFFICER_START = re.compile(
    r"^(?:chief|director\b|dy\b|deputy|superintending|executive|commissioner|[SEC]\.?E\b|DM\b)", re.IGNORECASE
)
# A last segment with one of these words is a body in its own right, so it is the one to name.
_INSTITUTION = re.compile(
    r"\b(Corporation|Company|Mission|Society|Agency|Authority|Board|University|Municipality|Municipal|Council|"
    r"Nigam|Trust|Ltd|Limited|NAC)\b",
    re.IGNORECASE,
)
_UNIT_TAIL = re.compile(r"\b(Unit|Cell|Wing|Office|Project)\b", re.IGNORECASE)
_PLACE_LAST = re.compile(r"([^,]+ (?:Circle|Division|Zone|Range)), ([A-Z][A-Za-z]+)")
_LOCAL_BODY = re.compile(
    r"\b(Municipal|Municipality|NAC|Metropolitan|Smart City|Council|Circle|Division|District|Jail)\b",
    re.IGNORECASE,
)
_BODY_NOUNS: tuple[tuple[str, str], ...] = (
    ("municip", "civic body"),
    (" nac", "civic body"),
    ("department", "department"),
    ("pwd", "department"),
    ("council", "council"),
    ("corporation", "corporation"),
    ("company", "company"),
    ("ltd", "company"),
    ("authority", "authority"),
    ("agency", "agency"),
    ("society", "society"),
    ("mission", "mission"),
    ("university", "university"),
    ("board", "board"),
    ("directorate", "directorate"),
    ("circle", "circle"),
    ("division", "division"),
    ("jail", "jail"),
)

# Only a registered-company ending counts. "Nigam" and "Corporation" alone can be a surname or a
# one-person firm, and people are never named.
_COMPANY_END = re.compile(r"\b(?:ltd|limited|llp|inc)\.?$", re.IGNORECASE)
_PERSON_MARK = re.compile(
    r"\b(?:prop|proprietor|jv|shri|sri|smt|mr|mrs|md|late|s/o|w/o|d/o|c/o)\b|[,/()\[\]]", re.IGNORECASE
)
_LOWER_WORDS = {"and", "of", "the", "for", "in", "at", "to", "on", "under", "by", "with", "cum"}
_TITLE_WORDS = {"LTD": "Ltd", "PVT": "Pvt", "CO": "Co", "INC": "Inc"}
_SHORT_PLAIN_WORDS = {"NEW", "OLD", "OIL", "GAS", "TAX", "LAW", "DAM", "BUS", "AIR", "SEA", "ART", "TEA", "ALL"}

# Matched in order, so the specific kinds of work win over the general ones.
_WORK_KINDS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), phrase)
    for pattern, phrase in (
        (r"\blift irrigation\b", "lift irrigation works"),
        (r"\broads?\b", "road works"),
        (r"\bbridges?\b", "bridge works"),
        (r"\bbuildings?\b", "building works"),
        (r"\bwater (?:works|supply)\b", "water works"),
        (r"\bcanals?\b", "canal works"),
        (r"\belectrical\b", "electrical works"),
        (r"\btoll\b", "toll gate works"),
        (r"\bsports goods\b", "sports goods"),
        (r"\boil\s*(?:/|&|and)\s*gas\b", "oil and gas"),
        (r"\bmaterials?\b", "goods supply"),
        (r"\bgoods\b", "goods supply"),
        (r"\bservices?\b", "services"),
        (r"\bconstruction\b", "construction works"),
        (r"\bcivil\b", "civil works"),
        (r"\bworks?\b", "works"),  # the portal's works/goods/services split, not a kind of work
    )
)

_TITLE_STARTS = (
    "construction", "improvement", "supply", "repair", "restoration", "procurement", "implementation",
    "establishment", "upgradation", "maintenance", "renovation", "installation", "development",
    "strengthening", "widening", "execution", "providing", "protection", "extension", "replacement",
)  # fmt: skip
# Titles that describe a stretch between landmarks often name householders, so they are not quoted.
_TITLE_UNSAFE = re.compile(
    r"\b(house|residence|ghar|shop|land of|plot of|from|near|sri|shri|smt|md|late|s/o|w/o|prop|no|br|rd|ch|km|pkg)\b"
    r"|[/_|#@\[\]]|\d[a-z]|[a-z]\d|compliance-review|redacted",
    re.IGNORECASE,
)
_TITLE_MIN_WORDS = 4
_TITLE_MAX = 80

_AWARD_TOTAL = re.compile(r"of ([\d,]+) authority awards")
_DIRECT_METHOD = re.compile(r"direct|nomination|single[\s_-]*(?:source|tender)", re.IGNORECASE)
_METHOD_NOTES: dict[str, str] = {
    "documented_pac_oem": "The record cites a single-source or proprietary-item basis for the purchase.",
    "documented_emergency_or_direct": "The record gives a stated reason for a direct or emergency purchase.",
    "direct": "The record lists the purchase method as direct.",
    "limited": "The record lists it as a limited tender.",
}


class _Body(NamedTuple):
    name: str  # readable name without the state, "" when the record gives nothing usable
    scoped: bool  # the cohort figures cover a sub-office of the named body, not all of it


class _Slot(NamedTuple):
    rank: int  # 0 is always kept, other sentences are added lowest rank first while the brief stays short
    variants: tuple[str, ...]  # the same sentence, longest wording first


def _slot(rank: int, *variants: str) -> _Slot:
    return _Slot(rank, tuple(dict.fromkeys(v for v in variants if v)))


def _compose(slots: list[_Slot]) -> str:
    """Join the slots in reading order, each at the longest wording that keeps the brief short."""
    picked = [slot.variants[-1] if slot.rank == 0 and slot.variants else "" for slot in slots]
    for index in sorted(range(len(slots)), key=lambda i: (slots[i].rank, i)):
        fallback = picked[index]
        for variant in slots[index].variants:
            picked[index] = variant
            if len(" ".join(p for p in picked if p)) <= _BRIEF_FIT:
                break
            picked[index] = fallback
    return " ".join(p for p in picked if p)


def fmt_inr(rupees: Any) -> str:
    """Rupee amount, skipping values too small or too large to be a real tender figure (unit slips)."""
    n = num(rupees)
    if n is None or not _MIN_PLAUSIBLE_INR <= n <= _MAX_PLAUSIBLE_INR:
        return ""
    if n < 1e7 and round(n / 1e5, 1) >= 100:
        return "₹1 crore"
    return _common_fmt_inr(n)


def _int(value: Any) -> int | None:
    n = num(value)
    return int(n) if n is not None and n == int(n) and 0 <= n <= _MAX_PLAUSIBLE_COUNT else None


def _usable_name(text: str) -> bool:
    low = text.lower().strip(" .-")
    if low in _NULL_NAMES or low.startswith("unspecified") or "redacted" in low:
        return False
    return bool(re.search(r"[A-Za-z]{2}", text))


def _state(finding: dict[str, Any]) -> str:
    """The state as a plain name, or "" for the Union government and for anything that is not a place name."""
    fields = (metrics(finding).get("state_or_ut"), finding.get("jurisdiction"))
    raw = next((value for value in fields if isinstance(value, str) and value.strip()), "")
    name = place({"jurisdiction": raw})
    if name == "Union government" or not _PLACE_OK.fullmatch(name) or not _usable_name(name):
        return ""
    return name.title() if name.isupper() or name.islower() else name


def _smart_title(text: str) -> str:
    """Title-case a mostly upper-case name while keeping short codes such as SKR or PWD."""
    letters = [c for c in text if c.isalpha()]
    if not letters or sum(c.isupper() for c in letters) / len(letters) < 0.8:
        return text
    words = []
    for i, word in enumerate(text.split(" ")):
        core = word.strip(".,()&-")
        if core in _TITLE_WORDS:
            words.append(word.replace(core, _TITLE_WORDS[core]))
        elif core.lower() in _LOWER_WORDS and i > 0:
            words.append(word.lower())
        elif len(core) <= 3 and core not in _SHORT_PLAIN_WORDS:
            words.append(word)
        else:
            words.append("-".join(part.capitalize() for part in word.split("-")))
    return " ".join(words)


def _dedupe_words(text: str) -> str:
    """Drop a word repeated straight after itself or at the very end ('X Division X', 'CAG CAG')."""
    out: list[str] = []
    for word in text.split():
        if not out or out[-1].lower() != word.lower():
            out.append(word)
    if len(out) > 2 and out[-1].lower() == out[0].lower():
        out.pop()
    return " ".join(out)


def _is_state_token(text: str, state: str) -> bool:
    low = text.lower()
    return bool(state) and (low == state.lower() or text == _STATE_ABBREVIATIONS.get(state.lower()))


def _tidy_segment(segment: str, state: str) -> tuple[str, bool]:
    """(readable name, True when a trailing unit such as a project cell was dropped from it)."""
    text = clean(segment)
    alias = _AUTHORITY_ALIASES.get(text.lower())
    if alias:
        return alias, False
    text = _OFFICER_PREFIX.sub("", text)
    unit = re.match(r"^RW(CIRCLE|DIVISION)\s*-\s*(.+)$", text, re.IGNORECASE)
    if unit:
        text = f"Rural Works {unit.group(1).capitalize()} {unit.group(2).title()}"
    text = re.sub(r"\bDivn\b\.?", "Division", text)
    text = re.sub(r"\bIrr\b\.?", "Irrigation", text)
    text = re.sub(r"\bfor SC$", "for Scheduled Castes", text)
    if state:
        text = re.sub(rf"\s*-\s*{re.escape(state)}$", "", text, flags=re.IGNORECASE)
    parts = [p for p in (clean(part) for part in text.split(",")) if p and not _is_state_token(p, state)]
    trimmed = len(parts) > 1 and all(_UNIT_TAIL.search(p) for p in parts[1:])
    text = parts[0] if trimmed else ", ".join(parts)
    dept = re.fullmatch(r"Department of ([^,]+)", text, re.IGNORECASE)
    if dept:
        text = f"{clean(dept.group(1))} Department"
    place_last = _PLACE_LAST.fullmatch(text)
    if place_last:
        text = f"{place_last.group(2)} {place_last.group(1)}"
    text = re.sub(r"\s*-\s*", " ", text)
    return clean(_dedupe_words(_smart_title(clean(text))).rstrip(".")), trimmed


def _stands_alone(segment: str) -> bool:
    return bool(
        _INSTITUTION.search(segment)
        and not _OFFICER_START.match(segment)
        and "," not in segment
        and len(segment) <= _NAME_MAX
    )


def _authority(finding: dict[str, Any], state: str) -> _Body:
    """The procuring body as a reader would name it, without the state."""
    # Split before clean(), which would turn the "||" hierarchy separator into a comma.
    source = finding.get("entity_name")
    if not isinstance(source, str) or not _usable_name(source):
        source = metrics(finding).get("authority")
    if not isinstance(source, str):
        return _Body("", False)
    segments = [s for s in (clean(part) for part in source.split("||")) if _usable_name(s)]
    if not segments:
        return _Body("", False)
    top, leaf = segments[0], segments[-1]
    names_leaf = (
        top.lower() in _UMBRELLA_SEGMENTS
        or top.lower() in _CODE_LEADS
        or bool(_CRYPTIC_LEAD.match(top))
        or _stands_alone(leaf)
    )
    use_leaf = len(segments) == 1 or names_leaf
    name, trimmed = _tidy_segment(leaf if use_leaf else top, state)
    if not _usable_name(name) or len(name) > _NAME_MAX:
        return _Body("", False)
    return _Body(name, trimmed or not use_leaf)


def _names_state(name: str, state: str) -> bool:
    if not state:
        return True
    low = state.lower()
    if low in name.lower():
        return True
    old = _OLD_STATE_NAMES.get(low)
    if old and old.lower() in name.lower():
        return True
    abbr = _STATE_ABBREVIATIONS.get(low)
    return bool(abbr and re.search(rf"\b{abbr}\b", name))


def _with_state(name: str, state: str) -> str:
    if not name:
        return ""
    if _names_state(name, state) or _LOCAL_BODY.search(name):
        return name
    return f"{state} {name}"


def _noun(body: _Body) -> str:
    """How to refer back to the body the cohort figures cover: 'department', 'council', 'same office'."""
    if body.scoped:
        return "same office"
    low = f" {body.name.lower()}"
    for needle, noun in _BODY_NOUNS:
        if needle in low:
            return noun
    return "public body"


def _source(m: dict[str, Any], state: str) -> str:
    """Where the record comes from, worded to take a plural verb."""
    portal = clean(m.get("portal")).lower() if isinstance(m.get("portal"), str) else ""
    if "world bank" in portal:
        return "World Bank project procurement records"
    if state and "tender" in portal:
        return f"{state} tender portal records"
    return "Public tender records"


def _brief_name(authority: str, state: str, source: str) -> str:
    """The body's name for the brief, with the state when the source phrase does not already give it."""
    return authority if state and state in source else _with_state(authority, state)


def _work(m: dict[str, Any]) -> str:
    raw = m.get("item_category")
    category = clean(raw).lower().replace("–", "-") if isinstance(raw, str) else ""
    if not category or len(category) > 45:
        return ""
    for pattern, phrase in _WORK_KINDS:
        if pattern.search(category):
            return phrase
    return ""


def _company(m: dict[str, Any]) -> str:
    """The winner's name, only when it is plainly a registered company. People are never named."""
    raw = m.get("winner_name")
    if not isinstance(raw, str):
        return ""
    name = re.sub(r"^M\s*/\s*S\b\.?\s*", "", clean(raw), flags=re.IGNORECASE)
    if not _usable_name(name) or len(name) > 60 or _PERSON_MARK.search(name) or not _COMPANY_END.search(name):
        return ""
    name = re.sub(r"\b(Pvt|Ltd|Co|Inc)\.", r"\1", _smart_title(name))
    return clean(name.rstrip("."))


def _quotable_title(m: dict[str, Any]) -> str:
    raw = m.get("tender_title")
    title = clean(raw).rstrip(".") if isinstance(raw, str) else ""
    if not title or len(title) > _TITLE_MAX or '"' in title or _TITLE_UNSAFE.search(title):
        return ""
    words = title.split()
    if len(words) < _TITLE_MIN_WORDS or words[0].lower() not in _TITLE_STARTS:
        return ""
    title = re.sub(r"\s+,", ",", re.sub(r"\(\s+", "(", re.sub(r"\s+\)", ")", title)))
    if title.count("(") != title.count(")"):
        return ""
    return _smart_title(title)


def _title_sentence(m: dict[str, Any]) -> str:
    title = _quotable_title(m)
    return f'The tender is titled "{title}".' if title else ""


def _value(finding: dict[str, Any], m: dict[str, Any]) -> tuple[str, str]:
    """(amount, kind) where kind is 'award', 'estimate' or '' when the record does not say which."""
    kind = m.get("amount_type")
    if kind == "award_value" and fmt_inr(m.get("award_value_inr")):
        return fmt_inr(m.get("award_value_inr")), "award"
    if kind == "tender_estimate" and fmt_inr(m.get("tender_estimate_inr")):
        return fmt_inr(m.get("tender_estimate_inr")), "estimate"
    return fmt_inr(finding.get("monetary_exposure_inr")), ""


def _value_clause(amount: str, kind: str) -> str:
    if not amount:
        return ""
    return {"award": f"awarded for {amount}", "estimate": f"estimated at {amount}"}.get(kind, f"valued at {amount}")


def _date(value: Any) -> str:
    d = parse_date(value)
    return fmt_date(d) if d is not None and 2000 <= d.year <= 2040 else ""


def _fy(value: Any) -> str:
    text = clean(value) if isinstance(value, str) else ""
    text = text[2:] if text.upper().startswith("FY") else text
    return text if re.fullmatch(r"20\d{2}-\d{2}", text) else ""


def _span(m: dict[str, Any]) -> str:
    start, end = _fy(m.get("fiscal_year_start")), _fy(m.get("fiscal_year_end"))
    if start and end and start != end:
        return f" from {start} to {end}" if start < end else ""
    if start or end:
        return f" in {start or end}"
    return ""


def _article(phrase: str) -> str:
    return "an" if phrase[:1].lower() in "aeiou" else "a"


def _pick(candidates: list[str]) -> str:
    """First headline that fits without the dispatcher's clip, else the shortest one."""
    options = [clean(c) for c in candidates if clean(c)]
    for option in options:
        if len(option) <= _HEADLINE_FIT:
            return option
    return min(options, key=len) if options else ""


def _triggers(m: dict[str, Any]) -> set[str]:
    raw = m.get("triggered_rules")
    return {r for r in raw if isinstance(r, str)} if isinstance(raw, (list, tuple)) else set()


def _short_window(m: dict[str, Any]) -> int | None:
    days = _int(m.get("bid_window_days"))
    if days is None or days >= _SHORT_WINDOW_DAYS:
        return None
    start, end = parse_date(m.get("publish_date")), parse_date(m.get("close_date"))
    # The portal rounds part-days down, so one day of slack. More than that and the field is not a window.
    if start and end and abs((end - start).days - days) > 1:
        return None
    return days


def _window_length(days: int) -> str:
    if days == 0:
        return "less than one day"
    return "one day" if days == 1 else f"{days} days"


def _window_sentence(days: int | None) -> str:
    return f"Bids were open for {_window_length(days)}." if days is not None else ""


def _bids(m: dict[str, Any]) -> tuple[int | None, int | None]:
    return _int(m.get("bids_received")), _int(m.get("technically_qualified_bids"))


def _is_single(m: dict[str, Any]) -> bool:
    """One effective bid, and no received or qualified count that contradicts it."""
    if _int(m.get("effective_bids")) != 1:
        return False
    received, qualified = _bids(m)
    return qualified == 1 or (received in (None, 1) and qualified in (None, 0))


def _single_kind(m: dict[str, Any]) -> tuple[str, int | None]:
    """('received', None), ('qualified', bids received or None): which count of one the record shows."""
    received, qualified = _bids(m)
    if qualified == 1 and received != 1:
        return "qualified", received if received is not None and received >= 2 else None
    return "received", None


def _bid_phrase(m: dict[str, Any]) -> str:
    kind, received = _single_kind(m)
    if kind == "qualified":
        return f"{received} bids, of which one qualified," if received else "one qualified bid"
    return "one bid"


def _bids_sentence(m: dict[str, Any]) -> str:
    received, qualified = _bids(m)
    if received is None or received < 1:
        return ""
    if received == 1:
        return "It drew one bid."
    if qualified is not None and 1 <= qualified < received:
        return f"It drew {received} bids, of which {'one' if qualified == 1 else qualified} qualified."
    return f"It drew {received} bids."


def _wins(m: dict[str, Any]) -> int | None:
    wins = _int(m.get("winner_total_wins"))
    return wins if wins is not None and wins >= 2 else None


def _award_total(finding: dict[str, Any], m: dict[str, Any], wins: int) -> int | None:
    """All awards the records list for the same buyer chain, the base of the count share.

    The engine stores the share but prints its base only in the summary, so the base is read from there
    and kept only when it reproduces the stored share.
    """
    share = num(m.get("winner_share_by_count_pct"))
    summary = finding.get("summary")
    found = _AWARD_TOTAL.search(summary) if isinstance(summary, str) else None
    if share is None or share <= 0 or found is None:
        return None
    total = _int(found.group(1).replace(",", ""))
    if total is None or total < wins or abs(round(wins / total * 100.0, 2) - share) > 0.011:
        return None
    return total


def _method(m: dict[str, Any]) -> str:
    """How the record says the purchase was run, when that bears on how many bids to expect."""
    exemption = m.get("exemption_status")
    if exemption in _METHOD_NOTES:
        return exemption
    method = m.get("procurement_method") if isinstance(m.get("procurement_method"), str) else ""
    if _DIRECT_METHOD.search(method):
        return "direct"
    return "limited" if "limited" in method.lower() else ""


def _method_note(m: dict[str, Any]) -> str:
    return _METHOD_NOTES.get(_method(m), "")


def _purchase_noun(m: dict[str, Any]) -> str:
    return "contract" if _method(m) == "direct" else "tender"


def _single_valid_wording(m: dict[str, Any]) -> str:
    # Sources without a qualified-bid count can only mean bids received.
    return "one bid" if _bids(m)[1] is None else "one bid or one qualified bid"


def _cohort_slot(m: dict[str, Any], body: _Body, shown: str, lead: str, rank: int) -> _Slot:
    """'<lead> is listed on N <body> contracts from A to B, K of them with one bid.'"""
    wins = _wins(m)
    if wins is None:
        return _slot(rank)
    count = fmt_num(wins, 0)
    by_noun = f"{count} of the {_noun(body)}'s contracts" if not body.scoped else f"{count} contracts"
    named = f"{count} {shown} contracts" if shown else by_noun
    single = _int(m.get("winner_single_bid_wins"))
    extra = ""
    if single and 2 <= single < wins:
        extra = f", {fmt_num(single, 0)} of them with {_single_valid_wording(m)}"
    elif single and single == wins:
        extra = f", {'both' if wins == 2 else 'all'} with {_single_valid_wording(m)}"
    span = _span(m)
    shapes = (f"{named}{span}{extra}", f"{named}{span}", named, by_noun)
    return _slot(rank, *(f"{lead} is listed on {shape}." for shape in shapes))


def _tender_sentence(finding: dict[str, Any], m: dict[str, Any], *, opening: str) -> str:
    """'<opening> was published on D and awarded for V.' Empty when neither fact is on record."""
    amount, kind = _value(finding, m)
    award, publish = _date(m.get("award_date")), _date(m.get("publish_date"))
    value_clause = _value_clause(amount, kind)
    if award and kind == "award":
        return f"{opening} was awarded on {award} for {amount}."
    if award:
        tail = f" and {value_clause}" if value_clause else ""
        return f"{opening} was awarded on {award}{tail}."
    if publish:
        tail = f" and {value_clause}" if value_clause else ""
        return f"{opening} was published on {publish}{tail}."
    return f"{opening} was {value_clause}." if value_clause else ""


def _repeat_winner(
    finding: dict[str, Any], m: dict[str, Any], state: str, body: _Body, *, bid_count_missing: bool = False
) -> tuple[str, str]:
    wins = _wins(m)
    if wins is None:
        return "", ""
    count = fmt_num(wins, 0)
    authority = body.name
    full = _with_state(authority, state)
    triggers = _triggers(m)
    count_share = num(m.get("winner_share_by_count_pct"))
    value_share = num(m.get("winner_share_by_value_pct"))
    total = _award_total(finding, m, wins)
    noun = _noun(body)
    notable = bool(triggers & {"P7-HIGH-AWARD-COUNT-SHARE", "P7-HIGH-AWARD-VALUE-SHARE"}) or (
        count_share is not None and count_share >= _SHARE_WORTH_SAYING_PCT
    )

    plain = [f"Same winner name listed on {count} {name} contracts" for name in (full, authority) if name]
    fallback = f"Same winner name listed on {count} contracts from one {state + ' ' if state else ''}public body"
    candidates: list[str] = []
    if bid_count_missing:
        candidates += [option for line in plain for option in (f"{line}, bid count not shown", line)]
    else:
        if full and total and notable and not body.scoped:
            share = f"all {count}" if total == wins else f"{count} of {fmt_num(total, 0)}"
            candidates.append(f"Same winner name listed on {share} {full} contracts")
        candidates += plain
    headline = _pick([*candidates, fallback])

    source = _source(m, state)
    shown = _brief_name(authority, state, source)
    who = _company(m) or "the same winner name"
    worth = fmt_inr(m.get("winner_cohort_total_inr"))
    worth = f", worth {worth} in all" if worth else ""
    contracts = f"{shown} contracts" if shown else "contracts from one public body"
    opening = f"{source} list {who} on {count} {contracts}"
    first = _slot(0, f"{opening}{_span(m)}{worth}.", f"{opening}{_span(m)}.", f"{opening}.")

    if total == wins:
        share_core = f"Those are all {count} awards listed for the {noun}"
    elif total:
        share_core = f"That is {count} of the {fmt_num(total, 0)} awards listed for the {noun}"
    elif notable and count_share:
        share_core = f"That is {fmt_pct(count_share)} of the awards listed for the {noun}"
    else:
        share_core = ""
    share_variants: list[str] = []
    if share_core:
        if value_share and "P7-HIGH-AWARD-VALUE-SHARE" in triggers:
            share_variants.append(f"{share_core}, and {fmt_pct(value_share)} of their value.")
        share_variants.append(f"{share_core}.")

    work = _work(m)
    this_one = f"This one, for {work}," if work and work != "works" else "This one"
    gap = "The award data does not show how many bids were received." if bid_count_missing else ""
    brief = _compose([
        first,
        _slot(1 if notable else 3, *share_variants),
        _slot(2, _tender_sentence(finding, m, opening=this_one)),
        _slot(0, gap),
        _slot(4, _bids_sentence(m) if (_bids(m)[0] or 0) >= 2 else ""),
        _slot(5, _method_note(m)),
        _slot(6, _title_sentence(m)),
    ])  # fmt: skip
    return headline, brief


def _rate_sentence(m: dict[str, Any], body: _Body) -> str:
    rate = num(m.get("authority_single_bid_rate_pct"))
    if "P7-HIGH-AUTHORITY-SB-RATE" not in _triggers(m) or not rate or not 0 < rate <= 100:
        return ""
    return f"Among the {_noun(body)}'s tenders with a bid count, {fmt_pct(rate)} had {_single_valid_wording(m)}."


def _single_first(
    finding: dict[str, Any], m: dict[str, Any], state: str, shown: str, *, noun: str, with_publish: bool
) -> _Slot:
    amount, kind = _value(finding, m)
    subject = " ".join(p for p in ("this", shown, _work(m), noun) if p)
    value = _value_clause(amount, kind)
    publish = f"published on {_date(m.get('publish_date'))}" if _date(m.get("publish_date")) else ""
    opening = f"{_source(m, state)} show {_bid_phrase(m)} for {subject}"
    tails = [" and ".join(f for f in facts if f) for facts in ((publish if with_publish else "", value), (value,))]
    return _slot(0, *(f"{opening}, {tail}." if tail else f"{opening}." for tail in tails))


def _single_bid(finding: dict[str, Any], m: dict[str, Any], state: str, body: _Body) -> tuple[str, str]:
    if not _is_single(m):
        return "", ""
    authority = body.name
    amount, kind = _value(finding, m)
    full = _with_state(authority, state)
    noun = _purchase_noun(m)
    headline_noun = f"direct {noun}" if noun == "contract" else noun
    single_kind, received = _single_kind(m)
    if single_kind == "received":
        leads = ("Only one bid recorded for", "One bid recorded for")
    elif received:
        leads = (f"Only one of {received} bids qualified for", f"One of {received} bids qualified for")
    else:
        leads = ("Only one qualified bid recorded for", "One qualified bid recorded for")

    def subject(name: str, priced: bool) -> str:
        if priced and amount:
            return f"a {amount} {name} {headline_noun}"
        return f"{_article(name)} {name} {headline_noun}"

    fallback = f"{state} public" if state else "public"
    candidates = [f"{lead} {subject(full, True)}" for lead in leads if full]
    candidates += [f"{leads[0]} {subject(full, False)}"] if full else []
    candidates += [f"{lead} {subject(authority, priced)}" for priced in (True, False) for lead in leads if authority]
    candidates += [f"{leads[0]} {subject(fallback, priced)}" for priced in (True, False)]
    headline = _pick(candidates)

    source = _source(m, state)
    company = _company(m)
    # A blank string is the engine saying the record has no winner. A missing field says nothing.
    winner_blank = isinstance(m.get("winner_name"), str) and not clean(m.get("winner_name"))
    no_winner = "The record does not name a winner." if winner_blank and kind == "estimate" else ""
    brief = _compose([
        _single_first(finding, m, state, _brief_name(authority, state, source), noun=noun, with_publish=True),
        _slot(0, _method_note(m)),
        _slot(1, _window_sentence(_short_window(m))),
        _slot(2, _rate_sentence(m, body)),
        _slot(3, no_winner),
        _slot(4, _title_sentence(m)),
        _slot(5, f"The winner is listed as {company}." if company else ""),
    ])  # fmt: skip
    return headline, brief


def _combined(finding: dict[str, Any], m: dict[str, Any], state: str, body: _Body) -> tuple[str, str]:
    wins = _wins(m)
    if not _is_single(m) or wins is None:
        return _single_bid(finding, m, state, body)
    count = fmt_num(wins, 0)
    authority = body.name
    full = _with_state(authority, state)
    amount, _ = _value(finding, m)
    noun = _purchase_noun(m)
    headline_noun = f"direct {noun}" if noun == "contract" else noun
    lead = "One qualified bid for" if _single_kind(m)[0] == "qualified" else "One bid for"
    tail = f", winner name on {count} contracts"

    def subject(name: str, priced: bool) -> str:
        if priced and amount:
            return f"{amount} {name} {headline_noun}"
        return f"{_article(name)} {name} {headline_noun}"

    names = [name for name in (full, authority, f"{state} public" if state else "public") if name]
    headline = _pick([f"{lead} {subject(name, priced)}{tail}" for name in names for priced in (True, False)])

    source = _source(m, state)
    shown = _brief_name(authority, state, source)
    window = _window_sentence(_short_window(m))
    winner = _company(m) or "The same winner name"
    brief = _compose([
        # A short window is the timing fact worth the space, so it replaces the publish date.
        _single_first(finding, m, state, "", noun=noun, with_publish=not window),
        _slot(0, _method_note(m)),
        _slot(0, window),
        _cohort_slot(m, body, shown, winner, 0),
        _slot(2, _rate_sentence(m, body)),
        _slot(3, _title_sentence(m)),
    ])  # fmt: skip
    return headline, brief


def _process_gap(finding: dict[str, Any], m: dict[str, Any], state: str, body: _Body) -> tuple[str, str]:
    authority = body.name
    amount, kind = _value(finding, m)
    names = [n for n in (_with_state(authority, state), authority, f"{state} public" if state else "public") if n]
    source = _source(m, state)
    shown = _brief_name(authority, state, source)
    work = _work(m)
    window = _short_window(m)
    bid_count_missing = _int(m.get("effective_bids")) is None
    not_shown = "The award data does not show how many bids were received."

    def subject(name: str, noun: str) -> str:
        return f"a {amount} {name} {noun}" if amount else f"{_article(name)} {name} {noun}"

    if window is not None:
        length = _window_length(window)
        heads = [f"Bids for {subject(name, 'tender')} were open for {length}" for name in names]
        tender = " ".join(part for part in ("this", shown, work, "tender") if part)
        brief = _compose([
            _slot(0, f"{source} show bids for {tender} were open for {length}."),
            _slot(1, _tender_sentence(finding, m, opening="It")),
            _slot(2, not_shown if bid_count_missing else _bids_sentence(m)),
            _cohort_slot(m, body, "", "The same winner name", 3),
        ])  # fmt: skip
        return _pick(heads), brief

    if not bid_count_missing:
        return "", ""
    if _wins(m) is not None:
        return _repeat_winner(finding, m, state, body, bid_count_missing=True)
    heads = [f"Bid count not shown for {subject(name, 'contract')}" for name in names]
    contract = " ".join(part for part in ("this", shown, work, "contract") if part)
    award = _date(m.get("award_date"))
    if award and kind == "award":
        tail = f", awarded on {award} for {amount}"
    else:
        facts = [fact for fact in (f"awarded on {award}" if award else "", _value_clause(amount, kind)) if fact]
        tail = f", {' and '.join(facts)}" if facts else ""
    opening = f"{source} do not show how many bids were received for {contract}"
    return _pick(heads), _compose([_slot(0, f"{opening}{tail}.", f"{opening}."), _slot(1, _title_sentence(m))])


def render(finding: dict[str, Any]) -> tuple[str, str]:
    if not isinstance(finding, dict):
        return "", ""
    m = metrics(finding)
    if not m:  # nothing structured to report, so leave it to the generic fallback
        return "", ""
    state = _state(finding)
    body = _authority(finding, state)
    raw_rule, raw_category = finding.get("rule_triggered"), m.get("signal_category")
    rule = raw_rule.strip().upper() if isinstance(raw_rule, str) else ""
    category = raw_category.strip().lower() if isinstance(raw_category, str) else ""

    if rule == "P7-COMBINED-SB-RW" or category == "single_bidder_repeat_winner":
        return _combined(finding, m, state, body)
    if rule == "P7-SINGLE-BID" or category == "unexplained_single_bidder":
        return _single_bid(finding, m, state, body)
    if rule == "P7-REPEAT-WINNER" or category == "repeat_winner_concentration":
        return _repeat_winner(finding, m, state, body)
    if rule == "P7-PROCESS-GAP" or category == "short_window_or_process_gap":
        return _process_gap(finding, m, state, body)
    return "", ""
