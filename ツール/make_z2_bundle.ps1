<#
.SYNOPSIS
    Z2工房(SOLIDIFY)へ Drawing-Generator-Agent を配置/更新するための git bundle を作る
    (Z2工房統合手順書_2026-08-11.md §2.3 の初回/差分の両方に対応)。

.DESCRIPTION
    既定では **ローカルで bundle を作るだけ**(scp/ssh は -Push を付けた時だけ実行)。
    実際にZ2へ触れる操作(git clone / git merge --ff-only)は破壊力があり、かつ
    「実行中ジョブが無いこと」の確認(Z2工房統合手順書§2.6)は人間の判断が要るため、
    **このスクリプトは clone/merge を絶対に自動実行しない**。次に打つべきコマンドを
    画面に出すだけに留める(-Push を付けても scp と退避ブランチ作成までしかしない)。

.PARAMETER Mode
    Initial(初回配置。git bundle --all) または Update(差分更新。git bundle <SinceCommit>..main)。

.PARAMETER SinceCommit
    Mode=Update の時に必須。Z2側の現在コミット
    (例 `ssh z2 "git -C C:/workshop/Drawing-Generator-Agent rev-parse HEAD"` で確認)。

.PARAMETER RepoDir
    束ねるリポジトリのローカルパス。既定はこのスクリプトの1階層上(Drawing-Generator-Agent自身)。
    姉妹リポジトリ(3D CAD Operator Agent = workshop)を束ねたい時は明示的に指定する
    (このスクリプトは汎用ツールであり、姉妹リポジトリの中身をこちらから書き換えることはない)。

.PARAMETER RemoteHost
    ssh設定名。既定 "z2"(開発機の ~/.ssh/config に登録済み前提。Z2工房統合手順書§2.1)。

.PARAMETER RemoteWorkshopDir
    Z2側の配置親ディレクトリ。既定 "C:/workshop"。

.PARAMETER RemoteRepoName
    Z2側のリポジトリフォルダ名。既定 "Drawing-Generator-Agent"。

.PARAMETER Push
    指定すると bundle 作成に続けて scp・(Update時のみ)退避ブランチ作成 まで実行する。
    **git clone / merge --ff-only の実行はしない**(実行中ジョブ0確認・工房が暇な時間帯かの
    人間判断が要るため。Z2工房統合手順書§2.4/§2.6)。

.EXAMPLE
    # 初回配置用bundleをローカルに作るだけ(Z2には一切触れない)
    powershell -File ツール\make_z2_bundle.ps1 -Mode Initial

.EXAMPLE
    # 差分更新: bundle作成 + scp + 退避ブランチ作成まで実行(実行中ジョブ0を確認してから使うこと)
    powershell -File ツール\make_z2_bundle.ps1 -Mode Update -SinceCommit 1308ac4 -Push
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Initial", "Update")]
    [string]$Mode,

    [string]$SinceCommit,

    [string]$RepoDir = (Join-Path $PSScriptRoot ".."),

    [string]$RemoteHost = "z2",

    [string]$RemoteWorkshopDir = "C:/workshop",

    [string]$RemoteRepoName = "Drawing-Generator-Agent",

    [switch]$Push
)

$ErrorActionPreference = "Stop"

if ($Mode -eq "Update" -and -not $SinceCommit) {
    throw ("Mode=Update には -SinceCommit が必須です(Z2側の現在コミット。" +
           "ssh $RemoteHost ""git -C $RemoteWorkshopDir/$RemoteRepoName rev-parse HEAD"" 等で確認)")
}

$RepoDir = (Resolve-Path $RepoDir).Path
Write-Host "リポジトリ: $RepoDir"

# --- 事前確認(未コミット変更はbundleに含まれないので警告するだけで止めない) ---
Push-Location $RepoDir
try {
    $dirty = git status --porcelain
    if ($dirty) {
        Write-Warning "作業ツリーに未コミット変更があります(bundleには含まれません):"
        $dirty | ForEach-Object { Write-Warning "  $_" }
    }
    $headCommit = (git rev-parse --short HEAD).Trim()
    Write-Host "現在のHEAD: $headCommit"
} finally {
    Pop-Location
}

$dateTag = Get-Date -Format "yyyyMMdd-HHmmss"
if ($Mode -eq "Initial") {
    $bundleName = "drawing_agent_$dateTag.bundle"
} else {
    $bundleName = "da_update_$dateTag.bundle"
}
$bundlePath = Join-Path $env:TEMP $bundleName

