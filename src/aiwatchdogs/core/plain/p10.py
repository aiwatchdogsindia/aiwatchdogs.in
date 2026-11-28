"""Plain-language writer for P10: custody and release records from courts and oversight bodies.

Most P10 findings are single court orders (a detention order struck down, a bail or release plea,
a case that refers to an acquittal) with no measured delay: ``overrun_days`` is zero or missing.
For those the text says what the order did and states plainly that no period of extra custody is on
record. Only when ``overrun_days`` is above zero does the text speak of extra days in custody.
A third group are general documents (parliament replies, NHRC and NALSA reports) that concern no
single case. Case titles carry the names of private people, so court findings never quote them.
"""

from __future__ import annotations

import re
from typing import Any

from ..safety import scan_forbidden_terms
from ._common import clean, fmt_date, fmt_days, fmt_inr, metrics, num, place

_HEAD_SOFT = 100
_HEAD_HARD = 110
_BRIEF_SOFT = 255

_PAREN = re.compile(r"\s*\([^()]*\)")
_REPEAT_ONE = re.compile(r"\b(\w+)(,? \1\b)+", re.IGNORECASE)
_REPEAT_TWO = re.compile(r"\b(\w+ \w+)(,? \1\b)+", re.IGNORECASE)

_GENERIC_PRISON = re.compile(r"^(?:prison\s*/|correctional institutions|prison / correctional authority)", re.IGNORECASE)
_PRISON_WORD = re.compile(r"\b(?:jail|prison|sub-jail|correctional home|police station)\b", re.IGNORECASE)
_PRISON_KIND_ONLY = re.compile(r"^(?:central|district|sub|special|open|model)[ -](?:jail|prison)$", re.IGNORECASE)
_SMALL_WORDS = frozenset({"of", "at", "and", "for", "the", "in"})

_LIBERTY = re.compile(
    r"set at liberty|released forthwith|shall be released|be released from|directed to be released",
    re.IGNORECASE,
)
# A release phrase only counts as a court direction when nothing in the words just before it negates
# it or marks it as a request: "could not be released from jail" records a failure to release, and
# "is sought to be quashed and the detenu set at liberty" is the petitioner's prayer, not an order.
_NEGATION = re.compile(
    r"\b(?:not|no|never|cannot|unable|without|neither|nor|refused|denied|sought|seeks?|seeking|prays?|prayed|prayer)\b|n't\b",
    re.IGNORECASE,
)


def _directs_release(direction: str) -> bool:
    for match in _LIBERTY.finditer(direction):
        before = direction[max(0, match.start() - 40) : match.start()]
        clause = re.split(r"[.;:]", before)[-1]
        if not _NEGATION.search(clause):
            return True
    return False


_PRISON_DEBRIS = re.compile(
    r"\s+(?:page|respondent|advocate|and|to|through|who|is|where|vide|dated|th)\b.*$|(?<=\w)[,\s-]+district\b.*$",
    re.IGNORECASE,
)
_OTHER_CASE = re.compile(r"any other case|other case/s|other criminal case", re.IGNORECASE)

_CASE_KINDS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^(?:CONT|CP|CPAN|CCP|CONTEMPT|MJC)\b|contempt", re.IGNORECASE), "contempt petition"),
    (re.compile(r"^(?:HCP|WPHC|HABC|HC|WPA\(H\)|W\.?P\.?H\.?C)\b|habeas", re.IGNORECASE), "habeas corpus petition"),
    (re.compile(r"^(?:W\.?P\.?\s*\(?CRL?\.?\)?|WPCR|WPCRL|CRWP|CRLWP|CR\.?WJC)", re.IGNORECASE), "criminal writ petition"),
    (re.compile(r"^(?:CRL\.?\s*A|CRA|CRIMINAL APPEAL)", re.IGNORECASE), "criminal appeal"),
    (re.compile(r"^(?:MCRC|BA|CRM)\b", re.IGNORECASE), "criminal application"),
)

