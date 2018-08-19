# Fix Intel CPU Throttling on Linux
Workaround for Linux throttling issues on Lenovo T480 / T480s / X1C6 notebooks as described [here](https://www.reddit.com/r/thinkpad/comments/870u0a/t480s_linux_throttling_bug/).

This script forces the CPU package power limit (PL1/2) to **44 W** (29 W on battery) and the temperature trip point to **95 'C** (85 'C on battery) by overriding default values in MSR and MCHBAR every 5 seconds (30 on battery) to block the Embedded Controller from resetting these values to default.

### Supported hardware
Other users have confirmed that the script is also working for these laptops:
- Lenovo T480
- Lenovo T480s
- Lenovo X1C5
- Lenovo X1C6
- Lenovo T580
- Lenovo L480
- Dell XPS 9370

I will keep this list updated.

### Is this script really doing something on my PC??
I suggest you to use the excellent **[s-tui](https://github.com/amanusk/s-tui)** tool to check and monitor the CPU usage, frequency, power and temperature under load!

### Undervolt
The script now also supports **undervolting** the CPU by configuring voltage offsets for CPU, cache, GPU, System Agent and Analog I/O planes. The script will re-apply undervolt on resume from standby and hibernate by listening to DBus signals.

### HWP override (EXPERIMENTAL)
I have found that under load my CPU was not always hitting max turbo frequency, in particular when using one/two cores only. For instance, when running [prime95](https://www.mersenne.org/download/) (1 core, test #1) my CPU is limited to about 3500 MHz over the theoretical 4000 MHz maximum. The reason is the value for the HWP energy performance [hints](http://manpages.ubuntu.com/manpages/artful/man8/x86_energy_perf_policy.8.html). By default TLP sets this value to "balance_performance" on AC in order to reduce the power consumption/heat in idle. By setting this value to "performance" I was able to reach 3900 MHz in the prime95 single core test, achieving a +400 MHz boost. Since this value forces the CPU to full speed even during idle, a new experimental feature allows to automatically set HWP to performance under load and revert it to balanced when idle. This feature can be enabled (in AC mode *only*) by setting to `True` the `HWP_Mode` parameter in the config file.

I have run **[Geekbench 4](https://browser.geekbench.com/v4/cpu/8656840)** and now I can get a score of 5391/17265! On balance_performance I can reach only 4672/16129, so **15% improvement** in single core and 7% in multicore, not bad ;)

### setting cTDP (EXPERIMENTAL)
On a lot of modern CPUs from Intel one can configure the TDP up or down based on predefined profiles. This is what this option does. For a i7-8650U normal would be 15W, up profile is setting it to 25W and down to 10W. You can lookup the values of your CPU at the Intel product website.

## Requirements
A stripped down version of the python module `python-periphery` is now built-in and it is used for accessing the MCHBAR register by memory mapped I/O. You also need `dbus` and `gobject` python bindings for listening to dbus signals on resume from sleep/hibernate.

### Secure Boot
Right now it is mandatory to **disable Secure Boot** (in BIOS) in order to avoid [Kernel Lockdown](https://lwn.net/Articles/706637/). In particular Lockdown restricts access to MSR and PCI BAR (via /dev/mem) which are required by this script.

### Thermald
As discovered by *DEvil0000* the Linux Thermal Monitor ([thermald](https://github.com/intel/thermal_daemon)) can conflict with the purpose of this script. In particular, thermald might be pre-installed (e.g. on Ubuntu) and configured in such a way to keep the CPU temperature below a certain threshold (~80 'C) by applying throtthling or messing up with RAPL or other CPU-specific registers. I strongly suggest to either disable/uninstall it or to review its default configuration.

### Update
The scripts is now running with Python3 by default (tested w/ 3.6) and a virtualenv is automatically created in `/opt/lenovo_fix`. Python2 should probably still work.

## Installation

### Arch Linux [AUR package](https://aur.archlinux.org/packages/lenovo-throttling-fix-git/):
```
yaourt -S lenovo-throttling-fix-git
sudo systemctl enable --now lenovo_fix.service
```
Thanks to *felixonmars* for creating and maintaining this package.

### Debian/Ubuntu
```
sudo apt install git virtualenv build-essential python3-dev libdbus-glib-1-dev libgirepository1.0-dev libcairo2-dev
git clone https://github.com/erpalma/lenovo-throttling-fix.git
sudo ./install.sh
```
If you own a X1C6 you can also check a tutorial for Ubuntu 18.04 [here](https://mensfeld.pl/2018/05/lenovo-thinkpad-x1-carbon-6th-gen-2018-ubuntu-18-04-tweaks/).

You should make sure that **_thermald_** is not setting it back down. Stopping/disabling it will do the trick:
```
sudo systemctl stop thermald.service
sudo systemctl disable thermald.service
```

### Fedora
```
dnf install cairo-gobject-devel gobject-introspection-devel dbus-glib-devel python-virtualenv
git clone https://github.com/erpalma/lenovo-throttling-fix.git
sudo ./install.sh
```
Feedback about Fedora installation is welcome.

### openSUSE
User *brycecordill* reported that the following dependecies are required for installing in openSUSE. I guess that python2 dependecies can be safely dropped. I would really appreciate any feedback from openSUSE users.
```
zypper install gcc python2-pip pyton3-devel python-devel dbus1-glib-devel python3-cairo-devel cairo-devel python2-cairo-devel python3-gobject-cairo gobject-introspection-devel python-virtualenv 
git clone https://github.com/erpalma/lenovo-throttling-fix.git
sudo ./install.sh
```

## Configuration
The configuration has moved to `/etc/lenovo_fix.conf`. Makefile does not overwrite your previous config file, so you need to manually check for differences in config file structure when updating the tool. If you want to overwrite the config with new defaults just issue `sudo cp etc/lenovo_fix.conf /etc`. There exist two profiles `AC` and `BATTERY` and the script can be totally disabled by setting `Enabled: False` in the `GENERAL` section. Undervolt is applied if any voltage plane in the config file (section UNDERVOLT) was set. Notice that the offset is in *mV* and only undervolting (*i.e.* negative values) is supported.
All fields accept floating point values as well as integers.

My T480s with i7-8550u is stable with:
```
[UNDERVOLT]
# CPU core voltage offset (mV)
CORE: -105
# Integrated GPU voltage offset (mV)
GPU: -85
# CPU cache voltage offset (mV)
CACHE: -105
# System Agent voltage offset (mV)
UNCORE: -85
# Analog I/O voltage offset (mV)
ANALOGIO: 0
```
**IMPORTANT:** Please notice that *my* system is stable with these values. Your notebook might crash even with slight undervolting! You should test your system and slowly incresing undervolt to find the maximum stable value for your CPU. You can check [this](https://www.notebookcheck.net/Intel-Extreme-Tuning-Utility-XTU-Undervolting-Guide.272120.0.html) tutorial if you don't know where to start.

## Debug
You can enable the `--debug` option to read back written values and check if the script is working properly. This is an example output:
```
./lenovo_fix.py --debug
[D] TEMPERATURE_TARGET - write 0xf - read 0xf
[D] Undervolt plane CORE - write 0xf2800000 - read 0xf2800000
[D] Undervolt plane GPU - write 0xf5200000 - read 0xf5200000
[D] Undervolt plane CACHE - write 0xf2800000 - read 0xf2800000
[D] Undervolt plane UNCORE - write 0xf5200000 - read 0xf5200000
[D] Undervolt plane ANALOGIO - write 0x0 - read 0x0
[D] MSR PACKAGE_POWER_LIMIT - write 0xcc816000dc80e8 - read 0xcc816000dc80e8
[D] MCHBAR PACKAGE_POWER_LIMIT - write 0xcc816000dc80e8 - read 0xcc816000dc80e8
[D] TEMPERATURE_TARGET - write 0xf - read 0xf
```

## Disclaimer
This script overrides the default values set by Lenovo. I'm using it without any problem, but it is still experimental so use it at your own risk.
