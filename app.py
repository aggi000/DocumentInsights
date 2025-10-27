# app.py
from __future__ import annotations
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional, Tuple, Dict, Any

from pypdf import PdfReader


_TOTAL_LABELS = (
    "total due",
    "amount due",
    "balance due",
    "total amount due",
    "final amount due",
    "final amount",
    "invoice total",
    "total amount",
    "total",
)

_DATE_LABELS = (
    "invoice date",
    "date",
    "issued on",
    "issue date",
    "billing date",
)

_PO_LABELS = (
    "po",
    "po #",
    "po no",
    "purchase order",
    "purchase order #",
)

_VENDOR_HINTS = (
    "invoice from",
    "from:",
    "vendor",
    "supplier",
    "billed by",
)

def _compile_label_pattern(label: str) -> re.Pattern:
    parts = [re.escape(p) for p in re.split(r"\s+", label.strip()) if p]
    if not parts:
        raise ValueError(f"Empty label: {label!r}")
    pattern = r"(?<!\w)" + r"\W*".join(parts) + r"(?!\w)"
    return re.compile(pattern, re.I)


_LABEL_PATTERNS: Dict[str, re.Pattern] = {
    label: _compile_label_pattern(label)
    for group in (_TOTAL_LABELS, _DATE_LABELS, _PO_LABELS)
    for label in group
}

AMOUNT_RE = re.compile(
    r"""
    (?<![\w])
    (?P<currency>[$£€])?
    \s*
    (?P<amount>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})?)
    (?![\w])
    """,
    re.VERBOSE,
)
_PO_TOKEN_RE = re.compile(r"[A-Z0-9]+(?:[-/#][A-Z0-9]+)*", re.I)
DATE_RES = [
    # 2025-10-15
    re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"),
    # 15/10/2025 or 15-10-2025
    re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b"),
    # Oct 15, 2025 / October 15, 2025
    re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+(\d{1,2}),?\s+(\d{2,4})\b", re.I),
]


@dataclass
class ExtractedField:
    name: str
    value: Any
    type: str
    confidence: float
    page: Optional[int] = None
    units: Optional[str] = None
    method: Optional[str] = None
    source: Optional[str] = None


@dataclass
class ExtractionResult:
    fields: List[ExtractedField]
    pages: int
    warnings: List[str]


def _pdf_to_lines(path: str) -> Tuple[List[str], int]:
    reader = PdfReader(path)
    pages = len(reader.pages)
    lines: List[str] = []
    for i in range(pages):
        text = reader.pages[i].extract_text() or ""
        for ln in text.splitlines():
            s = ln.strip()
            if s:
                lines.append((i + 1, s))
    # flatten to strings for easier scanning while preserving (page, text)
    return [f"{p}|||{t}" for p, t in lines], pages


def _norm_amount(s: str) -> Optional[Tuple[float, Optional[str]]]:
    m = AMOUNT_RE.search(s)
    if not m:
        return None
    amt = m.group("amount")
    cur = m.group("currency")
    try:
        val = float(amt.replace(",", "").replace(" ", ""))
    except ValueError:
        return None
    return val, {"$": "USD", "£": "GBP", "€": "EUR"}.get(cur) if cur else None


def _norm_date_fragment(y: int, m: int, d: int) -> Optional[date]:
    try:
        if y < 100:  # 2-digit year heuristic
            y += 2000 if y < 50 else 1900
        return date(y, m, d)
    except Exception:
        return None


def _extract_dates(s: str) -> List[date]:
    out: List[date] = []
    for rx in DATE_RES:
        for g in rx.finditer(s):
            if rx.pattern.startswith(r"\b(\d{4})-"):
                d = _norm_date_fragment(int(g[1]), int(g[2]), int(g[3]))
            elif "Jan" in rx.pattern or "Jan" in rx.pattern:
                # month name
                mon_map = {
                    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
                    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12
                }
                m = mon_map[g[1].lower()[:3]]
                d = _norm_date_fragment(int(g[3]), m, int(g[2]))
            else:
                # dd/mm/yyyy (assume non-US if ambiguous); you can flip if needed
                d = _norm_date_fragment(int(g[3]), int(g[2]), int(g[1]))
            if d:
                out.append(d)
    return out


def _label_in(s: str, labels: Tuple[str, ...]) -> bool:
    for lbl in labels:
        if _LABEL_PATTERNS[lbl].search(s):
            return True
    return False


def _value_after_delimiter(text: str) -> Optional[str]:
    for delim in (":", " - ", " – "):
        if delim in text:
            tail = text.split(delim, 1)[1].strip()
            if tail:
                return tail
    return None