Push-Location $RepoDir
try {
    if ($Mode -eq "Initial") {
        Write-Host "git bundle create (--all) ..."
        git bundle create $bundlePath --all
        if ($LASTEXITCODE -ne 0) { throw "git bundle create に失敗しました(exit=$LASTEXITCODE)" }
    } else {
        $range = "$SinceCommit..main"
        # 差分が空だと `git bundle create` 自体が「空bundleは作らない」と失敗する(exit128)。
        # 後段のclone/mergeも無意味になるため、bundle作成の前に件数を検知して分かりやすく止める。
        $revList = git rev-list $range
        $revCount = @($revList | Where-Object { $_ }).Count
        if ($revCount -eq 0) {
            Write-Warning "差分コミットが0件です($range)。Z2側は既に最新のため、bundleは作成しません。"
            exit 0
        }
        Write-Host "差分コミット数: $revCount"
        Write-Host "git bundle create ($range) ..."
        git bundle create $bundlePath $range
        if ($LASTEXITCODE -ne 0) { throw "git bundle create に失敗しました(exit=$LASTEXITCODE)" }
    }
} finally {
    Pop-Location
}

if (-not (Test-Path $bundlePath)) {
    throw "bundleが作成されませんでした: $bundlePath"
}
$bundleSizeMB = [math]::Round((Get-Item $bundlePath).Length / 1MB, 2)
Write-Host "bundle作成完了: $bundlePath ($bundleSizeMB MB)"

# git bundle の健全性チェック(壊れたbundleをscpしない)
git bundle verify $bundlePath | Out-Null
if ($LASTEXITCODE -ne 0) { throw "git bundle verify に失敗しました(壊れたbundleの疑い)" }
Write-Host "bundle検証: OK(git bundle verify)"

$remoteRepoPath = "$RemoteWorkshopDir/$RemoteRepoName"
$remoteBundlePath = "$RemoteWorkshopDir/$bundleName"
$snapshotBranch = "z2-snapshot-$(Get-Date -Format 'yyyyMMdd')"

Write-Host ""
Write-Host "=== 次に実行するコマンド(このスクリプトはここまでは自動実行しません) ==="
if ($Mode -eq "Initial") {
    Write-Host "scp `"$bundlePath`" ${RemoteHost}:${remoteBundlePath}"
    Write-Host "ssh $RemoteHost `"git clone $remoteBundlePath $remoteRepoPath; git -C $remoteRepoPath checkout main`""
} else {
    Write-Host "# 1) 実行中ジョブ0を確認(Z2工房統合手順書§2.6)。0でなければ以降は実行しないこと"
    Write-Host "scp `"$bundlePath`" ${RemoteHost}:${remoteBundlePath}"
    Write-Host "ssh $RemoteHost `"git -C $remoteRepoPath branch $snapshotBranch`"   # 適用前退避"
    $fetchCmd = "git -C $remoteRepoPath fetch $remoteBundlePath main:refs/remotes/bundle/main; " +
                "git -C $remoteRepoPath merge --ff-only bundle/main"
    Write-Host "ssh $RemoteHost `"$fetchCmd`""
}
Write-Host "# 後片付け: ssh $RemoteHost `"Remove-Item $remoteBundlePath -Force -Confirm:0`""
Write-Host ""

if ($Push) {
    Write-Host "-Push 指定: scp を実行します..."
    scp $bundlePath "${RemoteHost}:${remoteBundlePath}"
    if ($LASTEXITCODE -ne 0) { throw "scp に失敗しました(exit=$LASTEXITCODE)" }
    Write-Host "scp完了: ${RemoteHost}:${remoteBundlePath}"
    if ($Mode -eq "Update") {
        Write-Host "退避ブランチを作成します: $snapshotBranch"
        ssh $RemoteHost "git -C $remoteRepoPath branch $snapshotBranch"
        if ($LASTEXITCODE -ne 0) { throw "退避ブランチ作成に失敗しました(exit=$LASTEXITCODE)" }
    }
    Write-Host ""
    Write-Host "clone/merge --ff-only は自動実行していません。上記コマンドを人間が確認して実行してください"
    Write-Host "  (実行中ジョブ0の確認・工房が暇な時間帯であることの確認が先。Z2工房統合手順書§2.4/§2.6)"
} else {
    Write-Host "(-Push を付けていないため scp/ssh は実行していません。bundleはローカルに作成済みです)"
}
