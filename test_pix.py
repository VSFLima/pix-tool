"""Testes do modulo 1: pix_core (TLV, CRC16, BR Code, QR Code)."""

import base64
import sys
import unittest

sys.path.insert(0, "/data/data/com.termux/files/home/pix-tool")

import pix_core as pix


class TestCRC16(unittest.TestCase):
    def test_known_vectors(self):
        # Anexo Q: CRC16 sobre "123456789" = 0x29B1
        self.assertEqual(pix.crc16("123456789"), "29B1")
        self.assertEqual(pix.crc16(""), "FFFF")
        self.assertEqual(pix.crc16("000201"), pix.crc16("000201"))

    def test_deterministic_and_len(self):
        for payload in ("00020126", "BR.GOV.BCB.PIX", "a" * 100):
            value = pix.crc16(payload)
            self.assertEqual(len(value), 4)
            self.assertEqual(value, pix.crc16(payload))
            int(value, 16)  # precisa ser hex valido


class TestAmount(unittest.TestCase):
    def test_normalize(self):
        cases = {
            "10": "10.00",
            10: "10.00",
            10.5: "10.50",
            "10,50": "10.50",
            "R$ 1.234,56": "1234.56",
            "0.05": "0.05",
            0.1: "0.10",
        }
        for raw, expected in cases.items():
            self.assertEqual(pix.normalize_amount(raw), expected, raw)

    def test_rejects_garbage(self):
        for bad in ("abc", "10.999", "-5", "1.2.3", "10,00,00"):
            with self.assertRaises(ValueError):
                pix.normalize_amount(bad)

    def test_none_and_empty(self):
        self.assertIsNone(pix.normalize_amount(None))
        self.assertIsNone(pix.normalize_amount(""))


class TestKeyType(unittest.TestCase):
    def test_detect(self):
        cases = {
            "12345678901": "CPF",
            "12345678901234": "CNPJ",
            "+5511999999999": "PHONE",
            "fulano@example.com": "EMAIL",
            "7b5f0a1e-3c4d-4f5a-8b6c-9d0e1f2a3b4c": "EVP",
            "0" * 32: "RAND",
            "pix.magenpay.io/cob/vwTc_iYWSYe8hsiXfBk4RA": "URL",
            "https://pague.exemplo.com/pix/abc123": "URL",
            "exemplo.com/pix": "URL",
        }
        for key, expected in cases.items():
            self.assertEqual(pix.detect_key_type(key), expected, key)
            self.assertEqual(pix.validate_key(key), expected)

    def test_reject_invalid(self):
        for bad in ("abc", "123", "123456789012345", "z" * 32,
                    "a" * 31, "a" * 33, "+5512", "1234567890",
                    "http://", "sem.tld.x", "a b@c.com", "  "):
            with self.assertRaises(ValueError, msg=bad):
                pix.validate_key(bad)


class TestTLV(unittest.TestCase):
    def test_encode_decode_roundtrip(self):
        merchant = pix._tlv("00", "BR.GOV.BCB.PIX") + pix._tlv("01", "12345678901")
        data = pix._tlv("26", merchant)
        self.assertTrue(data.startswith(b"26"), data[:6])
        self.assertEqual(int(data[2:4]), len(merchant))
        items = pix._parse_tlv(data)
        self.assertEqual([t for t, _v, _s, _e in items], ["26"])
        inner = pix._parse_tlv(items[0][1].encode())
        flat = dict((t, v) for t, v, _s, _e in inner)
        self.assertEqual(flat["00"], "BR.GOV.BCB.PIX")
        self.assertEqual(flat["01"], "12345678901")

    def test_length_is_two_digits(self):
        # valor com 1 digito: "01" + "01" + "X"
        self.assertEqual(pix._tlv("05", "A"), b"0501A")
        self.assertEqual(pix._tlv("05", "A" * 99), b"0599" + b"A" * 99)

    def test_set_replaces_preserving_order(self):
        data = pix._tlv("01", "AAA") + pix._tlv("02", "BBB")
        updated = pix._set_tlv(data, "01", "ZZZ")
        flat = dict((t, v) for t, v, _s, _e in pix._parse_tlv(updated))
        self.assertEqual(flat, {"01": "ZZZ", "02": "BBB"})

    def test_set_removes(self):
        data = pix._tlv("01", "AAA") + pix._tlv("02", "BBB")
        updated = pix._set_tlv(data, "01", None)
        flat = dict((t, v) for t, v, _s, _e in pix._parse_tlv(updated))
        self.assertEqual(flat, {"02": "BBB"})

    def test_truncated_raises(self):
        with self.assertRaises(ValueError):
            pix._parse_tlv(b"2605123456")


