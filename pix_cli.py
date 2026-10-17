#!/usr/bin/env python3
"""
Modulo 2: CLI do pix-tool.

  ger      -> gera BR Code e QR Code (BR Code with payload)
  ler      -> le e decodifica um BR Code ou QR Code
  chave    -> troca a chave Pix de um codigo existente
  valor    -> define/remove o valor
  nome     -> define nome e cidade
  txid     -> define o identificador da transacao
  qrcode   -> converte BR Code <-> base64
"""

import argparse
import json
import sys

import pix_core as pix


def _read_payload(source):
    if source is None or source == "-":
        return sys.stdin.read()
    if source.startswith("@"):
        with open(source[1:], "r", encoding="utf-8") as fh:
            return fh.read()
    return source


def _to_brcode(source):
    """Aceita BR Code, base64 ou 'pix-qrcode-...' e devolve sempre o BR Code."""
    text = pix._clean_brcode(_read_payload(source))
    if text.startswith("000201"):
        return text
    return pix.from_qrcode_payload(text)


def _emit(result, as_json=False):
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print("=" * 60)
    for label, value in result["campos"]:
        print(f"{label:>18}: {value}")
    print("=" * 60)


def _describe(parsed, extra_lines=None):
    tipo = parsed["chave_tipo"] or "?"
    valor = parsed["valor"] or "(livre - usuario digita o valor)"
    campos = [
        ("Chave", parsed["chave"] or "-"),
        ("Tipo da chave", tipo),
        ("Nome", parsed["nome"] or "-"),
        ("Cidade", parsed["cidade"] or "-"),
        ("Valor", f"R$ {valor}" if parsed["valor"] else valor),
        ("txid", parsed["txid"] or "-"),
        ("Pais", parsed["pais"] or "-"),
        ("Moeda", parsed["moeda"] or "-"),
        ("CRC16", f"{parsed['crc16']} (valido)"),
        ("Tamanho", f"{parsed['tamanho']} de {pix.MAX_BRCODE_LEN} caracteres"),
    ]
    if parsed.get("url"):
        campos.append(("URL", parsed["url"]))
    for label, value in (extra_lines or []):
        campos.append((label, value))
    return {"campos": campos, "brcode": parsed["brcode"]}


# --------------------------------------------------------------------------
def cmd_gerar(args):
    code = pix.build_brcode(
        key=args.chave,
        value=args.valor,
        name=args.nome,
        city=args.cidade,
        txid=args.txid,
        url=args.url,
    )
    payload = pix.to_qrcode_payload(code)
    if args.json:
        print(json.dumps(
            {
                "chave": args.chave,
                "tipo_chave": pix.detect_key_type(args.chave),
                "valor": pix.parse_brcode(code)["valor"],
                "nome": args.nome,
                "cidade": args.cidade,
                "txid": args.txid,
                "brcode": code,
                "qrcode_payload": payload,
                "qrcode_pix_na_payload": f"pix-qrcode-{payload}",
            },
            ensure_ascii=False,
            indent=2,
        ))
        return
    print("\n--- BR CODE (copia e cola) ---")
    print(code)
    print("\n--- QR CODE / PIX NA PAYLOAD ---")
    print(payload)
    print(f"\nPrefixo: pix-qrcode-{payload}")
    print(f"\nNome: {args.nome}   |   Cidade: {args.cidade}   |   txid: {args.txid}")
    print(f"Valor: {'R$ ' + pix.parse_brcode(code)['valor'] if pix.parse_brcode(code)['valor'] else '(livre)'}")


def cmd_ler(args):
    parsed = pix.parse_any(_read_payload(args.codigo))
    if args.json:
        public = {k: v for k, v in parsed.items() if k != "raw_tags"}
        print(json.dumps(public, ensure_ascii=False, indent=2))
        return
    extra = None
    if not args.brcode:
        extra = [("BR CODE", parsed["brcode"])]
    _emit(_describe(parsed, extra), as_json=False)


def cmd_qrcode(args):
    if args.para == "qr":
        payload = pix.to_qrcode_payload(_to_brcode(args.codigo))
        # imprime so uma linha: saida multi-linha quebrava quem copias e cola
        print(f"pix-qrcode-{payload}" if args.com_prefixo else payload)
    else:
        print(pix.from_qrcode_payload(_read_payload(args.codigo)))


def _edit(args, **changes):
    edited = pix.edit_brcode(_to_brcode(args.codigo), **changes)
    parsed = pix.parse_brcode(edited)
    payload = pix.to_qrcode_payload(edited)
    if args.json:
        public = {k: v for k, v in parsed.items() if k != "raw_tags"}
        public["qrcode_payload"] = payload
        print(json.dumps(public, ensure_ascii=False, indent=2))
        return
    print("\n--- BR CODE ALTERADO (copia e cola) ---")
    print(edited)
    print("\n--- QR CODE / PIX NA PAYLOAD ---")
    print(payload)
    print(f"\nPrefixo: pix-qrcode-{payload}")
    _emit(_describe(parsed))


def cmd_chave(args):
    _edit(args, key=args.nova)


def cmd_valor(args):
    _edit(args, value=args.valor)


def cmd_nome(args):
    _edit(args, name=args.nome, city=args.cidade)


