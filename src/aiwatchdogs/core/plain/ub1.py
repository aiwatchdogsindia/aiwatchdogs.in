"""UB1 plain-language writer: Union Budget lines that ended well below their budget estimate.

Two record shapes reach this writer (see ``engines/union_budget.py``):

* ``actual_vs_budget_estimate``: the budget printed two years later gives the actual spending,
  which is below the budget estimate. ``actual_printed`` False means the actual column is the
  printed nil, which the engine reads as nothing spent.
* ``revised_vs_budget_estimate``: only the mid-year revised estimate is compared. No actual
  spending is known for the year, so the text never speaks of money being spent or unspent.

All amounts in ``anomaly_metrics`` are in crore of rupees. The "department" in the entity name
is the name of the Demand for Grants, which is a ministry or department for most lines and a
head such as "Police" or "Transfers to States" for the rest.
"""

from __future__ import annotations

import math
import re
from typing import Any

from ._common import clean, fiscal_year, fmt_inr, fmt_num, metrics, num

# The dispatcher clips at 110 and 260, and this writer stays clear of both. A headline keeps
# the year while that fits the soft limit and gives it up before it gives up part of a name.
_HEAD_TARGET = 90
_HEAD_SOFT = 96
_HEAD_LIMIT = 104
_HEAD_LAST_RESORT = 108
_BRIEF_TARGET = 225
_BRIEF_LIMIT = 250
_SHORT_NAME = 22

