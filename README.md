# lenovo-throttling-fix
Workaround for Linux throttling issues on Lenovo T480 / T480s / X1C6 notebooks as described [here](https://www.reddit.com/r/thinkpad/comments/870u0a/t480s_linux_throttling_bug/).

This script forces the CPU package power limit (PL1/2) to **45 W** and the temperature trip point to **97 C** by overriding default values in MSR and MCHBAR every 15 seconds to block the Embedded Controller from resetting these values to default.

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

## Disclaimer
This script overrides the default values set by Lenovo. I'm using it without any problem, but it is still experimental so use it at your own risk.
