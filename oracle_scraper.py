import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import pandas as pd
from playwright.sync_api import BrowserContext, Page, sync_playwright


OUTPUT_COLUMNS = [
    "Job Opportunity Name",
    "Employer Name",
    "Job Opportunity Stage",
    "Close Date",
    "Source",
    "Min Hours per Week",
    "Max Hours per Week",
    "Job Term",
    "Min Wage Normalized Hourly",
    "Max Wage Normalized Hourly",
    "Required Education",
    "Required Certifications & Licenses",
    "Category",
    "Vaccine Requirement",
    "Location",
    "Posting Date",
    "Original Min Compensation",
    "Original Max Compensation",
    "Compensation Period",
    "Compensation Currency",
    "Compensation Evidence",
    "Compensation Source",
    "Job Type Raw",
    "Job ID",
    "Job Description",
    "Responsibilities",
    "Qualifications",
    "Job Detail URL",
    "Source URL",
    "Date Scraped",
]

COMP_KEYWORDS = re.compile(
    r"(?:^|[._\-\s])(salary|compensation|pay|wage|remuneration|base[_ .-]?salary|minimum[_ .-]?salary|maximum[_ .-]?salary|pay[_ .-]?range|pay[_ .-]?rate|salary[_ .-]?range)(?:$|[._\-\s])",
    re.I,
)

COMP_FIELD_RE = re.compile(
    r"salary|compensation|wage|pay(?:rate|range|amount|minimum|maximum|min|max)?|base[_ .-]?salary",
    re.I,
)

ID_FIELD_RE = re.compile(
    r"(?:^|[._\-])(id|headerid|requisitionid|organizationid|geographyid|profileid|pageid|elementnumber|objectver|code)(?:$|[._\-])",
    re.I,
)

MONEY_RANGE_RE = re.compile(
    r"(?:USD\s*)?\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)\s*"
    r"(?:-|–|—|to|through)\s*"
    r"(?:USD\s*)?\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)",
    re.I,
)

MONEY_SINGLE_RE = re.compile(r"(?:USD\s*)?\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)", re.I)


@dataclass
class CompensationResult:
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    period: str = ""
    currency: str = ""
    evidence: str = ""
    source: str = ""


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    value = str(value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = (
        value.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&#39;", "'")
        .replace("&quot;", '"')
    )
    return re.sub(r"\s+", " ", value).strip()


def get_query_param(url: str, name: str) -> Optional[str]:
    values = parse_qs(urlparse(url).query).get(name)
    return values[0] if values else None


def first_nonempty(data: Dict[str, Any], names: List[str]) -> Any:
    for name in names:
        value = data.get(name)
        if value not in (None, "", [], {}):
            return value
    return ""


def flatten_json(obj: Any, path: str = "root") -> List[Tuple[str, Any]]:
    rows: List[Tuple[str, Any]] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            child = f"{path}.{key}"
            rows.append((child, value))
            rows.extend(flatten_json(value, child))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            child = f"{path}[{i}]"
            rows.append((child, value))
            rows.extend(flatten_json(value, child))
    return rows


def detect_period(text: str) -> str:
    lower = clean_text(text).lower()
    if any(x in lower for x in ["per hour", "hourly", "/hour", "/hr", "unittext\":\"hour", 'unittext":"hour']):
        return "Hourly"
    if any(x in lower for x in ["per year", "annual", "annually", "yearly", "per annum", "/year", "/yr", "unittext\":\"year", 'unittext":"year']):
        return "Annually"
    if any(x in lower for x in ["per week", "weekly", "/week"]):
        return "Weekly"
    if any(x in lower for x in ["per month", "monthly", "/month"]):
        return "Monthly"
    if any(x in lower for x in ["per day", "daily", "/day"]):
        return "Daily"
    return ""


def normalize_hourly(amount: Optional[float], period: str) -> Optional[float]:
    if amount is None:
        return None
    p = (period or "").lower()
    if p == "hourly":
        return round(float(amount), 2)
    if p == "annually":
        return round(float(amount) / 2080.0, 2)
    if p == "monthly":
        return round(float(amount) * 12.0 / 2080.0, 2)
    if p == "weekly":
        return round(float(amount) / 40.0, 2)
    if p == "daily":
        return round(float(amount) / 8.0, 2)
    return None


def infer_period(minimum: Optional[float], maximum: Optional[float], context: str) -> str:
    explicit = detect_period(context)
    if explicit:
        return explicit
    vals = [v for v in [minimum, maximum] if v is not None]
    if not vals:
        return ""
    if min(vals) >= 10000:
        return "Annually"
    if max(vals) <= 500:
        return "Hourly"
    return ""


def _float(value: Any) -> Optional[float]:
    try:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, str):
            value = value.replace(",", "").replace("$", "").strip()
            if not re.fullmatch(r"-?\d+(?:\.\d+)?", value):
                return None
        return float(value)
    except Exception:
        return None