_TITLE_TAIL = re.compile(r":\s+(?:actual expenditure in|revised estimate for|no (?:actual|revised)\b).*$", re.IGNORECASE)
# 'Co- contribution', 'De- addiction', 'Socio- Economic': a hyphenated word split by the PDF line wrap.
_WRAPPED_HYPHEN = re.compile(
    r"\b(pm|co|de|socio|para|non|sub|pre|post|multi|inter|intra|anti|semi|self|agro|bio|micro|macro|infra|tele|c|e)- (?=\w)",
    re.IGNORECASE,
)
_LOOSE_DASH = re.compile(r"(?<=[\w)])- (?=\w)")
_REPEATED_WORD = re.compile(r"\b(\w{2,})( \1\b)+", re.IGNORECASE)
_TRAILING_BRACKET = re.compile(r"^(.{12,}?)\s*\(([^()]{2,})\)$")
_SMALL_WORDS = frozenset({"a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the", "to", "under", "with"})
# Misprints in the budget papers that a reader would stumble on.
_SPELLING = {
    "misson": "mission",
    "infrastracture": "infrastructure",
    "buidling": "building",
    "indsutry": "industry",
    "managment": "management",
    "strengthing": "strengthening",
    "accomodation": "accommodation",
    "orgnaisation": "organisation",
    "quaters": "quarters",
    "resarch": "research",
    "physcially": "physically",
    "capacitites": "capacities",
    "acclerator": "accelerator",
}
_SPELLING_RE = re.compile(r"\b(" + "|".join(_SPELLING) + r")\b", re.IGNORECASE)
# Short everyday words that are not abbreviations when a whole name is printed in capitals.
_SHORT_PLAIN_WORDS = frozenset(
    ["aid", "air", "area", "army", "arts", "bank", "care", "cell", "city", "coal", "crop", "dam", "dams", "data", "debt", "drug", "fire", "fish", "food", "fund", "gas", "grid", "hill", "jute", "land", "law", "life", "loan", "main", "mass", "navy", "new", "oil", "pay", "plan", "port", "post", "rail", "road", "seed", "silk", "soil", "tax", "tea", "toll", "unit", "war", "west", "east", "wing", "wool", "work", "yoga", "zone"]
)
# Joining words a long name can end before and still read as a name.
_CLEAN_CUTS = (
    " for ", " under ", " by ", " given ", " as ", " in ", " at ", " to ", " on ", " through ",
    " including ", " from ", " towards ", " with ", " - ", " / ", " (", "/",
)  # fmt: skip
# Words a cut name must not end on.
_DANGLING = _SMALL_WORDS | {"already", "any", "those", "including", "its", "their", "other", "new", "based", "related", "engaged"}
_TERRITORIES = (
    "Andaman and Nicobar Islands",
    "Chandigarh",
    "Dadra and Nagar Haveli and Daman and Diu",
    "Dadra and Nagar Haveli",
    "Daman and Diu",
    "Delhi",
    "Jammu and Kashmir",
    "Ladakh",
    "Lakshadweep",
    "Puducherry",
)
_STATES = (
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat", "Haryana",
    "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur",
    "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana",
    "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
)  # fmt: skip
_PLACES = tuple(sorted((p.lower() for p in (*_TERRITORIES, *_STATES)), key=len, reverse=True))
_ADJECTIVES = frozenset({"Micro", "Small", "Medium", "New", "Rural", "Urban", "Social", "Public"})
_NEEDS_ARTICLE = re.compile(
    r"^(Ministry|Department|Election Commission|Rajya Sabha|Lok Sabha|Indian Audit|Cabinet|Supreme Court|"
    r"President|Vice[- ]President|Union Public Service Commission|Central Vigilance Commission)\b"
)
# Words that name no particular scheme or body. A line made only of these ('Secretariat',
# 'Other Programmes', 'Research and Development') says little without the place it sits in.
_GENERIC_WORDS = frozenset(
    ["activities", "administration", "administrative", "assistance", "attached", "autonomous", "bodies", "body", "building", "buildings", "capacity", "capital", "central", "charges", "communication", "component", "components", "conferences", "construction", "cooperation", "co-operation", "development", "direction", "economic", "education", "equipment", "establishment", "evaluation", "expenditure", "extension", "general", "grant", "grants", "headquarters", "human", "information", "infrastructure", "institutions", "international", "items", "loans", "machinery", "maintenance", "management", "mass", "members", "miscellaneous", "modernisation", "modernization", "monitoring", "office", "offices", "organisation", "organization", "other", "others", "outlay", "payments", "plan", "planning", "policy", "programme", "programmes", "program", "programs", "project", "projects", "provision", "provisions", "publications", "publicity", "purchase", "regulatory", "research", "residential", "resource", "resources", "revenue", "scheme", "schemes", "science", "secretariat", "services", "social", "special", "state", "states", "statutory", "strengthening", "studies", "subordinate", "sub-ordinate", "support", "survey", "technology", "training", "transfer", "transfers", "ut", "uts", "welfare", "works"]
)
_NAME_MATCH_NOTE = "The rows in the two budgets were matched by name."
_NO_ACTUALS_NOTE = "Actual spending for the year is not compared here."
_SHORTENED_NOTE = "The line's name is shortened here."


def _title_case(text: str) -> str:
    """Title-case a name printed in capitals. Short all-capital words are kept as abbreviations."""
    words = text.split(" ")
    long_caps = [w for w in words if len(w) > 5 and w.isalpha() and w.isupper()]
    if len(long_caps) < 2 or len(long_caps) * 2 < len(words):
        return text
    out: list[str] = []
    for i, word in enumerate(words):
        low = word.lower()
        if i and low in _SMALL_WORDS:
            out.append(low)
        elif word.isupper() and (len(word) > 4 or low in _SHORT_PLAIN_WORDS):
            out.append(low.capitalize())
        else:
            out.append(word)
    return " ".join(out)


def _respell(match: re.Match[str]) -> str:
    word = match.group(0)
    fixed = _SPELLING[word.lower()]
    if word.isupper():
        return fixed.upper()
    return fixed.capitalize() if word[0].isupper() else fixed


