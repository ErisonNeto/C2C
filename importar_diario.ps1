$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$inputFolder = Join-Path $root 'dados\entrada'

if (-not (Test-Path $python)) {
    throw "Ambiente Python não encontrado em $python. Execute a instalação indicada no README.md."
}
if (-not (Test-Path $inputFolder)) {
    throw "Pasta de entrada não encontrada: $inputFolder"
}

& $python (Join-Path $root 'importar_excel.py') --entrada $inputFolder
if ($LASTEXITCODE -ne 0) {
    throw "A importação C2C falhou com código $LASTEXITCODE. Confira os arquivos e a conexão com a JT API."
}
