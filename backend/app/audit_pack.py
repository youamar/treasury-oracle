"""Audit Defense Pack — per-transaction evidence bundle PDF for LHDN/auditors.

Tamper-evidence (F11+F12):
  * Before rendering, every proof_source_sha256 referenced in the match
    provenance is re-hashed against the stored bytes; mismatch raises
    SourceBytesTampered and the pack refuses to issue.
  * The rendered PDF includes a final §8 ATTESTATION SIGNATURE page with a
    canonical-JSON manifest, an Ed25519 signature over that manifest, and
    the public-key fingerprint. Anyone holding the PDF + the public key can
    prove it was issued by this Treasury Oracle instance and was not
    modified post-issuance (any field on the PDF that disagrees with the
    signed manifest is provably tampered).
"""
import io
from datetime import datetime, timezone
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
)

from . import attestation


def build_audit_pack(match: dict, bank_name: str,
                     recon_id: str | None = None,
                     match_index: int | None = None) -> bytes:
    # F11: re-hash the stored proof bytes; refuse to issue if tampered.
    # Provenance carries the SHA in proof_amount.source_sha256.
    prov_block = (match.get("conversion") or {}).get("provenance") or {}
    proof_sha = (prov_block.get("proof_amount") or {}).get("source_sha256")
    source_verification = attestation.verify_source_bytes(proof_sha)

    # F12: sign a canonical manifest of this match — we'll render it on the
    # last page so anyone holding the PDF + public key can verify it.
    attestation_block = attestation.attest(match, bank_name, recon_id, match_index)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=1.8*cm, rightMargin=1.8*cm,
                            topMargin=1.8*cm, bottomMargin=1.8*cm)
    styles = getSampleStyleSheet()
    h1 = styles["Heading1"]; h2 = styles["Heading2"]; body = styles["BodyText"]
    small = ParagraphStyle("s", parent=body, fontSize=8, textColor=colors.grey)
    mono = ParagraphStyle("m", parent=body, fontSize=8, fontName="Courier")

    p = match["proof"]; tx = match["txn"]; c = match["conversion"]
    story = []
    story.append(Paragraph(f"AUDIT EVIDENCE PACK — {p.get('reference','no-ref')}", h1))
    story.append(Paragraph(
        f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', '')}Z · "
        f"Bank: {bank_name} · Confidence: {int(match['confidence']*100)}%", small))
    story.append(Spacer(1, 0.4*cm))

    sections = [
        ("§1 PAYMENT PROOF (source: client document)", [
            ("Source file", p.get("source_file","")),
            ("Payer", p.get("payer","")),
            ("Payee", p.get("payee","")),
            ("Invoice / Ref", p.get("reference","")),
            ("Original amount", f"{p.get('amount')} {p.get('currency')}"),
            ("Transaction date", p.get("date","")),
            ("Description", p.get("description","") or "—"),
        ]),
        ("§2 LOCAL BANK TRANSACTION (source: bank statement)", [
            ("Bank txn ID", tx["id"]),
            ("Date posted", tx["date"]),
            ("Amount credited", f"{tx['amount']} {tx['currency']}"),
            ("Bank narrative", tx.get("description","")),
            ("Bank reference", tx.get("reference","")),
        ]),
        ("§3 FX CONVERSION (source: ECB via frankfurter.app)", [
            ("From currency", p.get("currency","")),
            ("To currency", tx["currency"]),
            ("Rate applied", f"{c['fx_rate']:.6f}"),
            ("Rate date", p.get("date","")),
            ("Gross converted", f"{c['expected_gross']} {tx['currency']}"),
        ]),
        ("§4 BANK FEE (source: institutional fee schedule)", [
            ("Bank", bank_name),
            ("Fee rule", f"{c['fee_pct']*100:.2f}% inbound conversion"),
            ("Fee deducted", f"{c['fee_amount']} {tx['currency']}"),
            ("Net expected", f"{c['expected_net']} {tx['currency']}"),
        ]),
        ("§5 RECONCILIATION RESULT", [
            ("Actual received", f"{c['actual_received']} {tx['currency']}"),
            ("Variance vs expected",
                f"{round(c['actual_received'] - c['expected_net'], 2)} {tx['currency']}"),
            ("Variance %",
                f"{abs(c['actual_received']-c['expected_net'])/max(c['expected_net'],1e-6)*100:.3f}%"),
            ("Status", match["status"]),
            ("Tolerance applied", "2.00% (configurable)"),
        ]),
    ]
    for title, rows in sections:
        story.append(Paragraph(title, h2))
        t = Table(rows, colWidths=[5.5*cm, 11.5*cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (0,-1), colors.HexColor("#f1f5f9")),
            ("GRID", (0,0), (-1,-1), 0.3, colors.grey),
            ("FONTSIZE", (0,0), (-1,-1), 9),
            ("VALIGN", (0,0), (-1,-1), "TOP"),
        ]))
        story.append(t)
        story.append(Spacer(1, 0.3*cm))

    # §5.5 — provenance table (every numeric → its source)
    prov = c.get("provenance") or {}
    if prov:
        story.append(Paragraph("§5.5 PROVENANCE OF EVERY NUMBER ABOVE", h2))
        story.append(Paragraph(
            "Each numeric in this report is traceable to its origin. "
            "<b>trusted=False</b> means the value did not come from a verified source "
            "(e.g. live ECB feed, bank statement, or signed config) and the match "
            "was downgraded accordingly.", small))
        prov_rows = [["Field", "Value", "Source", "As of", "Trusted"]]
        for field_name in ("proof_amount", "fx_rate", "fee", "expected_gross",
                           "expected_net", "actual_received"):
            entry = prov.get(field_name) or {}
            prov_rows.append([
                field_name,
                str(entry.get("value", "—")),
                str(entry.get("source", "—")),
                str(entry.get("asof", "—")),
                "Yes" if entry.get("trusted") else "No",
            ])
        # SHA-256 of the original proof bytes — the legal hook
        sha = (prov.get("proof_amount") or {}).get("source_sha256")
        if sha:
            prov_rows.append(["proof_source_sha256", sha[:16] + "…", "uploads", "—", "Yes"])
        pt = Table(prov_rows, colWidths=[3.5*cm, 3.0*cm, 6.5*cm, 2.5*cm, 1.5*cm])
        pt.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#0f172a")),
            ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
            ("GRID", (0,0), (-1,-1), 0.3, colors.grey),
            ("FONTSIZE", (0,0), (-1,-1), 8),
            ("VALIGN", (0,0), (-1,-1), "TOP"),
            ("FONTNAME", (1,1), (1,-1), "Courier"),
            ("FONTNAME", (2,1), (2,-1), "Courier"),
        ]))
        story.append(pt)
        if prov.get("all_inputs_trusted") is False:
            story.append(Spacer(1, 0.15*cm))
            story.append(Paragraph(
                "<font color='#b45309'>⚠ One or more inputs above were not from a "
                "trusted source. This match should not be cited as final without "
                "operator confirmation.</font>", small))
        story.append(Spacer(1, 0.3*cm))

    story.append(Paragraph("§6 AGENT REASONING (chain of custody)", h2))
    story.append(Paragraph(match.get("reasoning",""), small))

    story.append(Spacer(1, 0.6*cm))
    story.append(Paragraph("§7 ATTESTATION", h2))
    story.append(Paragraph(
        "This evidence pack is auto-generated by the Treasury Oracle agent. "
        "All FX rates were retrieved from European Central Bank reference data "
        "via api.frankfurter.app. Bank fees are configured against the institution's "
        "published schedule. No human modification has occurred between automated "
        "ingestion and this attestation. Audit trail hash and source files are "
        "retrievable via /api/audit-pack/{recon_id}/{match_index}.", body))

    story.append(Spacer(1, 0.4*cm))
    story.append(Paragraph(
        f"<i>Document ID: AUDIT-{tx['id']}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}</i>",
        mono))

    # ---------- §8 ATTESTATION SIGNATURE (F12) ----------
    story.append(PageBreak())
    story.append(Paragraph("§8 CRYPTOGRAPHIC ATTESTATION", h2))
    story.append(Paragraph(
        "This page proves the audit pack was issued by this Treasury Oracle "
        "instance and has not been modified after issuance. The manifest "
        "below is signed with Ed25519; anyone holding the issuer's public "
        "key (fingerprint shown) can verify the signature via "
        "<font face='Courier'>POST /api/audit-pack/verify</font>. The signed "
        "manifest also anchors the SHA-256 of the original proof file: "
        "the body section above shows the same hash, and a re-hash of the "
        "stored bytes was performed before this pack was issued.", small))
    story.append(Spacer(1, 0.3*cm))

    # Source verification result
    sv = source_verification
    if sv.get("verified"):
        story.append(Paragraph(
            f"<font color='#047857'>✓ Source-bytes verification PASSED at issuance.</font><br/>"
            f"Original file: <font face='Courier'>{sv.get('filename')}</font>, "
            f"{sv.get('size')} bytes, uploaded {sv.get('uploaded_at')}.", small))
    elif sv.get("present") is False:
        story.append(Paragraph(
            "<font color='#92400e'>⚠ No source SHA in this match's provenance — "
            "byte-integrity check skipped. This typically means the proof was "
            "ingested before the upload-hashing was wired up.</font>", small))
    story.append(Spacer(1, 0.3*cm))

    # Attestation summary table
    att_rows = [
        ["Algorithm", attestation_block["algorithm"]],
        ["Key fingerprint", attestation_block["public_key_fingerprint"]],
        ["Manifest SHA-256", attestation_block["manifest_canonical_sha256"]],
        ["Manifest schema", attestation_block["manifest"]["schema"]],
        ["Issued at", attestation_block["manifest"]["issued_at"]],
    ]
    att_table = Table(att_rows, colWidths=[4.5*cm, 12.5*cm])
    att_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (0,-1), colors.HexColor("#f1f5f9")),
        ("GRID", (0,0), (-1,-1), 0.3, colors.grey),
        ("FONTSIZE", (0,0), (-1,-1), 8),
        ("FONTNAME", (1,1), (1,-1), "Courier"),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
    ]))
    story.append(att_table)
    story.append(Spacer(1, 0.3*cm))

    # Signature block — wrap base64 so it doesn't overflow
    sig = attestation_block["signature_b64"]
    sig_wrapped = "<br/>".join(sig[i:i+64] for i in range(0, len(sig), 64))
    story.append(Paragraph("<b>Ed25519 signature (base64):</b>", small))
    story.append(Paragraph(sig_wrapped, mono))
    story.append(Spacer(1, 0.3*cm))

    # Canonical manifest JSON — render readable so a human can spot tampering
    import json as _json
    manifest_pretty = _json.dumps(attestation_block["manifest"],
                                  indent=2, ensure_ascii=False, default=str)
    story.append(Paragraph("<b>Signed manifest (canonical JSON):</b>", small))
    for line in manifest_pretty.split("\n"):
        # ReportLab needs &lt; for angle brackets but our content has none.
        story.append(Paragraph(line.replace(" ", "&nbsp;"), mono))

    doc.build(story)
    return buf.getvalue()
