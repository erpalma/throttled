# Replace GLib DBus Loop With dbus-next

## Goal

Remove the native `PyGObject`/GLib runtime dependency while preserving the existing DBus-driven behavior for AC/BATTERY changes and resume-from-sleep handling.

## Current State

`throttled.py` uses `dbus-python` for DBus access and `PyGObject` only for `GLib.MainLoop()`. The GLib loop dispatches callbacks registered with `bus.add_signal_receiver()`:

- `org.freedesktop.UPower.PropertiesChanged` updates `power['source']`.
- `org.freedesktop.login1.Manager.PrepareForSleep` reapplies undervolt and IccMax on resume.

The MSR, MCHBAR, config parsing, polling, monitor, and worker thread logic do not depend on GLib.

## Design

Use `dbus-next`'s asyncio backend as the DBus client stack. Keep the existing `power_thread` model: the thread continues to perform periodic register writes and sysfs polling. The main thread owns an asyncio loop that connects to the system bus, registers signal callbacks, and waits until interrupted.

`is_on_battery()` should no longer depend on `dbus-python`. Its sysfs path remains unchanged. Its DBus fallback should call a small synchronous helper that reads `org.freedesktop.UPower.OnBattery` by running the asyncio DBus property read to completion.

The daemon startup should:

- connect to the system bus with `dbus_next.aio.MessageBus(bus_type=BusType.SYSTEM)`;
- create a proxy for `/org/freedesktop/UPower` and register `PropertiesChanged`;
- create a proxy for `/org/freedesktop/login1` and register `PrepareForSleep` only when undervolt or IccMax is configured;
- use `await asyncio.Future()` as the long-running event wait.

Shutdown should cancel the asyncio wait, set `exit_event`, disconnect from DBus, and join worker threads as before.

## Dependencies

Replace:

- `dbus-python`
- `PyGObject`

with:

- `dbus-next==0.2.3`

Update `configparser` to `7.2.0`, the current PyPI release for Python 3.9+.

## Error Handling

DBus fallback failures in `is_on_battery()` should log the existing warning and assume battery power. DBus setup failures during daemon startup should remain fatal enough to surface immediately, matching today's behavior when the system bus/main loop cannot be initialized.

## Testing

Add unit tests for the DBus-facing helpers without connecting to the real system bus:

- AC/BATTERY callback unwraps `dbus_next.Variant` values and updates `power`.
- resume callback only reapplies settings when `sleeping` is false.
- configured undervolt/IccMax detection keeps the current behavior.

Run syntax compilation for `throttled.py` after the code change.
