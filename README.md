# lenovo-throttling-fix
Workaround for Linux throttling issues on Lenovo T480 / T480s / X1C6 notebooks as described [here](https://www.reddit.com/r/thinkpad/comments/870u0a/t480s_linux_throttling_bug/).

This script forces the CPU package power limit (PL1/2) to **44 W** (29 W on battery) and the temperature trip point to **97 'C** (85 'C on battery) by overriding default values in MSR and MCHBAR every 5 seconds (30 on battery) to block the Embedded Controller from resetting these values to default.

## Requirements
The python module `python-periphery` is used for accessing the MCHBAR register by memory mapped I/O. 

## Installation
```
git clone https://github.com/erpalma/lenovo-throttling-fix.git
sudo pip install python-periphery
sudo make install
sudo systemctl enable lenovo_fix.service
sudo systemctl start lenovo_fix.service
```

## Configuration
The script can be configured by editing `lenovo_fix.py` directly. There exist two profiles `AC` and `BATTERY`:
```
config = {
    'AC': {
        'UPDATE_RATE_SEC': 5,  # Update the registers every this many seconds
        'PL1_TDP_W': 44,  # Max package power for time window #1
        'PL1_DURATION_S': 28,  # Time window #1 duration
        'PL2_TDP_W': 44,  # Max package power for time window #2
        'PL2_DURATION_S': 0.002,  # Time window #2 duration
        'TRIP_TEMP_C': 97  # Max allowed temperature before throttling
    },
    'BATTERY': {
        'UPDATE_RATE_SEC': 30,  # Update the registers every this many seconds
        'PL1_TDP_W': 29,  # Max package power for time window #1
        'PL1_DURATION_S': 28,  # Time window #1 duration
        'PL2_TDP_W': 44,  # Max package power for time window #2
        'PL2_DURATION_S': 0.002,  # Time window #2 duration
        'TRIP_TEMP_C': 85  # Max allowed temperature before throttling
    },
}
```

## Disclaimer
This script overrides the default values set by Lenovo. I'm using it without any problem, but it is still experimental so use it at your own risk.