class TestBuildAndParse(unittest.TestCase):
    def test_full_roundtrip(self):
        code = pix.build_brcode(
            key="fulano@example.com",
            value="123,45",
            name="FULANO DE TAL",
            city="SAO PAULO",
            txid="pedido123",
        )
        parsed = pix.parse_brcode(code)
        self.assertTrue(parsed["crc16_valido"])
        self.assertEqual(parsed["chave"], "fulano@example.com")
        self.assertEqual(parsed["chave_tipo"], "EMAIL")
        self.assertEqual(parsed["valor"], "123.45")
        self.assertEqual(parsed["nome"], "FULANO DE TAL")
        self.assertEqual(parsed["cidade"], "SAO PAULO")
        self.assertEqual(parsed["txid"], "pedido123")
        self.assertEqual(parsed["gui"], "BR.GOV.BCB.PIX")
        self.assertEqual(parsed["moeda"], "986")
        self.assertEqual(parsed["pais"], "BR")
        self.assertLessEqual(parsed["tamanho"], 77 * 8)

    def test_without_value_means_open(self):
        code = pix.build_brcode(key="12345678901", name="LOJA", city="RECIFE")
        parsed = pix.parse_brcode(code)
        self.assertIsNone(parsed["valor"])
        self.assertEqual(parsed["txid"], "***")
        self.assertEqual(parsed["chave_tipo"], "CPF")

    def test_cnpj_and_evp(self):
        for key, kind in (
            ("11222333000181", "CNPJ"),
            ("7b5f0a1e-3c4d-4f5a-8b6c-9d0e1f2a3b4c", "EVP"),
            ("+5581988887777", "PHONE"),
            ("f" * 32, "RAND"),
        ):
            parsed = pix.parse_brcode(pix.build_brcode(key=key, name="X", city="Y"))
            self.assertEqual(parsed["chave_tipo"], kind)

    def test_crc_is_valid_every_time(self):
        for value in ("0.01", "1.00", "9999.99", "1000000.00"):
            parsed = pix.parse_brcode(
                pix.build_brcode(key="a@b.com", value=value, name="LOJA", city="SP")
            )
            self.assertTrue(parsed["crc16_valido"], value)

    def test_reject_oversized(self):
        with self.assertRaises(ValueError):
            pix.build_brcode(
                key="a@b.com", name="N" * 25, city="C" * 15, txid="X" * 25,
                value="999999999.99", additional_data="D" * 500,
            )

    def test_reject_invalid_gui(self):
        for bad in ("", "curto", "A" * 33, "BR.GOV.BCB PIX com espaco"):
            with self.assertRaises(ValueError, msg=bad):
                pix.validate_gui(bad)
        self.assertEqual(pix.validate_gui("BR.GOV.BCB.PIX"), "BR.GOV.BCB.PIX")

    def test_reject_missing_key(self):
        with self.assertRaises(ValueError):
            pix.build_brcode(key="", name="X", city="Y")


class TestCRCValidation(unittest.TestCase):
    def _tamper(self, code):
        """Troca um caractere dentro do campo 54 (valor), sem recalcular o CRC."""
        start = code.find("5405")
        self.assertNotEqual(start, -1, "campo 54 (valor) nao encontrado")
        pos = start + 4
        replacement = "9" if code[pos] != "9" else "8"
        return code[:pos] + replacement + code[pos + 1:]

    def test_detects_tampering(self):
        code = pix.build_brcode(key="a@b.com", value="10.00", name="LOJA", city="SAO PAULO")
        tampered = self._tamper(code)
        self.assertNotEqual(tampered, code)
        with self.assertRaises(ValueError) as ctx:
            pix.parse_brcode(tampered)
        self.assertIn("CRC16", str(ctx.exception))

    def test_detects_tampering_in_key(self):
        code = pix.build_brcode(key="a@b.com", name="LOJA", city="SAO PAULO")
        tampered = code.replace("a@b.com", "c@d.com", 1)
        with self.assertRaises(ValueError):
            pix.parse_brcode(tampered)

    def test_rejects_crc_out_of_position(self):
        code = pix.build_brcode(key="a@b.com", name="LOJA", city="SP")
        # CRC movido para o meio do BR Code
        broken = code[:6] + code[6:14] + code[-8:-4] + code[14:-8] + code[-4:]
        with self.assertRaises(ValueError):
            pix.parse_brcode(broken)

    def test_rejects_missing_crc(self):
        code = pix.build_brcode(key="a@b.com", name="LOJA", city="SP")
        with self.assertRaises(ValueError):
            pix.parse_brcode(code[:-8])

    def test_rejects_garbage(self):
        for bad in ("", "nao-e-pix", "000201", "0002012605", None):
            with self.assertRaises(ValueError):
                pix.parse_brcode(bad)

    def test_preserves_spaces_in_name_and_city(self):
        """Regressao: espaco interno em nome/cidade nao pode ser removido.

        "SAO PAULO" e "RIO DE JANEIRO" sao os casos mais comuns no Brasil;
        limpar todos os espacos corrompia o TLV e quebrava o CRC.
        """
        for name, city in (
            ("SAO PAULO", "SAO PAULO"),
            ("PADARIA SAO JOSE", "RIO DE JANEIRO"),
            ("MARIA DA CONCEICAO", "BELO HORIZONTE"),
            ("A", "B"),
        ):
            code = pix.build_brcode(key="a@b.com", name=name, city=city)
            parsed = pix.parse_brcode(code)
            self.assertTrue(parsed["crc16_valido"], f"{name} / {city}")
            self.assertEqual(parsed["nome"], name)
            self.assertEqual(parsed["cidade"], city)
            edited = pix.edit_brcode(code, value="1.00", name=name, city=city)
            self.assertEqual(pix.parse_brcode(edited)["nome"], name)
            self.assertEqual(pix.parse_brcode(edited)["cidade"], city)

    def test_strips_only_outer_whitespace(self):
        code = pix.build_brcode(key="a@b.com", name="SAO PAULO", city="SAO PAULO")
        for dirty in (code, f"  {code}  ", f"\n{code}\r\n", f"{code}\n"):
            parsed = pix.parse_brcode(dirty)
            self.assertTrue(parsed["crc16_valido"], repr(dirty[:20]))
            self.assertEqual(parsed["cidade"], "SAO PAULO")


