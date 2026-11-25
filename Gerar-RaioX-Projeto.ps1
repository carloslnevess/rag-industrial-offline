# ==============================================================================
# INSPETOR COMPLETO DO PROJETO - RAG INDUSTRIAL OFFLINE
# Gera um raio-x tecnico consolidado para analise precisa
# ==============================================================================

$ErrorActionPreference = "Stop"

$LabRoot    = "C:\Laboratorio"
$OutFile    = Join-Path $LabRoot "_auditoria_projeto.txt"
$Utf8NoBom  = New-Object System.Text.UTF8Encoding($false)

$IgnoreDirs = @("\.git", "\\bin\\", "\\obj\\", "\\\.venv\\", "\\node_modules\\", "\\__pycache__\\")
$TextExts   = @(".cs", ".py", ".html", ".css", ".js", ".sql", ".json", ".ps1", ".yml", ".yaml", ".env", ".md", ".csproj", ".gitignore")

if (-not (Test-Path $LabRoot)) {
    Write-Host "[ERRO] Diretorio $LabRoot nao encontrado." -ForegroundColor Red
    exit 1
}

$sb = New-Object System.Text.StringBuilder
[void]$sb.AppendLine("========================================================================")
[void]$sb.AppendLine("RAIO-X TECNICO COMPLETO - C:\Laboratorio")
[void]$sb.AppendLine("Gerado em: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
[void]$sb.AppendLine("========================================================================")
[void]$sb.AppendLine("")

[void]$sb.AppendLine("## 1. ESTRUTURA DE PASTAS E ARQUIVOS")
$allFiles = Get-ChildItem -Path $LabRoot -Recurse -File -ErrorAction SilentlyContinue | Where-Object {
    $full = $_.FullName
    $ignored = $false
    foreach ($pat in $IgnoreDirs) {
        if ($full -match $pat) { $ignored = $true; break }
    }
    (-not $ignored) -and ($_.Name -notlike "*.bak") -and ($_.Name -ne "_auditoria_projeto.txt")
}

foreach ($f in ($allFiles | Sort-Object FullName)) {
    $rel = $f.FullName.Substring($LabRoot.Length).TrimStart("\")
    $kb  = [math]::Round($f.Length / 1KB, 1)
    [void]$sb.AppendLine("  - ${rel} ($kb KB)")
}
[void]$sb.AppendLine("Total de arquivos relevantes: $($allFiles.Count)")
[void]$sb.AppendLine("")

[void]$sb.AppendLine("## 2. MAPA RAPIDO DE COMPONENTES")
$rotas = @()
$tabelas = @()
foreach ($f in $allFiles) {
    if ($TextExts -contains $f.Extension.ToLowerInvariant()) {
        $rel = $f.FullName.Substring($LabRoot.Length).TrimStart("\")
        $lines = Get-Content -Path $f.FullName -Encoding UTF8 -ErrorAction SilentlyContinue
        for ($i = 0; $i -lt $lines.Count; $i++) {
            $ln = $lines[$i].Trim()
            $lineNum = $i + 1
            if ($ln -match "app\.(MapGet|MapPost|MapDelete|MapHub|get|post|delete)\(") {
                $rotas += "  [${rel}:${lineNum}] $ln"
            }
            if ($ln -match "CREATE\s+TABLE\s+(IF\s+NOT\s+EXISTS\s+)?([a-zA-Z0-9_]+)") {
                $tabelas += "  [${rel}:${lineNum}] Tabela: $($matches[2])"
            }
        }
    }
}

[void]$sb.AppendLine("### Rotas / Endpoints / Hubs detectados:")
if ($rotas.Count -gt 0) { ($rotas | Select-Object -Unique) | ForEach-Object { [void]$sb.AppendLine($_) } }
else { [void]$sb.AppendLine("  (nenhum)") }

[void]$sb.AppendLine("### Tabelas SQL detectadas:")
if ($tabelas.Count -gt 0) { ($tabelas | Select-Object -Unique) | ForEach-Object { [void]$sb.AppendLine($_) } }
else { [void]$sb.AppendLine("  (nenhuma)") }
[void]$sb.AppendLine("")

[void]$sb.AppendLine("## 3. CONTEUDO DOS ARQUIVOS DO PROJETO")
foreach ($f in ($allFiles | Sort-Object FullName)) {
    $ext = $f.Extension.ToLowerInvariant()
    if ($TextExts -contains $ext) {
        $rel = $f.FullName.Substring($LabRoot.Length).TrimStart("\")
        $content = Get-Content -Path $f.FullName -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
        if ($null -eq $content) { $content = "" }

        if ($content.Length -gt 40000) {
            $content = $content.Substring(0, 40000) + "`r`n... [TRUNCADO POR TAMANHO > 40KB] ..."
        }

        [void]$sb.AppendLine("------------------------------------------------------------------------")
        [void]$sb.AppendLine("ARQUIVO: ${rel}")
        [void]$sb.AppendLine("------------------------------------------------------------------------")
        [void]$sb.AppendLine($content)
        [void]$sb.AppendLine("")
    }
}

[System.IO.File]::WriteAllText($OutFile, $sb.ToString(), $Utf8NoBom)
Write-Host "[OK] Relatorio gerado em: $OutFile" -ForegroundColor Green

try {
    Set-Clipboard -Value (Get-Content -Path $OutFile -Raw -Encoding UTF8)
    Write-Host "[OK] Conteudo copiado para a area de transferencia!" -ForegroundColor Cyan
} catch {
    Write-Host "[INFO] Abra $OutFile para copiar o conteudo." -ForegroundColor Yellow
}