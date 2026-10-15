param(
    [ValidateSet('start','stop','stop-app','status')][string]$Action = 'status',
    [ValidateSet('demo','acceptance')][string]$Environment = 'demo'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$envFile = "deploy/$Environment.env"
$project = if ($Environment -eq 'demo') { 'testmind' } else { 'testmind-acceptance' }
$port = if ($Environment -eq 'demo') { 26104 } else { 26114 }
$stateDir = Join-Path $root "output/runtime/$Environment"
New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
Push-Location $root
try {
    if ($Action -eq 'start') {
        docker compose -p $project --env-file $envFile -f deploy/docker-compose.yaml up -d --wait --wait-timeout 180
        if ($LASTEXITCODE -ne 0) { throw 'Dependency startup failed' }
        uv sync --frozen
        if ($LASTEXITCODE -ne 0) { throw 'Locked dependency installation failed' }
        uv run python scripts/init_demo.py --env-file $envFile
        if ($LASTEXITCODE -ne 0) { throw 'Migration or model collection initialization failed' }
        uv run python scripts/check_models.py --env-file $envFile
        if ($LASTEXITCODE -ne 0) { throw 'Offline model artifacts are not ready' }
        foreach ($component in @('api','worker','knowledge-worker','graph-worker')) {
            $pidFile = Join-Path $stateDir "$component.pid"
            if (Test-Path -LiteralPath $pidFile) {
                $savedPid = [int](Get-Content -LiteralPath $pidFile)
                $running = Get-CimInstance Win32_Process -Filter "ProcessId=$savedPid"
                if ($running -and $running.CommandLine -like "*scripts/run_demo.py $component*" -and $running.CommandLine -like "*--env-file $envFile*") { continue }
            }
            $process = Start-Process -FilePath (Join-Path $root '.venv/Scripts/python.exe') -WorkingDirectory $root -ArgumentList @('scripts/run_demo.py',$component,'--env-file',$envFile,'--port',"$port") -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $stateDir "$component.out.log") -RedirectStandardError (Join-Path $stateDir "$component.err.log")
            $process.Id | Set-Content -LiteralPath $pidFile
        }
    } elseif ($Action -in @('stop','stop-app')) {
        # Only matching project entrypoints and their Python children are stopped.
        $processes = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*scripts/run_demo.py*' -and $_.CommandLine -like "*--env-file $envFile*" }
        foreach ($process in $processes) { Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue }
        if ($Action -eq 'stop') {
            docker compose -p $project --env-file $envFile -f deploy/docker-compose.yaml stop
            if ($LASTEXITCODE -ne 0) { throw 'Dependency shutdown failed' }
        }
    }
    docker compose -p $project --env-file $envFile -f deploy/docker-compose.yaml ps
    if ($Action -notin @('stop','stop-app')) {
        try { Invoke-RestMethod "http://127.0.0.1:$port/ready" | ConvertTo-Json -Depth 5 } catch { Write-Output 'API is not ready; model workers may still be warming.' }
    }
} finally { Pop-Location }
