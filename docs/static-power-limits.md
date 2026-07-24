# Static power limits with Intel RAPL

If firmware does not periodically reset the package power limits, the kernel's
`intel_rapl` sysfs interface may be enough. This avoids running `throttled`
continuously.

> [!CAUTION]
> The values below are historical examples, not recommendations for your CPU.
> Incorrect power limits can cause instability, overheating, or hardware
> damage. Determine safe limits for the processor, voltage regulator, and
> cooling system before writing anything.

MCHBAR control through `intel-rapl-mmio` requires a kernel with the relevant
driver support (available in Linux 5.3 and newer). Some embedded controllers
overwrite these values, in which case a static configuration will not persist.

## Test the values

The following example sets both limits to 44 W, PL1's time window to 28 seconds,
and PL2's time window to 2.44 milliseconds:

```sh
# MSR constraints
echo 44000000 | sudo tee /sys/devices/virtual/powercap/intel-rapl/intel-rapl:0/constraint_0_power_limit_uw
echo 28000000 | sudo tee /sys/devices/virtual/powercap/intel-rapl/intel-rapl:0/constraint_0_time_window_us
echo 44000000 | sudo tee /sys/devices/virtual/powercap/intel-rapl/intel-rapl:0/constraint_1_power_limit_uw
echo 2440 | sudo tee /sys/devices/virtual/powercap/intel-rapl/intel-rapl:0/constraint_1_time_window_us

# MCHBAR constraints
echo 44000000 | sudo tee /sys/devices/virtual/powercap/intel-rapl-mmio/intel-rapl-mmio:0/constraint_0_power_limit_uw
echo 28000000 | sudo tee /sys/devices/virtual/powercap/intel-rapl-mmio/intel-rapl-mmio:0/constraint_0_time_window_us
echo 44000000 | sudo tee /sys/devices/virtual/powercap/intel-rapl-mmio/intel-rapl-mmio:0/constraint_1_power_limit_uw
echo 2440 | sudo tee /sys/devices/virtual/powercap/intel-rapl-mmio/intel-rapl-mmio:0/constraint_1_time_window_us
```

Not every kernel exposes both `intel-rapl` and `intel-rapl-mmio`. Only write to
paths that exist on the target system.

## Apply at boot with systemd-tmpfiles

After validating safe values, the same settings can be restored at boot with
`/etc/tmpfiles.d/power_limit.conf`:

```text
w /sys/devices/virtual/powercap/intel-rapl/intel-rapl:0/constraint_0_power_limit_uw - - - - 44000000
w /sys/devices/virtual/powercap/intel-rapl/intel-rapl:0/constraint_0_time_window_us - - - - 28000000
w /sys/devices/virtual/powercap/intel-rapl/intel-rapl:0/constraint_1_power_limit_uw - - - - 44000000
w /sys/devices/virtual/powercap/intel-rapl/intel-rapl:0/constraint_1_time_window_us - - - - 2440
w /sys/devices/virtual/powercap/intel-rapl-mmio/intel-rapl-mmio:0/constraint_0_power_limit_uw - - - - 44000000
w /sys/devices/virtual/powercap/intel-rapl-mmio/intel-rapl-mmio:0/constraint_0_time_window_us - - - - 28000000
w /sys/devices/virtual/powercap/intel-rapl-mmio/intel-rapl-mmio:0/constraint_1_power_limit_uw - - - - 44000000
w /sys/devices/virtual/powercap/intel-rapl-mmio/intel-rapl-mmio:0/constraint_1_time_window_us - - - - 2440
```

Test the file without rebooting:

```sh
sudo systemd-tmpfiles --create /etc/tmpfiles.d/power_limit.conf
```

If the values revert later, the embedded controller or another thermal manager
is still applying its own policy and the static approach is unsuitable.
