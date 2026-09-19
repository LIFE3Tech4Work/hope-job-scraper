import html
import json
import re
from typing import Any, Optional, Tuple


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_hourly(value: Any, period: str) -> Optional[float]:
    try:
        amount = float(str(value).replace(",", "").replace("$", "").strip())
    except Exception:
        return None
    p = (period or "").lower()
    if any(x in p for x in ["annual", "year", "yr"]):
        return round(amount / 2080, 2)
    if "month" in p:
        return round((amount * 12) / 2080, 2)
    if "week" in p:
        return round(amount / 40, 2)
    if "day" in p:
        return round(amount / 8, 2)
    if any(x in p for x in ["hour", "hr"]):
        return round(amount, 2)
    return None


def _period_from_context(context: str, amount: Optional[float] = None) -> str:
    low = (context or "").lower()
    if re.search(r"(?:per\s+hour|hourly|/\s*hr\.?|/\s*hour)", low):
        return "Hourly"
    if re.search(r"(?:per\s+year|annually|annual|yearly|/\s*yr\.?)", low):
        return "Annually"
    if re.search(r"(?:per\s+week|weekly|/\s*wk\.?)", low):
        return "Weekly"
    if re.search(r"(?:per\s+month|monthly|/\s*mo\.?)", low):
        return "Monthly"
    if amount is not None and amount >= 10000:
        return "Annually"
    return ""


def extract_compensation(text: str) -> Tuple[Optional[float], Optional[float], str, Optional[float], Optional[float], str]:
    text = clean_text(text)
    patterns = [
        re.compile(r"(?:USD\s*)?\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)\s*(?:-|–|—|to)\s*(?:USD\s*)?\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)", re.I),
        re.compile(r"(?:Min(?:imum)?\s*)?(?:USD\s*)?\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)\s*/\s*(Hr|Hour|Yr|Year|Week|Month)\.?\s*(?:Max(?:imum)?\s*)?(?:USD\s*)?\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)\s*/\s*(Hr|Hour|Yr|Year|Week|Month)\.?,?", re.I),
        re.compile(r"(?:Compensation|Salary|Pay|Rate)\s*:?\s*(?:USD\s*)?\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)(?:\s*(?:per\s+|/\s*)?(hour|hr|year|yr|week|month|annually|hourly))?", re.I),
        re.compile(r"(?:USD\s*)?\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)\s*(?:per\s+|/\s*)?(hour|hr|year|yr|week|month|annually|hourly)", re.I),
    ]
    for idx, pat in enumerate(patterns):
        m = pat.search(text)
        if not m:
            continue
        if idx == 0:
            lo = float(m.group(1).replace(",", "")); hi = float(m.group(2).replace(",", ""))
            around = text[max(0, m.start()-160):m.end()+160]
            period = _period_from_context(around, lo)
        elif idx == 1:
            lo = float(m.group(1).replace(",", "")); hi = float(m.group(3).replace(",", ""))
            unit = (m.group(2) or "").lower()
            if unit in {"hr", "hour"}:
                period = "Hourly"
            elif unit in {"yr", "year"}:
                period = "Annually"
            elif unit == "week":
                period = "Weekly"
            elif unit == "month":
                period = "Monthly"
            else:
                period = _period_from_context(m.group(2) + " " + m.group(4), lo)
            around = text[max(0, m.start()-120):m.end()+120]
        else:
            lo = hi = float(m.group(1).replace(",", ""))
            period = _period_from_context(" ".join(g or "" for g in m.groups()[1:]) + " " + text[max(0,m.start()-80):m.end()+80], lo)
            around = text[max(0, m.start()-120):m.end()+120]
        # Sanity guard against IDs, timestamps, requisition numbers, etc.
        if lo <= 0 or hi <= 0 or lo > 2_000_000 or hi > 2_000_000:
            continue
        if period == "Hourly" and (lo > 1000 or hi > 1000):
            continue
        return lo, hi, period, normalize_hourly(lo, period), normalize_hourly(hi, period), clean_text(around)
    return None, None, "", None, None, ""


def extract_hours(text: str):
    text = clean_text(text)
    m = re.search(r"at\s+least\s+(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)", text, re.I)
    if m:
        return float(m.group(1)), None
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)\s*(?:per\s+week|weekly)?", text, re.I)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)\s*(?:per\s+week|weekly)", text, re.I)
    if m:
        v = float(m.group(1)); return v, v
    return None, None


