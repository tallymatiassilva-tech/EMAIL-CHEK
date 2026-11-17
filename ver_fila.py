"""Lista a fila de treinamento formatada, com o texto bruto de cada documento.

Uso:
    python ver_fila.py                  # todos os pendentes
    python ver_fila.py --limite 5       # só os 5 mais recentes
    python ver_fila.py --remessa 10084206883004
    python ver_fila.py --tipo GUIA_FISCAL
    python ver_fila.py --todos          # inclui os já resolvidos
    python ver_fila.py --texto 3000     # mostra mais do texto bruto
    python ver_fila.py --json           # saída em JSON (para script)
"""

import argparse
import json
import sys

import database as db


def _carregar(remessa=None, tipo=None, incluir_resolvidos=False, limite=None):
    """Busca as entradas da fila aplicando os filtros pedidos."""
    sql = "SELECT * FROM fila_treinamento WHERE 1=1"
    params = []

    if not incluir_resolvidos:
        sql += " AND resolvido=0"
    if remessa:
        sql += " AND numero_remessa LIKE ?"
        params.append(f"%{remessa}%")
    if tipo:
        sql += " AND tipo_documento = ?"
        params.append(tipo)

    sql += " ORDER BY criado_em DESC, id DESC"
    if limite:
        sql += " LIMIT ?"
        params.append(limite)

    with db.cursor() as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def _formatar(entrada: dict, tamanho_texto: int) -> str:
    """Monta o bloco de exibição de uma entrada."""
    try:
        payload = json.loads(entrada.get("campos_json") or "{}")
    except json.JSONDecodeError:
        payload = {}

    faltando = payload.get("faltando") or []
    campos = payload.get("campos") or {}
    texto = payload.get("texto") or ""

    linhas = []
    linhas.append("=" * 78)
    linhas.append(
        f"ID {entrada['id']} | {entrada['numero_remessa']} | {entrada['tipo_documento']}"
    )
    linhas.append(
        f"Confiança: {entrada.get('confianca') or 0}% | "
        f"Criado em: {entrada.get('criado_em')} | "
        f"Resolvido: {'sim' if entrada.get('resolvido') else 'não'}"
    )
    linhas.append(f"Arquivo: {entrada.get('caminho_arquivo')}")
    linhas.append("")

    if faltando:
        linhas.append(f"FALTANDO ({len(faltando)}): {', '.join(faltando)}")
    else:
        linhas.append("FALTANDO: nenhum campo obrigatório")

    if campos:
        linhas.append("EXTRAÍDO:")
        for k, v in campos.items():
            if v in (None, "", []):
                continue
            valor = str(v)
            if len(valor) > 80:
                valor = valor[:77] + "..."
            linhas.append(f"  {k}: {valor}")

    linhas.append("")
    linhas.append(f"TEXTO BRUTO (primeiros {tamanho_texto} chars):")
    linhas.append("-" * 78)
    if texto:
        recorte = texto[:tamanho_texto]
        linhas.append(recorte)
        if len(texto) > tamanho_texto:
            linhas.append(f"... ({len(texto) - tamanho_texto} chars omitidos)")
    else:
        linhas.append("(sem texto gravado)")
    linhas.append("")

    return "\n".join(linhas)


def _resumo(entradas: list[dict]) -> str:
    """Conta por tipo de documento para dar uma visão geral."""
    if not entradas:
        return "Fila vazia."

    por_tipo = {}
    for e in entradas:
        por_tipo[e["tipo_documento"]] = por_tipo.get(e["tipo_documento"], 0) + 1

    partes = [f"{n} {t}" for t, n in sorted(por_tipo.items(), key=lambda x: -x[1])]
    return f"{len(entradas)} entrada(s): " + ", ".join(partes)


def main():
    parser = argparse.ArgumentParser(
        description="Lista a fila de treinamento formatada."
    )
    parser.add_argument("--remessa", help="Filtra por número de remessa (parcial)")
    parser.add_argument("--tipo", help="Filtra por tipo de documento")
    parser.add_argument("--limite", type=int, help="Máximo de entradas a mostrar")
    parser.add_argument(
        "--todos", action="store_true", help="Inclui entradas já resolvidas"
    )
    parser.add_argument(
        "--texto",
        type=int,
        default=1500,
        help="Quantos caracteres do texto bruto mostrar (padrão: 1500)",
    )
    parser.add_argument(
        "--json", action="store_true", help="Saída em JSON em vez de formatada"
    )
    args = parser.parse_args()

    db.init_db()
    entradas = _carregar(
        remessa=args.remessa,
        tipo=args.tipo,
        incluir_resolvidos=args.todos,
        limite=args.limite,
    )

    if args.json:
        print(json.dumps(entradas, ensure_ascii=False, indent=2, default=str))
        return 0

    if not entradas:
        print("Fila de treinamento vazia.")
        return 0

    print()
    print(_resumo(entradas))
    print()

    for entrada in entradas:
        print(_formatar(entrada, args.texto))

    print("=" * 78)
    print(f"Total exibido: {len(entradas)}")
    print()
    print("Para marcar como resolvido:")
    print("  python resolver_fila.py --id <ID>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
