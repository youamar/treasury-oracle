"""Generate mock payment proof images + a bank statement CSV for the demo.

Run:  python generate_samples.py
Outputs into ./samples/
"""
import csv
import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent / "samples"
OUT.mkdir(exist_ok=True)


def _font(size: int):
    for candidate in [
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]:
        if os.path.exists(candidate):
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def make_proof(filename: str, payer: str, payee: str, amount: float, ccy: str,
               date: str, ref: str, brand: str = "GlobalPay"):
    W, H = 720, 1000
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    f_brand = _font(36)
    f_h = _font(28)
    f_lbl = _font(18)
    f_val = _font(22)
    f_amt = _font(54)

    d.rectangle([(0, 0), (W, 90)], fill="#1f6feb")
    d.text((30, 25), brand, fill="white", font=f_brand)
    d.text((30, 130), "Payment Receipt", fill="black", font=f_h)
    d.line([(30, 175), (W - 30, 175)], fill="#888", width=2)

    d.text((30, 200), "Amount", fill="#555", font=f_lbl)
    d.text((30, 225), f"{ccy} {amount:,.2f}", fill="#1f6feb", font=f_amt)

    rows = [
        ("Status", "SUCCESS"),
        ("Date", date),
        ("From", payer),
        ("To", payee),
        ("Reference", ref),
        ("Method", "International Transfer"),
    ]
    y = 320
    for label, value in rows:
        d.text((30, y), label, fill="#555", font=f_lbl)
        d.text((220, y - 2), value, fill="black", font=f_val)
        y += 50

    d.line([(30, y + 10), (W - 30, y + 10)], fill="#ccc", width=1)
    d.text((30, y + 30), "This is a system-generated receipt.", fill="#888", font=f_lbl)

    img.save(OUT / filename)
    print(f"  wrote {filename}")


PROOFS = [
    # (file, payer, amount, ccy, date, ref)  -- all paid TO our SME "BrightTech Sdn Bhd"
    ("proof_01_usd.png",  "Acme Corp (USA)",        1000.00, "USD", "2026-05-20", "INV-2026-001"),
    ("proof_02_eur.png",  "Berlin Designs GmbH",     850.00, "EUR", "2026-05-21", "INV-2026-002"),
    ("proof_03_sgd.png",  "Singapore Holdings Pte",  500.00, "SGD", "2026-05-22", "INV-2026-003"),
    ("proof_04_gbp.png",  "London Media Ltd",        300.00, "GBP", "2026-05-22", "INV-2026-004"),
    ("proof_05_jpy.png",  "Tokyo Robotics KK",    120000.00, "JPY", "2026-05-23", "INV-2026-005"),
    ("proof_06_cny.png",  "Shenzhen Hardware Co",   3200.00, "CNY", "2026-05-23", "INV-2026-006"),
    # discrepancy: amount doesn't match what landed (triggers SWIFT trace)
    ("proof_07_usd_disc.png", "MysteryCo LLC",       500.00, "USD", "2026-05-24", "INV-2026-007"),
    # SOFT MATCH case: payer name is the boss's brother-in-law's personal account
    ("proof_08_soft.png",    "Wei Ming Tan (Personal)", 800.00, "USD", "2026-05-25", "INV-2026-008"),
]

PAYEE = "BrightTech Sdn Bhd"

print("Generating payment proofs...")
for p in PROOFS:
    make_proof(p[0], p[1], PAYEE, p[2], p[3], p[4], p[5])

# Bank statement (MYR account). Amounts use static rates from tools.py minus 0.5% fee.
# USD->MYR 4.72; EUR->MYR 5.10; SGD->MYR 3.52; GBP->MYR 5.95; JPY->MYR 0.031; CNY->MYR 0.65
STATEMENT_ROWS = [
    # date, amount (MYR credit), ccy, description, reference
    ("2026-05-20", round(1000 * 4.72 * 0.995, 2),  "MYR", "INWARD TT ACME CORP",          "INV-2026-001"),
    ("2026-05-21", round( 850 * 5.10 * 0.995, 2),  "MYR", "INWARD TT BERLIN DESIGNS",     "INV-2026-002"),
    ("2026-05-22", round( 500 * 3.52 * 0.995, 2),  "MYR", "INWARD TT SG HOLDINGS",        "INV-2026-003"),
    ("2026-05-23", round( 300 * 5.95 * 0.995, 2),  "MYR", "INWARD TT LONDON MEDIA",       "INV-2026-004"),
    ("2026-05-24", round(120000 * 0.031 * 0.995, 2),"MYR","INWARD TT TOKYO ROBOTICS",     "INV-2026-005"),
    ("2026-05-24", round(3200 * 0.65 * 0.995, 2),  "MYR", "INWARD TT SHENZHEN HW",        "INV-2026-006"),
    # mismatched amount for proof 7 (~10% off — should NOT match within 2% tolerance)
    ("2026-05-25", round( 500 * 4.72 * 0.995 * 0.85, 2), "MYR", "INWARD TT UNKNOWN PAYER", "MYS-999"),
    # orphan transaction with no proof
    ("2026-05-25", 1888.00, "MYR", "INWARD TT WALK-IN CUSTOMER", "WALKIN-001"),
    # SOFT MATCH target: amount off by ~8% (so strict tier fails) but invoice ref
    # appears in the bank narrative → soft matcher proposes it for human confirmation.
    ("2026-05-25", round(800 * 4.72 * 0.995 * 0.92, 2), "MYR",
     "INWARD TT ACME GLOBAL HOLDINGS REF INV-2026-008", "INV-2026-008"),
]

stmt_path = OUT / "bank_statement.csv"
with open(stmt_path, "w", newline="", encoding="utf-8") as fp:
    w = csv.writer(fp)
    w.writerow(["Date", "Amount", "Currency", "Description", "Reference"])
    for row in STATEMENT_ROWS:
        w.writerow(row)
print(f"  wrote bank_statement.csv  ({len(STATEMENT_ROWS)} rows)")
print(f"\nAll samples in: {OUT}")
