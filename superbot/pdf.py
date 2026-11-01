from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
from typing import Any

import segno
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _qr_url(cuit: int, payload: dict[str, Any], result: dict[str, Any]) -> str:
    data = {
        "ver": 1,
        "fecha": str(payload["fechaCbte"]),
        "cuit": cuit,
        "ptoVta": int(result["ptoVta"]),
        "tipoCmp": int(payload["cbteTipo"]),
        "nroCmp": int(result["cbteNro"]),
        "importe": float(payload["impTotal"]),
        "moneda": "PES",
        "ctz": 1,
        "tipoDocRec": int(payload.get("docTipo", 99)),
        "nroDocRec": int(str(payload.get("docNro", "0"))),
        "tipoCodAut": "E",
        "codAut": int(result["cae"]),
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(data, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    return f"https://www.afip.gob.ar/fe/qr/?p={encoded}"


def render_voucher_pdf(target: Path, cuit: int, payload: dict[str, Any],
                       result: dict[str, Any]) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    qr_buffer = io.BytesIO()
    segno.make(_qr_url(cuit, payload, result), error="m").save(
        qr_buffer, kind="png", scale=5, border=1
    )
    qr_buffer.seek(0)

    styles = getSampleStyleSheet()
    title = "NOTA DE CREDITO C" if int(payload["cbteTipo"]) == 13 else "FACTURA C"
    issuer = os.getenv("EMISOR_NOMBRE", "Empresa Calma")
    address = os.getenv("EMISOR_DOMICILIO", "-")
    number = f"{int(result['ptoVta']):05d}-{int(result['cbteNro']):08d}"
    total = f"$ {float(payload['impTotal']):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    doc = SimpleDocTemplate(
        str(target), pagesize=A4, rightMargin=14 * mm, leftMargin=14 * mm,
        topMargin=14 * mm, bottomMargin=14 * mm,
        title=f"{title} {number}", author=issuer,
    )
    story = [
        Table([
            [Paragraph(f"<b>{issuer}</b><br/>{address}<br/>CUIT {cuit}<br/>Responsable Monotributo", styles["BodyText"]),
             Paragraph(f"<b>{title}</b><br/><font size=18>C</font><br/>Nro. {number}<br/>Fecha {payload['fechaCbte']}", styles["BodyText"])],
        ], colWidths=[90 * mm, 82 * mm], style=TableStyle([
            ("BOX", (0, 0), (-1, -1), 1, colors.black),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ])),
        Spacer(1, 7 * mm),
        Paragraph(
            f"<b>Receptor:</b> Doc. {payload.get('docTipo', 99)} {payload.get('docNro', '0')} &nbsp;&nbsp; "
            f"<b>Condicion IVA:</b> {payload.get('condicionIvaReceptor', 5)}",
            styles["BodyText"],
        ),
        Spacer(1, 5 * mm),
        Table([
            ["Descripcion", "Importe"],
            [payload.get("descripcion") or "Productos / servicios", total],
            ["TOTAL", total],
        ], colWidths=[135 * mm, 37 * mm], style=TableStyle([
            ("GRID", (0, 0), (-1, -1), .5, colors.grey),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ])),
        Spacer(1, 10 * mm),
        Table([[
            Image(qr_buffer, 36 * mm, 36 * mm),
            Paragraph(
                f"Comprobante autorizado por ARCA<br/><b>CAE:</b> {result['cae']}<br/>"
                f"<b>Vencimiento CAE:</b> {result['caeVto']}", styles["BodyText"]
            ),
        ]], colWidths=[45 * mm, 127 * mm], style=TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ])),
    ]
    doc.build(story)
    return target
