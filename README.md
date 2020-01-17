# Prometheus Libvirt Exporter Charm

Reactive subordinate Juju charm providing prometheus-libvirt-exporter. Exporter uses Openstack metadata and passes these metadata linked to Libvirt usage metrics.

# Overview

Juju charm can relate to prometheus using a preferred endpoint `target` or alternatively `manual-jobs`.
Preferred endpoint assures that all units are in the same group, while manual jobs get unique names by prometheus.

## Bundle example

Charm is deployed with as a subordinate to nova-compute such as in example bundle.

```
series: xenial
applications:
  prometheus:
    charm: cs:bionic/prometheus2-12
    num_units: 1
  libvirt-exporter:
    charm: /tmp/charm-builds/prometheus-libvirt-exporter
    options:
      debug: true
  ubuntu:
    charm: cs:ubuntu
    num_units: 2
relations:
- - libvirt-exporter:prometheus-target
  - prometheus:target
- - ubuntu:juju-info
  - libvirt-exporter:nova-compute
```

## Scale out Usage

Subordinate charm scales with nova-compute (deployed to each node).
