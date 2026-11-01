from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from superbot.arca import ArcaError, ArcaService, build_voucher_data
from superbot.pdf import render_voucher_pdf


PAYLOAD = {
    "cbteTipo": 11,
    "concepto": 1,
    "docTipo": 99,
    "docNro": "0",
    "condicionIvaReceptor": 5,
    "impTotal": 12500.50,
    "fechaCbte": "2026-08-12",
    "descripcion": "Venta de indumentaria",
}


class Billing:
    def __init__(self, response=None, error=None, voucher_info=None):
        self.response = response
        self.error = error
        self.voucher_info = voucher_info
        self.created = None

    def getLastVoucher(self, point, voucher_type):
        return 8 if self.created is None else 9

    def createVoucher(self, data):
        self.created = data
        if self.error:
            raise self.error
        return self.response

    def getVoucherInfo(self, number, point, voucher_type):
        return self.voucher_info


def settings():
    return SimpleNamespace(
        afip_cuit=20123456789, afip_punto_venta=4, arca_production=False,
        afip_access_token="test-token", afip_cert_path=None, afip_key_path=None,
    )


class ArcaTests(unittest.TestCase):
    def test_builds_factura_c_and_credit_note_association(self):
        data = build_voucher_data(PAYLOAD, 9, 4, 20123456789)
        self.assertEqual(data["CbteTipo"], 11)
        self.assertEqual(data["CbteDesde"], 9)
        self.assertEqual(data["ImpNeto"], data["ImpTotal"])
        self.assertNotIn("FchServDesde", data)

        credit = build_voucher_data(
            {**PAYLOAD, "cbteTipo": 13, "asociadoCbteNro": 7}, 2, 4, 20123456789
        )
        self.assertEqual(credit["CbtesAsoc"][0]["Nro"], 7)

    def test_emits_and_normalizes_authorization(self):
        billing = Billing(response={"CAE": "12345678901234", "CAEFchVto": "20260822"})
        service = ArcaService(settings(), lambda options: SimpleNamespace(ElectronicBilling=billing))
        emission = service.emit(PAYLOAD)
        self.assertFalse(emission.reconciled)
        self.assertEqual(emission.result["cbteNro"], 9)
        self.assertEqual(emission.result["caeVto"], "2026-08-22")

    def test_reconciles_ambiguous_response_before_retry(self):
        info = {
            "CodAutorizacion": "12345678901234", "FchVto": "20260822",
            "ImpTotal": 12500.50, "DocNro": 0,
        }
        billing = Billing(error=TimeoutError("timeout"), voucher_info=info)
        service = ArcaService(settings(), lambda options: SimpleNamespace(ElectronicBilling=billing))
        emission = service.emit(PAYLOAD)
        self.assertTrue(emission.reconciled)
        self.assertEqual(emission.result["cae"], "12345678901234")

    def test_persists_number_before_calling_arca_and_recovers_after_process_crash(self):
        response = {"CAE": "12345678901234", "CAEFchVto": "20260822"}
        billing = Billing(response=response)
        service = ArcaService(settings(), lambda options: SimpleNamespace(ElectronicBilling=billing))
        prepared = []
        service.emit(PAYLOAD, before_create=lambda number, request: prepared.append((number, request)))
        self.assertEqual(prepared[0][0], 9)
        self.assertEqual(prepared[0][1]["CbteDesde"], 9)

        # Simula que el proceso murio luego de autorizar y antes de completar el job.
        recovered_billing = Billing(voucher_info={
            "CodAutorizacion": "12345678901234", "FchVto": "20260822",
            "ImpTotal": 12500.50, "DocNro": 0,
        })
        recovered_billing.created = {}  # getLastVoucher ya devuelve el numero 9.
        recovered = ArcaService(
            settings(), lambda options: SimpleNamespace(ElectronicBilling=recovered_billing)
        ).emit(PAYLOAD, expected_number=9)
        self.assertTrue(recovered.reconciled)

    def test_rejects_credit_note_without_association(self):
        with self.assertRaises(ArcaError):
            build_voucher_data({**PAYLOAD, "cbteTipo": 13}, 1, 4, 20123456789)

    def test_generates_local_pdf_with_qr(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "voucher.pdf"
            render_voucher_pdf(target, 20123456789, PAYLOAD, {
                "ptoVta": 4, "cbteNro": 9, "cae": "12345678901234",
                "caeVto": "2026-08-22",
            })
            self.assertTrue(target.is_file())
            self.assertGreater(target.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
