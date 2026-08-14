# move_dsh.ps1 — DSH 搬家脚本（E:\DeepSeek Harness），由独立进程执行
$log = "D:\dsh_move_log.txt"
function L($m) { "$(Get-Date -Format 'HH:mm:ss') $m" | Out-File $log -Append -Encoding utf8 }

L "=== 搬家开始 ==="
$src = "C:\Users\lin\AppData\Local\npm-cache\_npx\1e7f6d9597241db0"
$dst = "E:\DeepSeek Harness"

# 1. 杀 DSH 服务（25368 及子进程）
L "杀服务 taskkill /PID 25368 /T /F"
taskkill /PID 25368 /T /F 2>&1 | Out-File $log -Append -Encoding utf8
Start-Sleep 4

# 2. 复制到 E 盘
L "robocopy 复制 $src -> $dst"
robocopy $src $dst /E /NFL /NDL /NJH /NJS /NP 2>&1 | Out-File $log -Append -Encoding utf8
$rc = $LASTEXITCODE
L "robocopy 退出码: $rc (0-7 为成功)"
if ($rc -ge 8) { L "复制失败，中止搬家（原目录保留）"; exit 1 }

# 3. 删除 C 盘原目录
L "删除原目录"
Remove-Item $src -Recurse -Force -ErrorAction SilentlyContinue
Start-Sleep 1
if (Test-Path $src) { L "警告: 原目录删除不干净" }

# 4. 建立 junction（原路径 -> E 盘）
L "创建 junction"
cmd /c mklink /J "$src" "$dst" 2>&1 | Out-File $log -Append -Encoding utf8

# 5. 验证 junction
$isLink = (Get-Item $src -Force -ErrorAction SilentlyContinue).LinkType
L "junction 验证: LinkType=$isLink"

# 6. 重启服务（与原启动命令一致）
L "重启 DSH: npx -y @deepseek-ai/dsh web (cwd=D:\毕设一)"
Start-Process -FilePath "D:\NODE.JS\node.exe" `
  -ArgumentList @("D:\NODE.JS/node_modules/npm/bin/npx-cli.js", "-y", "@deepseek-ai/dsh", "web") `
  -WorkingDirectory "D:\毕设一" -WindowStyle Hidden `
  -RedirectStandardOutput "D:\dsh_stdout.log" -RedirectStandardError "D:\dsh_stderr.log"
L "重启已发起，脚本结束"