def _is_comp_path(path: str) -> bool:
    lower = path.lower()
    tokens = [t for t in re.split(r"[.\[\]_/\-]+", lower) if t]
    salary_tokens = (
        "salary", "minimumsalary", "maximumsalary", "minsalary", "maxsalary",
        "salaryminimum", "salarymaximum", "salarymin", "salarymax",
        "salaryamount", "salaryrange", "salaryfrequency", "salarybasis",
        "basesalary", "basepay", "compensation", "compensationminimum",
        "compensationmaximum", "compensationmin", "compensationmax",
        "pay", "payrange", "payrate", "payamount", "minimumPay",
        "maximumPay", "wage", "wagerange", "wagerate", "remuneration",
    )
    salary_tokens = tuple(x.lower() for x in salary_tokens)
    return any(t in salary_tokens or t.startswith("salary") or t.startswith("compensation") for t in tokens)


def _looks_like_id_path(path: str) -> bool:
    lower = path.lower()
    if re.search(r"(?:^|[.\[\]_/\-])(id|headerid|requisitionid|organizationid|geographyid|profileid|pageid|elementnumber|objectver)(?:$|[.\[\]_/\-])", lower):
        return True
    return lower.endswith("id") or ".id" in lower


def _valid_comp_amount(value: Optional[float], period: str = "") -> bool:
    if value is None:
        return False
    value = float(value)
    if value <= 0:
        return False
    p = (period or "").lower()
    if p == "hourly":
        return 5 <= value <= 1000
    if p == "daily":
        return 40 <= value <= 10000
    if p == "weekly":
        return 100 <= value <= 25000
    if p == "monthly":
        return 500 <= value <= 100000
    if p == "annually":
        return 10000 <= value <= 2000000
    # Unknown frequency: allow plausible hourly or annual amounts only.
    return (5 <= value <= 1000) or (10000 <= value <= 2000000)


def _finalize_comp(result: CompensationResult) -> Optional[CompensationResult]:
    if result.minimum is None and result.maximum is None:
        return None
    if result.minimum is None:
        result.minimum = result.maximum
    if result.maximum is None:
        result.maximum = result.minimum
    if result.minimum is not None and result.maximum is not None and result.minimum > result.maximum:
        result.minimum, result.maximum = result.maximum, result.minimum
    if not result.period:
        result.period = infer_period(result.minimum, result.maximum, result.evidence)
    if not (_valid_comp_amount(result.minimum, result.period) and _valid_comp_amount(result.maximum, result.period)):
        return None
    # Prevent internal numeric identifiers from masquerading as compensation.
    if result.maximum and result.maximum > 2000000 and result.period.lower() == "annually":
        return None
    return result