def _tidy(value: Any) -> str:
    """One printed name, with wrap hyphens, loose dashes, doubled words and cut-off tails repaired."""
    text = clean(value).replace("—", "-").replace("–", "-").replace(";", ",")
    if "…" in text:
        # The engine cuts very long names with an ellipsis, usually part way through a word.
        text = text.split("…")[0].rstrip()
        text = text[: text.rfind(" ")] if " " in text else text
    text = _WRAPPED_HYPHEN.sub(lambda m: f"{m.group(1)}-", text)
    text = _LOOSE_DASH.sub(" - ", text)
    text = re.sub(r"(?<=[A-Za-z])\((?=\w)", " (", text)
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+([),])", r"\1", text)
    text = re.sub(r"(?<=\w)/ (?=\w)", "/", text)
    text = text.rstrip("./ ")
    text = _REPEATED_WORD.sub(r"\1", text)
    text = _SPELLING_RE.sub(_respell, text)
    if text.count("(") != text.count(")"):
        text = text.replace("(", "").replace(")", "")
    return _title_case(clean(text))


def _is_abbreviation(text: str) -> bool:
    upper = sum(1 for c in text if c.isupper())
    lower = sum(1 for c in text if c.islower())
    return 2 <= len(text) <= 24 and upper >= 2 and upper >= lower


def _split_entity(finding: dict[str, Any]) -> tuple[list[str], str, bool]:
    """Return (heading path ending in the line name, department, name was cut short by the engine)."""
    raw = finding.get("entity_name")
    raw = raw if isinstance(raw, str) else ""
    if not raw.strip():
        title = finding.get("title")
        raw = _TITLE_TAIL.sub("", title) if isinstance(title, str) else ""
    raw = " ".join(raw.split())
    department = ""
    if raw.endswith(")"):
        depth = 0
        for i in range(len(raw) - 1, -1, -1):
            if raw[i] == ")":
                depth += 1
            elif raw[i] == "(":
                depth -= 1
                if depth == 0:
                    # 'Line name (Department)': the engine always joins the two with a space.
                    # A bare short form such as '(NNM)' belongs to the name, not to a department.
                    if i > 0 and raw[i - 1] == " " and not _is_abbreviation(raw[i + 1 : -1]):
                        department = raw[i + 1 : -1]
                        raw = raw[:i]
                    break
    truncated = "…" in raw
    parts: list[str] = []
    for piece in raw.split(" > "):
        name = _tidy(piece)
        if name and (not parts or parts[-1].lower() != name.lower()):
            parts.append(name)
    return parts, _tidy(department), truncated


def _territory(department: str) -> str:
    """The Union Territory a 'Ministry of Home Affairs (Lakshadweep)' demand belongs to, if any."""
    match = re.search(r"\(([^()]+)\)$", department)
    if not match:
        return ""
    inner = match.group(1).strip()
    for name in _TERRITORIES:
        if inner.lower() == name.lower():
            return name
    return ""


def _the_territory(territory: str) -> str:
    return f"the {territory}" if territory.endswith("Islands") else territory


def _with_article(department: str) -> str:
    return f"the {department}" if _NEEDS_ARTICLE.match(department) else department


def _short_department(department: str) -> str:
    """'Ministry of Environment, Forests and Climate Change' -> 'Ministry of Environment' (headline only)."""
    if department.endswith(")"):
        return ""  # 'Ministry of Ayurveda, ... (AYUSH)': the first word alone is not how it is known
    head = department.split(",")[0].strip()
    if head == department or len(head.split()) < 3 or head.split()[-1] in _ADJECTIVES:
        return ""
    return head


def _content_words(name: str) -> list[str]:
    return [w for w in re.findall(r"[a-z][a-z-]*", name.lower()) if len(w) > 1 and w not in _SMALL_WORDS]


def _is_generic(name: str) -> bool:
    """'Secretariat', 'Other Programmes', 'Monitoring and Evaluation': no scheme or body is named."""
    words = _content_words(name)
    return bool(words) and all(word in _GENERIC_WORDS for word in words)


