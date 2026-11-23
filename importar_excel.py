from __future__ import annotations

import argparse
import glob
import os
import re
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")
JT_API_URL = os.getenv("JT_API_URL", "https://jt-api.rockettechbr.com.br").rstrip("/")
JT_API_KEY = os.getenv("JT_API_KEY", "").strip()
RM_ALVO = "ORLEAN DA SILVA DO NASCIMENTO"
BATCH_SIZE = 500


def norm_text(value: Any) -> str:
    if pd.isna(value):
        return ""
    value = unicodedata.normalize("NFKD", str(value).strip())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"\s+", " ", value).upper()
    # Alguns relatórios prefixam o nome da base com "F ".
    return value[2:].strip() if value.startswith("F ") else value


def norm_code(value: Any) -> str:
    if pd.isna(value):
        return ""
    s = str(value).strip()
    return s[:-2] if s.endswith(".0") else s


def parse_excel_date(value: Any) -> str | None:
    if pd.isna(value) or str(value).strip() == "":
        return None
    if isinstance(value, (int, float)) and 20000 <= float(value) <= 80000:
        parsed = datetime(1899, 12, 30) + timedelta(days=float(value))
    else:
        parsed = pd.to_datetime(value, errors="coerce", dayfirst=True)
        if pd.isna(parsed):
            return None
        parsed = parsed.to_pydatetime()
    return parsed.date().isoformat()


def parse_money(value: Any) -> float:
    if pd.isna(value) or str(value).strip() == "":
        return 0.0
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    text = re.sub(r"[^0-9,.-]", "", str(value).strip())
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return round(float(text), 2)
    except ValueError:
        return 0.0


def pick_file(folder: Path, explicit: str | None, patterns: list[str], label: str) -> Path:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Arquivo {label} não encontrado: {path}")
        return path
    matches: list[Path] = []
    for pattern in patterns:
        matches.extend(Path(p) for p in glob.glob(str(folder / pattern)))
    matches = [p for p in matches if p.is_file() and not p.name.startswith("~$")]
    if not matches:
        raise FileNotFoundError(f"Nenhum arquivo de {label} encontrado em {folder}")
    return max(matches, key=lambda p: p.stat().st_mtime)


def read_gestao(path: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], int]:
    xls = pd.ExcelFile(path, engine="openpyxl")
    sheet = next((s for s in xls.sheet_names if norm_text(s) == "RESPONSAVEIS ATUAIS"), xls.sheet_names[0])
    df = pd.read_excel(path, sheet_name=sheet, engine="openpyxl")
    required = ["Nome da base", "Estação No.", "Responsável da Região", "Coordenador", "Supervisor"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Gestão/{sheet}: faltam colunas {missing}")

    status_col = df["Status atual"] if "Status atual" in df.columns else pd.Series("", index=df.index)
    bases: list[dict[str, Any]] = []
    name_codes: dict[str, list[dict[str, Any]]] = {}
    for i, row in df.iterrows():
        code = norm_code(row["Estação No."])
        name = str(row["Nome da base"]).strip() if pd.notna(row["Nome da base"]) else ""
        rm = str(row["Responsável da Região"]).strip() if pd.notna(row["Responsável da Região"]) else ""
        if not code or not name or RM_ALVO not in norm_text(rm):
            continue
        coord = str(row["Coordenador"]).strip() if pd.notna(row["Coordenador"]) else ""
        supervisor = str(row["Supervisor"]).strip() if pd.notna(row["Supervisor"]) else ""
        status = str(status_col.loc[i]).strip() if pd.notna(status_col.loc[i]) else ""
        ativo = not any(word in norm_text(status) for word in ("INATIV", "DESATIV", "ENCERRAD"))
        regional = str(row["Regional"]).strip() if "Regional" in df.columns and pd.notna(row.get("Regional")) else "ORLEAN"
        base = {"codigo_base": code, "base": name, "regional": regional, "rm": RM_ALVO,
                "sc": coord or supervisor or "Sem S/C", "ativo": ativo}
        bases.append(base)
        name_codes.setdefault(norm_text(name), []).append(base)

    # Garante uma linha de gestão por código; nomes repetidos entre códigos são ambíguos e ficam sem match.
    unique_by_code: dict[str, dict[str, Any]] = {}
    for base in bases:
        current = unique_by_code.get(base["codigo_base"])
        if current is None or (base["ativo"] and not current["ativo"]):
            unique_by_code[base["codigo_base"]] = base
    bases = list(unique_by_code.values())
    by_code = {b["codigo_base"]: b for b in bases}
    by_name: dict[str, dict[str, Any]] = {}
    ambiguous = 0
    for name, rows in name_codes.items():
        unique = {x["codigo_base"]: by_code[x["codigo_base"]] for x in rows if x["codigo_base"] in by_code}
        if len(unique) == 1:
            by_name[name] = next(iter(unique.values()))
        elif len(unique) > 1:
            ambiguous += 1
    return bases, by_name, ambiguous


def read_report(path: Path, by_name: dict[str, dict[str, Any]], by_code: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], int, int]:
    df = pd.read_excel(path, sheet_name=0, header=0, engine="openpyxl")
    if len(df.columns) < 10:
        raise ValueError(f"{path.name}: esperava ao menos 10 colunas (A até J); encontrei {len(df.columns)}")

    rows: dict[str, dict[str, Any]] = {}
    unmatched = 0
    invalid = 0
    for _, row in df.iterrows():
        remessa = str(row.iloc[0]).strip() if pd.notna(row.iloc[0]) else ""
        remessa = remessa[:-2] if remessa.endswith(".0") else remessa
        if not remessa or remessa.lower() in ("nan", "none"):
            continue
        data_envio = parse_excel_date(row.iloc[2])
        base_sender = str(row.iloc[4]).strip() if pd.notna(row.iloc[4]) else ""
        base = by_name.get(norm_text(base_sender)) or by_code.get(norm_code(base_sender))
        if not data_envio:
            invalid += 1
            continue
        if not base or not base["ativo"]:
            unmatched += 1
            continue
        rows[remessa] = {
            "remessa": remessa,
            "data_envio": data_envio,
            "base_key": base["codigo_base"],
            "base_remetente": base_sender,
            "frete_total": parse_money(row.iloc[9]),
            "rm": RM_ALVO,
            "sc": base["sc"],
        }
    return list(rows.values()), unmatched, invalid


