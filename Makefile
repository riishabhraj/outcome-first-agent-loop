SCENARIO ?= scenarios/public_01_dev.yaml
NAME ?= $(basename $(notdir $(SCENARIO)))

baseline:
	python -m sim baseline --scenario $(SCENARIO) --run-dir runs/base_$(NAME)
serve:
	python -m sim serve --scenario $(SCENARIO) --run-dir runs/mine_$(NAME) --force
grade:
	mkdir -p reports && python -m sim grade --run runs/mine_$(NAME) --baseline runs/base_$(NAME) --out reports/$(NAME).md
test:
	python -m pytest -q tests

# ---- controller
run:
	./run.sh http://127.0.0.1:8700
scenario:
	scripts/run_scenario.sh $(NAME)
crash:
	python scripts/crash_test.py public_02_incidents 8702
why:
	python -m controller why --db state/$(NAME).db --wf $(WF) --at "$(AT)"
