# Use one shell for all commands in a target recipe
.ONESHELL:
# Commands
.PHONY: build name list launch mount umount bootstrap up down ssh destroy lint upgrade force-upgrade
# Set default goal
.DEFAULT_GOAL := help
# Use bash shell in Make instead of sh
SHELL := /bin/bash
# Charm variables
CHARM_NAME := prometheus-libvirt-exporter
CHARM_STORE_URL := cs:~huntdatacenter/prometheus-libvirt-exporter
CHARM_HOMEPAGE := https://github.com/huntdatacenter/prometheus-libvirt-exporter-charm/
CHARM_BUGS_URL := https://github.com/huntdatacenter/prometheus-libvirt-exporter-charm/issues
CHARM_BUILD_DIR := /tmp/charm-builds
CHARM_PATH := $(CHARM_BUILD_DIR)/$(CHARM_NAME).charm

UBUNTU_VERSION = jammy
MOUNT_TARGET = /vagrant
DIR_NAME = "$(shell basename $(shell pwd))"
VM_NAME = juju-dev--$(DIR_NAME)

name:  ## Print name of the VM
	echo "$(VM_NAME)"

list:  ## List existing VMs
	multipass list

launch:
	multipass launch $(UBUNTU_VERSION) -v --timeout 3600 --name $(VM_NAME) --memory 4G --cpus 4 --disk 20G --cloud-init juju.yaml \
	&& multipass exec $(VM_NAME) -- cloud-init status

mount:
	echo "Assure allowed in System settings > Privacy > Full disk access for multipassd"
	multipass mount --type 'classic' --uid-map $(shell id -u):1000 --gid-map $(shell id -g):1000 $(PWD) $(VM_NAME):$(MOUNT_TARGET)

umount:
	multipass umount $(VM_NAME):$(MOUNT_TARGET)

bootstrap:
	$(eval ARCH := $(shell multipass exec $(VM_NAME) -- dpkg --print-architecture))
	multipass exec $(VM_NAME) -- juju bootstrap localhost lxd --bootstrap-constraints arch=$(ARCH) \
	&& multipass exec $(VM_NAME) -- juju add-model default

up: launch mount bootstrap  ## Start a VM

down:  ## Stop the VM
	multipass down $(VM_NAME)

ssh:  ## Connect into the VM
	multipass exec -d $(MOUNT_TARGET) $(VM_NAME) -- bash

destroy:  ## Destroy the VM
	multipass delete -v --purge $(VM_NAME)


lint: ## Run linter
	tox -e lint


build: ## Build charm
	mkdir -p $(CHARM_BUILD_DIR)
	tox -e build
	cp prometheus-libvirt-exporter_ubuntu-20.04-amd64_ubuntu-22.04-amd64.charm $(CHARM_PATH)


deploy: ## Deploy charm
	juju deploy $(CHARM_PATH)


upgrade: ## Upgrade charm
	juju upgrade-charm $(CHARM_NAME) --path $(CHARM_PATH)


force-upgrade: ## Force upgrade charm
	juju upgrade-charm $(CHARM_NAME) --path $(CHARM_PATH) --force-units


test-xenial-bundle: ## Test Xenial bundle
	tox -e test-xenial


test-bionic-bundle: ## Test Bionic bundle
	tox -e test-bionic


push: clean build generate-repo-info ## Push charm to stable channel
	@echo "Publishing $(CHARM_STORE_URL)"
	@export rev=$$(charm push $(CHARM_PATH) $(CHARM_STORE_URL) 2>&1 \
		| tee /dev/tty | grep url: | cut -f 2 -d ' ') \
	&& charm release --channel stable $$rev \
	&& charm grant $$rev --acl read everyone \
	&& charm set $$rev extra-info=$$(git rev-parse --short HEAD) \
		bugs-url=$(CHARM_BUGS_URL) homepage=$(CHARM_HOMEPAGE)


clean: ## Clean .tox and build
	@echo "Cleaning files"
	@if [ -d $(CHARM_PATH) ] ; then rm -r $(CHARM_PATH) ; fi
	@if [ -d .tox ] ; then rm -r .tox ; fi


# Internal targets
clean-repo:
	@if [ -n "$$(git status --porcelain)" ]; then \
		echo '!!! Hard resetting repo and removing untracked files !!!'; \
		git reset --hard; \
		git clean -fdx; \
	fi

generate-repo-info:
	@if [ -f $(CHARM_PATH)/repo-info ] ; then rm -r $(CHARM_PATH)/repo-info ; fi
	@echo "commit: $$(git rev-parse HEAD)" >> $(CHARM_PATH)/repo-info
	@echo "commit-short: $$(git rev-parse --short HEAD)" >> $(CHARM_PATH)/repo-info
	@echo "branch: $$(git rev-parse --abbrev-ref HEAD)" >> $(CHARM_PATH)/repo-info
	@echo "remote: $$(git config --get remote.origin.url)" >> $(CHARM_PATH)/repo-info
	@echo "generated: $$(date -u)" >> $(CHARM_PATH)/repo-info


# Display target comments in 'make help'
help: ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {sub("\\\\n",sprintf("\n%22c"," "), $$2);printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
