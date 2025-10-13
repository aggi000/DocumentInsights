from faker import Faker
from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML
import json, random, os
from datetime import date, timedelta
from pathlib import Path

fake = Faker()

# --- folders -------------------------------------------------
base = Path(__file__).resolve().parent
tpl_dir = base / "templates"
out_dir = base / "output"
(out_dir / "invoices").mkdir(parents=True, exist_ok=True)
(out_dir / "labels").mkdir(parents=True, exist_ok=True)

env = Environment(loader=FileSystemLoader(str(tpl_dir)))
template = env.get_template("invoice.html")

# --- helper --------------------------------------------------
def gen_invoice(idx:int):
    vendor = fake.company()
    invoice_date = (date.today() - timedelta(days=random.randint(0, 90))).isoformat()
    po_number = f"PO-{random.randint(10000, 99999)}"
    line_items = []
    for _ in range(random.randint(2, 5)):
        qty = random.randint(1, 10)
        price = round(random.uniform(10, 200), 2)
        line_items.append({
            "desc": fake.bs().title(),
            "qty": qty,
            "unit_price": price,
            "total": round(qty * price, 2)
        })
    subtotal = round(sum(i["total"] for i in line_items), 2)
    tax = round(subtotal * 0.05, 2)
    total = round(subtotal + tax, 2)

    html_str = template.render(
        vendor=vendor, invoice_date=invoice_date, po_number=po_number,
        line_items=line_items, subtotal=subtotal, tax=tax, invoice_total=total
    )

    pdf_path = out_dir / "invoices" / f"invoice_{idx:03d}.pdf"
    HTML(string=html_str).write_pdf(pdf_path)

    label = {
        "filename": pdf_path.name,
        "fields": [
            {"name": "vendor", "value": vendor},
            {"name": "invoice_date", "value": invoice_date},
            {"name": "po_number", "value": po_number},
            {"name": "invoice_total", "value": str(total)}
        ],
        "line_items": line_items
    }
    json_path = out_dir / "labels" / f"invoice_{idx:03d}.json"
    with open(json_path, "w") as f:
        json.dump(label, f, indent=2)
    print(f"✅ Created {pdf_path.name}")

# --- main ----------------------------------------------------
if __name__ == "__main__":
    for i in range(1, 10):   # 50 invoices
        gen_invoice(i)
    print("All invoices generated in output/invoices/")
