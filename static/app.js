const nf = new Intl.NumberFormat("pt-BR");
const money = new Intl.NumberFormat("pt-BR", {style:"currency", currency:"BRL"});
let chartDiario = null;
let chartAcumulado = null;
let chartSc = null;
let filtros = {bases:[], responsaveis:[]};

Chart.defaults.font.family = 'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
Chart.defaults.color = "#667085";
Chart.defaults.borderColor = "rgba(152, 162, 179, .16)";

function fmt(value) { return nf.format(Number(value || 0)); }
function fmtMoney(value) { return money.format(Number(value || 0)); }
function pct(value) { return `${(Number(value || 0) * 100).toFixed(1).replace(".", ",")}%`; }
function dateLocalISO(d) {
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;
}
function escaped(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
function dateLabel(iso) {
  const [year, month, day] = String(iso).slice(0,10).split("-");
  return `${day}/${month}`;
}
function classPct(value) { return value == null ? "sla-neutral" : Number(value) >= 1 ? "sla-good" : Number(value) >= 2/3 ? "sla-warning" : "sla-bad"; }

async function getJSON(url) {
  const sep = url.includes("?") ? "&" : "?";
  const response = await fetch(`${url}${sep}_t=${Date.now()}`, {cache:"no-store"});
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail || `${response.status} - ${response.statusText}`);
  return payload;
}

function setStatus(text, empty=false) {
  const el = document.getElementById("fonteResumo");
  el.textContent = text;
  el.classList.toggle("fonte-vazia", empty);
}

function montarQuery() {
  const params = new URLSearchParams();
  const start = document.getElementById("dataInicio").value;
  const end = document.getElementById("dataFim").value;
  const sc = document.getElementById("responsavel").value;
  const base = document.getElementById("base").value;
  if (start) params.set("data_inicio", start);
  if (end) params.set("data_fim", end);
  if (sc) params.set("sc", sc);
  if (base) params.set("base", base);
  return params.toString() ? `?${params.toString()}` : "";
}

function setBaseOptions(scValue, selected="") {
  const select = document.getElementById("base");
  const filtered = filtros.bases.filter((item) => !scValue || item.sc === scValue);
  select.innerHTML = '<option value="">Todas</option>' + filtered.map((item) =>
    `<option value="${escaped(item.codigo)}">${escaped(item.nome)}</option>`
  ).join("");
  if (selected && filtered.some((item) => item.codigo === selected)) select.value = selected;
}

async function carregarFiltros() {
  const data = await getJSON("/api/filtros");
  filtros = data;
  const s = document.getElementById("responsavel");
  s.innerHTML = '<option value="">Todos</option>' + (data.responsaveis || []).map((nome) =>
    `<option value="${escaped(nome)}">${escaped(nome)}</option>`
  ).join("");
  setBaseOptions("");
  if (!data.disponivel) {
    setStatus("C2C ainda não carregado no PostgreSQL. Faça uma importação válida antes de consultar o painel.", true);
    return;
  }

  const today = dateLocalISO(new Date());
  const max = data.data_max < today ? data.data_max : today;
  const currentMonthStart = `${today.slice(0,7)}-01`;
  let start = currentMonthStart;
  let end = max;
  if (end < start) {
    const latest = data.data_max;
    start = `${latest.slice(0,7)}-01`;
    end = latest;
  }
  if (data.data_min && start < data.data_min) start = data.data_min;
  if (end < start) end = start;
  document.getElementById("dataInicio").value = start;
  document.getElementById("dataFim").value = end;
  setStatus(`Fonte: export JMS • dados disponíveis de ${data.data_min} até ${data.data_max} • R/M Orlean`);
}

function drawDaily(data) {
  if (chartDiario) chartDiario.destroy();
  chartDiario = new Chart(document.getElementById("chartDiario"), {
    data: {
      labels: data.daily.map((x) => dateLabel(x.data)),
      datasets: [
        {type:"bar", label:"Remessas únicas", data:data.daily.map((x)=>x.pacotes),
          backgroundColor:"rgba(215,25,32,.76)", borderColor:"#d71920", borderWidth:1, borderRadius:5, maxBarThickness:22},
        {type:"line", label:"Meta diária", data:data.daily.map((x)=>x.meta_diaria),
          borderColor:"#159455", backgroundColor:"rgba(21,148,85,.06)", borderDash:[7,5],
          borderWidth:2, pointRadius:0, tension:0}
      ]
    },
    options: {
      responsive:true, maintainAspectRatio:false,
      interaction:{mode:"index",intersect:false},
      scales:{y:{beginAtZero:true,ticks:{precision:0}},x:{grid:{display:false},ticks:{maxTicksLimit:18}}},
      plugins:{legend:{position:"bottom"},tooltip:{callbacks:{label:(c)=>`${c.dataset.label}: ${fmt(c.raw)}`}}}
    }
  });
}