def compensation_candidates_from_json(obj: Any, label: str) -> List[Tuple[int, CompensationResult]]:
    flat = flatten_json(obj)
    candidates: List[Tuple[int, CompensationResult]] = []

    # Explicit currency strings are the strongest signal.
    for path, value in flat:
        if not isinstance(value, str):
            continue
        text = clean_text(value)
        context = f"{path} {text}"
        m = MONEY_RANGE_RE.search(text)
        if m:
            minimum = float(m.group(1).replace(",", ""))
            maximum = float(m.group(2).replace(",", ""))
            period = infer_period(minimum, maximum, context)
            result = _finalize_comp(CompensationResult(
                minimum=minimum, maximum=maximum, period=period, currency="USD",
                evidence=text[:500], source=f"{label}:{path}"
            ))
            if result:
                candidates.append((100, result))

    min_candidates: List[Tuple[str, float]] = []
    max_candidates: List[Tuple[str, float]] = []
    generic_candidates: List[Tuple[str, float]] = []
    period_candidates: List[str] = []
    currency_candidates: List[str] = []

    for path, value in flat:
        lower_path = path.lower()
        scalar = _float(value)
        comp_path = _is_comp_path(path)

        if isinstance(value, str):
            if comp_path or "unittext" in lower_path or "frequency" in lower_path or "salarybasis" in lower_path:
                p = detect_period(f"{lower_path} {value}")
                if p:
                    period_candidates.append(p)
            if "currency" in lower_path and len(value.strip()) <= 10:
                currency_candidates.append(value.strip().upper())

        if scalar is None or not comp_path or _looks_like_id_path(path):
            continue

        # Ignore obvious Oracle identifiers even if their parent object happens to mention pay.
        if scalar >= 1000000000:
            continue

        leaf = re.split(r"[.\[\]_/\-]+", lower_path)[-1]
        if re.search(r"min(?:imum)?|low|from", leaf):
            min_candidates.append((path, scalar))
        elif re.search(r"max(?:imum)?|high|to", leaf):
            max_candidates.append((path, scalar))
        elif re.search(r"value|amount|salary|pay|wage|rate", leaf):
            generic_candidates.append((path, scalar))

    minimum = min_candidates[0][1] if min_candidates else None
    maximum = max_candidates[0][1] if max_candidates else None
    evidence_parts: List[str] = []
    if min_candidates:
        evidence_parts.append(f"{min_candidates[0][0]}={minimum}")
    if max_candidates:
        evidence_parts.append(f"{max_candidates[0][0]}={maximum}")

    if minimum is None and maximum is None and generic_candidates:
        plausible = [(p, v) for p, v in generic_candidates if _valid_comp_amount(v)]
        if len(plausible) >= 2:
            vals = [v for _, v in plausible]
            minimum, maximum = min(vals), max(vals)
            evidence_parts = [f"{p}={v}" for p, v in plausible[:4]]
        elif len(plausible) == 1:
            minimum = maximum = plausible[0][1]
            evidence_parts = [f"{plausible[0][0]}={plausible[0][1]}"]

    if minimum is not None or maximum is not None:
        period = period_candidates[0] if period_candidates else infer_period(minimum, maximum, " ".join(evidence_parts))
        result = _finalize_comp(CompensationResult(
            minimum=minimum, maximum=maximum, period=period,
            currency=currency_candidates[0] if currency_candidates else "USD",
            evidence="; ".join(evidence_parts)[:500], source=label,
        ))
        if result:
            candidates.append((95 if min_candidates or max_candidates else 80, result))

    return candidates


def compensation_candidates_from_text(text: str, label: str) -> List[Tuple[int, CompensationResult]]:
    cleaned = clean_text(text)
    if not cleaned:
        return []
    candidates: List[Tuple[int, CompensationResult]] = []
    lower = cleaned.lower()
    windows: List[str] = []
    for term in ["salary", "compensation", "pay range", "base pay", "wage", "hourly rate", "minimum salary", "maximum salary", "pay"]:
        start = 0
        while True:
            pos = lower.find(term, start)
            if pos < 0:
                break
            windows.append(cleaned[max(0, pos - 250): pos + 700])
            start = pos + len(term)
    # Also scan the full text for explicit dollar ranges.
    windows.append(cleaned)

    for window in windows:
        m = MONEY_RANGE_RE.search(window)
        if m:
            minimum = float(m.group(1).replace(",", ""))
            maximum = float(m.group(2).replace(",", ""))
            result = _finalize_comp(CompensationResult(
                minimum=minimum, maximum=maximum, period=infer_period(minimum, maximum, window),
                currency="USD", evidence=clean_text(window)[:500], source=label,
            ))
            if result:
                candidates.append((100 if "$" in window else 90, result))
                continue

        # Single explicit dollar amount is valid only in a compensation-related window.
        if any(term in window.lower() for term in ["salary", "compensation", "pay", "wage", "rate"]):
            m1 = MONEY_SINGLE_RE.search(window)
            if m1:
                amount = float(m1.group(1).replace(",", ""))
                result = _finalize_comp(CompensationResult(
                    minimum=amount, maximum=amount, period=infer_period(amount, amount, window),
                    currency="USD", evidence=clean_text(window)[:500], source=label,
                ))
                if result:
                    candidates.append((85, result))

    return candidates