def _is_place_only(name: str) -> bool:
    """'Jammu and Kashmir and Himachal Pradesh': a line named after the places it covers."""
    text = name.lower()
    found = False
    for place in _PLACES:
        if place in text:
            text = text.replace(place, " ")
            found = True
    return found and all(word == "and" for word in re.findall(r"[a-z]+", text))


def _context_options(name: str, parent: str, department: str, territory: str) -> tuple[list[str], list[str]]:
    """Ways to say where the line sits, best first: ' in Lakshadweep', ' under the Ministry of Power'.

    The second list drops the article ('under Ministry of Power'), for when nothing else fits.
    """
    if territory:
        return [f" in {_the_territory(territory)}"], []
    options: list[str] = []
    terse: list[str] = []
    if parent and _is_generic(name) and not _is_generic(parent) and len(parent) <= 60:
        options.append(f" under {parent}")
    if department and department.lower() not in name.lower():  # 'Delhi Police' needs no 'under Police'
        options.append(f" under {_with_article(department)}")
        short = _short_department(department)
        if short:
            options.append(f" under {_with_article(short)}")
        if _with_article(department) != department:
            terse.append(f" under {department}")
    return options, terse


def _drop_dangling(text: str) -> str:
    words = text.rstrip(" ,-/(&").split(" ")
    while len(words) > 1 and words[-1].lower() in _DANGLING:
        words.pop()
    return " ".join(words).rstrip(" ,-/(&")


def _positions(text: str, needle: str, limit: int) -> list[int]:
    out: list[int] = []
    at = text.find(needle)
    while 0 <= at <= limit:
        out.append(at)
        at = text.find(needle, at + 1)
    return out


def _opening(name: str, at: int) -> str:
    """``name`` up to ``at`` as a name of its own, or '' when that leaves a bracket open or too little."""
    cut = _drop_dangling(name[:at])
    return cut if cut.count("(") == cut.count(")") and len(cut) >= 12 else ""


def _word_cut(name: str, room: int) -> str:
    """``name`` cut at the last word that fits. The last resort: it may stop part way through a phrase."""
    cut = name[: room + 1]
    cut = cut[: cut.rfind(" ")] if " " in cut[12:] else name[:room]
    if cut.count("(") != cut.count(")"):
        cut = cut[: cut.rfind("(")]
    words = cut.rstrip(" ,-/(&").split(" ")
    # '... and Response and Human' stopped one word into a new phrase: end before that 'and'.
    if len(words) > 4 and words[-2].lower() in ("and", "&"):
        words = words[:-2]
    return _drop_dangling(" ".join(words))


def _cut_name(name: str, room: int) -> str:
    """The longest opening of ``name`` that fits ``room`` and still reads as a name.

    Ends before a joining word, at a comma that does not sit inside a list, or before an 'and'
    that starts a further part of a long name. It may end before 'of', but not part way through
    an 'of X of Y' chain ('Project of Government' for 'Project of Government of India').
    """
    if len(name) <= room:
        return name
    clean_cuts: list[str] = []
    for word in _CLEAN_CUTS:
        clean_cuts.extend(_opening(name, at) for at in _positions(name, word, room))
    for joiner in (" and ", " & "):
        for at in _positions(name, joiner, room):
            before, after = name[:at].split(), name[at + len(joiner) :].split()
            # Half of a pair ('Iron' of 'Iron and Steel sector') is not a name.
            if len(before) >= 3 and len(after) >= 4 and before[-1].lower() not in _SMALL_WORDS:
                clean_cuts.append(_opening(name, at))
    for at in _positions(name, ", ", room):
        if "and" not in name[at + 2 :].lower().split()[:3]:
            clean_cuts.append(_opening(name, at))
    for at in _positions(name, " of ", room):
        if name[:at].split()[-2:-1] != ["of"]:
            clean_cuts.append(_opening(name, at))
    best = max(clean_cuts, key=len, default="")
    return best if len(best) >= 18 else _word_cut(name, room)