_RECORD_KINDS: dict[str, str] = {
    "nhrc_shrc": "human rights commission record",
    "nalsa_slsa": "legal services authority record",
    "prison_portal": "prison department record",
    "police_portal": "police record",
    "govt_ministry": "government record",
    "cag_audit": "CAG audit report",
}

# Ordinary words in document titles; anything else keeps its capital letter.
_TITLE_WORDS_TEXT = """access action administration administrative aid among and annual assistance audit awareness bail campaign
    cases children clinics committee compliance connected constitutional correctional country courts custodial
    custody days death deaths decisions delay demands detention different due fast filing for free full
    functioning grants handbook illegal implementation in increase inquiry intensive jail jails judicial
    languishing legal lodged marginalised mission mode notices of on outreach overcrowded overcrowding oversight
    overview parole pending police premature prison prisoners prisons procedure project recommendation
    release releases report research review rights schemes sections solicitation standard statistical
    the through timely to torture track trial trials under undertrial undertrials unlawful verdicts violence
    visits with women writs across applications arrest communities compensation counsel counsels criminal
    defense despite encounters evaluation executive fake female framework from grant homes human incidents
    increasing inmates inspect law manual matters mechanism minority model modified monitoring need new not
    operating or persons phase procedures programme programmes protection rape reform reforms regulation
    released remand representation safeguards scheme status strengthening summary support system taken
    their"""
_TITLE_WORDS = frozenset(_TITLE_WORDS_TEXT.split())

_CAUTION = re.compile(r"illegal|unlawful|violat|egregious|contemnor|alleg", re.IGNORECASE)


def _text(value: Any) -> str:
    """Field as clean text. Containers and booleans count as missing."""
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return clean(value)
    return ""


def _days(value: Any) -> int | None:
    n = num(value)
    if n is None or n <= 0:
        return None
    return round(n)


def _court(name: str) -> str:
    """'High Court of Judicature at Madras' -> 'Madras High Court'."""
    name = clean(_PAREN.sub("", name))
    if name.lower().startswith("supreme court"):
        return "Supreme Court"
    seat = re.match(r"^High Court (?:of Judicature )?(?:of|at|for) (?:the State of )?(.+)$", name, re.IGNORECASE)
    if seat:
        return f"{seat.group(1)} High Court"
    return name


def _prison(raw: str) -> str:
    """A named prison, cleaned of capitals, repeats and scraping debris, or '' when none is given."""
    name = clean(_PAREN.sub("", raw)).rstrip(" -,/")
    if not name or _GENERIC_PRISON.match(name) or not _PRISON_WORD.search(name):
        return ""
    name = _PRISON_DEBRIS.sub("", name).rstrip(" -,/")
    if name.isupper():
        name = name.title()
    name = _REPEAT_TWO.sub(r"\1", name)
    name = _REPEAT_ONE.sub(r"\1", name)
    words = name.replace(",", " ").split()
    if not words or len(name) > 48 or len(words) > 7 or _PRISON_KIND_ONLY.match(name):
        return ""
    # A lower-case word that is not a connector means the field caught a sentence fragment, not a name.
    if any(w[0].islower() and w.lower() not in _SMALL_WORDS for w in words[1:]):
        return ""
    return name[:1].upper() + name[1:]


def _case_kind(case_number: str, contempt: bool) -> str:
    if contempt:
        return "contempt petition"
    for pattern, kind in _CASE_KINDS:
        if pattern.search(case_number):
            return kind
    return "case"


def _fit(candidates: list[str]) -> str:
    usable = [clean(c) for c in candidates if clean(c)]
    for limit in (_HEAD_SOFT, _HEAD_HARD):
        for cand in usable:
            if len(cand) <= limit:
                return cand
    return ""


def _short_courts(court: str) -> list[str]:
    """The court name, then a shorter form for the one long name in the data."""
    out = [court]
    if "Jammu & Kashmir" in court:
        out.append(court.replace("Jammu & Kashmir", "J&K"))
    return out


def _join(*parts: str) -> str:
    return " ".join(p for p in parts if p)