class TestQrCode(unittest.TestCase):
    def test_roundtrip(self):
        code = pix.build_brcode(key="a@b.com", value="25.00", name="PADARIA", city="BELA")
        payload = pix.to_qrcode_payload(code)
        self.assertEqual(pix.from_qrcode_payload(payload), code)
        self.assertEqual(pix.from_qrcode_payload("pix-qrcode-" + payload), code)
        self.assertEqual(pix.from_qrcode_payload("Pix na Payload: " + payload), code)

    def test_parse_any_detects_format(self):
        code = pix.build_brcode(key="a@b.com", value="25.00", name="PADARIA", city="BELA")
        payload = pix.to_qrcode_payload(code)
        self.assertEqual(pix.parse_any(code)["brcode"], code)
        self.assertEqual(pix.parse_any(payload)["brcode"], code)
        self.assertEqual(pix.parse_any("  " + code + "\n ")["brcode"], code)

    def test_matches_hand_written_reference(self):
        """BR Code montado a mao (TLV escrito letra a letra) deve ser lido igual."""
        def tlv(tag, value):
            return f"{tag}{len(value):02d}{value}"

        merchant = tlv("00", "BR.GOV.BCB.PIX") + tlv("01", "fulano@exemplo.com")
        inner = tlv("05", "***") + tlv("52", "FULANO") + tlv("53", "SAO PAULO")
        body = (
            tlv("00", "01")
            + tlv("01", "11")
            + tlv("26", tlv("00", merchant))
            + tlv("52", "0000")
            + tlv("53", "986")
            + tlv("54", "10.00")
            + tlv("58", "BR")
            + tlv("59", "FULANO")
            + tlv("60", "SAO PAULO")
            + tlv("62", inner)
        )
        # CRC16 calculado bit a bit, independente da tabela do modulo
        crc = 0xFFFF
        for byte in (body + "6304").encode("ascii"):
            crc ^= byte << 8
            for _ in range(8):
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
        code = body + tlv("63", f"{crc:04X}")

        parsed = pix.parse_brcode(code)
        self.assertTrue(parsed["crc16_valido"])
        self.assertEqual(parsed["chave"], "fulano@exemplo.com")
        self.assertEqual(parsed["valor"], "10.00")
        self.assertEqual(parsed["txid"], "***")
        self.assertEqual(parsed["point_of_initiation"], "11")

    def test_crc_agrees_with_bitwise_implementation(self):
        """Cross-check: tabela do modulo vs implementacao bit a bit do Anexo Q."""
        def bitwise(text):
            crc = 0xFFFF
            for byte in text.encode("ascii"):
                crc ^= byte << 8
                for _ in range(8):
                    crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
            return f"{crc:04X}"

        for text in ("000201", "00020126580014BR.GOV.BCB.PIX01", "123456789", "SAO PAULO", "x" * 300):
            self.assertEqual(pix.crc16(text), bitwise(text), text)

    def test_rejects_empty_payload(self):
        with self.assertRaises(ValueError):
            pix.from_qrcode_payload("")

    def test_multiline_output_takes_first_line(self):
        """Regressao: saida com base64 + prefixo na mesma chamada quebrava o parse."""
        code = pix.build_brcode(key="a@b.com", value="1.00", name="LOJA", city="SAO PAULO")
        payload = pix.to_qrcode_payload(code)
        combined = f"{payload}\npix-qrcode-{payload}\n"
        self.assertEqual(pix.from_qrcode_payload(combined), code)
        self.assertEqual(pix.parse_any(combined)["valor"], "1.00")
        self.assertEqual(pix.parse_any(f"  {payload}  \npix-qrcode-{payload}")["valor"], "1.00")

    def test_rejects_non_base64(self):
        for bad in ("nao-e-base64!!!", "####", "%%%", "a"):
            with self.assertRaises(ValueError, msg=bad):
                pix.from_qrcode_payload(bad)