def _without_short_form(name: str) -> str:
    """The name without a trailing '(ABBR)' or '(erstwhile ...)', which a headline can leave out."""
    match = _TRAILING_BRACKET.match(name)
    if not match:
        return ""
    inner = match.group(2).strip()
    if _is_abbreviation(inner) or inner.lower().startswith(("erstwhile", "formerly", "earlier")):
        return match.group(1).strip()
    return ""


def _whole(n: float) -> bool:
    """Whether a figure is shown in whole crore: ₹100 crore and more, unless it ends in exactly .5."""
    return n >= 100 and abs(math.fmod(n, 1.0) - 0.5) > 1e-6


def _amount(crore: Any, exact: bool = False) -> str:
    """A crore figure in the unit a reader expects: ₹15.7 crore, ₹1,378 crore, ₹50 lakh."""
    n = num(crore)
    if n is None or n < 0:
        return ""
    if n < 1:
        return fmt_inr(n * 1e7)
    return f"₹{fmt_num(n, 0 if _whole(n) and not exact else 2)} crore"


def _figures(budget: float | None, other: float | None, gap: float | None) -> tuple[str, str, str]:
    """The budget, the figure set against it and the gap, written so that they still add up.

    Figures of ₹100 crore and more are rounded to whole crore. When that rounding would make
    the three disagree by a crore, all three are given to the paise the budget prints.
    """
    exact = False
    if budget is not None and other is not None and gap is not None:
        shown = [float(round(n)) if _whole(n) else n for n in (budget, other, gap)]
        exact = abs(shown[0] - shown[1] - shown[2]) > 0.5
    return _amount(budget, exact), _amount(other, exact), _amount(gap, exact)


def _pct(value: Any) -> str:
    """One decimal, rounded the way the engine rounds it for the stored title."""
    n = num(value)
    if n is None or n <= 0:
        return ""
    if n >= 100:
        return "100%"
    text = f"{n:.1f}"
    return f"{text.removesuffix('.0')}%"


def _share(part: float | None, whole: float | None, stored: Any) -> float | None:
    """The shortfall as a share of the budget, from the two amounts when both are present."""
    if part is not None and whole:
        return part / whole * 100.0
    return num(stored)


def _fill(template: str, subject: str) -> str:
    return template.replace("{s}", subject)


def _headline(
    templates: list[str], name: str, contexts: list[str], terse: list[str], needs_context: bool
) -> tuple[str, bool, str]:
    """Pick the richest headline that fits. Returns (headline, the name was cut, the context used).

    ``templates`` run from most to least detailed and carry a ``{s}`` slot for the subject.
    """
    rich = templates[0]
    for context in contexts:
        text = _fill(rich, name + context)
        if len(text) <= _HEAD_TARGET:
            return text, False, context
    if needs_context:
        # A name that says little alone gives up the year, and then some length, to keep its place.
        steps = [(_HEAD_SOFT, contexts), (_HEAD_LIMIT, contexts), (_HEAD_LIMIT, terse)]
        if len(name) <= 30:
            steps += [(_HEAD_LAST_RESORT, contexts), (_HEAD_LAST_RESORT, terse)]
        for limit, options in steps:
            for template in templates:
                for context in options:
                    text = _fill(template, name + context)
                    if len(text) <= limit:
                        return text, False, context
    elif len(name) <= _SHORT_NAME and len(name.split()) <= 3:
        for context in contexts:
            text = _fill(rich, name + context)
            if len(text) <= _HEAD_SOFT:
                return text, False, context
    base = _without_short_form(name)
    forms = [name, base] if base else [name]
    for limit in (_HEAD_SOFT, _HEAD_LIMIT):
        for template in templates:
            for form in forms:
                text = _fill(template, form)
                if len(text) <= limit:
                    return text, False, ""
    # The name has to be shortened. A leaner template wins only when it keeps clearly more of the name.
    best_name, best_template = "", ""
    for template in templates:
        cut = _cut_name(base or name, _HEAD_LIMIT - len(_fill(template, "")))
        if cut and (not best_name or len(cut) >= len(best_name) + 12):
            best_name, best_template = cut, template
    if best_name:
        return _fill(best_template, best_name), True, ""
    template = templates[-1]
    room = max(_HEAD_LIMIT - len(_fill(template, "")), 12)
    return _fill(template, name[:room].rstrip(" ,-/(&")), True, ""


