from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any

from google import genai
from google.genai import types


INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "mode": {"type": "string", "enum": ["query", "action", "clarify"]},
        "kind": {
            "type": "string",
            "enum": [
                "NONE", "STOCK_ADJUST", "INVOICE", "CREDIT_NOTE",
                "RENDITION_CREATE", "RENDITION_STATUS", "RENDITION_PAYMENT",
                "CONSIGNATION_CREATE", "CONSIGNATION_DISPATCH",
                "CONSIGNATION_CONFIRM", "CONSIGNATION_RESOLVE",
            ],
        },
        "message": {"type": "string"},
        "payload_json": {"type": "string"},
    },
    "required": ["mode", "kind", "message", "payload_json"],
}

SYSTEM_PROMPT = """
Sos el interprete operativo de Calma, una empresa argentina. Responde en espanol
rioplatense, breve y preciso. Recibis contexto REAL ya filtrado por permisos.
No inventes IDs, productos, montos ni estados. Nunca afirmes que ejecutaste algo.

Clasifica cada pedido:
- query: consulta de stock, rendiciones, comprobantes o resumen. Responde usando
  exclusivamente el contexto y usa kind NONE.
- action: propone UNA accion estructurada. La API de Calma la validara y siempre
  mostrara una vista previa antes de confirmar.
- clarify: falta un dato indispensable o hay ambiguedad. Pregunta concretamente.

Acciones y payload:
STOCK_ADJUST: {producto o stockProductoId, delta entero, reason}.
INVOICE: {impTotal, docTipo=99/80/96, docNro, condicionIvaReceptor,
 concepto=1/2/3, fechaCbte YYYY-MM-DD, descripcion, servDesde?, servHasta?}.
CREDIT_NOTE: igual a INVOICE mas asociadoCbteNro o asociadoVoucherId.
RENDITION_CREATE: {vendedorId?, items:[...], canal?}.
RENDITION_STATUS: {rendicionId, estado}.
RENDITION_PAYMENT: {rendicionId, items:[...], fecha?}.
CONSIGNATION_CREATE: {vendedorId, items:[...]}.
CONSIGNATION_DISPATCH: {consignacionId}.
CONSIGNATION_CONFIRM: {consignacionId, items:[...]}.
CONSIGNATION_RESOLVE: {consignacionId, items:[...]}.

payload_json debe ser un objeto JSON serializado, o "{}" para query/clarify.
Para consumidor final usa docTipo 99 y docNro "0". No elijas variantes si hay
mas de una coincidencia; pedile al usuario que indique el ID exacto.
""".strip()


@dataclass(frozen=True)
class Intent:
    mode: str
    kind: str
    message: str
    payload: dict[str, Any]


class GeminiInterpreter:
    def __init__(self, api_key: str, model: str, client: Any = None):
        self.client = client or genai.Client(api_key=api_key)
        self.model = model

    def interpret(self, context: dict[str, Any], text: str | None = None,
                  audio: bytes | None = None, mime_type: str = "audio/ogg") -> Intent:
        if not text and not audio:
            raise ValueError("Se necesita texto o audio.")
        context_json = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        prompt = (
            f"Fecha actual: {date.today().isoformat()}\n"
            f"Contexto autorizado de Calma:\n{context_json}\n\n"
            "Interpreta el pedido del usuario."
        )
        contents: list[Any] = [prompt]
        if text:
            contents.append(f"Pedido: {text}")
        if audio:
            contents.append(types.Part.from_bytes(data=audio, mime_type=mime_type))

        response = self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.1,
                response_mime_type="application/json",
                response_json_schema=INTENT_SCHEMA,
            ),
        )
        raw = json.loads(response.text or "{}")
        try:
            payload = json.loads(raw.get("payload_json") or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError("Gemini devolvio un payload invalido.") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Gemini devolvio un payload no valido.")
        return Intent(
            mode=str(raw.get("mode", "clarify")),
            kind=str(raw.get("kind", "NONE")),
            message=str(raw.get("message", "Necesito un poco mas de informacion.")),
            payload=payload,
        )
