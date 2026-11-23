from __future__ import annotations

import os
import re
import unicodedata
from calendar import monthrange
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

JT_API_URL = os.getenv("JT_API_URL", "https://jt-api.rockettechbr.com.br").rstrip("/")
JT_API_KEY = os.getenv("JT_API_KEY", "").strip()
RM_ALVO = "ORLEAN DA SILVA DO NASCIMENTO"

def local_today() -> date:
    return datetime.now(ZoneInfo("America/Fortaleza")).date()

app = FastAPI(title="Acompanhamento C2C | Orlean", version="1.0.0")


def norm(value: Any) -> str:
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", value).strip().upper()


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


def month_bounds(period: str) -> tuple[date, date, int]:
    if not re.fullmatch(r"\d{4}-\d{2}", period):
        raise HTTPException(status_code=422, detail="Período inválido. Use AAAA-MM.")
    year, month = map(int, period.split("-"))
    if month < 1 or month > 12:
        raise HTTPException(status_code=422, detail="Mês inválido.")
    start = date(year, month, 1)
    end = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    return start, end, monthrange(year, month)[1]


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return HTML


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "api_configurada": bool(JT_API_KEY), "rm": RM_ALVO}


@app.get("/api/dashboard")
def dashboard(periodo: str | None = Query(default=None)) -> dict[str, Any]:
    periodo = periodo or local_today().strftime("%Y-%m")
    inicio, fim, dias_mes = month_bounds(periodo)
    hoje = local_today()
    dia_corte = min(hoje.day, dias_mes) if (hoje.year, hoje.month) == (inicio.year, inicio.month) else dias_mes
    try:
        bases = query_rows(
            "SELECT codigo_base, base, sc, ativo FROM c2c_bases "
            f"WHERE UPPER(rm) = '{RM_ALVO}' AND ativo = TRUE ORDER BY sc, base"
        )
        remessas = query_rows(
            "SELECT r.remessa, r.data_envio, r.frete_total, r.base_remetente, "
            "COALESCE(b.base, r.base_remetente) AS base, "
            "COALESCE(b.sc, r.sc) AS sc, COALESCE(b.codigo_base, r.base_key) AS codigo_base "
            "FROM c2c_remessas r LEFT JOIN c2c_bases b ON b.codigo_base = r.base_key "
            f"WHERE r.data_envio >= '{inicio.isoformat()}' AND r.data_envio < '{fim.isoformat()}' "
            f"AND COALESCE(b.rm, r.rm) = '{RM_ALVO}' ORDER BY r.data_envio"
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    base_codes = {str(row.get("codigo_base", "")) for row in bases}
    base_count = len(base_codes)
    filtered = [row for row in remessas if str(row.get("codigo_base", "")) in base_codes]
    rows_by_id: dict[str, dict[str, Any]] = {}
    for row in filtered:
        shipment_id = str(row.get("remessa") or "").strip()
        if shipment_id:
            rows_by_id[shipment_id] = row
    filtered = list(rows_by_id.values())

    daily: dict[str, int] = {}
    by_sc: dict[str, dict[str, Any]] = {}
    by_base: dict[str, dict[str, Any]] = {}
    freight = 0.0
    for row in filtered:
        raw_date = row.get("data_envio")
        day = raw_date[:10] if isinstance(raw_date, str) else str(raw_date)
        daily[day] = daily.get(day, 0) + 1
        value = float(row.get("frete_total") or 0)
        freight += value
        sc = str(row.get("sc") or "Sem S/C")
        base = str(row.get("base") or row.get("base_remetente") or "Base sem nome")
        for key, label, collection in ((sc, sc, by_sc), (str(row.get("codigo_base") or base), base, by_base)):
            item = collection.setdefault(key, {"nome": label, "pacotes": 0, "frete": 0.0})
            item["pacotes"] += 1
            item["frete"] += value

    meta_mes = base_count * 30
    meta_ate_hoje = base_count * dia_corte
    realizados = len(filtered)
    meta_diaria = base_count
    sc_rows = []
    bases_by_sc: dict[str, int] = {}
    for row in bases:
        label = str(row.get("sc") or "Sem S/C")
        bases_by_sc[label] = bases_by_sc.get(label, 0) + 1
    for name, count in bases_by_sc.items():
        actual = int(by_sc.get(name, {}).get("pacotes", 0))
        target = count * 30
        pace = count * dia_corte
        sc_rows.append({"nome": name, "bases": count, "meta_mes": target, "meta_ate_hoje": pace,
                        "pacotes": actual, "atingimento": actual / pace if pace else 0,
                        "saldo_meta": max(target - actual, 0), "frete": round(float(by_sc.get(name, {}).get("frete", 0)), 2)})
    sc_rows.sort(key=lambda item: (item["atingimento"], -item["pacotes"]))
    base_rows = []
    for row in bases:
        key = str(row.get("codigo_base", ""))
        actual = int(by_base.get(key, {}).get("pacotes", 0))
        base_rows.append({"base": row.get("base") or key, "sc": row.get("sc") or "Sem S/C",
                          "pacotes": actual, "meta_ate_hoje": dia_corte,
                          "atingimento": actual / dia_corte if dia_corte else 0,
                          "saldo": max(dia_corte - actual, 0)})
    base_rows.sort(key=lambda item: (item["atingimento"], item["base"]))
    return {
        "periodo": periodo, "rm": RM_ALVO, "bases": base_count, "pacotes": realizados,
        "meta_mes": meta_mes, "meta_ate_hoje": meta_ate_hoje, "meta_diaria": meta_diaria,
        "atingimento": realizados / meta_ate_hoje if meta_ate_hoje else 0,
        "saldo_mes": max(meta_mes - realizados, 0), "frete_total": round(freight, 2),
        "media_por_base": round(realizados / base_count, 1) if base_count else 0,
        "dias_no_mes": dias_mes, "dia_corte": dia_corte, "daily": daily,
        "por_sc": sc_rows, "por_base": base_rows,
    }


HTML = r'''<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>C2C | Orlean</title><style>
:root{--red:#df171f;--ink:#22252c;--muted:#79808a;--line:#eceef1;--bg:#f5f6f8;--green:#17845b;--amber:#ce8510}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 Inter,Segoe UI,Arial,sans-serif}.top{background:#fff;border-bottom:1px solid var(--line);padding:17px max(22px,calc((100% - 1400px)/2));display:flex;align-items:center;justify-content:space-between;gap:20px}.brand{display:flex;align-items:center;gap:13px}.mark{background:var(--red);color:#fff;font-weight:900;font-size:13px;border-radius:8px;padding:9px 10px;letter-spacing:.5px}.title{font-weight:750;font-size:18px}.sub{color:var(--muted);font-size:12px;margin-top:2px}.controls{display:flex;align-items:center;gap:10px}.controls input,.controls button{font:inherit;background:#fff;border:1px solid #dfe2e6;border-radius:8px;padding:9px 12px}.controls button{background:var(--red);color:white;border-color:var(--red);font-weight:650;cursor:pointer}.wrap{max-width:1400px;margin:0 auto;padding:24px 22px 45px}.eyebrow{font-size:11px;font-weight:700;color:var(--muted);letter-spacing:.1em;text-transform:uppercase}.heading{display:flex;justify-content:space-between;align-items:end;margin-bottom:18px}.heading h1{font-size:23px;margin:5px 0 0}.pill{border-radius:100px;padding:6px 11px;background:#fff1f1;color:#ba151b;font-weight:700;font-size:12px}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.card{background:#fff;border:1px solid var(--line);border-radius:12px;padding:18px;box-shadow:0 2px 9px #20242c07}.kpi-label{font-size:12px;color:var(--muted);font-weight:600}.kpi{font-size:28px;letter-spacing:-.03em;font-weight:760;margin:9px 0 2px}.note{color:var(--muted);font-size:12px}.accent{color:var(--red)}.main-grid{display:grid;grid-template-columns:1.35fr 1fr;gap:14px;margin-top:15px}.card-head{display:flex;justify-content:space-between;align-items:start;margin-bottom:16px}.card h2{font-size:15px;margin:0}.card .desc{font-size:12px;color:var(--muted);margin-top:4px}.bars{height:210px;display:flex;align-items:end;gap:4px;border-bottom:1px solid var(--line);padding:10px 2px 0}.bar-col{flex:1;min-width:8px;height:100%;display:flex;flex-direction:column;justify-content:end;align-items:center;gap:6px}.bar{width:min(24px,75%);background:#f8a5a8;border-radius:5px 5px 0 0;min-height:2px;position:relative}.bar.today{background:var(--red)}.bar-col label{font-size:10px;color:var(--muted);padding-bottom:6px}.legend{font-size:11px;color:var(--muted);margin-top:9px}.progress{height:7px;background:#f0f1f3;border-radius:99px;overflow:hidden;margin:8px 0 4px}.progress i{height:100%;display:block;background:var(--red);border-radius:99px}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;white-space:nowrap}th{text-align:left;padding:10px 9px;color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.05em;border-bottom:1px solid var(--line)}td{padding:11px 9px;border-bottom:1px solid #f0f1f3;font-size:12px}td.num,th.num{text-align:right}.sc-name{font-weight:650}.status{font-size:11px;font-weight:700}.good{color:var(--green)}.warn{color:var(--amber)}.bad{color:var(--red)}.bottom-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:15px}.error{display:none;margin:0 0 15px;padding:12px 14px;border-radius:9px;background:#fff2e0;color:#835100}.foot{color:var(--muted);font-size:11px;margin-top:17px;text-align:right}
@media(max-width:950px){.grid{grid-template-columns:repeat(2,1fr)}.main-grid,.bottom-grid{grid-template-columns:1fr}}@media(max-width:560px){.top{align-items:flex-start;flex-direction:column}.controls{width:100%}.controls input{flex:1}.wrap{padding:18px 14px}.grid{gap:9px}.card{padding:14px}.kpi{font-size:23px}.heading h1{font-size:20px}}
</style></head><body><header class="top"><div class="brand"><div class="mark">J&T</div><div><div class="title">C2C · Acompanhamento mensal</div><div class="sub">Performance das bases · Regional Orlean</div></div></div><div class="controls"><input id="period" type="month"><button onclick="load()">Atualizar</button></div></header>
<main class="wrap"><div id="error" class="error"></div><div class="heading"><div><div class="eyebrow">Visão regional · C2C</div><h1 id="heading">Resultado do mês</h1></div><span class="pill">R/M ORLEAN</span></div>
<section class="grid"><article class="card"><div class="kpi-label">Pacotes realizados</div><div id="packages" class="kpi">—</div><div id="target-note" class="note">meta acumulada —</div></article><article class="card"><div class="kpi-label">Meta mensal C2C</div><div id="monthly" class="kpi">—</div><div id="base-note" class="note">— bases × 30 pacotes</div></article><article class="card"><div class="kpi-label">Atingimento no ritmo do mês</div><div id="attain" class="kpi">—</div><div id="pace" class="note">meta proporcional até hoje</div></article><article class="card"><div class="kpi-label">Frete total</div><div id="freight" class="kpi">—</div><div id="average" class="note">— por base no período</div></article></section>
<section class="main-grid"><article class="card"><div class="card-head"><div><h2>Volume diário</h2><div class="desc">Remessas únicas por data de envio</div></div><span class="eyebrow" id="daily-total"></span></div><div id="chart" class="bars"></div><div class="legend" id="daily-caption">Pacotes por dia · meta diária regional será exibida no resumo</div></article><article class="card"><div class="card-head"><div><h2>Acompanhamento por S/C</h2><div class="desc">Meta individual calculada por quantidade de bases</div></div></div><div class="table-wrap"><table><thead><tr><th>S/C</th><th class="num">Bases</th><th class="num">Realizado</th><th class="num">Meta no mês</th><th class="num">Ritmo</th></tr></thead><tbody id="sc-table"></tbody></table></div></article></section>
<section class="bottom-grid"><article class="card"><div class="card-head"><div><h2>Bases abaixo do ritmo</h2><div class="desc">Meta de 1 pacote por base ao dia</div></div></div><div class="table-wrap"><table><thead><tr><th>Base</th><th>S/C</th><th class="num">Pacotes</th><th class="num">Meta até hoje</th><th class="num">Status</th></tr></thead><tbody id="base-table"></tbody></table></div></article><article class="card"><div class="card-head"><div><h2>Como ler o C2C</h2></div></div><div class="note" style="line-height:1.8;color:#4b515b"><b>Meta contratada:</b> 30 remessas por base no mês.<br><b>Ritmo esperado:</b> 1 remessa por base a cada dia corrido do mês.<br><b>Meta do S/C:</b> número de bases sob gestão × 30.<br><b>Remessas:</b> contagem distinta pelo número da remessa (coluna A).<br><b>Frete:</b> soma do valor total (coluna J).<br><b>Escopo:</b> bases mapeadas na Gestão de Colaboradores e atribuídas ao R/M Orlean.</div></article></section>
<div class="foot">Atualizado a partir da última importação · Gestão vigente aplicada por base</div></main><script>
const fmt=n=>Number(n||0).toLocaleString('pt-BR');const money=n=>Number(n||0).toLocaleString('pt-BR',{style:'currency',currency:'BRL'});const pct=n=>`${(Number(n||0)*100).toFixed(1).replace('.',',')}%`;
const now=new Date();document.getElementById('period').value=`${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}`;
async function load(){const e=document.getElementById('error');e.style.display='none';try{const d=await fetch(`/api/dashboard?periodo=${document.getElementById('period').value}`).then(async r=>{const j=await r.json();if(!r.ok)throw Error(j.detail||'Erro ao carregar dashboard');return j});document.getElementById('heading').textContent=`Resultado de ${d.periodo.split('-').reverse().join('/')}`;document.getElementById('packages').textContent=fmt(d.pacotes);document.getElementById('target-note').textContent=`Meta proporcional: ${fmt(d.meta_ate_hoje)} · faltam ${fmt(Math.max(d.meta_ate_hoje-d.pacotes,0))} para o ritmo`;document.getElementById('monthly').textContent=fmt(d.meta_mes);document.getElementById('base-note').textContent=`${fmt(d.bases)} bases ativas × 30 pacotes`;document.getElementById('attain').textContent=pct(d.atingimento);document.getElementById('pace').textContent=`${fmt(d.pacotes)} de ${fmt(d.meta_ate_hoje)} esperados até o dia ${d.dia_corte}`;document.getElementById('freight').textContent=money(d.frete_total);document.getElementById('average').textContent=`${money(d.bases?d.frete_total/d.bases:0)} de frete médio por base`;
const days=Object.entries(d.daily).sort((a,b)=>a[0].localeCompare(b[0]));const max=Math.max(1,...days.map(x=>x[1]));document.getElementById('daily-total').textContent=`${days.length} dias com envio`;document.getElementById('daily-caption').textContent=`Meta diária regional: ${fmt(d.meta_diaria)} pacotes (${fmt(d.bases)} bases × 1)`;document.getElementById('chart').innerHTML=days.map(([day,n])=>`<div class="bar-col" title="${day}: ${fmt(n)} pacotes"><div class="bar ${day===new Date().toISOString().slice(0,10)?'today':''}" style="height:${Math.max(2,n/max*88)}%"></div><label>${day.slice(8,10)}</label></div>`).join('')||'<span class="note">Sem movimentação importada neste mês.</span>';
document.getElementById('sc-table').innerHTML=d.por_sc.map(x=>`<tr><td class="sc-name">${esc(x.nome)}</td><td class="num">${fmt(x.bases)}</td><td class="num">${fmt(x.pacotes)}</td><td class="num">${fmt(x.meta_mes)}</td><td class="num"><span class="status ${x.atingimento>=1?'good':x.atingimento>=.8?'warn':'bad'}">${pct(x.atingimento)}</span><div class="progress"><i style="width:${Math.min(100,x.atingimento*100)}%"></i></div></td></tr>`).join('')||'<tr><td colspan="5">Sem bases mapeadas.</td></tr>';
document.getElementById('base-table').innerHTML=d.por_base.slice(0,30).map(x=>`<tr><td>${esc(x.base)}</td><td>${esc(x.sc)}</td><td class="num">${fmt(x.pacotes)}</td><td class="num">${fmt(x.meta_ate_hoje)}</td><td class="num"><span class="status ${x.atingimento>=1?'good':x.atingimento>=.8?'warn':'bad'}">${x.atingimento>=1?'No ritmo':`${fmt(x.saldo)} faltando`}</span></td></tr>`).join('')||'<tr><td colspan="5">Sem bases mapeadas.</td></tr>';
}catch(err){e.textContent=`Não foi possível carregar os dados: ${err.message}`;e.style.display='block'}}function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}load();
</script></body></html>'''