def _budget_phrase(department: str, territory: str) -> str:
    """'the Ministry of Power budget', 'the budget for Police'."""
    if not department:
        return ""
    if territory and department.startswith("Ministry of Home Affairs"):
        return f"the Ministry of Home Affairs budget for {_the_territory(territory)}"
    if re.match(r"(Ministry|Department)\b", department) or department.endswith("Department"):
        return f"the {department} budget"
    return f"the budget for {_with_article(department)}"


def _place_sentences(
    name: str, parent: str, department: str, territory: str, context: str, cut: bool, truncated: bool
) -> tuple[list[str], list[str]]:
    """Sentences saying which budget line this is: (those that give a shortened name in full, the rest)."""
    budget = _budget_phrase(department, territory)
    named: list[str] = []
    if cut and not truncated:
        if parent and budget:
            named.append(f"The line is {name}, listed under {parent} in {budget}.")
        if budget:
            named.append(f"The line is {name}, in {budget}.")
        named.append(f"The line is {name}.")
    # What the headline already says is not said again.
    has_parent = bool(parent) and context == f" under {parent}"
    has_department = bool(department) and context in (f" under {_with_article(department)}", f" under {department}")
    plain: list[str] = []
    if parent and budget and not has_parent and not has_department:
        plain.append(f"The line is listed under {parent} in {budget}.")
    if budget and not has_department:
        plain.append(f"The line is in {budget}.")
    if parent and not has_parent:
        plain.append(f"The line is listed under {parent}.")
    return named, plain


def _join(*sentences: str) -> str:
    return " ".join(s for s in sentences if s)


def _brief(leads: list[str], must: str, optional: str, named: list[str], plain: list[str], cut: bool) -> str:
    """Join the figures sentence with as much of the rest as fits.

    ``leads`` run from the full figures sentence to leaner ones. A caveat in ``must`` is always
    kept. A name the headline shortened is given in full when any lead leaves room for it.
    """
    for limit in (_BRIEF_TARGET, _BRIEF_LIMIT):
        for lead in leads:
            for place in named:
                text = _join(lead, must, place)
                if len(text) <= limit:
                    return text
    note = _SHORTENED_NOTE if cut else ""
    extras = [optional, ""] if optional else [""]
    for limit in (_BRIEF_TARGET, _BRIEF_LIMIT):
        for place in plain:
            for extra in extras:
                text = _join(leads[0], must, extra, note, place)
                if len(text) <= limit:
                    return text
    for extra in extras:
        text = _join(leads[0], must, extra, note)
        if len(text) <= _BRIEF_LIMIT:
            return text
    return _join(leads[0], must)


def _is_revised_only(finding: dict[str, Any], m: dict[str, Any]) -> bool:
    compare = clean(m.get("compare")).lower()
    rule = clean(finding.get("rule_triggered")).upper()
    if compare.startswith("revised") or rule.startswith("UB1-RE-"):
        return True
    if compare.startswith("actual") or rule.startswith("UB1-ACTUAL-"):
        return False
    return "actual" not in m and "unspent" not in m and "revised_cut_pct" in m


def _positive(value: Any) -> float | None:
    n = num(value)
    return n if n is not None and n > 0 else None