def extract_education(text: str) -> str:
    low = clean_text(text).lower()
    result = []
    mapping = [
        (["high school diploma", "high school diploma/ged", "ged"], "High School / GED"),
        (["associate's degree", "associate degree", "associates degree"], "Associate Degree"),
        (["bachelor's degree", "bachelors degree", "bachelor degree", "baccalaureate"], "Bachelor's Degree"),
        (["master's degree", "masters degree", "master degree"], "Master's Degree"),
        (["juris doctor", "j.d.", "doctorate", "doctoral degree", "ph.d", "phd"], "Doctorate"),
    ]
    for terms, label in mapping:
        if any(t in low for t in terms):
            result.append(label)
    return "; ".join(dict.fromkeys(result))


def extract_required_certs(text: str) -> str:
    t = clean_text(text)
    result = []
    patterns = [
        (r"(?:F-?02|F02)(?:\s+certification)?\s*[-:]?\s*REQUIRED", "F02 Certification"),
        (r"Security\s+license\s*[-:]?\s*REQUIRED", "Security License"),
        (r"Driver'?s\s+License(?:[^.;]{0,80})Required", "Driver's License"),
        (r"Commercial\s+Driver'?s\s+License(?:[^.;]{0,80})Required", "Commercial Driver's License"),
        (r"\bCDL(?:\s*-?\s*Class\s*[ABC])?(?:[^.;]{0,80})Required", None),
        (r"\bPMP\b(?:[^.;]{0,80})Required", "PMP"),
        (r"\bCISSP\b(?:[^.;]{0,80})Required", "CISSP"),
        (r"\bCISM\b(?:[^.;]{0,80})Required", "CISM"),
        (r"\bGIAC\b(?:[^.;]{0,80})Required", "GIAC"),
        (r"\bS-?95\b(?:[^.;]{0,80})Required", "NYC S-95"),
        (r"\bS-?12\b(?:[^.;]{0,80})Required", "NYC S-12"),
        (r"(?:fingerprint|fingerprinting)\s+clearance", "Fingerprint Clearance"),
    ]
    for pat, label in patterns:
        for m in re.finditer(pat, t, re.I):
            result.append(label or clean_text(re.split(r"required", m.group(0), flags=re.I)[0]))
    # Generic short lines containing required + license/certification.
    for sentence in re.split(r"(?<=[.;])\s+|\n+", t):
        low = sentence.lower()
        if "required" in low and any(k in low for k in ["license", "certification", "certificate", "clearance"]):
            candidate = clean_text(re.sub(r"\b(required|must|required\.)\b.*$", "", sentence, flags=re.I)).strip(" :-")
            if 2 < len(candidate) <= 120:
                result.append(candidate)
    return "; ".join(dict.fromkeys(x for x in result if x))


def category_from_title(title: str) -> str:
    t = f" {clean_text(title).lower()} "
    if any(x in t for x in [" engineer", "engineering"]): return "Engineering"
    if any(x in t for x in ["information technology", " it ", "developer", "systems analyst", "cyber", "software"]): return "Information Technology"
    if any(x in t for x in ["maintenance", "mechanic", "technician", "building operator", "facilities", "facility"]): return "Building Operations / Maintenance"
    if any(x in t for x in ["social worker", "case manager", "clinical", "residential specialist", "peer specialist"]): return "Social Services"
    if any(x in t for x in ["property manager", "property management"]): return "Property Management"
    if any(x in t for x in ["attorney", "paralegal", "counsel", "legal"]): return "Legal"
    if any(x in t for x in ["teacher", "instructor", "facilitator", "education"]): return "Education"
    if any(x in t for x in ["security guard", "security supervisor"]): return "Security"
    return "Unclassified"


def job_term(text: str) -> str:
    low = f" {clean_text(text).lower()} "
    if any(x in low for x in ["temporary", " temp ", "seasonal", "internship", "intern ", "co-op", "coop"]): return "Temporary"
    if any(x in low for x in ["contractor", "contract role", "contract position", "fixed term"]): return "Contract"
    if any(x in low for x in ["full-time", "full time", "part-time", "part time", "regular"]): return "Permanent"
    return ""


def vaccine_requirement(text: str) -> str:
    low = clean_text(text).lower()
    if any(x in low for x in ["vaccination required", "vaccine required", "must be vaccinated", "proof of vaccination"]): return "Yes"
    if any(x in low for x in ["vaccination not required", "vaccine not required"]): return "No"
    return "Not Stated"


def json_text(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return str(obj)