def _lower_title(text: str) -> str:
    """Sentence-case a document topic, leaving names and acronyms capitalised."""

    def lower(match: re.Match[str]) -> str:
        word = match.group(0)
        return word.lower() if word.lower() in _TITLE_WORDS else word

    return re.sub(r"\b[A-Z][a-z]+\b", lower, text)


def _tidy(text: str) -> str:
    out = re.sub(r"\s+[—–]\s+", ", ", text).replace("—", ", ").replace(";", ",")
    return clean(re.sub(r"\s+,", ",", out))


def _document(m: dict[str, Any], finding: dict[str, Any], rule: str) -> tuple[str, str]:
    """Headline and brief for a general document (parliament reply, commission report, manual)."""
    title = _text(m.get("case_title"))
    portal = _text(m.get("portal_category"))
    issuer = _text(m.get("acquittal_court")) or _text(finding.get("entity_name"))
    when = fmt_date(m.get("document_date"))
    if re.search(r"\b(?:vs?\.?|versus)\b", title, re.IGNORECASE):
        title = ""  # a case title naming private parties

    house = re.match(r"^(Lok Sabha|Rajya Sabha)\b", title or issuer)
    if portal == "parliament" and house:
        committee = "Committee" in title
        kind = f"{house.group(1)} committee report" if committee else f"{house.group(1)} reply"
    else:
        kind = _RECORD_KINDS.get(portal, "official record")

    headline = ""
    question = re.match(r"^(Lok Sabha|Rajya Sabha) [US]Q \([^)]*\):\s*(.+)$", title)
    if question:
        topic = _lower_title(_tidy(question.group(2)))
        headline = _fit([f"{question.group(1)} reply on {topic}", f"{question.group(1)} reply on {_PAREN.sub('', topic)}"])
    elif title:
        month = re.search(r"\((?:Vol\.[^)]*?[—–-]\s*)?([A-Z][a-z]+ \d{4})\)", title)
        doc, _, topic = title.rpartition(": ")
        link = " covers "
        if not doc:
            doc, _, topic = title.partition(" — ")
            link = ": "
        doc_name = _tidy(re.sub(r"\s*\((?:Vol\.|No\.|HRC)[^)]*\)", "", doc))
        if month and "Newsletter" in doc:
            doc_name = f"{doc_name}, {month.group(1)}"
        topic = _lower_title(_tidy(topic))
        names = [doc_name]
        abbreviated = re.match(r"^.+? \(([A-Z&]{3,8})\),? (.+)$", doc_name)
        if abbreviated:
            names.append(f"{abbreviated.group(1)} {abbreviated.group(2)}")
        names += [clean(_PAREN.sub("", n)) for n in list(names)]
        forms = [f"{n}{link}{t}" for n in names for t in (topic, clean(_PAREN.sub("", topic)))]
        if not topic:
            forms = names
        headline = _fit(forms)
    if not headline:
        short = clean(_PAREN.sub("", issuer))
        headline = _fit([f"{short} record on custody and release", f"{kind.capitalize()} on custody and release"])

    subject = {
        "P10-STATUTORY-UNLAWFUL-DETENTION": "detention, time spent in custody or release",
        "P10-WARRANT-TRANSMISSION-DELAY": "release warrants and how they reach prisons",
    }.get(rule, "undertrial detention, release delays or custody rules")
    article = "An" if kind[0].lower() in "aeio" else "A"
    opening = f"This is {article.lower()} {kind} dated {when}." if when else f"This is {article.lower()} {kind}."
    brief = (
        f"{opening} The audit lists it as a general record on {subject}. "
        "It is not about one case and gives no count of extra days in custody."
    )
    return headline, brief


