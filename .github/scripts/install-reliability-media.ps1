$ErrorActionPreference = 'Stop'
# Pinned upstream release asset and its published SHA-256.
# Month-end builds are retained for two years; daily builds expire after 14 days.
# https://github.com/BtbN/FFmpeg-Builds/releases/tag/autobuild-2026-10-31-12-59
$archiveName = 'ffmpeg-n8.1.3-27-g8dca1586f4-win64-gpl-8.1.zip'
$expectedHash = '401dffcf14c301082e1fc03b0bc876b92ea676bfc521fc56e80a1537d137034f'
$toolRoot = Join-Path $env:GITHUB_WORKSPACE 'tmp/ci-tools/ffmpeg'
New-Item -ItemType Directory -Force -Path $toolRoot | Out-Null
$ffmpeg = Join-Path $toolRoot 'ffmpeg.exe'
$ffprobe = Join-Path $toolRoot 'ffprobe.exe'
if (!(Test-Path -LiteralPath $ffmpeg) -or !(Test-Path -LiteralPath $ffprobe)) {
    $archive = Join-Path $env:TEMP $archiveName
    Invoke-WebRequest -Uri "https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-10-31-12-59/$archiveName" -OutFile $archive
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) {
        throw 'FFmpeg archive SHA-256 mismatch'
    }
    $expanded = Join-Path $env:TEMP 'ffmpeg-expanded'
    Expand-Archive -LiteralPath $archive -DestinationPath $expanded
    foreach ($name in @('ffmpeg.exe', 'ffprobe.exe')) {
        $matches = @(Get-ChildItem -LiteralPath $expanded -Recurse -File -Filter $name)
        if ($matches.Count -ne 1) { throw "Expected exactly one $name in verified archive" }
        Copy-Item -LiteralPath $matches[0].FullName -Destination (Join-Path $toolRoot $name)
    }
}
# Also verify restored cache bytes before executing either program.
$binaryHashes = @{
    'ffmpeg.exe' = 'c46215f8889634396f0ac0acac738ef2145175e5b6caf28b3daa8ac9a9980c5f'
    'ffprobe.exe' = 'b77db2dfcbf08886d6a29bc1fabdceea3dd559751b03087205e3e13ec85c3e15'
}
foreach ($name in $binaryHashes.Keys) {
    if ((Get-FileHash -LiteralPath (Join-Path $toolRoot $name) -Algorithm SHA256).Hash.ToLowerInvariant() -ne $binaryHashes[$name]) {
        throw "Verified media binary SHA-256 mismatch: $name"
    }
}
"CREATOROS_FFMPEG=$ffmpeg" | Out-File -FilePath $env:GITHUB_ENV -Encoding utf8 -Append
"CREATOROS_FFPROBE=$ffprobe" | Out-File -FilePath $env:GITHUB_ENV -Encoding utf8 -Append
