"""Exportação dos reembolsos conciliados para Excel.

Gera um arquivo .xlsx com:
- Aba "Reembolsos": todos os registros com status, score e valores
- Aba "Divergências": apenas os que precisam de revisão
- Aba "Resumo": contagens por status e por camada
"""

from datetime import datetime
from pathlib import Path

import database as db
import persistence as ps
from config import PASTA_EXPORTACAO, Status
from log_manager import get_logger

log = get_logger("export")

# Status que exigem ação humana
STATUS_CRITICOS = {
    Status.DIV_VALOR,
    Status.DIV_CNPJ,
    Status.DIV_NF,
    Status.DIV_DATA,
    Status.REVISAO,
    Status.AGRUPADO,
}

# Ordem de exibição das colunas
COLUNAS = [
    ("numero_remessa", "Remessa", 16),
    ("numero_nf", "NF", 12),
    ("numero_guia", "Guia", 18),
    ("cliente", "Cliente", 32),
    ("cnpj_cliente", "CNPJ", 20),
    ("uf", "UF", 6),
    ("tributo", "Tributo", 12),
    ("valor_guia", "Valor Guia", 14),
    ("data_emissao", "Emissão", 12),
    ("data_vencimento", "Vencimento", 12),
    ("valor_pago_jadlog", "Pago Jadlog", 14),
    ("data_pagamento_jadlog", "Data Pgto Jadlog", 16),
    ("valor_reembolso_cliente", "Reembolso Cliente", 18),
    ("data_reembolso_cliente", "Data Reembolso", 15),
    ("dias_reembolso", "Dias", 8),
    ("status", "Status", 24),
    ("score", "Score", 8),
    ("data_atualizacao", "Atualizado em", 20),
]


def _linhas_para_excel(registros: list[dict]) -> list[list]:
    """Converte os registros do banco em linhas de planilha."""
    return [
        [r.get(campo) if r.get(campo) is not None else "" for campo, _, _ in COLUNAS]
        for r in registros
    ]


def _escrever_cabecalho(ws, cor: str):
    """Escreve o cabeçalho formatado na primeira linha."""
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    for col_idx, (_, titulo, largura) in enumerate(COLUNAS, 1):
        cell = ws.cell(row=1, column=col_idx, value=titulo)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=cor)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.column_dimensions[get_column_letter(col_idx)].width = largura


def _escrever_dados(ws, registros: list[dict]):
    """Escreve as linhas de dados com formatação de valor."""
    from openpyxl.styles import Alignment

    for row_idx, linha in enumerate(_linhas_para_excel(registros), 2):
        for col_idx, valor in enumerate(linha, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=valor)
            campo = COLUNAS[col_idx - 1][0]
            if campo.startswith("valor_"):
                cell.number_format = "R$ #,##0.00"
            elif campo == "score":
                cell.alignment = Alignment(horizontal="center")


def exportar(caminho_destino: str | Path | None = None) -> Path:
    """Exporta todos os reembolsos para um arquivo Excel.

    Retorna o caminho do arquivo gerado.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font

    if caminho_destino is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        caminho_destino = PASTA_EXPORTACAO / f"reembolsos_{stamp}.xlsx"

    caminho_destino = Path(caminho_destino)
    caminho_destino.parent.mkdir(parents=True, exist_ok=True)

    registros = db.listar()
    if not registros:
        log.warning("Nenhum reembolso no banco para exportar")
        return caminho_destino

    wb = Workbook()

    # ------------------------------------------------------------------
    # ABA 1: REEMBOLSOS
    # ------------------------------------------------------------------
    ws = wb.active
    ws.title = "Reembolsos"
    _escrever_cabecalho(ws, "1F4E78")
    _escrever_dados(ws, registros)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    # ------------------------------------------------------------------
    # ABA 2: DIVERGÊNCIAS
    # ------------------------------------------------------------------
    criticos = [r for r in registros if r.get("status") in STATUS_CRITICOS]
    ws2 = wb.create_sheet("Divergências")
    _escrever_cabecalho(ws2, "C00000")
    _escrever_dados(ws2, criticos)
    ws2.freeze_panes = "A2"
    ws2.auto_filter.ref = ws2.dimensions

    # ------------------------------------------------------------------
    # ABA 3: RESUMO
    # ------------------------------------------------------------------
    ws3 = wb.create_sheet("Resumo")

    ws3["A1"] = "RESUMO DA CONCILIAÇÃO"
    ws3["A1"].font = Font(bold=True, size=14)
    ws3.merge_cells("A1:C1")

    ws3["A3"] = "Total de reembolsos"
    ws3["B3"] = len(registros)

    ws3["A5"] = "Por status"
    ws3["A5"].font = Font(bold=True)
    row = 6
    for status, qtd in sorted(
        ps.estatisticas_por_status().items(), key=lambda x: -x[1]
    ):
        ws3.cell(row=row, column=1, value=status)
        ws3.cell(row=row, column=2, value=qtd)
        row += 1

    row += 1
    ws3.cell(row=row, column=1, value="Por camada").font = Font(bold=True)
    row += 1
    for camada, qtd in sorted(
        ps.estatisticas_por_camada().items(), key=lambda x: -x[1]
    ):
        ws3.cell(row=row, column=1, value=camada)
        ws3.cell(row=row, column=2, value=qtd)
        row += 1

    ws3.column_dimensions["A"].width = 32
    ws3.column_dimensions["B"].width = 12

    wb.save(caminho_destino)
    log.info("Exportado: %s (%d registros)", caminho_destino, len(registros))
    return caminho_destino
