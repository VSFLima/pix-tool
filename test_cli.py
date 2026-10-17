"""Testes do modulo 2: CLI (fluxo ponta a ponta, modulo -> integracao)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, "pix_cli.py")


def run(*args, stdin=None):
    return subprocess.run(
        [sys.executable, CLI, *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestCliGerar(unittest.TestCase):
    def test_gerar_emite_brcode_e_payload(self):
        r = run("gerar", "fulano@exemplo.com", "-v", "25,00", "-n", "PADARIA", "-c", "SAO PAULO")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("BR CODE", r.stdout)
        self.assertIn("QR CODE", r.stdout)
        self.assertIn("pix-qrcode-", r.stdout)

    def test_gerar_json_consistente(self):
        r = run("--json", "gerar", "12345678901", "-v", "10", "-n", "LOJA", "-c", "RECIFE")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["chave"], "12345678901")
        self.assertEqual(data["tipo_chave"], "CPF")
        self.assertEqual(data["valor"], "10.00")
        self.assertEqual(data["brcode"].startswith("000201"), True)
        self.assertTrue(data["qrcode_pix_na_payload"].startswith("pix-qrcode-"))

    def test_gerar_sem_valor(self):
        r = run("--json", "gerar", "+5511999999999", "-n", "X", "-c", "Y")
        data = json.loads(r.stdout)
        self.assertIsNone(data["valor"])
        self.assertEqual(data["tipo_chave"], "PHONE")

    def test_gerar_chave_invalida_falha(self):
        r = run("gerar", "nao-e-chave", "-n", "X", "-c", "Y")
        self.assertEqual(r.returncode, 1)
        self.assertIn("ERRO", r.stderr)
        self.assertIn("chave Pix invalida", r.stderr)


class TestCliLer(unittest.TestCase):
    def setUp(self):
        self.r = run("--json", "gerar", "fulano@exemplo.com", "-v", "25,00",
                     "-n", "PADARIA", "-c", "SAO PAULO", "-t", "pedido1")
        self.data = json.loads(self.r.stdout)

    def test_ler_brcode(self):
        r = run("--json", "ler", self.data["brcode"])
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["chave"], "fulano@exemplo.com")
        self.assertEqual(out["valor"], "25.00")
        self.assertEqual(out["txid"], "pedido1")
        self.assertTrue(out["crc16_valido"])

    def test_ler_qrcode_payload(self):
        r = run("--json", "ler", self.data["qrcode_pix_na_payload"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["chave"], "fulano@exemplo.com")

    def test_ler_relatorio_texto(self):
        r = run("ler", self.data["brcode"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Tipo da chave", r.stdout)
        self.assertIn("EMAIL", r.stdout)
        self.assertIn("R$ 25.00", r.stdout)
        self.assertIn("valido", r.stdout)

    def test_ler_com_espacos_e_quebras(self):
        code = self.data["brcode"]
        r = run("--json", "ler", f"\n  {code}  \n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["cidade"], "SAO PAULO")

    def test_ler_crc_invalido_falha(self):
        code = self.data["brcode"]
        r = run("ler", code[:-4] + "0000")
        self.assertEqual(r.returncode, 1)
        self.assertIn("CRC16 invalido", r.stderr)

    def test_ler_arquivo_e_stdin(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
            fh.write(self.data["brcode"])
            path = fh.name
        try:
            r = run("--json", "ler", f"@{path}")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout)["chave"], "fulano@exemplo.com")

            r = run("--json", "ler", "-", stdin=self.data["qrcode_pix_na_payload"])
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout)["chave"], "fulano@exemplo.com")
        finally:
            os.unlink(path)

    def test_ler_arquivo_inexistente(self):
        r = run("ler", "@/nao/existe/pix.txt")
        self.assertEqual(r.returncode, 2)
        self.assertIn("ERRO de arquivo", r.stderr)


class TestCliQrcode(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(run("--json", "gerar", "a@b.com", "-v", "5").stdout)

    def test_brcode_para_qr(self):
        r = run("qrcode", "qr", self.data["brcode"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), self.data["qrcode_payload"])

    def test_brcode_para_qr_com_prefixo(self):
        r = run("qrcode", "qr", self.data["brcode"], "--com-prefixo")
        self.assertEqual(r.stdout.strip(), "pix-qrcode-" + self.data["qrcode_payload"])

    def test_com_prefixo_saida_uma_linha(self):
        r = run("qrcode", "qr", self.data["brcode"], "--com-prefixo")
        self.assertEqual(len(r.stdout.strip().splitlines()), 1)

    def test_ida_e_volta_do_prefixo(self):
        r = run("--json", "ler", run("qrcode", "qr", self.data["brcode"], "--com-prefixo").stdout)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["chave"], "a@b.com")

    def test_qr_para_brcode(self):
        r = run("qrcode", "brcode", self.data["qrcode_pix_na_payload"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), self.data["brcode"])


class TestCliEditar(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(
            run("--json", "gerar", "antigo@exemplo.com", "-v", "10.00",
                "-n", "NOME ANTIGO", "-c", "CIDADE ANTIGA", "-t", "tx001").stdout
        )
        self.brcode = self.data["brcode"]

    def _edit(self, subcomando, *args):
        """Monta: pix --json <subcomando> <brcode> <args...>"""
        r = run("--json", subcomando, self.brcode, *args)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_trocar_chave(self):
        out = self._edit("chave", "novo@exemplo.com")
        self.assertEqual(out["chave"], "novo@exemplo.com")
        self.assertEqual(out["valor"], "10.00")
        self.assertEqual(out["nome"], "NOME ANTIGO")
        self.assertEqual(out["txid"], "tx001")
        self.assertTrue(out["crc16_valido"])
        self.assertIn("qrcode_payload", out)

    def test_trocar_chave_para_cpf_muda_tipo(self):
        out = self._edit("chave", "12345678901")
        self.assertEqual(out["chave_tipo"], "CPF")

    def test_trocar_chave_invalida_nao_altera_nada(self):
        r = run("chave", self.brcode, "invalida")
        self.assertEqual(r.returncode, 1)
        self.assertIn("chave Pix invalida", r.stderr)

    def test_definir_valor(self):
        for raw, expected in (("1500", "1500.00"), ("99,90", "99.90"), ("0,01", "0.01")):
            out = self._edit("valor", raw)
            self.assertEqual(out["valor"], expected, raw)
            self.assertTrue(out["crc16_valido"])

    def test_remover_valor_com_zero(self):
        out = self._edit("valor", "0")
        self.assertIsNone(out["valor"])
        self.assertTrue(out["crc16_valido"])

    def test_definir_nome_e_cidade(self):
        out = self._edit("nome", "NOVA LOJA", "RIO DE JANEIRO")
        self.assertEqual(out["nome"], "NOVA LOJA")
        self.assertEqual(out["cidade"], "RIO DE JANEIRO")
        self.assertTrue(out["crc16_valido"])

    def test_definir_txid(self):
        out = self._edit("txid", "pedido99")
        self.assertEqual(out["txid"], "pedido99")
        out = self._edit("txid", "***")
        self.assertEqual(out["txid"], "***")

    def test_txid_invalido(self):
        r = run("txid", self.brcode, "com espaco")
        self.assertEqual(r.returncode, 1)
        self.assertIn("txid invalido", r.stderr)

    def test_edit_texto_mostra_brcode_e_qrcode(self):
        r = run("chave", self.brcode, "novo@exemplo.com")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("BR CODE ALTERADO", r.stdout)
        self.assertIn("QR CODE", r.stdout)
        self.assertIn("pix-qrcode-", r.stdout)
        self.assertIn("novo@exemplo.com", r.stdout)

    def test_cadeia_de_edicoes_acumulativas(self):
        """Duas edicoes seguidas nao podem se sobrescrever."""
        r = run("--json", "nome", self.brcode, "LOJA NOVA", "CAMPINAS")
        brcode = json.loads(r.stdout)["brcode"]
        r = run("--json", "valor", brcode, "77.77")
        out = json.loads(r.stdout)
        self.assertEqual(out["nome"], "LOJA NOVA")
        self.assertEqual(out["cidade"], "CAMPINAS")
        self.assertEqual(out["valor"], "77.77")
        r = run("--json", "chave", out["brcode"], "final@exemplo.com")
        out = json.loads(r.stdout)
        self.assertEqual(
            (out["chave"], out["nome"], out["cidade"], out["valor"], out["txid"]),
            ("final@exemplo.com", "LOJA NOVA", "CAMPINAS", "77.77", "tx001"),
        )
        self.assertTrue(out["crc16_valido"])

    def test_edit_a_partir_do_qrcode_payload(self):
        payload = self.data["qrcode_pix_na_payload"]
        r = run("--json", "chave", payload, "outro@exemplo.com")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["chave"], "outro@exemplo.com")

    def test_edit_saida_e_reaproveitavel(self):
        out = self._edit("chave", "novo@exemplo.com")
        r = run("ler", out["brcode"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("novo@exemplo.com", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
