from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Callable

from afip import Afip

from .config import Settings


class ArcaError(RuntimeError):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


def _date_int(value: Any) -> int:
    raw = str(value or date.today().isoformat()).replace("-", "")[:8]
    if len(raw) != 8 or not raw.isdigit():
        raise ArcaError(f"Fecha fiscal invalida: {value}", retryable=False)
    return int(raw)


def _iso_date(value: Any) -> str:
    raw = str(value or "").replace("-", "")[:8]
    if len(raw) != 8 or not raw.isdigit():
        raise ArcaError("ARCA no devolvio un vencimiento de CAE valido.")
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"


def build_voucher_data(payload: dict[str, Any], number: int, point_of_sale: int,
                       issuer_cuit: int) -> dict[str, Any]:
    cbte_tipo = int(payload["cbteTipo"])
    concepto = int(payload.get("concepto", 1))
    total = float(Decimal(str(payload["impTotal"])).quantize(Decimal("0.01")))
    data: dict[str, Any] = {
        "CantReg": 1,
        "PtoVta": point_of_sale,
        "CbteTipo": cbte_tipo,
        "Concepto": concepto,
        "DocTipo": int(payload.get("docTipo", 99)),
        "DocNro": int(str(payload.get("docNro", "0"))),
        "CbteDesde": number,
        "CbteHasta": number,
        "CbteFch": _date_int(payload.get("fechaCbte")),
        "ImpTotal": total,
        "ImpTotConc": 0,
        "ImpNeto": total,
        "ImpOpEx": 0,
        "ImpIVA": 0,
        "ImpTrib": 0,
        "MonId": "PES",
        "MonCotiz": 1,
        "CondicionIVAReceptorId": int(payload.get("condicionIvaReceptor", 5)),
    }
    if concepto in (2, 3):
        data.update({
            "FchServDesde": _date_int(payload.get("servDesde") or payload.get("fechaCbte")),
            "FchServHasta": _date_int(payload.get("servHasta") or payload.get("fechaCbte")),
            "FchVtoPago": _date_int(payload.get("fechaCbte")),
        })
    if cbte_tipo == 13:
        associated = payload.get("asociadoCbteNro")
        if not associated:
            raise ArcaError("La Nota de Credito necesita una Factura C asociada.", retryable=False)
        data["CbtesAsoc"] = [{
            "Tipo": 11, "PtoVta": point_of_sale, "Nro": int(associated),
            "Cuit": str(issuer_cuit),
        }]
    return data


def _extract_authorization(info: dict[str, Any], number: int,
                           point_of_sale: int) -> dict[str, Any]:
    cae = info.get("CAE") or info.get("CodAutorizacion") or info.get("cae")
    due = info.get("CAEFchVto") or info.get("FchVto") or info.get("cae_vto")
    if not cae or not due:
        raise ArcaError("ARCA informo el comprobante pero no devolvio CAE completo.")
    return {
        "cbteNro": number,
        "ptoVta": point_of_sale,
        "cae": str(cae),
        "caeVto": _iso_date(due),
        "arca": info,
    }


@dataclass
class Emission:
    result: dict[str, Any]
    request: dict[str, Any]
    reconciled: bool


class ArcaService:
    def __init__(self, settings: Settings, afip_factory: Callable[[dict[str, Any]], Any] = Afip):
        options: dict[str, Any] = {
            "CUIT": settings.afip_cuit,
            "production": settings.arca_production,
            "access_token": settings.afip_access_token,
        }
        if (settings.afip_cert_path and settings.afip_cert_path.is_file()
                and settings.afip_key_path and settings.afip_key_path.is_file()):
            options["cert"] = settings.afip_cert_path.read_text(encoding="utf-8")
            options["key"] = settings.afip_key_path.read_text(encoding="utf-8")
        self.afip = afip_factory(options)
        self.cuit = settings.afip_cuit
        self.point_of_sale = settings.afip_punto_venta

    def server_status(self) -> Any:
        return self.afip.ElectronicBilling.getServerStatus()

    def emit(self, payload: dict[str, Any], expected_number: int | None = None,
             before_create: Callable[[int, dict[str, Any]], Any] | None = None) -> Emission:
        cbte_type = int(payload["cbteTipo"])
        billing = self.afip.ElectronicBilling
        last = int(billing.getLastVoucher(self.point_of_sale, cbte_type))
        number = int(expected_number or (last + 1))
        if expected_number:
            info = billing.getVoucherInfo(number, self.point_of_sale, cbte_type) if last >= number else None
            if info:
                expected_total = Decimal(str(payload["impTotal"])).quantize(Decimal("0.01"))
                actual_total = Decimal(str(info.get("ImpTotal", expected_total))).quantize(Decimal("0.01"))
                expected_doc = int(str(payload.get("docNro", "0")))
                actual_doc = int(info.get("DocNro", expected_doc))
                if actual_total == expected_total and actual_doc == expected_doc:
                    return Emission(
                        result=_extract_authorization(info, number, self.point_of_sale),
                        request=build_voucher_data(payload, number, self.point_of_sale, self.cuit),
                        reconciled=True,
                    )
                raise ArcaError("El numero fiscal preparado pertenece a otro comprobante.", False)
            if last != number - 1:
                raise ArcaError("La secuencia fiscal cambio antes de emitir; requiere revision.", False)
        request = build_voucher_data(payload, number, self.point_of_sale, self.cuit)
        if before_create:
            before_create(number, request)
        try:
            response = billing.createVoucher(request)
            return Emission(
                result=_extract_authorization(response, number, self.point_of_sale),
                request=request,
                reconciled=False,
            )
        except Exception as exc:
            # Una interrupcion puede ocurrir despues de que ARCA autorizo. Antes de
            # reintentar, se consulta exactamente el numero que intentamos emitir.
            try:
                current_last = int(billing.getLastVoucher(self.point_of_sale, cbte_type))
                info = billing.getVoucherInfo(number, self.point_of_sale, cbte_type) \
                    if current_last >= number else None
                if info:
                    expected_total = Decimal(str(payload["impTotal"])).quantize(Decimal("0.01"))
                    actual_total = Decimal(str(info.get("ImpTotal", expected_total))).quantize(Decimal("0.01"))
                    expected_doc = int(str(payload.get("docNro", "0")))
                    actual_doc = int(info.get("DocNro", expected_doc))
                    if actual_total == expected_total and actual_doc == expected_doc:
                        return Emission(
                            result=_extract_authorization(info, number, self.point_of_sale),
                            request=request,
                            reconciled=True,
                        )
                    raise ArcaError("El numero fiscal fue ocupado por otro comprobante; requiere revision.", False)
            except ArcaError:
                raise
            except Exception:
                pass
            message = str(exc)
            retryable = not any(marker in message.lower() for marker in (
                "rechaz", "inval", "no corresponde", "no autorizado", "10016"
            ))
            raise ArcaError(message or "Fallo la emision en ARCA.", retryable) from exc
