.PHONY: install run daemon test clean

install:
	chmod +x scripts/*.sh
	./scripts/install.sh

run:
	python -m ubuntu_clipboard

daemon:
	python -m ubuntu_clipboard.daemon

test:
	python -m ubuntu_clipboard.daemon --once
	python -c "from ubuntu_clipboard.history import HistoryManager; hm=HistoryManager(); hm.add('سلام دنیا'); hm.add('https://example.com'); hm.add('#ff5500'); print('items:', hm.count()); [print(f\"[{i.type}] {i.preview}\") for i in hm.list(limit=5)]"

clean:
	rm -rf build dist *.egg-info __pycache__ .pytest_cache
	find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
