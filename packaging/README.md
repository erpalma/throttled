# Packaging throttled

This directory documents the installation contract shared by upstream release
artifacts and downstream distribution packages.

## Runtime contract

- Linux on a supported Intel CPU, with access to `/dev/cpu/*/msr` and
  `/dev/mem`.
- Python 3.10 or newer.
- The `dbus-fast` Python module and a running system D-Bus.
- `modprobe` from kmod, `setpci` from pciutils, and UPower.
- Root privileges. The daemon is not intended to run in an unprivileged
  container, Flatpak, Snap, or AppImage sandbox.

Distribution package names differ. The upstream DEB and RPM currently use
`python3-dbus-fast`, while Arch Linux uses `python-dbus-fast`. Packagers should
map the runtime requirements to the native names of their distribution rather
than installing Python dependencies with pip.

## Filesystem contract

Native packages are staged by `scripts/stage-package.sh` with this layout:

```text
/etc/throttled.conf
/usr/bin/throttled
/usr/lib/throttled/mmio.py
/usr/lib/throttled/throttled.py
/usr/lib/throttled/throttled_version.py
/usr/lib/systemd/system/throttled.service
/usr/share/doc/throttled/copyright
```

`/etc/throttled.conf` must be treated as a conffile and preserved across
upgrades. The `/usr/bin/throttled` wrapper is the stable entry point for native
packages. The source installer instead creates the same console entry point
inside `/opt/throttled/venv`.

When `stage-package.sh` is called with `--with-openrc`, it also renders the
native OpenRC service at `/etc/init.d/throttled`. nFPM includes that file only
in APK packages and includes the systemd unit only in DEB/RPM packages.

The systemd service in `systemd/throttled.service` is the source-install
variant. The staging script derives the native variant from it, changing only
the executable path. This keeps restart behavior and future service hardening
in one place.

## Building artifacts

Build a Python wheel and source archive:

```sh
python3 -m pip install build
python3 -m build
```

The wheel contains the Python modules and console entry point. The source
archive additionally contains the configuration, service files, staging
scripts, and nFPM definition needed by downstream packagers.

Build the lightweight Debian package using only `dpkg-deb`:

```sh
./scripts/build-deb.sh --version 0.12.2
```

Build DEB, RPM, and APK packages with nFPM:

```sh
./scripts/build-packages.sh --version 0.12.2 --release 1
```

Pass `--packager deb`, `--packager rpm`, or `--packager apk` to build selected
formats. The generic RPM dependency names currently target Fedora-family
distributions. A native openSUSE package should override dependency names and
be built in OBS. The APK targets Alpine edge/testing, where `py3-dbus-fast` is
available.

## Service lifecycle

When systemd is running, the upstream Debian package reloads the manager,
enables the service, and restarts it after installation or upgrade, preserving
its existing behavior. The generic RPM follows distribution conventions: a new
installation does not start or enable the service, while an upgrade restarts it
only if it is already active. RPM users enable it explicitly with
`systemctl enable --now throttled.service`.

Removal stops and disables an installed service. Maintainer scripts detect
containers and chroots without active systemd and skip service management
there.

The APK installs an OpenRC service but leaves enable/start policy to the
administrator:

```sh
rc-update add throttled default
rc-service throttled start
```

RPM removal scripts distinguish an actual uninstall from the removal of the old
package during an upgrade. This prevents the old package scriptlet from stopping
the newly installed service.

OpenRC and runit files remain available for source and downstream packages in
`openrc/` and `runit/`.