class TestEdit(unittest.TestCase):
    def setUp(self):
        self.code = pix.build_brcode(
            key="antigo@exemplo.com", value="10.00", name="NOME ANTIGO",
            city="CIDADE ANTIGA", txid="tx001",
        )

    def parsed(self, code):
        return pix.parse_brcode(code)

    def test_change_key_keeps_everything_else(self):
        edited = pix.edit_brcode(self.code, key="novo@exemplo.com")
        p = self.parsed(edited)
        self.assertEqual(p["chave"], "novo@exemplo.com")
        self.assertEqual(p["valor"], "10.00")
        self.assertEqual(p["nome"], "NOME ANTIGO")
        self.assertEqual(p["txid"], "tx001")
        self.assertTrue(p["crc16_valido"])

    def test_change_key_to_cpf_switches_type(self):
        p = self.parsed(pix.edit_brcode(self.code, key="12345678901"))
        self.assertEqual(p["chave_tipo"], "CPF")
        self.assertEqual(p["chave"], "12345678901")

    def test_change_key_rejects_invalid(self):
        with self.assertRaises(ValueError):
            pix.edit_brcode(self.code, key="nao-e-chave")

    def test_set_value(self):
        for raw, expected in (("1500", "1500.00"), ("99,90", "99.90"), (5, "5.00")):
            p = self.parsed(pix.edit_brcode(self.code, value=raw))
            self.assertEqual(p["valor"], expected)
            self.assertTrue(p["crc16_valido"])

    def test_remove_value_becomes_open(self):
        p = self.parsed(pix.edit_brcode(self.code, value=0))
        self.assertIsNone(p["valor"])
        self.assertTrue(p["crc16_valido"])

    def test_edit_name_and_city_propagate_to_payload(self):
        edited = pix.edit_brcode(self.code, name="NOVO NOME", city="NOVA CIDADE")
        p = self.parsed(edited)
        self.assertEqual(p["nome"], "NOVO NOME")
        self.assertEqual(p["cidade"], "NOVA CIDADE")
        inner = dict(
            (t, v) for t, v, _s, _e in pix._parse_tlv(p["raw_tags"]["62"].encode())
        )
        self.assertEqual(inner["52"], "NOVO NOME")
        self.assertEqual(inner["53"], "NOVA CIDADE")

    def test_edit_txid(self):
        p = self.parsed(pix.edit_brcode(self.code, txid="pedido99"))
        self.assertEqual(p["txid"], "pedido99")
        self.assertEqual(self.parsed(pix.edit_brcode(self.code, txid="***"))["txid"], "***")
        self.assertEqual(self.parsed(pix.edit_brcode(self.code, txid=""))["txid"], "***")

    def test_edit_txid_rejects_invalid(self):
        for bad in ("com espaco", "acentuação", "X" * 26, "X" * 0 + "!"):
            with self.assertRaises(ValueError):
                pix.edit_brcode(self.code, txid=bad)

    def test_edit_all_at_once(self):
        edited = pix.edit_brcode(
            self.code, key="+5511998887777", value="7.77", name="JOAO",
            city="CURITIBA", txid="abc",
        )
        p = self.parsed(edited)
        self.assertEqual(
            (p["chave"], p["valor"], p["nome"], p["cidade"], p["txid"]),
            ("+5511998887777", "7.77", "JOAO", "CURITIBA", "abc"),
        )
        self.assertEqual(p["chave_tipo"], "PHONE")
        self.assertTrue(p["crc16_valido"])

    def test_edit_preserves_length_limits(self):
        p = self.parsed(pix.edit_brcode(self.code, name="N" * 40, city="C" * 40))
        self.assertEqual(len(p["nome"]), 25)
        self.assertEqual(len(p["cidade"]), 15)
        self.assertTrue(p["crc16_valido"])

    def test_edit_output_is_valid_qrcode(self):
        edited = pix.edit_brcode(self.code, key="novo@exemplo.com", value="42.00")
        payload = pix.to_qrcode_payload(edited)
        self.assertEqual(pix.parse_any(payload)["chave"], "novo@exemplo.com")
        self.assertEqual(pix.parse_any(payload)["valor"], "42.00")

    def test_edit_does_not_duplicate_crc(self):
        """Regressao: o CRC antigo precisa ser removido antes de recalcular.

        Sem remover a tag 63 anterior, o BR Code editado saia com dois campos
        '6304' e os aplicativos de banco recusavam o codigo.
        """
        edited = pix.edit_brcode(self.code, key="novo@exemplo.com")
        self.assertEqual(edited.count("6304"), 1, edited)
        self.assertEqual(edited[-8:-4], "6304")
        self.assertTrue(edited.endswith(pix.crc16(edited[:-8] + "6304")))

    def test_repeated_edits_never_accumulate_crc(self):
        code = self.code
        for i in range(10):
            code = pix.edit_brcode(code, key=f"user{i}@exemplo.com", value=f"{i + 1}.00")
            self.assertEqual(code.count("6304"), 1, f"iteracao {i}: {code}")
            self.assertTrue(pix.parse_brcode(code)["crc16_valido"])
        self.assertEqual(pix.parse_brcode(code)["chave"], "user9@exemplo.com")
        self.assertEqual(pix.parse_brcode(code)["valor"], "10.00")

    def test_built_brcode_has_exactly_one_crc(self):
        code = pix.build_brcode(key="a@b.com", name="SAO PAULO", city="SAO PAULO")
        self.assertEqual(code.count("6304"), 1, code)

    def test_edit_rejects_non_brcode(self):
        with self.assertRaises(ValueError):
            pix.edit_brcode("lixo", key="a@b.com")


class TestBuildKeyValidation(unittest.TestCase):
    def test_build_rejects_invalid_key(self):
        for bad in ("nao-e-chave", "", "123", "abc", None, "a" * 33):
            with self.assertRaises(ValueError, msg=repr(bad)):
                pix.build_brcode(key=bad, name="X", city="Y")