def cmd_txid(args):
    _edit(args, txid=args.txid)


def cmd_normalizar(args):
    code = _to_brcode(args.codigo)
    normalized = pix.normalize_brcode(
        code, value=args.valor, txid=args.txid,
        url_in_99=not args.sem_url_99,
    )
    parsed = pix.parse_brcode(normalized)
    payload = pix.to_qrcode_payload(normalized)
    if args.json:
        public = {k: v for k, v in parsed.items() if k != "raw_tags"}
        public["brcode_original"] = code
        public["qrcode_payload"] = payload
        print(json.dumps(public, ensure_ascii=False, indent=2))
        return
    print("\n--- ORIGINAL ---")
    print(code)
    print("\n--- NORMALIZADO (BR Code copia e cola) ---")
    print(normalized)
    print("\n--- QR CODE / PIX NA PAYLOAD ---")
    print(payload)
    _emit(_describe(parsed))


def cmd_diagnostico(args):
    parsed = pix.parse_any(_read_payload(args.codigo))
    diag = pix.diagnose_value_authoritativeness(parsed["brcode"])
    if args.json:
        diag["brcode"] = parsed["brcode"]
        print(json.dumps(diag, ensure_ascii=False, indent=2))
        return
    print(f"\n  VEREDITO: {diag['veredito']}  (confiabilidade: {diag['nivel']})")
    print(f"\n  campo 01 (estatico/dinamico) : {diag['campo_01']}")
    print(f"  tipo da chave                : {diag['chave_tipo']}")
    print(f"  campo 99 (URL do servidor)   : {'presente' if diag['campo_99_presente'] else 'ausente'}")
    print(f"  valor gravado no codigo      : {diag['valor_atual'] or '(nenhum - usuario digita)'}")
    print("\n  Por que:")
    for reason in diag["motivos"]:
        print(f"    - {reason}")
    if diag["alternativa"]:
        print(f"\n  Como resolver: {diag['alternativa']}")
    print()


def build_parser():
    p = argparse.ArgumentParser(
        prog="pix",
        description="Gerador, leitor e editor de BR Code / QR Code Pix.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--json", action="store_true", help="saida em JSON")
    sub = p.add_subparsers(dest="comando", required=True)
    g = sub.add_parser("gerar", help="cria BR Code + QR Code a partir da chave")
    g.add_argument("chave")
    g.add_argument("-v", "--valor", default=None)
    g.add_argument("-n", "--nome", default=None)
    g.add_argument("-c", "--cidade", default=None)
    g.add_argument("-t", "--txid", default="***")
    g.add_argument("--url", default=None)
    g.set_defaults(func=cmd_gerar)

    l = sub.add_parser("ler", help="decodifica um BR Code ou QR Code")
    l.add_argument("codigo", help="BR Code, base64, 'pix-qrcode-...' ou @arquivo / '-'")
    l.add_argument("--brcode", action="store_true", help="nao mostra o BR Code no relatorio")
    l.set_defaults(func=cmd_ler)

    q = sub.add_parser("qrcode", help="converte entre BR Code e base64")
    q.add_argument("para", choices=["qr", "brcode"])
    q.add_argument("codigo", help="ou @arquivo / '-' para stdin")
    q.add_argument("--com-prefixo", action="store_true")
    q.set_defaults(func=cmd_qrcode)

    c = sub.add_parser("chave", help="troca a chave Pix")
    c.add_argument("codigo", help="BR Code, ou @arquivo / '-'")
    c.add_argument("nova")
    c.set_defaults(func=cmd_chave)

    v = sub.add_parser("valor", help="define ou remove o valor")
    v.add_argument("codigo", help="BR Code, ou @arquivo / '-'")
    v.add_argument("valor", help="valor, ou 0 para deixar livre")
    v.set_defaults(func=cmd_valor)

    n = sub.add_parser("nome", help="define nome e cidade")
    n.add_argument("codigo", help="BR Code, ou @arquivo / '-'")
    n.add_argument("nome")
    n.add_argument("cidade")
    n.set_defaults(func=cmd_nome)

    t = sub.add_parser("txid", help="define o identificador da transacao")
    t.add_argument("codigo", help="BR Code, ou @arquivo / '-'")
    t.add_argument("txid")
    t.set_defaults(func=cmd_txid)

    n = sub.add_parser(
        "normalizar",
        help="reescreve o codigo na forma estrita do Anexo Q (para apps rigorosos)",
    )
    n.add_argument("codigo", help="BR Code, ou @arquivo / '-'")
    n.add_argument("-v", "--valor", default=None, help="valor a fixar (ex.: 0,01)")
    n.add_argument("-t", "--txid", default=None)
    n.add_argument("--sem-url-99", action="store_true",
                   help="nao coloca a URL no campo 99 (Payment Initiation Data)")
    n.set_defaults(func=cmd_normalizar)

    d = sub.add_parser(
        "diagnostico",
        help="diz se o valor pode ser fixado dentro do codigo",
    )
    d.add_argument("codigo", help="BR Code, QR Code, ou @arquivo / '-'")
    d.set_defaults(func=cmd_diagnostico)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except ValueError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"ERRO de arquivo: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
