from __future__ import annotations
from fastapi import FastAPI, HTTPException
from app import extract_invoice_fields
import hashlib
import base64
import time
from pydantic import BaseModel, Field
from typing import Optional, List, Literal, Any
import requests
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
import os

load_dotenv()
JAVA_BASE_URL = os.getenv("JAVA_BASE_URL", "http://localhost:8080")
JAVA_TIMEOUT_SEC = float(os.getenv("JAVA_TIMEOUT_SEC", "10"))
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY")
EXTRACTION_VERSION = 0
FieldType = Literal["string", "number", "date", "currency", "int","float"]
class Options(BaseModel):
    doc_type: Optional[str] = "invoice"
    lang: Optional[str] = "en"
    ocr: Optional[bool] = False

class InvoiceRequest(BaseModel):
    filepath: str = Field(..., description="Path to the invoice file to be processed")

class ExtractedField(BaseModel):
    name: str
    value: Any
    type: FieldType
    units: Optional[str] = None
    confidence: float
    page: Optional[int] = None
    method: Optional[str] = None
    source: Optional[str] = None

class DocumentResponse(BaseModel):
    documentHash: str
    filename: str
    mime : str = None
    pages: int

class ExtractionResponse(BaseModel):
    extractionVersion: int = EXTRACTION_VERSION
    runtimeMs: float
    warnings: List[str] = []
    fields: List[ExtractedField]

class InvoiceResponse(BaseModel):
    status: Literal["success", "error"]
    document: DocumentResponse
    extraction: ExtractionResponse


def _java_headers(idem_key: str | None = None) -> dict:
    h = {
        "Content-Type": "application/json",
    }
    if INTERNAL_API_KEY:
        h["X-Internal-Key"] = INTERNAL_API_KEY
    if idem_key:
        h["Idempotency-Key"] = idem_key     # dedupe on Java side
    return h
def _create_or_get_document(doc_hash: str, name: str, mime: str, pages: int) -> int:
    """
    Calls POST /api/documents and returns the integer documentId.
    Align the payload keys to your CreateDocumentRequest.
    """
    url = f"{JAVA_BASE_URL}/api/documents"
    print(JAVA_BASE_URL)    
    payload = {
        # TODO: align these keys to your CreateDocumentRequest DTO
        "documentHash": doc_hash,
        "name": name,
        "mime": mime,
        "pages": pages,
    }
    r = requests.post(url, json=payload, headers=_java_headers(doc_hash), timeout=JAVA_TIMEOUT_SEC)
    if r.status_code not in (200, 201):  # your controller returns 201 CREATED
        raise RuntimeError(f"Doc create failed ({r.status_code}): {r.text}")

    data = r.json()
    # TODO: extract the *actual* id field name from CreateDocumentResponse
    # Common patterns: "documentId", "id"
    doc_id = data.get("documentId") or data.get("id")
    if not isinstance(doc_id, int):
        raise RuntimeError(f"Could not parse documentId from response: {data}")
    return doc_id

def _post_extraction(document_id: int, invoice_payload: dict, idem_key: str):
    """
    Calls POST /api/documents/{documentId}/extractions
    Build the body to match CreateExtractionRequest exactly.
    """
    url = f"{JAVA_BASE_URL}/api/documents/{document_id}/extractions"
    print(JAVA_BASE_URL)

    # Map our generic InvoiceResponse → your CreateExtractionRequest
    # TODO: adjust keys/types to your DTO. This is a sensible default:
    extraction = invoice_payload["extraction"]
    fields = extraction.get("fields", [])

    req_body = {
        # examples you likely want; rename to match your CreateExtractionRequest:
        "extractionVersion": EXTRACTION_VERSION, 
        "runtimeMs": extraction.get("runtimeMs"),
        "warnings": extraction.get("warnings", []),
        "fields": [
            {
                "name": f["name"],
                "value": f.get("value"),
                "type": f.get("type"),
                "confidence": f.get("confidence"),
                "page": f.get("page"),
                "units": f.get("units"),
                "method": f.get("method"),
                "source": f.get("source"),
            }
            for f in fields
        ],
        # You might also want to include document-level info if your DTO expects it:
        # "documentHash": invoice_payload["document"]["documentHash"],
        # "mime": invoice_payload["document"]["mime"],
        # "pages": invoice_payload["document"]["pages"],
        # "fileName": invoice_payload["document"]["name"],
    }

    r = requests.post(url, json=req_body, headers=_java_headers(idem_key), timeout=JAVA_TIMEOUT_SEC)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Extraction post failed ({r.status_code}): {r.text}")
    return r

app = FastAPI(title = "DocInsights Extractor", version="1.0.0")

@app.post("/extract", response_model=InvoiceResponse)
def extract(req:InvoiceRequest):
    start = time.perf_counter()

    try:
        h = hashlib.sha3_256()
        filepath = req.filepath
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):  # 1MB chunks
                h.update(chunk)
        digest = h.digest()  # 32 bytes
        document_hash = base64.urlsafe_b64encode(digest).rstrip(b'=').decode("ascii")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    try:
        result = extract_invoice_fields(req.filepath)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
    end = time.perf_counter()
    runtime_ms = end - start
    doc_resp = DocumentResponse(
        documentHash=document_hash,
        filename=req.filepath.split("/")[-1],
        mime="application/pdf",
        pages=result.pages
    )
    ext_resp = ExtractionResponse(
        runtimeMs=runtime_ms,
        warnings=result.warnings,
        fields=[ExtractedField(**field.__dict__) for field in result.fields]
    )   
    invoice_payload = InvoiceResponse(
        status="success",
        document=doc_resp,
        extraction=ext_resp
    ).model_dump()
    doc_hash = doc_resp.documentHash
    push_status = {"ok": False, "error": None, "documentId": None}

    try:
        # 1) Create or get the document, receive integer documentId
        document_id = _create_or_get_document(
            doc_hash=doc_hash,
            name=doc_resp.filename,
            mime=doc_resp.mime,
            pages=doc_resp.pages,
        )
        push_status["documentId"] = document_id

        # 2) Post the extraction to /api/documents/{documentId}/extractions
        _post_extraction(document_id=document_id, invoice_payload=invoice_payload, idem_key=doc_hash)
        push_status["ok"] = True
    except Exception as e:
        push_status["error"] = str(e)

    # Return the extraction result + push status (handy while integrating)
    return JSONResponse(
        status_code=200,
        content={
            **invoice_payload,
            "pushToJava": push_status,
        },
    )
@app.get("/getAllData")
def get_data():
    try:
        r = requests.get(f"{JAVA_BASE_URL}/api/documents/allExtractions", headers=_java_headers(), timeout=JAVA_TIMEOUT_SEC)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": str(e)}