class TestRealWorldRobustness(unittest.TestCase):
    """Codigos reais de PSP/bancos tem campos a mais e ordem diferente.

    O parser nao pode quebrar nem recusar esses campos desconhecidos.
    """

    def _with_extra_fields(self, brcode):
        def bitwise(text):
            crc = 0xFFFF
            for byte in text.encode("utf-8"):
                crc ^= byte << 8
                for _ in range(8):
                    crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
            return f"{crc:04X}"

        def tlv(tag, value):
            return f"{tag}{len(value):02d}{value}"

        body = brcode[:-8]
        rebuilt = body + tlv("05", "abc") + tlv("81", "999999") + tlv("80", "54321")
        return rebuilt + "6304" + bitwise(rebuilt + "6304")

    def test_ignores_unknown_fields(self):
        code = pix.build_brcode(key="a@b.com", value="10.00", name="LOJA", city="SAO PAULO")
        parsed = pix.parse_brcode(self._with_extra_fields(code))
        self.assertEqual(parsed["chave"], "a@b.com")
        self.assertEqual(parsed["valor"], "10.00")
        self.assertEqual(parsed["cidade"], "SAO PAULO")
        self.assertTrue(parsed["crc16_valido"])

    def test_dynamic_code_with_url(self):
        code = pix.build_brcode(
            key="a@b.com", value="7.00", name="LOJA", city="RECIFE",
            txid="abc123", url="https://pague.exemplo.com/pix/abc123",
        )
        parsed = pix.parse_brcode(code)
        self.assertTrue(parsed["crc16_valido"])
        self.assertEqual(parsed["point_of_initiation"], "12")
        self.assertEqual(parsed["url"], "https://pague.exemplo.com/pix/abc123")
        self.assertEqual(parsed["txid"], "abc123")
        self.assertEqual(pix.parse_any(pix.to_qrcode_payload(code))["url"], parsed["url"])

    def test_long_but_legal_code(self):
        code = pix.build_brcode(
            key="loja-com-nome-bem-comprido@dominio.com.br",
            value="123456.78",
            name="MERCADINHO DO JOAO BATISTA",
            city="SAO BERNARDO DO CAMPO",
            txid="TX99887766",
        )
        parsed = pix.parse_brcode(code)
        self.assertTrue(parsed["crc16_valido"])
        self.assertLessEqual(parsed["tamanho"], 77 * 8)
        self.assertEqual(pix.parse_any(pix.to_qrcode_payload(code))["brcode"], code)

    def test_edit_preserves_url_and_unknown_fields(self):
        code = pix.build_brcode(
            key="a@b.com", name="LOJA", city="SP", txid="t1",
            url="https://pague.exemplo.com/pix/t1",
        )
        edited = pix.edit_brcode(code, value="50.00", txid="t2")
        parsed = pix.parse_brcode(edited)
        self.assertEqual(parsed["url"], "https://pague.exemplo.com/pix/t1")
        self.assertEqual(parsed["txid"], "t2")
        self.assertEqual(parsed["valor"], "50.00")
        self.assertTrue(parsed["crc16_valido"])
        self.assertEqual(edited.count("6304"), 1)

    def test_fuzz_random_mutations_never_crash(self):
        """Entrada corrompida deve dar ValueError, nunca outra excecao."""
        import random
        rng = random.Random(42)
        code = pix.build_brcode(key="a@b.com", value="10.00", name="SAO PAULO", city="SAO PAULO")
        base = pix.to_qrcode_payload(code)
        for _ in range(400):
            mode = rng.randrange(4)
            if mode == 0:
                data = list(code)
            elif mode == 1:
                data = list(base)
            elif mode == 2:
                data = list(rng.choice([code, base]))
            else:
                data = list(rng.choice([code, base]))
            pos = rng.randrange(len(data))
            data[pos] = rng.choice("0123456789ABCXYZ*@.-+/")
            text = "".join(data)
            try:
                pix.parse_any(text)
            except ValueError:
                pass
            except Exception as exc:  # noqa: BLE001 - este e o teste
                self.fail(f"excecao inesperada ({type(exc).__name__}: {exc}) em {text[:40]!r}")