def _actual_texts(m: dict[str, Any], year: str) -> tuple[list[str], list[str], str] | None:
    """(headline templates, figures sentences from full to lean, caveat), or None for an inconsistent record."""
    budget = _positive(m.get("budget_estimate"))
    actual = num(m.get("actual"))
    revised = num(m.get("revised_estimate"))
    unspent = num(m.get("unspent"))
    if budget is not None and actual is not None:
        unspent = round(budget - actual, 2)
    pct = _share(unspent, budget, m.get("unspent_pct"))
    if (actual is not None and actual < 0) or (pct is not None and pct <= 0):
        return None  # spending at or above the budget, or a negative figure: not a shortfall
    printed = m.get("actual_printed") is not False or bool(actual)
    in_year = f" in {year}" if year else ""
    for_year = f" for {year}" if year else ""
    nothing_spent = (not printed) or actual == 0
    b_text, a_text, u_text = _figures(budget, None if nothing_spent else actual, None if nothing_spent else unspent)
    b_round, a_round = _amount(budget), _amount(actual)

    if nothing_spent:
        verb = "recorded" if printed else "shown"
        templates = [f"No spending {verb} on {{s}}{in_year}", f"No spending {verb} on {{s}}"]
        if b_round:
            templates[:0] = [
                f"No spending {verb} on {{s}}{in_year} against a {b_round} budget",
                f"No spending {verb} on {{s}} against a {b_round} budget",
            ]
    elif pct is not None and pct >= 99.95 and a_round and b_round:
        # The percentage would round to 100% although something was spent, so give the amounts.
        templates = [
            f"Spending on {{s}} was {a_round} against a {b_round} budget{in_year}",
            f"Spending on {{s}} was {a_round} against a {b_round} budget",
        ]
    elif _pct(pct):
        templates = [
            f"Spending on {{s}} was {_pct(pct)} below budget{in_year}",
            f"Spending on {{s}} was {_pct(pct)} below budget",
        ]
    else:
        templates = [f"Spending on {{s}} was below budget{in_year}", "Spending on {s} was below budget"]

    # A revised estimate of exactly zero may be the printed nil, and one that reads the same as the budget is no news.
    r_text = _amount(revised) if revised and budget is not None and abs(revised - budget) >= 0.005 else ""
    if r_text == _amount(budget):
        r_text = ""
    opening = f"Union Budget papers show {b_text} was budgeted{for_year}" if b_text else ""
    if not opening:
        leads = [f"Union Budget papers show spending{for_year} was below the budget estimate."]
    elif nothing_spent:
        tail = "no actual spending is printed for this line" if not printed else "the actual spending printed is nil"
        leads = [f"{opening}, later revised to {r_text}, and {tail}."] if r_text else []
        leads.append(f"{opening} and {tail}.")
    elif a_text:
        spent = f"{a_text} was spent"
        gap = f", {u_text} below the budget" if u_text else ""
        leads = []
        if r_text:
            leads += [f"{opening}, revised to {r_text}, and {spent}{gap}.", f"{opening}, revised to {r_text}, and {spent}."]
        leads += [f"{opening} and {spent}{gap}.", f"{opening} and {spent}."]
    else:
        leads = [f"{opening}."]
    caveat = _NAME_MATCH_NOTE if clean(m.get("link")).lower() == "name" else ""
    return list(dict.fromkeys(templates)), list(dict.fromkeys(leads)), caveat


