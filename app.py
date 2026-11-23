from __future__ import annotations

import os
import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
JT_API_URL = os.getenv("JT_API_URL", "https://jt-api.rockettechbr.com.br").rstrip("/")
JT_API_KEY = os.getenv("JT_API_KEY", "").strip()
RM_ALVO = "ORLEAN DA SILVA DO NASCIMENTO"

app = FastAPI(title="C2C | Regional Orlean", version="1.1.0")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


def local_today() -> date:
    return datetime.now(ZoneInfo("America/Fortaleza")).date()


def query_rows(sql: str) -> list[dict[str, Any]]:
    if not JT_API_KEY:
        raise RuntimeError("JT_API_KEY não configurada no serviço do Render.")
    response = requests.post(
        f"{JT_API_URL}/query",
        headers={"X-API-Key": JT_API_KEY, "Content-Type": "application/json"},
        json={"sql": sql, "params": []},
        timeout=(15, 120),
    )
    if not response.ok:
        raise RuntimeError(f"JT API /query HTTP {response.status_code}: {response.text[:500]}")
    payload = response.json()
    return [dict(zip(payload.get("colunas") or [], row)) for row in payload.get("rows") or []]


def sql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def as_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def load_bases() -> list[dict[str, Any]]:
    return query_rows(
        "SELECT codigo_base, base, sc, ativo FROM c2c_bases "
        f"WHERE UPPER(rm) = {sql_quote(RM_ALVO)} AND ativo = TRUE ORDER BY sc, base"
    )


def load_data_range() -> tuple[date | None, date | None]:
    rows = query_rows(
        "SELECT MIN(data_envio) AS data_min, MAX(data_envio) AS data_max "
        f"FROM c2c_remessas WHERE UPPER(rm) = {sql_quote(RM_ALVO)}"
    )
    if not rows:
        return None, None
    return as_date(rows[0].get("data_min")), as_date(rows[0].get("data_max"))


def month_keys(start: date, end: date) -> list[tuple[int, int]]:
    keys: list[tuple[int, int]] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        keys.append((year, month))
        month = month + 1 if month < 12 else 1
        if month == 1:
            year += 1
    return keys


def eligible_days(start: date, end: date) -> int:
    if end < start:
        return 0
    total = 0
    current = start
    while current <= end:
        if current.day <= 30:
            total += 1
        current += timedelta(days=1)
    return total


def date_range_default(data_min: date | None, data_max: date | None) -> tuple[date, date]:
    today = local_today()
    start = date(today.year, today.month, 1)
    end = min(today, data_max) if data_max else today
    if end < start and data_max:
        end = data_max
        start = date(end.year, end.month, 1)
    if data_min and start < data_min:
        start = data_min
    if end < start:
        end = start
    return start, end


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return (ROOT / "templates" / "index.html").read_text(encoding="utf-8")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "api_configurada": bool(JT_API_KEY), "rm": RM_ALVO}


@app.get("/api/filtros")
def filtros() -> dict[str, Any]:
    try:
        bases = load_bases()
        data_min, data_max = load_data_range()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    responsaveis = sorted({str(item.get("sc") or "Sem S/C") for item in bases})
    return {
        "disponivel": bool(bases and data_min and data_max),
        "rm": RM_ALVO,
        "data_min": data_min.isoformat() if data_min else None,
        "data_max": data_max.isoformat() if data_max else None,
        "responsaveis": responsaveis,
        "bases": [
            {"codigo": str(item.get("codigo_base") or ""),
             "nome": str(item.get("base") or item.get("codigo_base") or ""),
             "sc": str(item.get("sc") or "Sem S/C")}
            for item in bases
        ],
    }


