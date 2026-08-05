# Instala as tarefas agendadas do projeto (Windows).
#
# NAO e executado automaticamente: registrar tarefa agendada e configuracao
# persistente da maquina e liga o envio de ordens sem ninguem por perto.
# Rode manualmente quando quiser ligar a operacao diaria:
#
#     powershell -ExecutionPolicy Bypass -File scripts\install_scheduled_tasks.ps1
#
# Para remover:
#     Unregister-ScheduledTask -TaskName "B3_*" -Confirm:$false

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$pythonw = "$env:LOCALAPPDATA\Programs\Python\Python312\pythonw.exe"
if (-not (Test-Path $pythonw)) { $pythonw = (Get-Command pythonw).Source }

Write-Host "raiz do projeto: $root"
Write-Host "interpretador:   $pythonw"

# pythonw.exe roda sem console -- o processo nao aparece na area de trabalho e
# nao morre se alguma janela de terminal for fechada.

function New-B3Task($name, $script, $args, $trigger, $desc) {
    $action = New-ScheduledTaskAction -Execute $pythonw `
        -Argument "`"$root\$script`" $args" -WorkingDirectory $root
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Hours 12)
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger `
        -Settings $settings -Description $desc -Force | Out-Null
    Write-Host "  registrada: $name"
}

# 1) motor ao vivo -- comeca 10:05, dias uteis
$t1 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 10:05
New-B3Task "B3_MotorAoVivo" "scripts\10_run_live.py" "" $t1 `
    "Motor de day-trade sistematico em conta demo (B3)"

# 2) relatorio diario -- 18:30, dias uteis
$t2 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 18:30
New-B3Task "B3_RelatorioDiario" "scripts\11_daily_report.py" "" $t2 `
    "Relatorio diario + conferencia de custo realizado vs assumido"

# 3) atualizacao de dados e re-selecao -- sabado 09:00
$t3 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At 09:00
New-B3Task "B3_ManutencaoSemanal" "scripts\12_weekly_maintenance.py" "" $t3 `
    "Baixa dados novos, revalida e recongela a configuracao operacional"

# 4) L2 (book) do contrato vigente do WIN -- so leitura, sem ordem
$t4 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 08:55
New-B3Task "B3_HarvestL2WIN" "scripts\13_harvest_l2_win.py" "" $t4 `
    "Captura continua do book L2 (DOM) do contrato vigente do WIN"

# 5) dado oficial diario da B3 (PricRpt) -> data/oficial_b3/win_diario.db.
# Ate 05/08/2026 este pipeline vivia fora do repositorio (em "New OpenCode
# Project"); trazido para dentro a pedido explicito -- so a doc de engenharia
# reversa (EXTRACAO_DADOS_B3.md) continua la, intocada por decisao anterior.
$t5 = New-ScheduledTaskTrigger -Daily -At 08:00
New-B3Task "B3_HarvestWINDiario" "scripts\14_harvest_win_diario.py" "" $t5 `
    "PricRpt oficial da B3 -> data/oficial_b3/win_diario.db"

Write-Host "`npronto. Confira com: Get-ScheduledTask -TaskName 'B3_*'"
