all: install

install:
	install -d /usr/local/sbin/
	install -m 700 lenovo_fix.py /usr/local/sbin/
	install -d /etc/systemd/system/
	install -m 644 lenovo_fix.service /etc/systemd/system/