import re
from typing import Dict, Iterable, List, Optional

import pypdf

PDF_PATH = "output/invoices/invoice_010.pdf"

TOTAL_LABELS = (
    "total due",
    "amount due",
    "balance due",
    "total amount due",
    "final amount due",
    "final amount",
    "invoice total",
    "total amount",
    "amount payable",
)

DATE_LABELS = (
    "invoice date",
    "date of invoice",
    "date issued",
    "issued on",
    "billing date",
    "bill date",
    "statement date",
)

VENDOR_LABELS = (
    "vendor",
    "vendor name",
    "supplier",
    "supplier name",
    "bill from",
    "seller",
    "from",
    "issued by",
)

PO_LABELS = (
    "invoice",
    "invoice #",
    "invoice number",
    "invoice no",
    "po number",
    "po #",
    "po no",
    "purchase order",
    "purchase order number",
    "order no",
    "order number",
    "po",
)

MONTH_PATTERN = (
    "jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    "jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)


def build_label_pattern(labels: Iterable[str]) -> str:
    parts = sorted((re.escape(label) for label in labels), key=len, reverse=True)
    return "(?:" + "|".join(parts) + ")"


TOTAL_LABEL_PATTERN = build_label_pattern(TOTAL_LABELS)
DATE_LABEL_PATTERN = build_label_pattern(DATE_LABELS)
VENDOR_LABEL_PATTERN = build_label_pattern(VENDOR_LABELS)
PO_LABEL_PATTERN = build_label_pattern(PO_LABELS)

AMOUNT_PATTERN = r"[$€£]?\s*[-\d.,]+"

TOTAL_PATTERN = re.compile(
    rf"(?P<label>{TOTAL_LABEL_PATTERN})[\s:]*?(?P<value>{AMOUNT_PATTERN})",
    re.IGNORECASE,
)

DATE_PATTERN = re.compile(
    rf"""
    (?P<label>{DATE_LABEL_PATTERN})
    [\s:,-]*
    (?P<value>
        (?:\d{{1,2}}[/-]){{2}}\d{{2,4}} |
        \d{{4}}(?:[/-]\d{{1,2}}){{2}} |
        \d{{1,2}}\s+(?:{MONTH_PATTERN})\s+\d{{2,4}} |
        (?:{MONTH_PATTERN})\s+\d{{1,2}},?\s+\d{{2,4}}
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

VENDOR_INLINE_PATTERN = re.compile(
    rf"(?P<label>{VENDOR_LABEL_PATTERN})[\s:,-]+(?P<value>[^\n]+)", re.IGNORECASE
)

VENDOR_STANDALONE_PATTERN = re.compile(
    rf"^\s*(?:{VENDOR_LABEL_PATTERN})\s*$", re.IGNORECASE
)

PO_PATTERN = re.compile(
    rf"(?P<label>{PO_LABEL_PATTERN})[\s#:,-]*?(?P<value>[A-Z0-9][A-Z0-9\-\/]+)",
    re.IGNORECASE,
)


def extract_pages(path: str) -> List[str]:
    reader = pypdf.PdfReader(path)
    return [(page.extract_text() or "") for page in reader.pages]


def search_pattern(pages: List[str], pattern: re.Pattern) -> Optional[Dict[str, str]]:
    for page_number, page_text in enumerate(pages, start=1):
        match = pattern.search(page_text)
        if match:
            return {
                "page": page_number,
                "label": match.group("label"),
                "value": match.group("value").strip(),
            }
    return None


def find_vendor(pages: List[str]) -> Optional[Dict[str, str]]:
    for page_number, page_text in enumerate(pages, start=1):
        inline_match = VENDOR_INLINE_PATTERN.search(page_text)
        if inline_match:
            return {
                "page": page_number,
                "label": inline_match.group("label"),
                "value": inline_match.group("value").strip(),
            }

        lines = [line.strip() for line in page_text.splitlines()]
        for idx, line in enumerate(lines[:-1]):
            if VENDOR_STANDALONE_PATTERN.match(line) and lines[idx + 1]:
                return {
                    "page": page_number,
                    "label": line,
                    "value": lines[idx + 1].strip(),
                }
    return None


def main(pdf_path: str) -> None:
    try:
        pages = extract_pages(pdf_path)
    except Exception as err:
        print(f"Error reading PDF: {err}")
        return

    total = search_pattern(pages, TOTAL_PATTERN)
    invoice_date = search_pattern(pages, DATE_PATTERN)
    vendor = find_vendor(pages)
    po_number = search_pattern(pages, PO_PATTERN)

    print("Total due:", total)
    print("Invoice date:", invoice_date)
    print("Vendor:", vendor)
    print("PO number:", po_number)
    extracted_fields = {
        "total": total,
        "invoice_date": invoice_date,
        "vendor": vendor,
        "po_number": po_number,
    }
    return extracted_fields

if __name__ == "__main__":
    main(PDF_PATH)