def _maybe_vendor(line: str) -> Optional[Tuple[str, float]]:
    # crude heuristic: if a line is ALLCAPS words and contains vendor hints nearby
    txt = line.split("|||", 1)[1]
    if len(txt) < 3:
        return None
    # Strong hint: "Invoice from X", "Vendor: X"
    if any(h in txt.lower() for h in _VENDOR_HINTS):
        # take the bit after colon if present
        cand = _value_after_delimiter(txt) or txt
        cand = cand.strip()
        if cand and len(cand) <= 80:
            return cand, 0.9
        return None
    # fallback: first non-empty, mostly alphabetic, title-case line could be vendor
    if re.fullmatch(r"[A-Za-z0-9&@.\-,' ]{3,80}", txt) and (txt.isupper() or txt.istitle()):
        return txt, 0.4
    return None


def extract_invoice_fields(path: str) -> ExtractionResult:
    lines, pages = _pdf_to_lines(path)
    warnings: List[str] = []
    fields: Dict[str, ExtractedField] = {}

    # pass 1: look for labeled totals and dates/po around labels
    for line in lines:
        page_num, txt = line.split("|||", 1)
        p = int(page_num)

        # TOTAL
        if _label_in(txt, _TOTAL_LABELS):
            amt = _norm_amount(txt)
            if amt:
                val, unit = amt
                fields["total"] = ExtractedField(
                    name="total",
                    value=val,
                    units=unit or "USD",
                    type="currency",
                    confidence=0.9,
                    page=p,
                    method="regex",
                    source="label+amount",
                )

        # INVOICE DATE
        if _label_in(txt, _DATE_LABELS):
            dates = _extract_dates(txt)
            if dates:
                # choose earliest sensible date on same line
                d = min(dates)
                fields["invoiceDate"] = ExtractedField(
                    name="invoiceDate",
                    value=d.isoformat(),
                    type="date",
                    confidence=0.85,
                    page=p,
                    method="regex",
                    source="label+date",
                )

        # PO
        if _label_in(txt, _PO_LABELS):
            candidate = _value_after_delimiter(txt) or txt
            token_match = None
            for token in _PO_TOKEN_RE.findall(candidate):
                # ensure there is at least one digit to avoid capturing words like "Number"
                if any(ch.isdigit() for ch in token):
                    token_match = token
                    break
            if token_match:
                fields["poNumber"] = ExtractedField(
                    name="poNumber",
                    value=token_match,
                    type="string",
                    confidence=0.8,
                    page=p,
                    method="regex",
                    source="label+token",
                )

        # VENDOR (hinted)
        vend = _maybe_vendor(line)
        if vend:
            value, conf = vend
            existing = fields.get("vendor")
            if not existing or conf >= existing.confidence:
                fields["vendor"] = ExtractedField(
                    name="vendor",
                    value=value,
                    type="string",
                    confidence=conf,
                    page=p,
                    method="heuristic" if conf < 0.9 else "label",
                    source="header-line" if conf < 0.9 else "label",
                )

    # pass 2: if missing total/date, scan unlabeled candidates
    if "total" not in fields:
        # pick the largest amount on the document as a fallback
        best: Tuple[float, int, Optional[str], str] | None = None
        for line in lines:
            p, txt = line.split("|||", 1)
            am = _norm_amount(txt)
            if not am:
                continue
            val, unit = am
            if (best is None) or (val > best[0]):
                best = (val, int(p), unit, txt)
        if best:
            val, p, unit, _ = best
            fields["total"] = ExtractedField(
                name="total",
                value=val,
                units=unit or "USD",
                type="currency",
                confidence=0.7,
                page=p,
                method="regex",
                source="max-amount",
            )
        else:
            warnings.append("total:not_found")

    if "invoiceDate" not in fields:
        # pick earliest plausible date we see anywhere
        candidates: List[Tuple[date, int]] = []
        for line in lines:
            p, txt = line.split("|||", 1)
            for d in _extract_dates(txt):
                candidates.append((d, int(p)))
        if candidates:
            d, p = sorted(candidates)[0]
            fields["invoiceDate"] = ExtractedField(
                name="invoiceDate",
                value=d.isoformat(),
                type="date",
                confidence=0.6,
                page=p,
                method="regex",
                source="any-date-earliest",
            )
        else:
            warnings.append("invoiceDate:not_found")

    return ExtractionResult(fields=list(fields.values()), pages=pages, warnings=warnings)
