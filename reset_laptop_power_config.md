# 1. Reset Hard Disk Timeout to default (usually 20 mins / 1200 seconds)
powercfg /setacvalueindex scheme_current sub_disk diskidle 1200

# 2. Reset Wireless Adapter to 'Medium Power Saving'
powercfg /setacvalueindex scheme_current 19cbb8fa-5279-450e-9fac-8a3d5fedd0c1 12bbebe6-58d6-4636-95bb-3217ef867c1a 2

# 3. Re-enable Hybrid Sleep
powercfg /setacvalueindex scheme_current sub_sleep hybridsleep 1

# 4. Re-enable USB Selective Suspend
powercfg /setacvalueindex scheme_current 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-a5d4-53bb8f07e226 1

# 5. Reset PCI Express (ASPM) to 'Moderate Power Savings'
powercfg /setacvalueindex scheme_current sub_pciexpress aspm 1

# 6. Reset Minimum Processor State to 5% (Standard idle)
powercfg /setacvalueindex scheme_current 54533251-82be-4824-96c1-47b60b740d00 893dee8e-2bef-41e0-89c6-b55d0929964c 5

# 7. Restore Lid Close Action to 'Sleep'
powercfg /setacvalueindex scheme_current sub_buttons lidaction 1

# Apply all changes immediately
powercfg /setactive scheme_current

Write-Host "Power settings have been restored to standard laptop behavior." -ForegroundColor Green