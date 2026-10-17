"""
Modulo 1: nucleo EMV/Pix (TLV, CRC16-CCITT, BR Code, QR Code payload).

Especificacao: Manual de Padroes para Iniciacao do Pix (Bacen), Anexo Q.
Formato: TLV (Tag-Length-Value) com campo "payload" (ID 62) contendo o
BR Code propriamente dito.
"""

import base64
import re

# --------------------------------------------------------------------------
# Tabela de tags EMV usadas no Pix
# --------------------------------------------------------------------------
TAG = {
    "00": "Payload Format Indicator",
    "01": "Point of Initiation Method",
    "26": "Merchant Account Information (Pix - template com GUI)",
    "27": "Merchant Account Information (template com url)",
    "52": "Merchant Category Code",
    "53": "Transaction Currency",
    "54": "Transaction Amount",
    "58": "Country Code",
    "59": "Beneficiary Name",
    "60": "Beneficiary City",
    "62": "Additional Data Field (payload do Pix)",
    "63": "CRC16",
    "64": "Merchant Account Information (GUI generico)",
    "80": "Postal Code",
    "81": "Additional Data",
    "99": "Payment Initiation Data (URL de copia e cola -servico do PSP)",
}

# Tag 62 -> sub-tags do payload Pix
TAG_PAYLOAD = {
    "00": "Merchant Account Information (GUI)",
    "01": "Point of Initiation Method",
    "02": "Additional Data",
    "03": "TxID (identificador da transacao)",
    "04": "URL do payload",
    "05": "Merchant Account Information (cartao/loja)",
    "51": "Merchant Category Code",
    "52": "Merchant Name",
    "53": "Merchant City",
    "54": "Support Phone",
    "55": "Support URL",
    "56": "Merchant Assigned Info",
    "57": "Additional Data (registro)",
    "58": "Country Code",
    "59": "Beneficiary Name",
    "60": "Beneficiary City",
}

# Tipos de chave Pix aceitos pelo Bacen. "URL" = Pix por link (chave dinamica),
# usado por PSPs que emitem cobranca com a propria URL como chave.
TIPOS_CHAVE = {
    "CPF": r"^\d{11}$",
    "CNPJ": r"^\d{14}$",
    "PHONE": r"^\+55\d{10,11}$",
    "EMAIL": r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    "EVP": r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
    "RAND": r"^[0-9a-fA-F]{32}$",
    "URL": r"^(?:https?://)?[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}"
            r"(?::\d{1,5})?(?:/[A-Za-z0-9\-._~%!$&'()*+,;=:@]*)*$",
}

# GUI (Merchant Account Info) aceito no Pix. O padrao e o do Banco Central.
GUI_PADRAO = "BR.GOV.BCB.PIX"
GUI_RE = re.compile(r"^[A-Za-z0-9.]{8,32}$")

MAX_BRCODE_LEN = 77 * 8
MAX_CRC16 = 4
MIN_TXID = 1
MAX_TXID = 25
MAX_NAME = 25
MAX_CITY = 15


# --------------------------------------------------------------------------
# CRC16-CCITT (polinomio 0x1021, init 0xFFFF) - Anexo Q
# --------------------------------------------------------------------------
def _crc16_table():
    table = []
    for byte in range(256):
        crc = byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
        table.append(crc)
    return table


_CRC_TABLE = _crc16_table()


def crc16(data):
    """CRC16-CCITT-FALSE sobre bytes. Retorna string hex de 4 caracteres."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    crc = 0xFFFF
    for byte in data:
        crc = ((crc << 8) & 0xFFFF) ^ _CRC_TABLE[((crc >> 8) ^ byte) & 0xFF]
    return format(crc, "04X")


# --------------------------------------------------------------------------
# Helpers de valor
# --------------------------------------------------------------------------
def normalize_amount(value):
    """Aceita '10', 10, '10.5', '10,50' -> '10.50' (sempre 2 casas)."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        text = f"{value:.2f}"
    else:
        text = str(value).strip().replace("R$", "").replace(" ", "").strip()
        if "," in text:
            text = text.replace(".", "").replace(",", ".")
    if not re.fullmatch(r"\d+(\.\d{1,2})?", text):
        raise ValueError(f"valor invalido: {value!r}")
    if len(text.split(".")[0]) > 13:
        raise ValueError(f"valor acima do limite Pix: {value!r}")
    return f"{float(text):.2f}"


