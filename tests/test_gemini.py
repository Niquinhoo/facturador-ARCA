from __future__ import annotations

import unittest
from types import SimpleNamespace

from superbot.gemini import GeminiInterpreter


class Models:
    def generate_content(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(text=(
            '{"mode":"action","kind":"STOCK_ADJUST",'
            '"message":"Voy a preparar el ajuste.",'
            '"payload_json":"{\\"stockProductoId\\":42,\\"delta\\":3,\\"reason\\":\\"devolucion\\"}"}'
        ))


class GeminiTests(unittest.TestCase):
    def test_returns_only_a_structured_proposal(self):
        models = Models()
        interpreter = GeminiInterpreter("fake", "gemini-test", SimpleNamespace(models=models))
        intent = interpreter.interpret({"stock": [{"stockProductoId": 42}]}, text="suma tres")
        self.assertEqual(intent.kind, "STOCK_ADJUST")
        self.assertEqual(intent.payload["delta"], 3)
        self.assertIn("response_json_schema", models.kwargs["config"].model_dump(exclude_none=True))


if __name__ == "__main__":
    unittest.main()
