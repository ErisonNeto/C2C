# C2C · Acompanhamento Orlean

Dashboard web para acompanhar a meta mensal C2C da regional Orlean. A meta é calculada como **30 pacotes por base ativa no mês**, com ritmo de **1 pacote por base por dia corrido**. O S/C é identificado pela Gestão de Colaboradores: Coordenador quando preenchido; caso contrário, Supervisor.

## O que aparece no painel

- Pacotes únicos, meta do mês e meta proporcional até o dia selecionado.
- Atingimento do ritmo, saldo para a meta e valor de frete total.
- Volume por dia, resultado por S/C e bases abaixo do ritmo.
- Período selecionável por mês; apenas bases mapeadas para o R/M Orlean entram nos indicadores.

O importador lê as colunas **A** (remessa), **C** (data do envio), **E** (base remetente) e **J** (frete total) do export JMS. A remessa é a chave única; repetir uma carga atualiza os dados da remessa e não duplica o volume.

## Importar diariamente no Windows

Crie uma pasta `dados\entrada` neste projeto e coloque nela o export diário `Exportar pedido JMS de envio*.xlsx` e a planilha atual de Gestão de Colaboradores. Na primeira configuração, instale as dependências:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Crie um arquivo `.env` na raiz do projeto com a chave que já é usada para acessar a JT API:

```dotenv
JT_API_URL=https://jt-api.rockettechbr.com.br
JT_API_KEY=COLOQUE_SUA_CHAVE_AQUI
```

Depois que o relatório estiver baixado na pasta, rode primeiro a conferência e, em seguida, a importação:

```powershell
.\.venv\Scripts\python.exe .\importar_excel.py --entrada .\dados\entrada --dry-run
.\.venv\Scripts\python.exe .\importar_excel.py --entrada .\dados\entrada
```

O importador seleciona o arquivo JMS e a Gestão mais recentes na pasta. O relatório precisa estar completo antes da carga. Arquivos temporários do Excel (`~$`) são ignorados. A importação envia o cadastro atualizado de bases e as remessas para as tabelas `c2c_bases` e `c2c_remessas` pela rota `/import` da JT API.

### Execução automática

Para automatizar, configure o Agendador de Tarefas do Windows para executar o segundo comando acima **depois do horário em que o export JMS chega à pasta**. Use como programa:

```text
C:\CAMINHO\DO\PROJETO\.venv\Scripts\python.exe
```

E como argumentos:

```text
C:\CAMINHO\DO\PROJETO\importar_excel.py --entrada C:\CAMINHO\DO\PROJETO\dados\entrada
```

Defina também o campo **Iniciar em** como a pasta raiz do projeto. O script não acessa o JMS nem baixa arquivos por conta própria: ele processa o export e a Gestão já salvos na pasta.

## Render

O `render.yaml` configura o serviço web. No Render, defina `JT_API_KEY` como variável secreta; `JT_API_URL` já vem preenchida. O painel usa a JT API apenas no servidor, sem expor a chave no navegador.

```text
Build: pip install -r requirements.txt
Start: uvicorn app:app --host 0.0.0.0 --port $PORT
```

## Desenvolvimento local

```powershell
.\.venv\Scripts\uvicorn.exe app:app --reload
```

Abra `http://127.0.0.1:8000`.
