# Cleanup script to detach any attached virtual hard disks (VHD/VHDX) and dismount ISO images
# This can be called after a cancelled build or manually if needed.

# Detach any VHD/VHDX attached via DiskPart (select first attached vdisk)
try {
    $dpScript = @"
list vdisk
select vdisk 1
detach vdisk
exit
"@
    $tmpFile = Join-Path $env:TEMP "detach_vdisk.txt"
    $dpScript | Out-File -Encoding ascii -Force $tmpFile
    Write-Host "[cleanup] Detaching any attached VHD/VHDX via DiskPart..." -ForegroundColor Cyan
    diskpart /s $tmpFile | Out-Null
    Remove-Item -Force $tmpFile -ErrorAction SilentlyContinue
} catch {
    Write-Host "[cleanup] Detach VHD error: $($_.Exception.Message)" -ForegroundColor Yellow
}

# Dismount any mounted ISO images
try {
    $imgs = Get-DiskImage | Where-Object { $_.Attached -eq $true }
    foreach ($img in $imgs) {
        Write-Host "[cleanup] Dismounting ISO image: $($img.ImagePath)" -ForegroundColor Cyan
        Dismount-DiskImage -ImagePath $img.ImagePath -ErrorAction SilentlyContinue
    }
} catch {
    Write-Host "[cleanup] Dismount ISO error: $($_.Exception.Message)" -ForegroundColor Yellow
}

Write-Host "[cleanup] Done." -ForegroundColor Green