function drawCumulative(data) {
  if (chartAcumulado) chartAcumulado.destroy();
  chartAcumulado = new Chart(document.getElementById("chartAcumulado"), {
    type:"line",
    data:{labels:data.daily.map((x)=>dateLabel(x.data)),datasets:[
      {label:"Realizado acumulado",data:data.daily.map((x)=>x.acumulado),borderColor:"#d71920",
        backgroundColor:"rgba(215,25,32,.08)",pointBackgroundColor:"#fff",pointBorderColor:"#d71920",
        pointBorderWidth:2,fill:true,borderWidth:3,pointRadius:2.5,tension:.28},
      {label:"Meta acumulada",data:data.daily.map((x)=>x.meta_acumulada),borderColor:"#159455",
        borderWidth:2,pointRadius:0,borderDash:[7,5],tension:0}
    ]},
    options:{responsive:true,maintainAspectRatio:false,interaction:{mode:"index",intersect:false},
      scales:{y:{beginAtZero:true,ticks:{precision:0}},x:{grid:{display:false},ticks:{maxTicksLimit:18}}},
      plugins:{legend:{position:"bottom"},tooltip:{callbacks:{label:(c)=>`${c.dataset.label}: ${fmt(c.raw)} pacotes`}}}}
  });
}

function drawSc(data) {
  if (chartSc) chartSc.destroy();
  chartSc = new Chart(document.getElementById("chartSc"), {
    type:"bar",
    data:{labels:data.por_sc.map((x)=>x.nome),datasets:[
      {label:"Realizado",data:data.por_sc.map((x)=>x.pacotes),backgroundColor:"rgba(215,25,32,.86)",borderRadius:6},
      {label:"Meta do período",data:data.por_sc.map((x)=>x.meta_periodo),backgroundColor:"rgba(21,148,85,.50)",borderRadius:6}
    ]},
    options:{indexAxis:"y",responsive:true,maintainAspectRatio:false,scales:{x:{beginAtZero:true,ticks:{precision:0}},y:{grid:{display:false}}},
      plugins:{legend:{position:"bottom"},tooltip:{callbacks:{label:(c)=>`${c.dataset.label}: ${fmt(c.raw)}`}}}}
  });
}

function renderScTable(rows) {
  const tbody = document.getElementById("tbodySc");
  tbody.innerHTML = rows.map((x)=>`<tr>
    <td>${escaped(x.nome)}</td><td>${fmt(x.bases)}</td><td>${fmt(x.pacotes)}</td>
    <td>${fmt(x.meta_periodo)}</td><td class="${classPct(x.atingimento)}">${pct(x.atingimento)}</td>
  </tr>`).join("") || '<tr><td colspan="5">Sem dados neste filtro.</td></tr>';
}

function renderBaseRows(rows) {
  const under = rows.filter((x)=>x.saldo>0).slice(0,15);
  document.getElementById("tbodyBasesBaixo").innerHTML = under.map((x)=>`<tr>
    <td>${escaped(x.nome)}</td><td>${escaped(x.sc)}</td><td>${fmt(x.pacotes)}</td>
    <td>${fmt(x.meta_periodo)}</td><td class="${classPct(x.atingimento)}">${fmt(x.saldo)}</td>
  </tr>`).join("") || '<tr><td colspan="5">Bases no ritmo no período.</td></tr>';

  document.getElementById("tbodyRankingBases").innerHTML = rows.map((x,i)=>`<tr>
    <td>${i+1}</td><td>${escaped(x.codigo)}</td><td>${escaped(x.nome)}</td><td>${escaped(x.sc)}</td>
    <td>${fmt(x.pacotes)}</td><td>${fmt(x.meta_periodo)}</td><td>${fmt(x.saldo)}</td>
    <td class="${classPct(x.atingimento)}">${pct(x.atingimento)}</td><td>${fmtMoney(x.frete)}</td>
  </tr>`).join("") || '<tr><td colspan="9">Sem bases no filtro.</td></tr>';
}

