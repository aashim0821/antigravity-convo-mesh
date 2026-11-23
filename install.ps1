[CmdletBinding()]
param(
    [string]$TargetDir = (Join-Path $env:USERPROFILE ".gemini\config\plugins\convo-mesh"),
    [string]$PythonPath = "",
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

if ($Uninstall) {
    if (Test-Path $TargetDir) {
        Remove-Item -Path $TargetDir -Recurse -Force
        Write-Host "Removed convo-mesh plugin from: $TargetDir"
    } else {
        Write-Host "convo-mesh is not installed at: $TargetDir"
    }
    Write-Host "Note: Your local database (~/.gemini/antigravity/convo-mesh/mesh.db) was preserved."
    exit 0
}

function Resolve-Python310Plus {
    param([string]$ExplicitPath)

    $candidates = @()
    if ($ExplicitPath -and $ExplicitPath.Trim() -ne "") {
        $candidates += ,@($ExplicitPath.Trim())
    } else {
        $candidates += ,@("python")
        $candidates += ,@("python3")
        $candidates += ,@("py", "-3")
    }

    $probeCode = "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
    $execCode = "import sys; print(sys.executable)"

    foreach ($cand in $candidates) {
        $exe = $cand[0]
        $prefixArgs = @()
        if ($cand.Count -gt 1) {
            $prefixArgs = $cand[1..($cand.Count - 1)]
        }

        $cmd = Get-Command $exe -ErrorAction SilentlyContinue
        if (-not $cmd -and -not (Test-Path $exe)) {
            continue
        }

        try {
            & $exe @prefixArgs -c $probeCode 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) {
                $resolved = (& $exe @prefixArgs -c $execCode 2>$null | Select-Object -First 1).Trim()
                if ($resolved -and (Test-Path $resolved)) {
                    return $resolved
                }
            }
        } catch {
            continue
        }
    }

    throw "Python 3.10+ is required. Install Python 3.10+ or pass -PythonPath <path-to-python.exe>."
}

$SourceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ResolvedPython = Resolve-Python310Plus -ExplicitPath $PythonPath
Write-Host "Using Python: $ResolvedPython"
Write-Host "Installing convo-mesh from $SourceRoot -> $TargetDir"

New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null

$RootFiles = @("plugin.json", "cli.py")
foreach ($file in $RootFiles) {
    $src = Join-Path $SourceRoot $file
    $dst = Join-Path $TargetDir $file
    if (-not (Test-Path $src)) {
        throw "Missing source file: $src"
    }
    Copy-Item -Path $src -Destination $dst -Force
}

$SubDirs = @("rules", "skills", "core", "hooks", "mcp")
foreach ($dir in $SubDirs) {
    $srcDir = Join-Path $SourceRoot $dir
    $dstDir = Join-Path $TargetDir $dir
    if (-not (Test-Path $srcDir)) {
        throw "Missing source directory: $srcDir"
    }
    if (Test-Path $dstDir) {
        Remove-Item -Path $dstDir -Recurse -Force
    }
    New-Item -ItemType Directory -Path $dstDir -Force | Out-Null
    Get-ChildItem -Path $srcDir -Recurse -File | Where-Object {
        $_.FullName -notmatch "__pycache__" -and $_.Extension -ne ".pyc"
    } | ForEach-Object {
        $rel = $_.FullName.Substring($srcDir.Length).TrimStart('\', '/')
        $destFile = Join-Path $dstDir $rel
        $destParent = Split-Path -Parent $destFile
        New-Item -ItemType Directory -Path $destParent -Force | Out-Null
        Copy-Item -Path $_.FullName -Destination $destFile -Force
    }
}

# Generate hooks.json and mcp_config.json with the resolved Python path (UTF-8 without BOM)
$genScript = @'
import json
import sys
from pathlib import Path

target_dir = Path(sys.argv[1])
py_exe = sys.argv[2]
quoted_cmd = f'"{py_exe}" hooks/mesh_hook.py'

hooks_cfg = {
    "convo-mesh-coordinator": {
        "SessionStart": [{"type": "command", "command": quoted_cmd, "timeout": 10}],
        "PreInvocation": [{"type": "command", "command": quoted_cmd, "timeout": 10}],
        "PostToolUse": [
            {
                "matcher": "write_file|edit_file",
                "hooks": [{"type": "command", "command": quoted_cmd, "timeout": 10}],
            }
        ],
        "Stop": [{"type": "command", "command": quoted_cmd, "timeout": 10}],
    }
}

mcp_cfg = {
    "mcpServers": {
        "convo-mesh": {
            "command": py_exe,
            "type": "stdio",
            "args": ["-u", "mcp/server.py"],
        }
    }
}

(target_dir / "hooks.json").write_text(json.dumps(hooks_cfg, indent=2) + "\n", encoding="utf-8")
(target_dir / "mcp_config.json").write_text(json.dumps(mcp_cfg, indent=2) + "\n", encoding="utf-8")
'@

$genScript | & $ResolvedPython - $TargetDir $ResolvedPython
if ($LASTEXITCODE -ne 0) {
    throw "Failed to generate hooks.json and mcp_config.json"
}

# Post-install smoke test using isolated temporary DB
$tmpDir = Join-Path $env:TEMP ("convo_mesh_install_check_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null
$prevDb = $env:CONVO_MESH_DB
try {
    $env:CONVO_MESH_DB = Join-Path $tmpDir "smoke.db"
    Push-Location $TargetDir

    $hookInput = '{"hookEventName":"SessionStart","conversationId":"install-check","workspacePaths":["."]}'
    $hookOut = $hookInput | & $ResolvedPython "hooks/mesh_hook.py"
    if ($LASTEXITCODE -ne 0) { throw "Post-install hook check exited $LASTEXITCODE" }
    $null = $hookOut | ConvertFrom-Json

    $mcpInput = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}' + "`n" +
                '{"jsonrpc":"2.0","method":"notifications/initialized"}' + "`n" +
                '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' + "`n"
    $mcpOut = $mcpInput | & $ResolvedPython -u "mcp/server.py"
    if ($LASTEXITCODE -ne 0) { throw "Post-install MCP check exited $LASTEXITCODE" }
    $mcpLines = $mcpOut -split "`r?`n" | Where-Object { $_.Trim() -ne "" }
    $toolsResp = $mcpLines[1] | ConvertFrom-Json
    if ($toolsResp.result.tools.Count -ne 14) {
        throw "Expected 14 MCP tools, got $($toolsResp.result.tools.Count)"
    }
    Pop-Location
} finally {
    if ($null -eq $prevDb) {
        Remove-Item Env:CONVO_MESH_DB -ErrorAction SilentlyContinue
    } else {
        $env:CONVO_MESH_DB = $prevDb
    }
    if (Test-Path $tmpDir) {
        Remove-Item -Path $tmpDir -Recurse -Force
    }
}

Write-Host "`n[OK] convo-mesh installed and verified at: $TargetDir"
Write-Host "Next steps:"
Write-Host "  1. Open a new Antigravity conversation (or reload Antigravity)."
Write-Host "  2. Verify 'convo-mesh' appears under Additional Options (...) > MCP Servers or Skills & Customizations."
