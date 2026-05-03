.PHONY: install run service logs

install:
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -r requirements.txt

run:
	.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

run-prod:
	.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1

service:
	sudo cp auto-yiff.service /etc/systemd/system/
	sudo systemctl daemon-reload
	sudo systemctl enable auto-yiff
	sudo systemctl start auto-yiff
	sudo systemctl status auto-yiff

logs:
	sudo journalctl -u auto-yiff -f --no-pager
