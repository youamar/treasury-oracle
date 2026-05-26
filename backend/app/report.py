"""Generate PDF reconciliation report from matcher output."""
import io
from datetime import datetime, timezone
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
)


def build_report_pdf(result: dict, bank_name: str = "default") -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    h1 = styles["Heading1"]
    h2 = styles["Heading2"]
    body = styles["BodyText"]
    small = ParagraphStyle("small", parent=body, fontSize=8, textColor=colors.grey)

    story = []
    story.append(Paragraph("Cross-Border Reconciliation Report", h1))
    story.append(Paragraph(
        f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · Bank: {bank_name}",
        small,
    ))
    story.append(Spacer(1, 0.4*cm))

    s = result["summary"]
    summary_data = [
        ["Total proofs", s["total_proofs"]],
        ["Total bank txns", s["total_txns"]],
        ["Matched", s["matched"]],
        ["Unmatched proofs", s["unmatched_proofs"]],
        ["Unmatched txns", s["unmatched_txns"]],
    ]
    t = Table(summary_data, colWidths=[6*cm, 3*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (0,-1), colors.lightgrey),
        ("GRID", (0,0), (-1,-1), 0.3, colors.grey),
        ("FONTSIZE", (0,0), (-1,-1), 9),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.6*cm))

    story.append(Paragraph("Matched Transactions", h2))
    if not result["matches"]:
        story.append(Paragraph("<i>No matches.</i>", body))
    for i, m in enumerate(result["matches"], 1):
        p = m["proof"]; tx = m["txn"]; c = m["conversion"]
        rows = [
            ["#", f"Match {i}"],
            ["Proof", f"{p.get('source_file','')} · {p['amount']} {p['currency']} on {p.get('date','?')}"],
            ["Payer", p.get("payer") or "—"],
            ["Reference", p.get("reference") or "—"],
            ["Bank txn", f"{tx['id']} · {tx['amount']} {tx['currency']} on {tx['date']}"],
            ["FX rate", f"{c['fx_rate']:.4f}"],
            ["Expected gross", f"{c['expected_gross']} {tx['currency']}"],
            ["Bank fee", f"{c['fee_amount']} ({c['fee_pct']*100:.2f}%)"],
            ["Expected net", f"{c['expected_net']} {tx['currency']}"],
            ["Actual received", f"{c['actual_received']} {tx['currency']}"],
            ["Confidence", f"{m['confidence']*100:.0f}%"],
        ]
        tbl = Table(rows, colWidths=[3.5*cm, 13*cm])
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (0,-1), colors.whitesmoke),
            ("GRID", (0,0), (-1,-1), 0.25, colors.lightgrey),
            ("FONTSIZE", (0,0), (-1,-1), 8),
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1f6feb")),
            ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ]))
        story.append(tbl)
        story.append(Paragraph(f"<i>Reasoning:</i> {m['reasoning']}", small))
        story.append(Spacer(1, 0.3*cm))

    story.append(PageBreak())
    story.append(Paragraph("Discrepancy Summary", h2))
    if not result["unmatched_proofs"]:
        story.append(Paragraph("<i>No discrepancies. All proofs reconciled.</i>", body))
    for i, u in enumerate(result["unmatched_proofs"], 1):
        story.append(Paragraph(
            f"<b>#{i}</b> {u.get('source_file','')} — {u.get('amount','?')} {u.get('currency','?')} on {u.get('date','?')}",
            body,
        ))
        story.append(Paragraph(f"<i>Reason:</i> {u.get('reason','')}", small))
        story.append(Spacer(1, 0.2*cm))

    if result["unmatched_txns"]:
        story.append(Spacer(1, 0.4*cm))
        story.append(Paragraph("Unmatched Bank Transactions", h2))
        rows = [["ID", "Date", "Amount", "Ccy", "Description"]]
        for t in result["unmatched_txns"]:
            rows.append([t["id"], t["date"], t["amount"], t["currency"], t.get("description","")[:40]])
        tbl = Table(rows, colWidths=[2*cm, 2.5*cm, 2.5*cm, 1.5*cm, 8*cm])
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#6e7681")),
            ("TEXTCOLOR", (0,0), (-1,0), colors.white),
            ("GRID", (0,0), (-1,-1), 0.25, colors.lightgrey),
            ("FONTSIZE", (0,0), (-1,-1), 8),
        ]))
        story.append(tbl)

    story.append(Spacer(1, 0.6*cm))
    story.append(Paragraph("Agent Reasoning Trace", h2))
    for line in result.get("trace", []):
        story.append(Paragraph(line.replace("✓","[OK]").replace("✗","[X]"), small))

    doc.build(story)
    return buf.getvalue()