def choose_compensation(sources: List[Tuple[str, Any]]) -> CompensationResult:
    ranked: List[Tuple[int, CompensationResult]] = []
    for label, source in sources:
        if isinstance(source, (dict, list)):
            ranked.extend(compensation_candidates_from_json(source, label))
        elif isinstance(source, str):
            ranked.extend(compensation_candidates_from_text(source, label))

    if not ranked:
        return CompensationResult()
    ranked.sort(key=lambda x: x[0], reverse=True)
    return ranked[0][1]


def extract_hours(detail: Dict[str, Any], page_text: str) -> Tuple[Optional[float], Optional[float]]:
    raw = first_nonempty(detail, ["WorkHours", "JobSchedule", "Schedule"])
    text = f"{raw} {page_text}"
    range_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)\s*(?:per week|weekly)?", text, re.I)
    if range_match:
        return float(range_match.group(1)), float(range_match.group(2))
    at_least = re.search(r"(?:at least|minimum|min\.?)[^0-9]{0,15}(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)", text, re.I)
    if at_least:
        return float(at_least.group(1)), None
    single = re.search(r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)\s*(?:per week|weekly)", text, re.I)
    if single:
        value = float(single.group(1))
        return value, value
    return None, None


def extract_education(text: str) -> str:
    lower = clean_text(text).lower()
    found: List[str] = []
    rules = [
        (["high school diploma", "high school diploma/ged", "ged"], "High School / GED"),
        (["associate's degree", "associate degree", "associates degree"], "Associate Degree"),
        (["bachelor's degree", "bachelor degree", "bachelors degree"], "Bachelor's Degree"),
        (["master's degree", "master degree", "masters degree"], "Master's Degree"),
        (["juris doctor", "juris doctorate", "j.d.", "doctorate", "doctoral degree", "ph.d", "phd"], "Doctorate"),
    ]
    for terms, label in rules:
        if any(term in lower for term in terms):
            found.append(label)
    return "; ".join(dict.fromkeys(found))


def extract_certifications(text: str) -> str:
    cleaned = clean_text(text)
    found: List[str] = []

    # First prioritize the explicit Licenses and Certifications section.
    section_match = re.search(
        r"Licenses and Certifications\s+(.*?)(?:Physical Demands|Additional Physical Demands|$)",
        cleaned, re.I
    )
    section = section_match.group(1) if section_match else ""

    required_text = section
    # Include other explicit required license statements outside the section.
    for m in re.finditer(r"[^.]{0,160}(?:license|certification|certified|admitted to practice law)[^.]{0,160}required[^.]*", cleaned, re.I):
        required_text += " " + m.group(0)

    patterns = [
        (r"\bDriver'?s License\b", "Driver's License"),
        (r"\bCommercial Driver'?s License(?:\s*\(CDL\))?(?:\s*-?\s*Class\s*[ABC])?\b", None),
        (r"\bCDL(?:\s*-?\s*Class\s*[ABC])?\b", None),
        (r"\bProject Management Professional\s*\(PMP\)\b", "Project Management Professional (PMP)"),
        (r"\bPMP\b", "PMP"),
        (r"\bProfessional Engineer\s*\(PE\)\b", "Professional Engineer (PE)"),
        (r"\bProfessional Engineer\b", "Professional Engineer"),
        (r"\bCISSP\b", "CISSP"),
        (r"\bCISM\b", "CISM"),
        (r"\bGIAC\b", "GIAC"),
        (r"\bCertified Internal Auditor\s*\(CIA\)\b", "Certified Internal Auditor (CIA)"),
        (r"\bCertified Safety Professional\s*\(CSP\)\b", "Certified Safety Professional (CSP)"),
        (r"\bCWI\b", "CWI"),
        (r"\bASNT Level\s*(?:I|II|III)\b", None),
        (r"\bASME Section IX\b", "ASME Section IX"),
        (r"\bAPI 1104\b", "API 1104"),
        (r"\bNYC S-95\b|\bS-95 certification\b", "NYC S-95"),
        (r"\bNYC S-12\b|\bS-12 certification\b", "NYC S-12"),
        (r"\bFDNY Cert(?:ificate)? of Fitness\b", "FDNY Certificate of Fitness"),
    ]
    for pattern, label in patterns:
        for match in re.finditer(pattern, required_text, re.I):
            found.append(label or clean_text(match.group(0)))

    if re.search(r"admitted to practice law in (?:the state of )?New York[^.]{0,60}required", cleaned, re.I):
        found.append("New York Bar Admission")

    return "; ".join(dict.fromkeys(found))


