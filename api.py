from __future__ import annotations
from fastapi import FastAPI, HTTPException, UploadFile, File
from app import extract_invoice_fields
import hashlib
import base64
import time
from pydantic import BaseModel, Field
from typing import Optional, List, Literal, Any
import requests
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
import os
import tempfile
from pathlib import Path

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
    payload = {
        "documentHash": doc_hash,
        "name": name,
        "mime": mime,
        "pages": pages,
    }
    r = requests.post(url, json=payload, headers=_java_headers(doc_hash), timeout=JAVA_TIMEOUT_SEC)
    if r.status_code not in (200, 201):  # your controller returns 201 CREATED
        raise RuntimeError(f"Doc create failed ({r.status_code}): {r.text}")

    data = r.json()
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
    extraction = invoice_payload["extraction"]
    fields = extraction.get("fields", [])

    req_body = {
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
    }

    r = requests.post(url, json=req_body, headers=_java_headers(idem_key), timeout=JAVA_TIMEOUT_SEC)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Extraction post failed ({r.status_code}): {r.text}")
    return r


def _process_extraction(filepath: str, display_name: str | None = None, mime_type: str | None = None) -> dict:
    """
    Run the Python extraction pipeline and push results to the Java backend.
    Returns the combined response payload that the API endpoints expose.
    """
    start = time.perf_counter()
    try:
        h = hashlib.sha3_256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        digest = h.digest()
        document_hash = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        result = extract_invoice_fields(filepath)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    runtime_ms = time.perf_counter() - start
    doc_resp = DocumentResponse(
        documentHash=document_hash,
        filename=Path(display_name or filepath).name,
        mime=mime_type or "application/pdf",
        pages=result.pages,
    )
    ext_resp = ExtractionResponse(
        runtimeMs=runtime_ms,
        warnings=result.warnings,
        fields=[ExtractedField(**field.__dict__) for field in result.fields],
    )
    invoice_payload = InvoiceResponse(
        status="success",
        document=doc_resp,
        extraction=ext_resp,
    ).model_dump()

    doc_hash = doc_resp.documentHash
    push_status = {"ok": False, "error": None, "documentId": None}
    try:
        document_id = _create_or_get_document(
            doc_hash=doc_hash,
            name=doc_resp.filename,
            mime=doc_resp.mime,
            pages=doc_resp.pages,
        )
        push_status["documentId"] = document_id
        _post_extraction(document_id=document_id, invoice_payload=invoice_payload, idem_key=doc_hash)
        push_status["ok"] = True
    except Exception as exc:
        push_status["error"] = str(exc)

    return {
        **invoice_payload,
        "pushToJava": push_status,
    }


app = FastAPI(title = "DocInsights Extractor", version="1.0.0")
app.mount("/ui", StaticFiles(directory="ui", html=True), name="ui")

@app.post("/extract", response_model=InvoiceResponse)
def extract(req:InvoiceRequest):
    data = _process_extraction(req.filepath)
    return JSONResponse(status_code=200, content=data)

@app.get("/getAllData")
def get_data():
    try:
        resp = requests.get(
            f"{JAVA_BASE_URL}/api/documents/allExtractions",
            headers=_java_headers(),
            timeout=JAVA_TIMEOUT_SEC,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Java backend request failed: {exc}") from exc

    try:
        return resp.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Java backend returned non-JSON response") from exc


@app.post("/extract/upload")
async def extract_upload(file: UploadFile = File(...)):
    """
    Accept a file upload, run extraction, and return the same payload as /extract.
    """
    suffix = Path(file.filename or "upload").suffix
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix or ".pdf") as tmp:
            tmp_path = tmp.name
            while True:
                chunk = await file.read(1 << 20)
                if not chunk:
                    break
                tmp.write(chunk)
        result = _process_extraction(
            tmp_path,
            display_name=file.filename or Path(tmp_path).name,
            mime_type=file.content_type,
        )
        return JSONResponse(status_code=200, content=result)
    finally:
        await file.close()
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