def detect_key_type(key):
    if key is None or key == "":
        return None
    key = str(key).strip()
    for name, pattern in TIPOS_CHAVE.items():
        if re.match(pattern, key):
            return name
    return None


def validate_key(key):
    key = (key or "").strip()
    key_type = detect_key_type(key)
    if key_type is None:
        raise ValueError(
            f"chave Pix invalida: {key!r}. Tipos aceitos: "
            "CPF, CNPJ, telefone (+55...), email, EVP (UUID), chave aleatoria (32 hex) "
            "ou URL de Pix por link"
        )
    return key_type


def validate_gui(gui):
    gui = (gui or "").strip()
    if not GUI_RE.match(gui):
        raise ValueError(f"GUI (Merchant Account Info) invalido: {gui!r}")
    return gui


def _tlv(tag, value):
    if value is None:
        return b""
    if isinstance(value, str):
        value = value.encode("utf-8")
    length = len(value)
    if length > 9999:
        raise ValueError("valor TLV acima de 9999 bytes")
    return f"{tag}{length:02d}".encode("ascii") + value


def _parse_tlv(data):
    """Retorna [(tag, value_str, inicio, fim)] preservando ordem."""
    out = []
    i = 0
    n = len(data)
    while i < n:
        if i + 4 > n:
            raise ValueError(f"TLV truncado na posicao {i}")
        try:
            tag = data[i:i + 2].decode("ascii")
        except UnicodeDecodeError:
            raise ValueError(f"tag TLV invalida na posicao {i}")
        try:
            length = int(data[i + 2:i + 4])
        except ValueError:
            raise ValueError(f"TLV invalido na posicao {i}")
        start = i + 4
        end = start + length
        if end > n:
            raise ValueError(f"TLV truncado na tag {tag} na posicao {i}")
        try:
            value = data[start:end].decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError(f"valor TLV nao e UTF-8 valido (tag {tag}, posicao {i})")
        out.append((tag, value, i, end))
        i = end
    return out


def _set_tlv(data, tag, new_value):
    """Substitui, remove ou INSERE uma tag preservando a ordem.

    O Pix exige os campos em ordem crescente de tag, com o CRC16 (63) sempre no
    fim. Inserir no fim quebrava essa ordem e alguns validadores recusavam o
    codigo. Quando a tag nao existe, ela entra na posicao correta.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    items = _parse_tlv(data)
    result = b""
    replaced = False
    for t, v, _s, _e in items:
        if t == tag:
            if replaced or new_value is None:
                continue
            result += _tlv(tag, new_value)
            replaced = True
        elif new_value is not None and not replaced and t > tag:
            # posicao onde a nova tag deve entrar
            result += _tlv(tag, new_value)
            replaced = True
            result += _tlv(t, v)
        else:
            result += _tlv(t, v)
    if not replaced and new_value is not None:
        result += _tlv(tag, new_value)
    return result


def _looks_like_tlv(text):
    if isinstance(text, bytes):
        data = text
    else:
        data = str(text).encode("utf-8")
    try:
        items = _parse_tlv(data)
    except (ValueError, UnicodeDecodeError):
        return False
    return bool(items)


def _decode_merchant_account(value):
    """Extrai GUI / chave / URL de um Merchant Account Information (26, 27, 64).

    Existem duas formas na pratica e ambas circulam no mercado:

      aninhada (spec):  26 -> 00 (sub-template) -> [00 GUI, 01 chave]
      plana  (real):    26 -> 00 (GUI direta)    +  25 (URL) ou 01 (chave)

    A forma plana e usada por varios PSPs (ex.: MagentaPay). Suportar apenas a
    forma aninhada fazia o parser recusar codigos reais de producao.
    """
    if isinstance(value, str):
        value = value.encode("utf-8")
    out = {"gui": None, "chave": None, "url": None}
    for tag, val, _s, _e in _parse_tlv(value):
        if tag == "00":
            if _looks_like_tlv(val):
                nested = _decode_merchant_account(val)
                for k, v2 in nested.items():
                    if out[k] is None:
                        out[k] = v2
                continue
            out["gui"] = out["gui"] or val
        elif tag == "01":
            out["chave"] = out["chave"] or val
        elif tag in ("02", "03"):
            out["chave"] = out["chave"] or val
        elif tag in ("25", "26"):
            out["url"] = out["url"] or val
    return out


def _with_crc(body):
    """Acrescenta o campo 63 com o CRC16 recalculado. Sempre devolve str.

    Remove qualquer campo 63 existente antes de acrescentar o novo: sem isso o
    BR Code editado sairia com dois CRCs e os bancos rejeitariam o pagamento.
    """
    if isinstance(body, str):
        body = body.encode("utf-8")
    body = _drop_tag(body, "63")
    return (body + _tlv("63", crc16(body + b"6304"))).decode("utf-8")


def _drop_tag(data, tag):
    """Remove todas as ocorrencias de uma tag, preservando a ordem do resto."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    out = b""
    for t, v, _s, _e in _parse_tlv(data):
        if t != tag:
            out += _tlv(t, v)
    return out