def _revised_texts(m: dict[str, Any], year: str) -> tuple[list[str], list[str], str] | None:
    """(headline templates, figures sentences from full to lean, caveat), or None for an inconsistent record."""
    budget = _positive(m.get("budget_estimate"))
    revised = num(m.get("revised_estimate"))
    cut = num(m.get("revised_cut"))
    if budget is not None and revised is not None:
        cut = round(budget - revised, 2)
    pct = _share(cut, budget, m.get("revised_cut_pct"))
    if (revised is not None and revised < 0) or (pct is not None and pct <= 0):
        return None  # a revised estimate at or above the budget, or a negative figure: not a cut
    printed = m.get("revised_printed") is not False or bool(revised)
    with_year = f"{year} budget" if year else "Budget"
    for_year = f" for {year}" if year else ""
    nil = (not printed) or revised == 0
    b_text, r_text, c_text = _figures(budget, None if nil else revised, None if nil else cut)
    b_round, r_round = _amount(budget), _amount(revised)

    if not printed:
        no_estimate = f"No {year} revised estimate shown for {{s}}" if year else "No revised estimate shown for {s}"
        templates = [no_estimate, "No revised estimate shown for {s}"]
        if b_round:
            templates.insert(0, f"{no_estimate} against a {b_round} budget")
    elif revised == 0 and b_round:
        templates = [
            f"{with_year} for {{s}} was revised from {b_round} to nil",
            f"{with_year} for {{s}} was revised to nil",
            "Budget for {s} was revised to nil",
        ]
    elif pct is not None and pct >= 99.95 and r_round and b_round:
        templates = [
            f"{with_year} for {{s}} was revised from {b_round} to {r_round}",
            f"{with_year} for {{s}} was revised to {r_round}",
            f"Budget for {{s}} was revised to {r_round}",
        ]
    elif _pct(pct):
        templates = [f"{with_year} for {{s}} was revised down by {_pct(pct)}", f"Budget for {{s}} was revised down by {_pct(pct)}"]
    else:
        templates = [f"{with_year} for {{s}} was revised down", "Budget for {s} was revised down"]

    opening = f"Union Budget papers show {b_text} was budgeted{for_year}" if b_text else ""
    if not opening:
        leads = [f"Union Budget papers show the revised estimate{for_year} was below the budget estimate."]
    elif not printed:
        leads = [f"{opening} and no revised estimate is printed for this line."]
    elif revised == 0:
        leads = [f"{opening} and the revised estimate printed is nil."]
    elif r_text:
        gap = f", {c_text} lower" if c_text else ""
        leads = [f"{opening} and the estimate was later revised to {r_text}{gap}.", f"{opening} and the estimate was later revised to {r_text}."]
    else:
        leads = [f"{opening}."]
    return list(dict.fromkeys(templates)), list(dict.fromkeys(leads)), ""


def render(finding: dict[str, Any]) -> tuple[str, str]:
    """Return (headline, brief) for one UB1 finding, or ("", "") when no budget line can be named."""
    if not isinstance(finding, dict):
        return "", ""
    m = metrics(finding)
    parts, department, truncated = _split_entity(finding)
    if not parts:
        return "", ""
    if all(_positive(m.get(key)) is None for key in ("budget_estimate", "unspent_pct", "revised_cut_pct")):
        return "", ""  # no figure at all: nothing specific can be said
    year = fiscal_year(finding)
    if not re.fullmatch(r"\d{4}-\d{2,4}", year):
        year = ""
    revised_only = _is_revised_only(finding, m)
    texts = _revised_texts(m, year) if revised_only else _actual_texts(m, year)
    if texts is None:
        return "", ""
    templates, leads, caveat = texts

    name = parts[-1]
    parent = parts[-2] if len(parts) > 1 else ""
    territory = _territory(department)
    if territory and name.lower().endswith(f" - {territory.lower()}"):
        # 'Tribal Area Component - Ladakh' in the Ladakh demand: the place is said once, as 'in Ladakh'.
        name = name[: -len(territory) - 3].rstrip() or name
    if parent and _is_place_only(name) and not territory:
        # 'Helicopter Services > Jammu and Kashmir and Himachal Pradesh' is not spending on the two places.
        name, parent = f"{parent} - {name}", ""
    contexts, terse = _context_options(name, parent, department, territory)
    needs_context = bool(territory) or _is_generic(name)
    headline, cut, context = _headline(templates, name, contexts, terse, needs_context)
    named, plain = _place_sentences(name, parent, department, territory, context, cut, truncated)
    brief = _brief(leads, caveat, _NO_ACTUALS_NOTE if revised_only else "", named, plain, cut)
    return headline, brief