def _overrun(m: dict[str, Any], court: str, prison: str, order_type: str, days: int, comp: str) -> tuple[str, str]:
    """A case where the record counts days of custody beyond a court order."""
    span = fmt_days(days)
    after = " after an acquittal" if order_type == "acquittal" else ""
    courts = _short_courts(court) if court else [""]
    heads: list[str] = []
    for c in courts:
        who = f"{c} order" if c else "Court order"
        if comp:
            heads.append(f"{who} records {span} of extra custody{after}, {comp} compensation")
        heads.append(f"{who} records {span} of extra custody{after}")
    headline = _fit(heads)

    doc_date = fmt_date(m.get("document_date"))
    acq_date = fmt_date(m.get("acquittal_order_date"))
    release = fmt_date(m.get("release_date"))
    source = f"A {court} order" if court else "A court order"
    if doc_date:
        source += f" dated {doc_date}"
    where = f" at {prison}" if prison else ""
    anchor = ""
    if order_type == "acquittal" and acq_date and acq_date != doc_date:
        anchor = f" after an acquittal on {acq_date}"
    elif order_type == "acquittal":
        anchor = " after an acquittal"
    parts = [f"{source} records {span} of extra custody{where}{anchor}."]
    if release and release != doc_date and m.get("release_verified") is True:
        parts.append(f"Release is recorded on {release}.")
    if comp:
        parts.append(f"The court ordered {comp} in compensation.")
    if m.get("stage_3_contempt_verified") is True:
        parts.append("A contempt action is on record.")
    brief = ""
    for part in parts:
        if len(_join(brief, part)) <= _BRIEF_SOFT:
            brief = _join(brief, part)
    return headline, brief


def _order(
    m: dict[str, Any], finding: dict[str, Any], court: str, prison: str, order_type: str, comp: str
) -> tuple[str, str]:
    """A single court order with no measured period of extra custody."""
    state = place(finding)
    if state == "Union government":
        state = ""
    direction = _text(m.get("judicial_direction_summary"))
    liberty = _directs_release(direction)
    other_case = bool(_OTHER_CASE.search(direction))
    contempt = m.get("stage_3_contempt_verified") is True or bool(_text(m.get("contempt_court")))
    case_number = _text(m.get("acquittal_case_number")) or _text(m.get("contempt_case_number"))
    kind = _case_kind(case_number, contempt and order_type == "bail_release_order")
    verified = m.get("stage_1_acquittal_verified") is not False
    courts = _short_courts(court) if court else ["A court"]
    same_place = state.lower().replace(" and ", " & ") in court.lower().replace(" and ", " & ")
    in_state = f" in {state}" if state and not same_place else ""
    when = fmt_date(m.get("document_date")) or fmt_date(m.get("acquittal_order_date"))
    on_date = f" on {when}" if when else ""

    per_court: list[list[str]] = []
    for c in courts:
        heads: list[str] = []
        if comp:
            base = [f"{c} ordered {comp} compensation in a custody case{in_state}"]
        elif order_type == "detention_quashed" and verified:
            base = []
            if liberty and prison:
                heads.append(f"{c} struck down a detention order and directed release from {prison}")
            if prison:
                heads.append(f"{c} struck down a detention order for a person held at {prison}")
            if liberty:
                base.append(f"{c} struck down a detention order and directed the detainee's release")
            base.append(f"{c} struck down a detention order{in_state}")
        elif order_type == "detention_quashed":
            heads += [f"{c} ruled on a challenge to a detention order at {prison}"] if prison else []
            base = [f"{c} ruled on a challenge to a detention order{in_state}"]
        elif order_type == "acquittal":
            heads += [f"{c} order refers to an acquittal in a custody case at {prison}"] if prison else []
            base = [f"{c} order refers to an acquittal in a custody case{in_state}"]
        elif order_type == "conviction_set_aside":
            base = [f"{c} order on custody after a conviction was set aside{in_state}"]
        elif contempt:
            heads += [f"{c} heard a contempt plea over a release order in a case from {prison}"] if prison else []
            base = [f"{c} heard a contempt plea over a release order{in_state}"]
        elif liberty:
            heads += [f"{c} directed the release of a person held at {prison}"] if prison else []
            base = [f"{c} directed a person's release in a custody case{in_state}"]
        else:
            heads += [f"{c} ruled on a bail or release plea from a prisoner at {prison}"] if prison else []
            base = [f"{c} ruled on a bail or release plea{in_state}"]
        # Without a prison to tell near-identical orders apart, the date does that job.
        heads += [f"{b}{on_date}" for b in base] + base
        per_court.append(heads)
    # Try each wording with the full court name, then the short one, before moving to a plainer wording.
    depth = max(len(h) for h in per_court)
    headline = _fit([h[i] for i in range(depth) for h in per_court if i < len(h)])

    closing = "No extra days in custody are on record."
    brief = ""
    # The closing line is what keeps this fair, so a long court name gives way before it does.
    for name in _short_courts(court) if court else [""]:
        detail = _order_detail(name, kind, on_date, prison, order_type, verified, liberty, other_case, contempt)
        parts = [detail, f"The court ordered {comp} in compensation." if comp else "", closing]
        brief = _join(*parts)
        if len(brief) <= _BRIEF_SOFT:
            break
    else:
        brief = _join(_order_detail("", kind, on_date, "", order_type, verified, liberty, other_case, contempt), closing)
    return headline, brief