def _check_crc(brcode, items=None):
    """Valida o CRC16 (tag 63), que deve ser o ultimo campo do BR Code."""
    if items is None:
        items = _parse_tlv(brcode.encode("utf-8"))
    if not items or items[-1][0] != "63":
        raise ValueError("BR Code sem campo CRC16 (6304) ou com CRC fora de posicao")
    tag, received, start, end = items[-1]
    if end - start - 4 != 4:
        raise ValueError("campo CRC16 mal formado")
    expected = crc16(brcode[:start] + "6304")
    if received.upper() != expected:
        raise ValueError(f"CRC16 invalido: recebido {received}, esperado {expected}")
    return True


# --------------------------------------------------------------------------
# BR Code
# --------------------------------------------------------------------------
def build_brcode(
    key,
    value=None,
    name=None,
    city=None,
    txid="***",
    country="BR",
    gui=GUI_PADRAO,
    additional_data=None,
    url=None,
):
    """Monta um BR Code (string copia-e-cola) a partir dos dados."""
    gui = validate_gui(gui)
    if not key:
        raise ValueError("chave Pix obrigatoria")
    validate_key(key)

    merchant = _tlv("00", gui) + _tlv("01", key)
    if len(merchant) > 99:
        raise ValueError(
            f"Merchant Account Info tem {len(merchant)} bytes; o limite do spec Pix e 99 "
            "(a chave e a GUI somam ao total)"
        )
    payload_ma = _tlv("00", merchant)

    body = b""
    body += _tlv("00", "01")                       # Payload Format Indicator
    body += _tlv("01", "12" if url else "11")      # 11 = estatica, 12 = dinamica (uso unico)
    body += _tlv("26", payload_ma)                 # Merchant Account Info - Pix
    body += _tlv("52", "0000")                     # Merchant Category Code
    body += _tlv("53", "986")                      # BRL
    body += _tlv("54", normalize_amount(value))
    body += _tlv("58", country)
    body += _tlv("59", name)
    body += _tlv("60", city)
    if additional_data:
        body += _tlv("05", additional_data)

    inner = b""
    inner += _tlv("05", txid)
    inner += _tlv("52", name)
    inner += _tlv("53", city)
    inner += _tlv("54", country)
    body += _tlv("62", inner)
    body += _tlv("99", url)

    brcode = _with_crc(body)
    if isinstance(brcode, bytes):
        brcode = brcode.decode("utf-8")
    if len(brcode) > MAX_BRCODE_LEN:
        raise ValueError(
            f"BR Code com {len(brcode)} caracteres excede o limite de {MAX_BRCODE_LEN}"
        )
    return brcode


