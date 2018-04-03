all: install

install:
	install -d /usr/local/sbin/
	install -d /etc/systemd/system/
	install -m 700 lenovo_fix.py /usr/local/sbin/
	install -m 644 systemd/lenovo_fix.service /etc/systemd/system/
	@if test -f /etc/lenovo_fix.conf; then \
    	echo "/etc/lenovo_fix.conf already exists; overwrite manually"; \
	else \
    	install -m 644 etc/lenovo_fix.conf /etc/; \
    fi