@app.get("/api/dashboard")
def dashboard(
    data_inicio: str | None = Query(default=None),
    data_fim: str | None = Query(default=None),
    sc: str | None = Query(default=None),
    base: str | None = Query(default=None),
) -> dict[str, Any]:
    try:
        all_bases = load_bases()
        data_min, data_max = load_data_range()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    default_start, default_end = date_range_default(data_min, data_max)
    try:
        start = date.fromisoformat(data_inicio) if data_inicio else default_start
        end = date.fromisoformat(data_fim) if data_fim else default_end
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Datas inválidas; use AAAA-MM-DD.") from exc
    if end < start:
        raise HTTPException(status_code=422, detail="A data final deve ser igual ou posterior à data inicial.")

    valid_sc = {str(item.get("sc") or "Sem S/C") for item in all_bases}
    if sc and sc not in valid_sc:
        raise HTTPException(status_code=422, detail="S/C selecionado não pertence à regional Orlean.")
    scoped_bases = [
        item for item in all_bases
        if (not sc or str(item.get("sc") or "Sem S/C") == sc)
        and (not base or str(item.get("codigo_base") or "") == base)
    ]
    if base and not any(str(item.get("codigo_base") or "") == base for item in all_bases):
        raise HTTPException(status_code=422, detail="Base selecionada não pertence à regional Orlean.")
    if sc and base and not scoped_bases:
        raise HTTPException(status_code=422, detail="A base selecionada não pertence ao S/C informado.")

    codes = [str(item.get("codigo_base") or "") for item in scoped_bases if item.get("codigo_base")]
    bases_count = len(codes)
    end_exclusive = end + timedelta(days=1)
    where_codes = ", ".join(sql_quote(code) for code in codes) or "''"
    sql = (
        "SELECT r.remessa, r.data_envio, r.frete_total, r.base_remetente, "
        "COALESCE(b.base, r.base_remetente) AS base, "
        "COALESCE(b.sc, r.sc) AS sc, COALESCE(b.codigo_base, r.base_key) AS codigo_base "
        "FROM c2c_remessas r LEFT JOIN c2c_bases b ON b.codigo_base = r.base_key "
        f"WHERE r.data_envio >= {sql_quote(start.isoformat())} "
        f"AND r.data_envio < {sql_quote(end_exclusive.isoformat())} "
        f"AND COALESCE(b.rm, r.rm) = {sql_quote(RM_ALVO)} "
        f"AND COALESCE(b.codigo_base, r.base_key) IN ({where_codes}) "
        "ORDER BY r.data_envio"
    )
    try:
        raw_rows = query_rows(sql)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    unique: dict[str, dict[str, Any]] = {}
    for row in raw_rows:
        shipment = str(row.get("remessa") or "").strip()
        if shipment:
            unique[shipment] = row
    rows = list(unique.values())

    per_day: dict[str, int] = defaultdict(int)
    per_sc: dict[str, dict[str, float]] = defaultdict(lambda: {"pacotes": 0, "frete": 0.0})
    per_base: dict[str, dict[str, float]] = defaultdict(lambda: {"pacotes": 0, "frete": 0.0})
    freight_total = 0.0
    for row in rows:
        d = as_date(row.get("data_envio"))
        if not d:
            continue
        key = d.isoformat()
        per_day[key] += 1
        freight = float(row.get("frete_total") or 0)
        freight_total += freight
        sc_name = str(row.get("sc") or "Sem S/C")
        base_code = str(row.get("codigo_base") or "")
        per_sc[sc_name]["pacotes"] += 1
        per_sc[sc_name]["frete"] += freight
        per_base[base_code]["pacotes"] += 1
        per_base[base_code]["frete"] += freight

    daily = []
    cumulative_actual = 0
    cumulative_target = 0
    current = start
    while current <= end:
        key = current.isoformat()
        target_today = bases_count if current.day <= 30 else 0
        actual_today = per_day.get(key, 0)
        cumulative_actual += actual_today
        cumulative_target += target_today
        daily.append({
            "data": key,
            "pacotes": actual_today,
            "meta_diaria": target_today,
            "acumulado": cumulative_actual,
            "meta_acumulada": cumulative_target,
        })
        current += timedelta(days=1)

    month_count = len(month_keys(start, end))
    meta_month = bases_count * 30 * month_count
    meta_period = bases_count * eligible_days(start, end)
    actual = len(rows)
    sc_group_counts: dict[str, int] = defaultdict(int)
    base_to_sc: dict[str, str] = {}
    base_labels: dict[str, str] = {}
    for item in scoped_bases:
        code = str(item.get("codigo_base") or "")
        name = str(item.get("base") or code)
        sc_name = str(item.get("sc") or "Sem S/C")
        sc_group_counts[sc_name] += 1
        base_to_sc[code] = sc_name
        base_labels[code] = name

    sc_rows = []
    for name, n_bases in sc_group_counts.items():
        count = int(per_sc.get(name, {}).get("pacotes", 0))
        target_period = n_bases * eligible_days(start, end)
        target_month = n_bases * 30 * month_count
        sc_rows.append({
            "nome": name, "bases": n_bases, "pacotes": count,
            "meta_periodo": target_period, "meta_mensal": target_month,
            "atingimento": count / target_period if target_period else 0,
            "frete": round(float(per_sc.get(name, {}).get("frete", 0)), 2),
            "saldo": max(target_period - count, 0),
        })
    sc_rows.sort(key=lambda item: (item["atingimento"], item["pacotes"]))

    base_rows = []
    for item in scoped_bases:
        code = str(item.get("codigo_base") or "")
        count = int(per_base.get(code, {}).get("pacotes", 0))
        target = eligible_days(start, end)
        base_rows.append({
            "codigo": code, "nome": base_labels.get(code, code),
            "sc": base_to_sc.get(code, "Sem S/C"), "pacotes": count,
            "meta_periodo": target, "atingimento": count / target if target else 0,
            "frete": round(float(per_base.get(code, {}).get("frete", 0)), 2),
            "saldo": max(target - count, 0),
        })
    base_rows.sort(key=lambda item: (item["atingimento"], item["pacotes"], item["nome"]))

    return {
        "rm": RM_ALVO,
        "data_inicio": start.isoformat(), "data_fim": end.isoformat(),
        "bases": bases_count, "pacotes": actual,
        "meta_periodo": meta_period, "meta_mensal": meta_month,
        "atingimento": actual / meta_period if meta_period else 0,
        "faltam_ritmo": max(meta_period - actual, 0),
        "saldo_meta_mensal": max(meta_month - actual, 0),
        "frete_total": round(freight_total, 2),
        "frete_medio": round(freight_total / actual, 2) if actual else 0,
        "meta_diaria": bases_count, "daily": daily,
        "por_sc": sc_rows, "por_base": base_rows,
        "linhas_unicas": len(rows),
    }
