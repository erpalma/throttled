# Fix T480 / T480s / X1C6 Throttling on Linux
Workaround for Linux throttling issues on Lenovo T480 / T480s / X1C6 notebooks as described [here](https://www.reddit.com/r/thinkpad/comments/870u0a/t480s_linux_throttling_bug/).

This script forces the CPU package power limit (PL1/2) to **44 W** (29 W on battery) and the temperature trip point to **97 'C** (85 'C on battery) by overriding default values in MSR and MCHBAR every 5 seconds (30 on battery) to block the Embedded Controller from resetting these values to default.

### Undervolt
The script now also supports **undervolting** the CPU by configuring voltage offsets for CPU, cache, GPU, System Agent and Analog I/O planes. The script will re-apply undervolt on resume from standby and hibernate by listening to DBus signals.

## Requirements
The python module `python-periphery` is used for accessing the MCHBAR register by memory mapped I/O. You also need `dbus` and `gobject` python bindings for listening to dbus signals on resume from sleep/hibernate.

## Installation
```
git clone https://github.com/erpalma/lenovo-throttling-fix.git
sudo -H pip install python-periphery
sudo apt install python-dbus python-gobject
sudo make install
sudo systemctl enable lenovo_fix.service
sudo systemctl start lenovo_fix.service
```

## Configuration
The configuration has moved to `/etc/lenovo_fix.conf`. Makefile does not overwrite your previous config file, so you need to manually check for differences in config file structure when updating the tool. If you want to overwrite the config with new defaults just issue `sudo cp etc/lenovo_fix.conf /etc`. There exist two profiles `AC` and `BATTERY` and the script can be totally disabled by setting `Enabled: False` in the `GENERAL` section. Undervolt is applied if any voltage plane in the config file (section UNDERVOLT) was set. Notice that the offset is in *mV* and only undervolting (*i.e.* negative values) is supported.  
All fields accept floating point values as well as integers. 

My T480s with i7-8550u is stable with:
```
[UNDERVOLT]
# CPU core voltage offset (mV)
CORE: -110
# Integrated GPU voltage offset (mV)
GPU: -90
# CPU cache voltage offset (mV)
CACHE: -110
# System Agent voltage offset (mV)
UNCORE: -90
# Analog I/O voltage offset (mV)
ANALOGIO: 0
```

## Disclaimer
This script overrides the default values set by Lenovo. I'm using it without any problem, but it is still experimental so use it at your own risk. This script can be probably adapted/used on other notebooks too.