class TestRealWorldFlatTemplate(unittest.TestCase):
    """Regressao: codigos reais com Merchant Account Info na FORMA PLANA.

    Codigo real de PSP (MagentaPay): campo 26 no formato plano
    (00 = GUI direta + 25 = URL), em vez do sub-template aninhado do spec.
    O parser so aceitava a forma aninhada e recusava este codigo.
    """

    REAL = (
        "00020101021226640014br.gov.bcb.pix2542pix.magenpay.io/cob/vwTc_iYWSYe8hsiXfBk4RA"
        "5204000053039865802BR5925BOAS NOVAS PAGAMENTOS LTD6013FLORIANOPOLIS62070503***63041EA8"
    )

    REAL_A55 = (
        "00020101021226820014br.gov.bcb.pix2560qrcode.a55scd.com.br/v1/"
        "4d1e35e3-53a8-4bb0-9a3f-bafd853b06b75204000053039865802BR5917MARKETSUPRIMENTOS"
        "6008SAOPAULO62070503***63045BA8"
    )

    def test_ler_codigo_real_da_a55(self):
        parsed = pix.parse_brcode(self.REAL_A55)
        self.assertTrue(parsed["crc16_valido"])
        self.assertEqual(parsed["gui"], "br.gov.bcb.pix")
        self.assertEqual(parsed["chave_tipo"], "URL")
        self.assertEqual(parsed["nome"], "MARKETSUPRIMENTOS")
        self.assertEqual(parsed["cidade"], "SAOPAULO")
        self.assertIsNone(parsed["valor"])
        self.assertNotIn("54", parsed["raw_tags"])
        self.assertNotIn("1000", self.REAL_A55)

    def test_setar_valor_em_codigo_da_a55(self):
        edited = pix.edit_brcode(self.REAL_A55, value="0,01")
        parsed = pix.parse_brcode(edited)
        self.assertTrue(parsed["crc16_valido"])
        self.assertEqual(parsed["valor"], "0.01")
        self.assertEqual(parsed["nome"], "MARKETSUPRIMENTOS")
        self.assertEqual(edited.count("6304"), 1)
        self.assertEqual(
            [t for t, _v, _s, _e in pix._parse_tlv(edited.encode())],
            ["00", "01", "26", "52", "53", "54", "58", "59", "60", "62", "63"],
        )

    def test_ler_codigo_real(self):
        parsed = pix.parse_brcode(self.REAL)
        self.assertTrue(parsed["crc16_valido"])
        self.assertEqual(parsed["gui"], "br.gov.bcb.pix")
        self.assertEqual(parsed["chave"], "pix.magenpay.io/cob/vwTc_iYWSYe8hsiXfBk4RA")
        self.assertEqual(parsed["chave_tipo"], "URL")
        self.assertEqual(parsed["url"], "pix.magenpay.io/cob/vwTc_iYWSYe8hsiXfBk4RA")
        self.assertEqual(parsed["nome"], "BOAS NOVAS PAGAMENTOS LTD")
        self.assertEqual(parsed["cidade"], "FLORIANOPOLIS")
        self.assertEqual(parsed["point_of_initiation"], "12")
        self.assertIsNone(parsed["valor"])
        self.assertEqual(parsed["txid"], "***")

    def test_setar_valor_em_codigo_real(self):
        edited = pix.edit_brcode(self.REAL, value="0,01")
        parsed = pix.parse_brcode(edited)
        self.assertTrue(parsed["crc16_valido"])
        self.assertEqual(parsed["valor"], "0.01")
        self.assertEqual(parsed["nome"], "BOAS NOVAS PAGAMENTOS LTD")
        self.assertEqual(parsed["cidade"], "FLORIANOPOLIS")
        self.assertEqual(parsed["chave"], "pix.magenpay.io/cob/vwTc_iYWSYe8hsiXfBk4RA")
        self.assertEqual(parsed["txid"], "***")
        self.assertEqual(edited.count("6304"), 1)
        self.assertEqual(edited[-8:-4], "6304")
        self.assertEqual(
            [t for t, _v, _s, _e in pix._parse_tlv(edited.encode())],
            ["00", "01", "26", "52", "53", "54", "58", "59", "60", "62", "63"],
        )

    def test_crc_do_valor_editado_confere_bit_a_bit(self):
        edited = pix.edit_brcode(self.REAL, value="0,01")
        crc = 0xFFFF
        for byte in (edited[:-8] + "6304").encode("ascii"):
            crc ^= byte << 8
            for _ in range(8):
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
        self.assertEqual(edited[-4:], format(crc, "04X"))

    def test_trocar_chave_em_codigo_real(self):
        edited = pix.edit_brcode(self.REAL, key="novo@exemplo.com")
        parsed = pix.parse_brcode(edited)
        self.assertTrue(parsed["crc16_valido"])
        self.assertEqual(parsed["chave"], "novo@exemplo.com")
        self.assertEqual(parsed["chave_tipo"], "EMAIL")

    def test_editar_todos_os_campos_do_codigo_real(self):
        edited = pix.edit_brcode(
            self.REAL, key="loja@exemplo.com", value="1234.56",
            name="NOVA EMPRESA", city="SAO PAULO", txid="pedido1",
        )
        parsed = pix.parse_brcode(edited)
        self.assertTrue(parsed["crc16_valido"])
        self.assertEqual(
            (parsed["chave"], parsed["valor"], parsed["nome"], parsed["cidade"], parsed["txid"]),
            ("loja@exemplo.com", "1234.56", "NOVA EMPRESA", "SAO PAULO", "pedido1"),
        )
        self.assertLessEqual(parsed["tamanho"], 77 * 8)

    def test_ida_e_volta_pelo_qrcode(self):
        edited = pix.edit_brcode(self.REAL, value="0,01")
        payload = pix.to_qrcode_payload(edited)
        self.assertEqual(pix.parse_any(payload)["valor"], "0.01")
        self.assertEqual(pix.parse_any(pix.to_qrcode_payload(self.REAL))["nome"],
                         "BOAS NOVAS PAGAMENTOS LTD")


class TestTagOrder(unittest.TestCase):
    """Regressao: novos campos precisam entrar em ordem crescente de tag."""

    def test_inserted_tag_lands_in_sorted_position(self):
        code = pix.edit_brcode(self._minimal(), value="10.00")
        tags = [t for t, _v, _s, _e in pix._parse_tlv(code.encode())]
        self.assertEqual(tags, ["00", "01", "26", "52", "53", "54", "58", "59", "60", "62", "63"])
        self.assertEqual(tags, sorted(tags), f"tags fora de ordem: {tags}")
        self.assertEqual(tags[-1], "63")

    def test_real_code_stays_sorted_after_edit(self):
        code = pix.edit_brcode(TestRealWorldFlatTemplate.REAL, value="0,01", name="X")
        tags = [t for t, _v, _s, _e in pix._parse_tlv(code.encode())]
        self.assertEqual(tags, sorted(tags), f"tags fora de ordem: {tags}")
        self.assertTrue(pix.parse_brcode(code)["crc16_valido"])

    def test_replace_keeps_position(self):
        base = pix.edit_brcode(self._minimal(), value="10.00")
        changed = pix.edit_brcode(base, value="99.99")
        self.assertEqual(
            [t for t, _v, _s, _e in pix._parse_tlv(changed.encode())],
            [t for t, _v, _s, _e in pix._parse_tlv(base.encode())],
        )
        self.assertEqual(pix.parse_brcode(changed)["valor"], "99.99")

    @staticmethod
    def _minimal():
        return pix.build_brcode(key="a@b.com", name="LOJA", city="SAO PAULO")