def _clean_brcode(value):
    """Normaliza entrada colada pelo usuario.

    Remove apenas espacos nas pontas e quebras de linha. Espacos INTERNOS sao
    preservados: nome e cidade podem legitimamente conter espaco ("SAO PAULO",
    "RIO DE JANEIRO") e remove-los corromperia o TLV.
    """
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return str(value).replace("\r", "").replace("\n", "").replace("\t", "").strip()


def parse_brcode(brcode):
    """Decodifica um BR Code e retorna dicionario com todos os campos."""
    brcode = _clean_brcode(brcode)
    if not brcode:
        raise ValueError("BR Code vazio")
    if not brcode.startswith("000201"):
        raise ValueError("BR Code invalido: nao comeca com 000201 (Payload Format Indicator)")

    items = _parse_tlv(brcode.encode("utf-8"))
    flat = {t: v for t, v, _s, _e in items}

    out = {
        "brcode": brcode,
        "tamanho": len(brcode),
        "payload_format": flat.get("00"),
        "point_of_initiation": flat.get("01"),
        "valor": None,
        "moeda": flat.get("53"),
        "pais": flat.get("58"),
        "nome": flat.get("59"),
        "cidade": flat.get("60"),
        "crc16": flat.get("63"),
        "txid": None,
        "chave": None,
        "gui": None,
        "chave_tipo": None,
        "url": flat.get("99"),
        "raw_tags": {t: v for t, v, _s, _e in items},
    }

    if "54" in flat and flat["54"] not in ("0", "0.00", "0.0"):
        out["valor"] = normalize_amount(flat["54"])

    for ma_tag in ("26", "27", "64"):
        if ma_tag not in flat:
            continue
        decoded = _decode_merchant_account(flat[ma_tag])
        if out["gui"] is None and decoded["gui"]:
            out["gui"] = decoded["gui"]
        if out["chave"] is None and decoded["chave"]:
            out["chave"] = decoded["chave"]
        if out["url"] is None and decoded["url"]:
            out["url"] = decoded["url"]

    # Nao existe tag 01 em codigos baseados em URL: nesse caso a propria URL e a
    # chave que o app do banco abre.
    if not out["chave"] and out["url"]:
        out["chave"] = out["url"]
        out["chave_tipo"] = "URL"

    payload = flat.get("62")
    if payload:
        for t, v, _s, _e in _parse_tlv(payload.encode("utf-8")):
            if t == "05":
                out["txid"] = v
            elif t == "04":
                out["url"] = v

    if out["chave_tipo"] is None and out["chave"]:
        out["chave_tipo"] = detect_key_type(out["chave"])

    out["crc16_valido"] = _check_crc(brcode, items)
    return out


# --------------------------------------------------------------------------
# QR Code (formato "BR Code with payload")
# --------------------------------------------------------------------------
def to_qrcode_payload(brcode):
    """'BR Code with payload' = base64 do BR Code. Campo "Pix na Payload"."""
    if isinstance(brcode, bytes):
        brcode = brcode.decode("utf-8")
    return base64.b64encode(brcode.encode("utf-8")).decode("ascii")


def from_qrcode_payload(payload):
    """Aceita base64 puro, 'pix-qrcode-...' ou 'Pix na Payload: ...'."""
    if not payload:
        raise ValueError("payload vazio")
    text = str(payload)
    # base64 nao contem quebras de linha: usa so a primeira linha nao vazia,
    # senao saidas com varias linhas (ex.: base64 + prefixo) viram lixo.
    for line in text.splitlines():
        if line.strip():
            text = line.strip()
            break
    else:
        raise ValueError("payload vazio")
    text = re.sub(r"^pix-qrcode-", "", text, flags=re.IGNORECASE)
    if text.lower().startswith("pix na payload"):
        text = text.split(":", 1)[1].strip()
    text = text.split()[0]
    text += "=" * (-len(text) % 4)
    try:
        return base64.b64decode(text, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"payload base64 invalido: {exc}")


