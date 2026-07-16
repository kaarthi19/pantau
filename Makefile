.PHONY: install dry-run run collect score render digest sweep ics test clean

install:
	pip install -r requirements.txt

dry-run:            ## collect + per-source counts, no writes
	python -m pantau.run --dry-run

run:                ## full pipeline (keyword mode unless ANTHROPIC_API_KEY set)
	python -m pantau.run

collect:
	python -m pantau.run --stage collect

score:
	python -m pantau.run --stage score

render:
	python -m pantau.run --stage render

digest:
	python -m pantau.run --stage digest --force-digest

sweep:              ## force the monthly agentic fellowship sweep
	python -m pantau.run --stage score --force-sweep

ics:                ## regenerate calendar/pantau.ics from Tier-0 programs
	python scripts/make_ics.py

test:
	python -m pytest tests/ -q

clean:
	rm -rf **/__pycache__ .pytest_cache