class TestNormalize(unittest.TestCase):
    """Normalizador: reescreve codigos de PSP na forma estrita do Anexo Q."""

    def setUp(self):
        self.real = TestRealWorldFlatTemplate.REAL

    def test_normaliza_codigo_real_de_psp(self):
        out = pix.normalize_brcode(self.real, value="0,01")
        parsed = pix.parse_brcode(out)
        self.assertTrue(parsed["crc16_valido"])
        self.assertEqual(parsed["gui"], "BR.GOV.BCB.PIX")
        self.assertEqual(parsed["valor"], "0.01")
        self.assertEqual(parsed["nome"], "BOAS NOVAS PAGAMENTOS LTD")
        self.assertEqual(parsed["cidade"], "FLORIANOPOLIS")
        self.assertEqual(parsed["chave"], "pix.magenpay.io/cob/vwTc_iYWSYe8hsiXfBk4RA")
        self.assertEqual(parsed["url"], "pix.magenpay.io/cob/vwTc_iYWSYe8hsiXfBk4RA")
        self.assertEqual(parsed["point_of_initiation"], "12")
        self.assertLessEqual(parsed["tamanho"], 77 * 8)

    def test_normalizado_usa_forma_aninhada_do_26(self):
        out = pix.normalize_brcode(self.real, value="0,01")
        ma = pix.parse_brcode(out)["raw_tags"]["26"]
        items = pix._parse_tlv(ma.encode())
        self.assertEqual([t for t, _v, _s, _e in items], ["00"])
        sub = pix._parse_tlv(items[0][1].encode())
        self.assertEqual([t for t, _v, _s, _e in sub], ["00", "01"])
        self.assertEqual(dict((t, v) for t, v, _s, _e in sub)["00"], "BR.GOV.BCB.PIX")

    def test_normalizado_tem_54_62_completo_e_99(self):
        out = pix.normalize_brcode(self.real, value="0,01")
        tags = [t for t, _v, _s, _e in pix._parse_tlv(out.encode())]
        # o CRC16 (63) e sempre o ultimo; os demais devem estar em ordem crescente
        self.assertEqual(tags[-1], "63")
        self.assertEqual(tags[:-1], sorted(tags[:-1]), f"tags fora de ordem: {tags}")
        for required in ("54", "58", "59", "60", "62", "99"):
            self.assertIn(required, tags, f"falta o campo {required}")
        inner = dict(
            (t, v) for t, v, _s, _e
            in pix._parse_tlv(pix.parse_brcode(out)["raw_tags"]["62"].encode())
        )
        self.assertEqual(inner["52"], "BOAS NOVAS PAGAMENTOS LTD")
        self.assertEqual(inner["53"], "FLORIANOPOLIS")
        self.assertEqual(inner["05"], "***")

    def test_normalizado_tem_um_unico_crc(self):
        out = pix.normalize_brcode(self.real, value="0,01")
        self.assertEqual(out.count("6304"), 1)
        self.assertTrue(out.endswith(pix.crc16(out[:-8] + "6304")))

    def test_normalizar_e_idempotente(self):
        once = pix.normalize_brcode(self.real, value="0,01")
        twice = pix.normalize_brcode(once, value="0,01")
        self.assertEqual(once, twice)

    def test_normaliza_codigo_ja_estrito(self):
        code = pix.build_brcode(key="a@b.com", value="5.00", name="LOJA", city="SAO PAULO")
        self.assertEqual(pix.normalize_brcode(code), code)

    def test_normalizar_codigo_estatico_nao_inventa_url(self):
        code = pix.build_brcode(key="a@b.com", value="5.00", name="LOJA", city="SAO PAULO")
        out = pix.normalize_brcode(code)
        self.assertNotIn("99", pix.parse_brcode(out)["raw_tags"])
        self.assertEqual(pix.parse_brcode(out)["point_of_initiation"], "11")

    def test_normalizar_preserva_txid_e_aceita_troca(self):
        out = pix.normalize_brcode(self.real, value="1.00", txid="pedido9")
        self.assertEqual(pix.parse_brcode(out)["txid"], "pedido9")

    def test_normalizar_rejeita_lixo(self):
        for bad in ("", "nao-e-pix", "0002012605"):
            with self.assertRaises(ValueError, msg=bad):
                pix.normalize_brcode(bad, value="1.00")