def parse_any(payload):
    """Detecta automaticamente BR Code ou QR Code e devolve o dicionario parseado."""
    if payload is None:
        raise ValueError("payload vazio")
    text = str(payload)
    # Nao limpar quebras de linha aqui: base64 colado junto de outras linhas
    # precisa preservar a separacao para que a primeira linha possa ser usada.
    if _clean_brcode(text).startswith("000201"):
        return parse_brcode(text)
    return parse_brcode(from_qrcode_payload(text))


# --------------------------------------------------------------------------
# Diagnostico: o valor local (campo 54) e autoritativo neste codigo?
# --------------------------------------------------------------------------
def diagnose_value_authoritativeness(brcode):
    """Diz se da para fixar o valor dentro do proprio codigo.

    Tres campos decidem, conforme o Anexo Q:

      01 = 11  estatico  -> o app le o codigo como esta; o 54 manda
      01 = 12  dinamico  -> o app DEVE consultar a URL (campo 99) e usar o
                            valor do servidor; o 54 local e apenas sugestao
      chave = URL (Pix por link) -> a chave E um endereco web; o app abre a
                            pagina e o valor vem de la
    """
    parsed = parse_brcode(brcode)
    poi = parsed.get("point_of_initiation")
    key_type = parsed.get("chave_tipo")
    has_99 = bool(parsed.get("url") and "99" in parsed.get("raw_tags", {}))
    url_key = key_type == "URL"
    # a unica fonte de valor externo e uma URL. Sem URL em lugar nenhum, o
    # codigo e autossuficiente e o campo 54 e autoritativo, com ou sem campo 01.
    has_url = has_99 or url_key

    reasons = []
    if poi == "11":
        reasons.append("campo 01 = 11 (estatico): o app le o codigo como esta")
    elif poi == "12":
        reasons.append("campo 01 = 12 (dinamico): o app espera buscar a URL")
    elif poi is None:
        reasons.append("campo 01 ausente: codigo fixo, sem URL para consultar")

    if url_key:
        reasons.append("chave do tipo URL: o app abre a pagina e o valor vem de la")
    else:
        reasons.append(f"chave do tipo {key_type}: chave estatica, sem URL para consultar")
    if has_99:
        reasons.append("campo 99 presente: o valor vem da URL do servidor")
    elif not url_key:
        reasons.append("nenhuma URL no codigo: nao ha onde buscar o valor, o 54 manda")

    if not has_url:
        verdict = "MODIFICAVEL"
        level = "confiavel"
    elif poi == "12" or url_key:
        verdict = "INSTAVEL"
        level = "depende do app"
    else:
        verdict = "INDETERMINADO"
        level = "desconhecido"

    return {
        "veredito": verdict,
        "nivel": level,
        "campo_01": poi,
        "chave_tipo": key_type,
        "campo_99_presente": has_99,
        "tem_valor_gravado": parsed["valor"] is not None,
        "valor_atual": parsed["valor"],
        "motivos": reasons,
        "alternativa": (
            "use uma chave estatica (CPF/CNPJ/email/telefone) com 01=11: "
            "aí o campo 54 e autoritativo"
            if has_url
            else None
        ),
    }


# --------------------------------------------------------------------------
# Normalizacao para o formato estrito do Anexo Q
# --------------------------------------------------------------------------
def normalize_brcode(brcode, value=None, txid=None, gui=None, url_in_99=True):
    """Reescreve um BR Code na forma estrita do Anexo Q.

    Codigos de PSP no mercado costumam divergir do padrao (GUI em minusculas,
    URL em sub-tag 25 dentro do template 26, campo 54 ausente em codigo
    dinamico, payload 62 sem nome/cidade). Parsers estritos de alguns
    aplicativos rejeitam essas formas. Aqui o codigo e remontado do zero,
    preservando os dados e recalculando o CRC16.
    """
    parsed = parse_brcode(brcode)
    if not parsed["chave"]:
        raise ValueError("BR Code sem chave para normalizar")

    key = parsed["chave"]
    url = parsed["url"] if key == parsed["url"] else None
    name = (parsed["nome"] or "")[:MAX_NAME]
    city = (parsed["cidade"] or "")[:MAX_CITY]
    if txid is None:
        txid = parsed["txid"] or "***"

    amount = value if value is not None else parsed["valor"]
    return build_brcode(
        key=key,
        value=amount,
        name=name,
        city=city,
        txid=txid,
        gui=gui or GUI_PADRAO,
        url=url if url_in_99 else None,
    )