def api_import(table: str, key: list[str], records: list[dict[str, Any]], create: bool) -> None:
    if not JT_API_KEY:
        raise RuntimeError("JT_API_KEY não configurada. Crie um arquivo .env com sua chave da JT API.")
    url = f"{JT_API_URL}/import"
    headers = {"X-API-Key": JT_API_KEY, "Content-Type": "application/json"}
    for start in range(0, len(records), BATCH_SIZE):
        batch = records[start:start + BATCH_SIZE]
        payload = {"tabela": table, "chave": key, "registros": batch,
                   "criar_tabela": create, "adicionar_colunas": create}
        response = requests.post(url, json=payload, headers=headers, timeout=(15, 120))
        if not response.ok:
            raise RuntimeError(f"Falha ao importar {table}, lote {start+1}: HTTP {response.status_code} {response.text[:700]}")
        print(f"  {table}: {min(start+len(batch), len(records)):,}/{len(records):,} enviados")


def main() -> None:
    parser = argparse.ArgumentParser(description="Importa diariamente o relatório C2C e a Gestão para a JT API.")
    parser.add_argument("--entrada", default=".", help="Pasta com os arquivos Excel do dia")
    parser.add_argument("--relatorio", help="Caminho explícito para o export JMS")
    parser.add_argument("--gestao", help="Caminho explícito para Gestão de colaboradores")
    parser.add_argument("--dry-run", action="store_true", help="Valida e mostra resumo sem enviar dados")
    args = parser.parse_args()
    folder = Path(args.entrada).expanduser().resolve()
    report = pick_file(folder, args.relatorio, ["Exportar pedido JMS de envio*.xlsx", "*pedido*JMS*envio*.xlsx"], "relatório JMS C2C")
    gestao_file = pick_file(folder, args.gestao, ["Gestão de colaboradores*.xlsx", "Gestao de colaboradores*.xlsx", "*Responsáveis atuais*.xlsx", "*Responsaveis atuais*.xlsx"], "Gestão de colaboradores")

    bases, by_name, ambiguous = read_gestao(gestao_file)
    by_code = {b["codigo_base"]: b for b in bases}
    remessas, unmatched, invalid = read_report(report, by_name, by_code)
    active = [b for b in bases if b["ativo"]]
    if not active:
        raise RuntimeError("Nenhuma base ativa do R/M Orlean foi encontrada na Gestão. Nada foi enviado.")
    if not remessas:
        raise RuntimeError("Nenhuma remessa válida e mapeada para Orlean. Nada foi enviado.")

    period = sorted({x["data_envio"] for x in remessas})
    print("=" * 68)
    print("IMPORTAÇÃO C2C · ORLEAN")
    print(f"Relatório: {report.name}")
    print(f"Gestão: {gestao_file.name}")
    print(f"Bases ativas Orlean: {len(active)} | Meta mensal: {len(active) * 30:,} pacotes")
    print(f"Remessas únicas mapeadas: {len(remessas):,} | Período: {period[0]} a {period[-1]}")
    print(f"Linhas sem match/fora do escopo: {unmatched:,} | Datas inválidas: {invalid:,} | Nomes ambíguos na Gestão: {ambiguous:,}")
    if args.dry_run:
        print("DRY-RUN: nenhuma gravação realizada.")
        return

    # Roster do R/M é refeito em cada carga para atualizar S/C, região e status atual.
    api_import("c2c_bases", ["codigo_base"], bases, create=True)
    api_import("c2c_remessas", ["remessa"], remessas, create=True)
    print("Importação concluída. Reenviar o mesmo relatório é seguro: a remessa é chave única.")


if __name__ == "__main__":
    main()
