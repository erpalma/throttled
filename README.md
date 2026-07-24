# throttled

[![Latest release](https://img.shields.io/github/v/release/erpalma/throttled)](https://github.com/erpalma/throttled/releases/latest)
[![CI](https://github.com/erpalma/throttled/actions/workflows/ci.yml/badge.svg)](https://github.com/erpalma/throttled/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/github/license/erpalma/throttled)](https://github.com/erpalma/throttled/blob/master/LICENSE)

`throttled` is a Linux daemon that prevents unwanted CPU throttling on some
Intel-based systems. It periodically restores package power limits (PL1/PL2)
and the temperature target in MSR and MCHBAR registers when firmware or the
embedded controller resets them.

It also supports separate AC and battery profiles, undervolting, IccMax,
cTDP, HWP hints, and live throttling diagnostics.

> [!CAUTION]
> `throttled` runs as root and writes directly to CPU and chipset registers.
> Incorrect power, temperature, voltage, or current values can make a system
> unstable or damage hardware. Review the configuration for your CPU and
> cooling system before enabling the service.

## When to use it

Use `throttled` when an Intel laptop or small-form-factor system runs below its
expected frequency because firmware repeatedly applies overly conservative
power or temperature limits. Confirm the problem first with
[`s-tui`](https://github.com/amanusk/s-tui), `turbostat`, or:

```sh
sudo throttled --monitor
```

The project started as a workaround for the Lenovo T480, T480s, and X1 Carbon
Gen 6, but users have reported success on many Lenovo, Dell, HP, Microsoft, and
ASUS systems. Support depends on the CPU, firmware, and kernel rather than the
laptop brand. See the
[community-reported hardware list](https://github.com/erpalma/throttled/blob/master/docs/supported-hardware.md).

## Features

- Restores configurable PL1, PL2, time-window, and temperature limits.
- Uses independent AC and battery profiles and reacts to power-source changes.
- Re-applies settings after suspend and hibernation through system D-Bus.
- Supports configurable CPU, cache, GPU, System Agent, and Analog I/O
  undervolting where firmware permits it.
- Supports IccMax overrides and experimental cTDP, HWP, and BD PROCHOT controls.
- Reloads `/etc/throttled.conf` automatically when the file changes.
- Reports thermal, power, current, and cross-domain throttling causes in real
  time.
- Runs with systemd, OpenRC, or runit.

## Installation

### Release packages

The [latest release](https://github.com/erpalma/throttled/releases/latest)
provides native packages and Python distributions. Native packages include the
daemon, default configuration, and service definition.

| System | Package |
| --- | --- |
| Debian-family system with `python3-dbus-fast` | [DEB](https://github.com/erpalma/throttled/releases/download/v0.12.2/throttled_0.12.2_all.deb) |
| Fedora-family system | [RPM](https://github.com/erpalma/throttled/releases/download/v0.12.2/throttled-0.12.2-1.noarch.rpm) |
| Alpine edge/testing | [APK](https://github.com/erpalma/throttled/releases/download/v0.12.2/throttled_0.12.2-1_noarch.apk) |
| Python 3.10+ | [wheel](https://github.com/erpalma/throttled/releases/download/v0.12.2/throttled-0.12.2-py3-none-any.whl) |
| Packagers | [source archive](https://github.com/erpalma/throttled/releases/download/v0.12.2/throttled-0.12.2.tar.gz) |

Verify downloads with the
[`SHA256SUMS`](https://github.com/erpalma/throttled/releases/download/v0.12.2/SHA256SUMS)
file published with the release.

DEB:

```sh
sudo apt install ./throttled_0.12.2_all.deb
```

On a running systemd host, the DEB enables and starts the service.

Fedora:

```sh
sudo dnf install ./throttled-0.12.2-1.noarch.rpm
sudo systemctl enable --now throttled.service
```

Alpine:

```sh
doas apk add --allow-untrusted ./throttled_0.12.2-1_noarch.apk
doas rc-update add throttled default
doas rc-service throttled start
```

The upstream APK is not repository-signed, which is why local installation
requires `--allow-untrusted`.

The wheel installs the `throttled` command only. It does not install the
privileged service, OS dependencies, or `/etc/throttled.conf`; end users should
prefer a native package or the source installer.

### Distribution packages

- Arch Linux: `sudo pacman -S throttled`
- Alpine edge/testing: `doas apk add throttled`
- Fedora:
  [Copr package](https://copr.fedorainfracloud.org/coprs/abn/throttled/)
- Gentoo: `sudo emerge -av sys-power/throttled`

Distribution packages are maintained independently and can lag behind the
latest upstream release.

### Install from source

Install Python 3.10 or newer, the Python virtual-environment tooling, `git`,
`kmod`, `pciutils`, and UPower using your distribution package manager. Then:

```sh
git clone https://github.com/erpalma/throttled.git
cd throttled
sudo ./install.sh
```

The installer creates an isolated environment in `/opt/throttled`, preserves
an existing `/etc/throttled.conf`, detects systemd, OpenRC, or runit, and
enables the service. Use `--init systemd|openrc|runit` to override detection,
or `--no-start` to install without enabling or starting it.

## Configuration

The configuration file is `/etc/throttled.conf`. It contains separate `[AC]`
and `[BATTERY]` profiles for power limits, update intervals, temperature
targets, and experimental controls:

```ini
[GENERAL]
Enabled: True
Autoreload: True

[AC]
Update_Rate_s: 5
PL1_Tdp_W: 44
PL1_Duration_s: 28
PL2_Tdp_W: 44
PL2_Duration_S: 0.002
Trip_Temp_C: 95

[BATTERY]
Update_Rate_s: 30
PL1_Tdp_W: 29
PL1_Duration_s: 28
PL2_Tdp_W: 44
PL2_Duration_S: 0.002
Trip_Temp_C: 85
```

These are project defaults, not recommendations for every system. Check your
processor limits and cooling capacity before changing them. The service reloads
valid configuration changes automatically when `Autoreload` is enabled.

### Undervolting and IccMax

Voltage offsets can be configured independently in `[UNDERVOLT.AC]` and
`[UNDERVOLT.BATTERY]`. Only zero or negative millivolt values are accepted.
Start at zero and test small changes under load; values stable on one CPU can
crash another CPU of the same model.

Undervolting is disabled by firmware or microcode on many newer Intel systems.
`throttled` cannot bypass a locked voltage interface.

IccMax values in `[ICCMAX.AC]` and `[ICCMAX.BATTERY]` are absolute current
limits in amperes, not offsets. Inspect the system defaults with `--monitor`
before enabling them.

## Operation and diagnostics

Check the service:

```sh
systemctl status throttled.service
journalctl -u throttled.service -b
```

On OpenRC, use `rc-service throttled status`; on runit, use
`sv status throttled`.

Monitor throttling causes and register values:

```sh
sudo throttled --monitor
sudo throttled --monitor 0.5
sudo throttled --debug
```

The monitor distinguishes thermal, power, current, and cross-domain limits.
`--debug` reads back written values and prints CPU feature and thermal status
information. Both commands run the control loop in the foreground, so stop the
service first to avoid running two instances and press `Ctrl+C` when finished.
Run `throttled --help` for all command-line options.

## Requirements and known conflicts

- Linux on a supported Intel CPU.
- Python 3.10 or newer.
- Access to `/dev/cpu/*/msr` and `/dev/mem`.
- A kernel with `CONFIG_X86_MSR` and `CONFIG_DEVMEM`.
- `dbus-fast`, a running system D-Bus, UPower, `modprobe`, and `setpci`.
- Root privileges; containers, Flatpak, Snap, and AppImage sandboxes are not
  supported deployment targets.

Secure Boot commonly enables Kernel Lockdown, which can block the MSR and PCI
BAR access required by `throttled`. Check the service log and
`/sys/kernel/security/lsm` before changing security settings. Disabling Secure
Boot or Kernel Lockdown reduces system protection and should be an informed,
system-specific decision. Hardened kernels can deny `/dev/mem` access even
without Lockdown.

`thermald` can conflict by applying its own RAPL or temperature policy. Do not
disable it blindly: some platforms work better with `thermald --adaptive`.
Compare monitoring output with each setup and keep the policy that behaves
correctly on your hardware.

TLP only overlaps with `throttled` when the experimental `HWP_Mode` setting is
enabled. In that case, disable TLP's `CPU_ENERGY_PERF_POLICY_ON_*` settings by
assigning an empty string, as described in the
[TLP conflict guide](https://linrunner.de/tlp/faq/conflicts.html). Avoid running
multiple tools that continuously overwrite the same HWP/EPP policy.

Manufacturer utilities can persist quiet or power-saving profiles in firmware,
so settings previously selected in another operating system may continue to
limit performance after booting Linux.

If firmware does not repeatedly reset the limits, the kernel's `intel_rapl`
interface may be sufficient and avoids a resident daemon. See the
[static RAPL alternative](https://github.com/erpalma/throttled/blob/master/docs/static-power-limits.md).

## Updating and removal

For a native package, use the distribution package manager and review changes
to `/etc/throttled.conf` after upgrading.

For a source installation:

```sh
cd throttled
git pull --ff-only
sudo ./install.sh
```

To stop and disable the service:

```sh
sudo systemctl disable --now throttled.service
```

OpenRC users can run `sudo rc-service throttled stop` followed by
`sudo rc-update del throttled default`. Runit users can run
`sudo sv down throttled` and remove the `/var/service/throttled` link. Remove
native packages with the corresponding package manager.

## Packaging and development

Build the Python wheel and source archive:

```sh
python3 -m pip install build
python3 -m build
```

Build all native package formats with
[nFPM](https://nfpm.goreleaser.com/):

```sh
./scripts/build-packages.sh
```

Use `--packager deb`, `--packager rpm`, or `--packager apk` to select a
format. The lightweight DEB builder only requires `dpkg-deb`:

```sh
./scripts/build-deb.sh
```

See the
[packaging guide](https://github.com/erpalma/throttled/blob/master/packaging/README.md)
for the runtime and filesystem contract, dependency mapping, service lifecycle,
and downstream packaging notes.

Run the test suite with:

```sh
python3 -m unittest discover -s tests
```

Bug reports and pull requests are welcome in the
[issue tracker](https://github.com/erpalma/throttled/issues).

## Project history

The project was created for a
[firmware throttling problem](https://www.reddit.com/r/thinkpad/comments/870u0a/t480s_linux_throttling_bug/)
seen on the 2018 Lenovo ThinkPad generation. It was originally installed as
`lenovo_fix` and was renamed to `throttled` in 2021. Current installations use
`/etc/throttled.conf`, the `throttled` command, and `throttled.service`.

## License

`throttled` is available under the
[MIT License](https://github.com/erpalma/throttled/blob/master/LICENSE).