# --------------------------------------------------------------------------
# Edicao de um BR Code existente
# --------------------------------------------------------------------------
def edit_brcode(brcode, key=None, value=None, name=None, city=None, txid=None):
    """Edita campos de um BR Code existente e recalcula o CRC16."""
    data = _clean_brcode(brcode).encode("utf-8")
    if not data.startswith(b"000201"):
        raise ValueError("BR Code invalido")

    items = _parse_tlv(data)
    flat = {t: v for t, v, _s, _e in items}

    def current_payload():
        """Valor atual do campo 62, ja com as edicoes aplicadas nesta chamada."""
        for t, v, _s, _e in _parse_tlv(data):
            if t == "62":
                return v
        return flat.get("62")

    if key is not None:
        new_key = key.strip()
        validate_key(new_key)
        ma = flat.get("26")
        if not ma:
            raise ValueError("BR Code sem Merchant Account Information (26)")
        ma_val = ma.encode("utf-8")
        if _looks_like_tlv(ma_val) and any(
            t == "00" and _looks_like_tlv(v) for t, v, _s, _e in _parse_tlv(ma_val)
        ):
            ma_template = None
            for t, v, _s, _e in _parse_tlv(ma_val):
                if t == "00":
                    ma_template = v
            if ma_template is None:
                raise ValueError("BR Code sem template de Merchant Account Info (26/00)")
            ma_new = _set_tlv(ma_template.encode("utf-8"), "01", new_key)
            if len(ma_new) > 99:
                raise ValueError(
                    f"Merchant Account Info ficaria com {len(ma_new)} bytes; limite e 99"
                )
            data = _set_tlv(data, "26", _tlv("00", ma_new).decode("utf-8"))
        else:
            ma_new = _set_tlv(ma_val, "01", new_key)
            if len(ma_new) > 99:
                raise ValueError(
                    f"Merchant Account Info ficaria com {len(ma_new)} bytes; limite e 99"
                )
            data = _set_tlv(data, "26", ma_new.decode("utf-8"))

    if value is not None:
        if str(value).strip() in ("", "0", "0.00", "0.0"):
            data = _set_tlv(data, "54", None)
        else:
            data = _set_tlv(data, "54", normalize_amount(value))

    if name is not None:
        name = str(name)[:MAX_NAME]
        data = _set_tlv(data, "59", name)
        if current_payload():
            payload = _set_tlv(current_payload().encode("utf-8"), "52", name)
            data = _set_tlv(data, "62", payload.decode("utf-8"))

    if city is not None:
        city = str(city)[:MAX_CITY]
        data = _set_tlv(data, "60", city)
        if current_payload():
            payload = _set_tlv(current_payload().encode("utf-8"), "53", city)
            data = _set_tlv(data, "62", payload.decode("utf-8"))

    if txid is not None:
        txid = str(txid)
        if txid == "" or txid == "***":
            txid = "***"
        else:
            if not re.fullmatch(r"[A-Za-z0-9]{%d,%d}" % (MIN_TXID, MAX_TXID), txid):
                raise ValueError(
                    f"txid invalido: {txid!r} (use apenas letras/numeros, "
                    f"{MIN_TXID}-{MAX_TXID} caracteres)"
                )
        if not current_payload():
            raise ValueError("BR Code sem campo de payload (62)")
        payload = _set_tlv(current_payload().encode("utf-8"), "05", txid)
        data = _set_tlv(data, "62", payload.decode("utf-8"))

    return _with_crc(data)