function setKpis(data) {
  document.getElementById("totalPacotes").textContent = fmt(data.pacotes);
  document.getElementById("metaMensal").textContent = fmt(data.meta_mensal);
  document.getElementById("metaMensalNota").textContent = `${fmt(data.bases)} bases × 30 (mínimo 20) × ${monthSpan(data.data_inicio,data.data_fim)} mês(es) no período`;
  document.getElementById("atingimento").className = classPct(data.atingimento);
  document.getElementById("atingimento").textContent = pct(data.atingimento);
  document.getElementById("metaPeriodoNota").textContent = `${fmt(data.pacotes)} de ${fmt(data.meta_periodo)} esperados no período`;
  document.getElementById("freteTotal").textContent = fmtMoney(data.frete_total);
  document.getElementById("freteMedioNota").textContent = `${fmtMoney(data.frete_medio)} por remessa`;
  document.getElementById("basesFiltradas").textContent = fmt(data.bases);
  document.getElementById("metaPeriodo").textContent = fmt(data.meta_periodo);
  document.getElementById("faltamRitmo").textContent = fmt(data.faltam_ritmo);
  document.getElementById("freteMedio").textContent = fmtMoney(data.frete_medio);
}
function monthSpan(start,end) {
  const a=start.slice(0,7).split("-").map(Number), b=end.slice(0,7).split("-").map(Number);
  return Math.max(1,(b[0]-a[0])*12+b[1]-a[1]+1);
}

async function carregarDashboard() {
  const data = await getJSON(`/api/dashboard${montarQuery()}`);
  setKpis(data);
  drawDaily(data);
  drawCumulative(data);
  drawSc(data);
  renderScTable(data.por_sc);
  renderBaseRows(data.por_base);
  setStatus(`Fonte: export JMS • período ${data.data_inicio} a ${data.data_fim} • ${fmt(data.bases)} bases no escopo filtrado • R/M Orlean`);
}

async function baixarRanking() {
  const data = await getJSON(`/api/dashboard${montarQuery()}`);
  const headers = ["#", "Código", "Base", "S/C", "Remessas", "Meta no período", "Faltam", "Atingimento", "Frete total"];
  const quote = (v) => `"${String(v ?? "").replaceAll('"','""')}"`;
  const rows = [headers.map(quote).join(";"), ...data.por_base.map((x,i)=>[
    i+1,x.codigo,x.nome,x.sc,x.pacotes,x.meta_periodo,x.saldo,pct(x.atingimento),fmtMoney(x.frete)
  ].map(quote).join(";"))];
  const blob = new Blob(["\ufeff"+rows.join("\r\n")],{type:"text/csv;charset=utf-8;"});
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href=url;link.download=`ranking_c2c_${document.getElementById("dataInicio").value}_a_${document.getElementById("dataFim").value}.csv`;
  document.body.appendChild(link);link.click();link.remove();URL.revokeObjectURL(url);
}

function showError(error) {
  const box=document.getElementById("erro");
  box.textContent=`Falha ao carregar o C2C: ${error.message}`;
  box.hidden=false;
}
async function apply() {
  document.getElementById("erro").hidden=true;
  try { await carregarDashboard(); } catch (error) { showError(error); }
}

document.getElementById("responsavel").addEventListener("change",()=>{
  setBaseOptions(document.getElementById("responsavel").value);
});
document.getElementById("btnAplicar").addEventListener("click",apply);
document.getElementById("btnLimpar").addEventListener("click",async()=>{
  document.getElementById("responsavel").value="";
  setBaseOptions("");
  try { await carregarFiltros(); await apply(); } catch(error) { showError(error); }
});
document.getElementById("btnBaixarRanking").addEventListener("click",async()=>{
  try { await baixarRanking(); } catch(error) { showError(error); }
});

async function iniciar() {
  try {
    await carregarFiltros();
    if (filtros.disponivel) await carregarDashboard();
  } catch(error) { showError(error); }
}
iniciar();