def job_term(detail: Dict[str, Any], title: str) -> str:
    raw = " ".join(clean_text(detail.get(k)) for k in ["ContractType", "WorkerType", "JobType", "JobSchedule"])
    lower = f"{title} {raw}".lower()
    if any(x in lower for x in ["temporary", "temp ", "temp coop", "co-op", "coop", "intern"]):
        return "Temporary"
    if any(x in lower for x in ["contractor", "contract role", "fixed term"]):
        return "Contract"
    if any(x in lower for x in ["full time", "full-time", "part time", "part-time", "regular"]):
        return "Permanent"
    return ""


def normalize_oracle_date(value: Any) -> str:
    text = clean_text(value)
    if not text:
        return ""
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(ZoneInfo("America/New_York"))
        return dt.date().isoformat()
    except Exception:
        return text[:10] if len(text) >= 10 else text


def vaccine_requirement(text: str) -> str:
    lower = clean_text(text).lower()
    if any(x in lower for x in ["vaccination not required", "vaccine not required"]):
        return "No"
    if any(x in lower for x in ["vaccination required", "vaccine required", "must be vaccinated", "proof of vaccination"]):
        return "Yes"
    return "Not Stated"


class OracleScraper:
    def __init__(self, search_url: str, debug_dir: str = "debug"):
        self.search_url = search_url
        parsed = urlparse(search_url)
        self.base_url = f"{parsed.scheme}://{parsed.netloc}"
        parts = parsed.path.split("/")
        if "sites" not in parts:
            raise ValueError("This does not look like an Oracle Candidate Experience URL.")
        self.site_number = parts[parts.index("sites") + 1]
        self.location_id = get_query_param(search_url, "locationId")
        self.debug_dir = Path(debug_dir)
        self.debug_dir.mkdir(parents=True, exist_ok=True)

    def _search_page(self, context: BrowserContext, offset: int, limit: int = 25) -> Dict[str, Any]:
        endpoint = f"{self.base_url}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
        finder_parts = [
            f"siteNumber={self.site_number}",
            "facetsList=LOCATIONS;WORK_LOCATIONS;WORKPLACE_TYPES;TITLES;CATEGORIES;ORGANIZATIONS;POSTING_DATES;FLEX_FIELDS",
            f"limit={limit}",
            f"offset={offset}",
        ]
        if self.location_id:
            finder_parts.append(f"locationId={self.location_id}")
        finder_parts.append("sortBy=POSTING_DATES_DESC")
        finder = "findReqs;" + ",".join(finder_parts)
        response = context.request.get(
            endpoint,
            params={
                "onlyData": "true",
                "expand": "requisitionList.workLocation,requisitionList.otherWorkLocations,requisitionList.secondaryLocations,flexFieldsFacet.values,requisitionList.requisitionFlexFields",
                "finder": finder,
            },
            timeout=120000,
        )
        if not response.ok:
            raise RuntimeError(f"Oracle search API failed: {response.status} {response.text()[:1000]}")
        return response.json()

    def get_search_jobs(self, context: BrowserContext, max_jobs: Optional[int] = None) -> List[Dict[str, Any]]:
        jobs: List[Dict[str, Any]] = []
        offset = 0
        page_size = 25
        total = None

        while True:
            data = self._search_page(context, offset=offset, limit=page_size)
            items = data.get("items", [])
            if not items:
                break
            result = items[0]
            batch = result.get("requisitionList", []) or []
            if total is None:
                total = result.get("TotalJobsCount")
            if not batch:
                break
            jobs.extend(batch)
            if max_jobs and len(jobs) >= max_jobs:
                jobs = jobs[:max_jobs]
                break
            if total is not None and len(jobs) >= int(total):
                break
            offset += len(batch)
            if len(batch) < page_size:
                break
            if offset > 5000:
                break
        return jobs

    def get_detail_by_finder(self, context: BrowserContext, job_id: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        endpoint = f"{self.base_url}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
        response = context.request.get(
            endpoint,
            params={
                "onlyData": "true",
                "expand": "all",
                "finder": f"ById;Id={job_id},siteNumber={self.site_number}",
            },
            timeout=120000,
        )
        raw: Dict[str, Any] = {}
        if response.ok:
            try:
                raw = response.json()
                items = raw.get("items", [])
                return (items[0] if items else {}), raw
            except Exception:
                pass

        # No-cache variant is documented by Oracle and can expose fresher tenant fields.
        response2 = context.request.get(
            endpoint,
            params={
                "onlyData": "true",
                "expand": "all",
                "finder": f"ByIdNoCache;Id={job_id},siteNumber={self.site_number}",
            },
            timeout=120000,
        )
        if response2.ok:
            try:
                raw = response2.json()
                items = raw.get("items", [])
                return (items[0] if items else {}), raw
            except Exception:
                pass

        return {}, raw

    def capture_detail_page(self, page: Page, job_id: str) -> Tuple[str, str, str, List[str], List[Any], List[Any]]:
        detail_url = f"{self.base_url}/hcmUI/CandidateExperience/en/sites/{self.site_number}/job/{job_id}"
        network_json: List[Any] = []

        def on_response(response):
            try:
                if urlparse(response.url).netloc != urlparse(self.base_url).netloc:
                    return
                ctype = (response.headers.get("content-type") or "").lower()
                if "json" not in ctype:
                    return
                if "hcmRestApi" not in response.url:
                    return
                payload = response.json()
                network_json.append({"url": response.url, "payload": payload})
            except Exception:
                return

        page.on("response", on_response)
        try:
            page.goto(detail_url, wait_until="domcontentloaded", timeout=120000)
            page.wait_for_timeout(3000)
            body_text = clean_text(page.locator("body").inner_text())
            raw_html = page.content()
            script_texts = [x for x in page.locator("script").all_text_contents() if x.strip()]
            ld_json: List[Any] = []
            for raw in page.locator('script[type="application/ld+json"]').all_text_contents():
                try:
                    ld_json.append(json.loads(raw))
                except Exception:
                    if raw.strip():
                        ld_json.append(raw)
            return detail_url, body_text, raw_html, script_texts, ld_json, network_json
        finally:
            try:
                page.remove_listener("response", on_response)
            except Exception:
                pass

    def parse_record(
        self,
        list_job: Dict[str, Any],
        detail: Dict[str, Any],
        detail_raw: Dict[str, Any],
        detail_url: str,
        page_text: str,
        raw_html: str,
        script_texts: List[str],
        ld_json: List[Any],
        network_json: List[Any],
    ) -> Dict[str, Any]:
        title = clean_text(detail.get("Title") or list_job.get("Title"))
        description = clean_text(detail.get("ExternalDescriptionStr") or list_job.get("ShortDescriptionStr"))
        responsibilities = clean_text(detail.get("ExternalResponsibilitiesStr") or list_job.get("ExternalResponsibilitiesStr"))
        qualifications = clean_text(detail.get("ExternalQualificationsStr") or list_job.get("ExternalQualificationsStr"))
        all_text = " ".join([title, description, responsibilities, qualifications, page_text])

        comp_sources: List[Tuple[str, Any]] = [
            ("oracle_detail_finder", detail_raw),
            ("oracle_detail_item", detail),
        ]
        for idx, obj in enumerate(ld_json):
            comp_sources.append((f"json_ld_{idx}", obj))
        for idx, obj in enumerate(network_json):
            comp_sources.append((f"network_{idx}", obj))
        for idx, script in enumerate(script_texts):
            comp_sources.append((f"script_{idx}", script))
        comp_sources.append(("rendered_html", raw_html))
        comp_sources.append(("rendered_job_page", page_text))

        comp = choose_compensation(comp_sources)
        min_hourly = normalize_hourly(comp.minimum, comp.period)
        max_hourly = normalize_hourly(comp.maximum, comp.period)

        min_hours, max_hours = extract_hours(detail, page_text)
        raw_job_type = clean_text(first_nonempty(detail, ["JobType", "WorkerType", "ContractType", "JobSchedule"]))
        category = clean_text(detail.get("Category") or list_job.get("Category") or detail.get("JobFunctionCode") or list_job.get("JobFunction") or list_job.get("JobFamily"))
        location = clean_text(detail.get("PrimaryLocation") or list_job.get("PrimaryLocation"))
        employer = clean_text(detail.get("LegalEmployer") or list_job.get("LegalEmployer") or "Con Edison")
        close_date = normalize_oracle_date(detail.get("ExternalPostedEndDate") or list_job.get("PostingEndDate") or "")
        posting_date = normalize_oracle_date(detail.get("ExternalPostedStartDate") or list_job.get("PostedDate") or "")

        return {
            "Job Opportunity Name": title,
            "Employer Name": employer,
            "Job Opportunity Stage": "Open",
            "Close Date": close_date,
            "Source": "Oracle Recruiting Cloud",
            "Min Hours per Week": min_hours,
            "Max Hours per Week": max_hours,
            "Job Term": job_term(detail, title),
            "Min Wage Normalized Hourly": min_hourly,
            "Max Wage Normalized Hourly": max_hourly,
            "Required Education": extract_education(qualifications),
            "Required Certifications & Licenses": extract_certifications(qualifications),
            "Category": category,
            "Vaccine Requirement": vaccine_requirement(all_text),
            "Location": location,
            "Posting Date": posting_date,
            "Original Min Compensation": comp.minimum,
            "Original Max Compensation": comp.maximum,
            "Compensation Period": comp.period,
            "Compensation Currency": comp.currency,
            "Compensation Evidence": comp.evidence,
            "Compensation Source": comp.source,
            "Job Type Raw": raw_job_type,
            "Job ID": list_job.get("Id") or detail.get("Id") or "",
            "Job Description": description,
            "Responsibilities": responsibilities,
            "Qualifications": qualifications,
            "Job Detail URL": detail_url,
            "Source URL": self.search_url,
            "Date Scraped": datetime.now().isoformat(timespec="seconds"),
        }

    def scrape(
        self,
        max_jobs: Optional[int] = None,
        save_debug: bool = True,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> pd.DataFrame:
        records: List[Dict[str, Any]] = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            page.goto(self.search_url, wait_until="domcontentloaded", timeout=120000)
            page.wait_for_timeout(2000)

            jobs = self.get_search_jobs(context, max_jobs=max_jobs)
            total = len(jobs)

            for index, list_job in enumerate(jobs, start=1):
                title = clean_text(list_job.get("Title"))
                job_id = str(list_job.get("Id") or "")
                if progress_callback:
                    progress_callback(index, total, title)

                detail, detail_raw = self.get_detail_by_finder(context, job_id)
                detail_url, page_text, raw_html, script_texts, ld_json, network_json = self.capture_detail_page(page, job_id)

                if save_debug:
                    job_dir = self.debug_dir / job_id
                    job_dir.mkdir(parents=True, exist_ok=True)
                    (job_dir / "detail_finder.json").write_text(json.dumps(detail_raw, indent=2, default=str), encoding="utf-8")
                    (job_dir / "network.json").write_text(json.dumps(network_json, indent=2, default=str), encoding="utf-8")
                    (job_dir / "json_ld.json").write_text(json.dumps(ld_json, indent=2, default=str), encoding="utf-8")
                    (job_dir / "page.txt").write_text(page_text, encoding="utf-8")
                    (job_dir / "page.html").write_text(raw_html, encoding="utf-8")
                    (job_dir / "scripts.txt").write_text("\n\n--- SCRIPT ---\n\n".join(script_texts), encoding="utf-8")

                records.append(
                    self.parse_record(
                        list_job=list_job,
                        detail=detail,
                        detail_raw=detail_raw,
                        detail_url=detail_url,
                        page_text=page_text,
                        raw_html=raw_html,
                        script_texts=script_texts,
                        ld_json=ld_json,
                        network_json=network_json,
                    )
                )
                time.sleep(0.15)

            browser.close()

        return pd.DataFrame(records, columns=OUTPUT_COLUMNS)


def scrape_oracle(
    search_url: str,
    max_jobs: Optional[int] = None,
    debug_dir: str = "debug",
    save_debug: bool = True,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> pd.DataFrame:
    return OracleScraper(search_url, debug_dir=debug_dir).scrape(
        max_jobs=max_jobs,
        save_debug=save_debug,
        progress_callback=progress_callback,
    )