def _order_detail(
    court: str,
    kind: str,
    on_date: str,
    prison: str,
    order_type: str,
    verified: bool,
    liberty: bool,
    other_case: bool,
    contempt: bool,
) -> str:
    """One sentence saying what the order did."""
    the_court = f"the {court}" if court else "a court"
    intro = f"In a {kind}, {the_court}{on_date}" if kind != "case" else f"{the_court[:1].upper()}{the_court[1:]}{on_date}"
    held = f", held at {prison}," if prison else ""
    at = f" for a person held at {prison}" if prison else ""
    unless = " unless wanted in another case" if other_case else ""
    if order_type == "detention_quashed" and verified:
        if liberty:
            return f"{intro} set aside a detention order and directed that the detainee{held} be freed{unless}."
        return f"{intro} set aside a detention order{at}."
    if order_type == "detention_quashed":
        return f"{intro} ruled on a challenge to a detention order{at}."
    if order_type == "acquittal":
        return f"{intro} gave an order that refers to an acquittal and deals with custody or release{at}."
    if order_type == "conviction_set_aside":
        return f"{intro} dealt with custody after a conviction was set aside."
    if contempt:
        named = f" The prison named is {prison}." if prison else ""
        return f"{intro} dealt with whether an earlier release order was followed.{named}"
    if liberty:
        return f"{intro} directed that a person{held} be freed{unless}."
    return f"{intro} ruled on bail, remission or early release" + (f" of a prisoner at {prison}." if prison else ".")


def render(finding: dict[str, Any]) -> tuple[str, str]:
    """Return (headline, brief) for one P10 finding, or ('', '') when the fields say too little."""
    if not isinstance(finding, dict):
        return "", ""
    m = metrics(finding)
    rule = _text(finding.get("rule_triggered")).upper()
    order_type = _text(m.get("acquittal_order_type")).lower()
    portal = _text(m.get("portal_category")).lower()
    court_name = _text(m.get("acquittal_court")) or _text(m.get("contempt_court"))
    if not m or not (order_type or court_name or _text(m.get("case_title"))):
        return "", ""

    is_court = "court" in court_name.lower() and "commission" not in court_name.lower()
    if order_type == "institutional_compliance_review" and not (portal == "court_portal" and is_court):
        headline, brief = _document(m, finding, rule)
        if scan_forbidden_terms(headline):
            headline = "Official record on custody and release"
        return headline[:1].upper() + headline[1:], "" if scan_forbidden_terms(brief) else brief
    else:
        court = _court(court_name) if is_court else ""
        prison = _prison(_text(m.get("prison_name")))
        comp = fmt_inr(m.get("compensation_inr"))
        days = _days(m.get("overrun_days"))
        if days:
            headline, brief = _overrun(m, court, prison, order_type, days, comp)
        else:
            headline, brief = _order(m, finding, court, prison, order_type, comp)

    if not headline or scan_forbidden_terms(headline) or _CAUTION.search(headline):
        return "", ""
    if scan_forbidden_terms(brief) or _CAUTION.search(brief):
        brief = ""
    return headline[:1].upper() + headline[1:], brief
