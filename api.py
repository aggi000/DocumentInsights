from fastapi import FastAPI, Body
from app import main
import hashlib
import base64
import time
from pydantic import BaseModel
from typing import Optional

class Options(BaseModel):
    doc_type: Optional[str] = "invoice"
    lang: Optional[str] = "en"
    ocr: Optional[bool] = False

class InvoiceRequest(BaseModel):
    filepath: str
    options: Optional[Options] = None

class data(BaseModel):
    vendor: Optional[dict]
    invoice_date: Optional[dict]
    po_number: Optional[dict]
    total: Optional[dict]

class InvoiceResponse(BaseModel):
    data: dict
    status: str
    documentID: str
    processing_time: float

app = FastAPI()
@app.get("/")
async def root():
    return {"message": "Hello World"}

@app.post("/invoice/")
async def process_invoice(req: InvoiceRequest) -> InvoiceResponse:
    start_time = time.perf_counter()
    filepath = req.filepath
    h = hashlib.sha3_256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):  # 1MB chunks
            h.update(chunk)
    digest = h.digest()  # 32 bytes
    documentID = base64.urlsafe_b64encode(digest).rstrip(b'=').decode("ascii")
    end_time = time.perf_counter()
    processing_time = end_time - start_time
    return InvoiceResponse(data=main(filepath), status="success", documentID=documentID, processing_time=processing_time)

