# 启动 Claude Code（含代理设置）
# 用法：在终端运行 .\start_claude.ps1
# 说明：环境变量仅在本次终端会话内有效，关闭窗口后自动消失，不影响系统全局设置

$env:HTTP_PROXY  = "http://127.0.0.1:7897"
$env:HTTPS_PROXY = "http://127.0.0.1:7897"
$env:NO_PROXY    = "localhost,127.0.0.1,192.168.88.128"

Write-Host "代理已设置：$env:HTTPS_PROXY" -ForegroundColor Green
Write-Host "启动 Claude Code..." -ForegroundColor Cyan

claude