class TestDiagnose(unittest.TestCase):
    """Classifica se o campo 54 e autoritativo em cada tipo de estrutura."""

    def test_estatico_com_chave_estatica_e_modificavel(self):
        for key in ("12345678901", "11222333000181", "a@b.com", "+5511999999999", "0" * 32):
            code = pix.build_brcode(key=key, name="LOJA", city="SAO PAULO")
            diag = pix.diagnose_value_authoritativeness(code)
            self.assertEqual(diag["veredito"], "MODIFICAVEL", key)
            self.assertEqual(diag["nivel"], "confiavel")
            self.assertIsNone(diag["alternativa"])
            self.assertTrue(pix.parse_brcode(
                pix.edit_brcode(code, value="0,01"))["crc16_valido"])

    def test_dinamico_com_url_no_99_nao_e_modificavel(self):
        code = pix.build_brcode(
            key="a@b.com", name="LOJA", city="SP", txid="t1",
            url="https://pague.exemplo.com/pix/t1",
        )
        diag = pix.diagnose_value_authoritativeness(code)
        self.assertEqual(diag["veredito"], "INSTAVEL")
        self.assertTrue(diag["campo_99_presente"])
        self.assertIn("alternativa", diag)
        self.assertTrue(diag["alternativa"])

    def test_chave_url_e_instavel_mesmo_sem_99(self):
        for real in (TestRealWorldFlatTemplate.REAL, TestRealWorldFlatTemplate.REAL_A55):
            diag = pix.diagnose_value_authoritativeness(real)
            self.assertEqual(diag["veredito"], "INSTAVEL", real[:30])
            self.assertEqual(diag["chave_tipo"], "URL")
            self.assertEqual(diag["nivel"], "depende do app")
            self.assertFalse(diag["tem_valor_gravado"])

    def test_normalizar_url_para_estatico_nao_resolve_o_valor(self):
        """Achado importante: trocar 01=12 para 01=11 NAO torna o codigo confiavel.

        O campo 01 vira estatico e o 99 some, mas a chave continua sendo uma
        URL, e o app ainda pode abrir a pagina. So uma chave de verdade
        (CPF/CNPJ/email/telefone) torna o 54 autoritativo.
        """
        out = pix.normalize_brcode(TestRealWorldFlatTemplate.REAL, value="0,01",
                                   url_in_99=False)
        diag = pix.diagnose_value_authoritativeness(out)
        self.assertEqual(diag["campo_01"], "11")
        self.assertFalse(diag["campo_99_presente"])
        self.assertEqual(diag["chave_tipo"], "URL")
        self.assertEqual(diag["veredito"], "INSTAVEL", "chave URL continua instavel")
        self.assertTrue(diag["alternativa"])
        self.assertEqual(pix.parse_brcode(out)["valor"], "0.01")

    def test_diagnostico_relata_valor_gravado(self):
        code = pix.build_brcode(key="a@b.com", value="42.42", name="L", city="SP")
        diag = pix.diagnose_value_authoritativeness(code)
        self.assertTrue(diag["tem_valor_gravado"])
        self.assertEqual(diag["valor_atual"], "42.42")

    def test_codigo_sem_campo_01_e_chave_estatica_e_confiavel(self):
        """Achado: a ausencia de URL e o que importa, nao a ausencia do campo 01.

        muito codigo de chave fixa omite o campo 01 mesmo assim. Sem URL em
        lugar nenhum, o codigo e autossuficiente e o 54 e autoritativo.
        """
        code = (
            "00020126580014br.gov.bcb.pix0136c8a8c5e9-236b-4334-a2c5-7223fc45f1cf"
            "520400005303986540530.005802BR5925MARCUS VINICIUS DE JESUS "
            "60085500016162070503***6304D134"
        )
        parsed = pix.parse_brcode(code)
        self.assertIsNone(parsed["point_of_initiation"])
        self.assertEqual(parsed["chave_tipo"], "EVP")
        self.assertEqual(parsed["valor"], "30.00")
        self.assertTrue(parsed["crc16_valido"])
        diag = pix.diagnose_value_authoritativeness(code)
        self.assertEqual(diag["veredito"], "MODIFICAVEL")
        self.assertEqual(diag["nivel"], "confiavel")
        self.assertIsNone(diag["alternativa"])
        self.assertFalse(diag["campo_99_presente"])

    def test_valor_desse_codigo_pode_ser_trocado(self):
        code = (
            "00020126580014br.gov.bcb.pix0136c8a8c5e9-236b-4334-a2c5-7223fc45f1cf"
            "520400005303986540530.005802BR5925MARCUS VINICIUS DE JESUS "
            "60085500016162070503***6304D134"
        )
        edited = pix.edit_brcode(code, value="0,01")
        parsed = pix.parse_brcode(edited)
        self.assertTrue(parsed["crc16_valido"])
        self.assertEqual(parsed["valor"], "0.01")
        self.assertEqual(parsed["chave"], "c8a8c5e9-236b-4334-a2c5-7223fc45f1cf")
        self.assertEqual(parsed["nome"], "MARCUS VINICIUS DE JESUS ")
        self.assertEqual(edited.count("6304"), 1)
        self.assertEqual(
            [t for t, _v, _s, _e in pix._parse_tlv(edited.encode())],
            ["00", "26", "52", "53", "54", "58", "59", "60", "62", "63"],
        )

    def test_diagnostico_rejeita_lixo(self):
        for bad in ("", "nao-e-pix", "0002012605"):
            with self.assertRaises(ValueError, msg=bad):
                pix.diagnose_value_authoritativeness(bad)


if __name__ == "__main__":
    unittest.main(verbosity=2)
