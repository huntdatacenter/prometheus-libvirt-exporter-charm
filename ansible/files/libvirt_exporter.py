#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Libvirt exporter
================

Libvirt node export for OpenStack deployments.

Exporter relies on specific OS metadata.

Dependency on packages:
* python3-libvirt

======= =========
Stats   Reference
======= =========
CPU:    :py:func:`node_exporters.libvirt.libvirt_only.get_cpu_stats`
Traffic :py:func:`node_exporters.libvirt.libvirt_only.get_net_stats`
Disk    :py:func:`node_exporters.libvirt.libvirt_only.get_disk_io_stats`
Memory  :py:func:`node_exporters.libvirt.libvirt_only.get_mem_stats`
======= =========

.. list-table:: Time Units
   :widths: 25 75
   :header-rows: 1

   * - Code
     - Unit
   * - time
     - seconds
   * - mtime
     - milliseconds
   * - utime
     - microseconds (export)
   * - ntime
     - nanoseconds (cgroup stats)

"""
import argparse
import sys

from prometheus_client import start_http_server
from prometheus_client.core import REGISTRY
from prometheus_client.core import GaugeMetricFamily

try:
    from libvirtmetadata import LibvirtMetadata
except Exception:
    libvirtmetadata = None
try:
    from scheduler import Scheduler
except Exception:
    scheduler = None


class CustomCollector(object):
    def __init__(self, helper, helper_name='unknown', libv_meta=None):
        self.ALL_STATS = []
        self.HELPER = helper
        self.HELPER_NAME = helper_name
        self.libv_meta = libv_meta

    def collect(self):
        items = {}

        for stat in self.ALL_STATS:
            if stat[0] not in items:
                items[stat[0]] = []
            items[stat[0]].append(stat[1:])

        for key, values in items.items():
            labels = values[0][0]
            g = GaugeMetricFamily(key, self.HELPER, labels=labels)
            for labels, metadata, value in values:
                g.add_metric(metadata, value)
            yield g

        try:
            if self.libv_meta:
                g = GaugeMetricFamily(
                    'libvirt_connection_status',
                    'Libvirt status none (-1), connected (0), or error (1)',
                    labels=['node_exporter']
                )
                g.add_metric([self.HELPER_NAME], self.libv_meta.status)
                yield g
        except Exception:
            pass


def get_cpu_stats(stats):
    """
    Get cpu stats.

    .. list-table:: CPU stats
       :widths: 25 75
       :header-rows: 1

       * - Measure
         - Description
       * - User time (cpu_user_utime)
         - Amount of time the processor worked on the specific program
       * - System time (cpu_system_utime)
         - Amount of time the processor worked on OS functions connected to that specific program
       * - Total cpu time (cpu_total_utime)
         - Provided by OS (surprisingly it is not just a sum of user and system)
       * - VCPU time (vcpu_utime)
         - CPU time measured for each vcpu separately as one metric only, but can
           provide info about balancing inside (whether they actually use all their VCPUs)
       * - VCPU max count (vcpu_max_count)
         - How many VCPUs can be used by VM (quota)
       * - VCPU use count (vcpu_use_count)
         - How many VCPUs are currently used by VM (allocated)

    Limitations:
      * Domain total cpu data require cgroup CPUACCT controller to be mounted,
        in case of issues with measurement this has to be checked.

    :param dict stats: statistics data retrieved from libvirt.

    :return dict: stats
    """
    recount_cpu_time = False
    items = {
        'cpu_total_utime': int(stats.get('cpu.time', 0) / 1000),  # CPUACCT
        'cpu_user_utime': int(stats.get('cpu.user', 0) / 1000),  # CPUACCT
        'cpu_system_utime': int(stats.get('cpu.system', 0) / 1000),  # CPUACCT
        'cpu_max_count': stats.get('vcpu.maximum', 0),
        'cpu_use_count': 0,
        'variable': {},
    }

    if items['cpu_total_utime'] == 0:
        # If cgroup fails - missing measures in total
        recount_cpu_time = True

    for i in range(items.get('cpu_max_count')):
        if recount_cpu_time:
            items['cpu_total_utime'] += int(
                stats.get('vcpu.{}.time'.format(i), 0) / 1000)
        vcpu_state = stats.get('vcpu.{}.state'.format(i), 0)
        items['cpu_use_count'] += 1 if vcpu_state == 1 else 0
        items['variable']['vcpu:{}'.format(i)] = {
            'vcpu_utime': int(stats.get('vcpu.{}.time'.format(i), 0) / 1000),
        }
    return items


def get_net_stats(stats):
    """
    Get network traffic stats.

    Covers all interfaces - it is a sum of internal and external traffic - not just ingress/egress

    .. list-table:: Traffic stats
       :widths: 25 75
       :header-rows: 1

       * - Measure
         - Description
       * - TX count (net_tx_count)
         - Amount of processed transmissions
       * - RX count (net_rx_count)
         - Amount of processed receives
       * - TX bytes (net_tx_bytes)
         - Total transmitted volume in bytes
       * - RX bytes (net_rx_bytes)
         - Total received volume in bytes

    To measure ingress/egress we don't use data from compute nodes.

    TODO:
      * Could also get drop and error counts if wanted.

    :param dict stats: statistics data retrieved from libvirt.

    :return dict: stats
    """
    items = {
        'net_tx_count': 0,
        'net_rx_count': 0,
        'net_tx_bytes': 0,
        'net_rx_bytes': 0,
        'net_use_count': stats.get('net.count', 0),
    }

    for i in range(items.get('net_use_count')):
        items['net_tx_count'] += stats.get('net.{}.tx.pkts'.format(i), 0)
        items['net_rx_count'] += stats.get('net.{}.rx.pkts'.format(i), 0)
        items['net_tx_bytes'] += stats.get('net.{}.tx.bytes'.format(i), 0)
        items['net_rx_bytes'] += stats.get('net.{}.rx.bytes'.format(i), 0)
    return items


def get_disk_io_stats(stats):
    """
    Get disk IO stats.

    IO stats are available both as total and per device.

    .. list-table:: IO stats
       :widths: 25 75
       :header-rows: 1

       * - Measure
         - Description
       * - Read count (disk_read_count)
         - The number of read input/output operations generated by a process,
           including file, network, and device I/Os. I/O Reads directed to CONSOLE
           (console input object) handles are not counted.
       * - Write count (disk_write_count)
         - The number of write input/output operations generated by a process,
           including file, network, and device I/Os. I/O Writes directed to
           CONSOLE (console input object) handles are not counted.
       * - Read bytes (disk_read_bytes)
         - The number of bytes read in input/output operations generated by
           a process, including file, network, and device I/Os. I/O Read Bytes
           directed to CONSOLE (console input object) handles are not counted.
       * - Write bytes (disk_write_bytes)
         - The number of bytes written in input/output operations generated by
           a process, including file, network, and device I/Os. I/O Write Bytes
           directed to CONSOLE (console input object) handles are not counted.
       * - Disk use count (disk_use_count)
         - Amount of used devices.

    Limitations:
      * CPU read/write (IO) provide different information than storage (e.g. ceph) stats would show,
        because for cpu IO operations are not just disk operations (includes file, network, and device I/Os).
      * https://superuser.com/questions/993966/what-does-i-o-reads-or-writes-and-i-o-read-bytes-or-write-bytes-mean

    TODO:
      * Could also get flush reqs count and times

    :param dict stats: statistics data retrieved from libvirt.

    :return dict: stats
    """
    items = {
        'disk_write_bytes': 0,
        'disk_read_bytes': 0,
        'disk_write_count': 0,
        'disk_read_count': 0,
        'disk_use_count': stats.get('block.count', 0),
        'variable': {},
    }

    for i in range(items.get('disk_use_count', 0)):
        items['disk_write_bytes'] += stats.get(
            'block.{}.wr.bytes'.format(i), 0)
        items['disk_read_bytes'] += stats.get('block.{}.rd.bytes'.format(i), 0)
        items['disk_write_count'] += stats.get(
            'block.{}.wr.times'.format(i), 0)
        items['disk_read_count'] += stats.get('block.{}.rd.times'.format(i), 0)
        disk_name = stats.get('block.{}.name'.format(i), None)
        if disk_name:
            items['variable']['device:{}'.format(disk_name)] = {
                'dev_total_bytes': stats.get('block.{}.physical'.format(i), 0),
                'dev_write_bytes': stats.get('block.{}.wr.bytes'.format(i), 0),
                'dev_read_bytes': stats.get('block.{}.rd.bytes'.format(i), 0),
                'dev_write_count': stats.get('block.{}.wr.times'.format(i), 0),
                'dev_read_count': stats.get('block.{}.rd.times'.format(i), 0),
            }
    return items


def get_mem_stats(domain, stats):
    """
    Get memory stats.

    .. list-table:: Memory stats
       :widths: 25 75
       :header-rows: 1

       * - Measure
         - Description
       * - Total (mem_max_bytes)
         - Amount of memory specified on domain creation of VM
       * - Used (mem_use_bytes)
         - Returns values close to total, because VM holds all the memory needed as
           appears from the outside and does not reflect internal state of OS here.
       * - Free (mem_free_bytes)
         - Amount of memory left unused by the system (opposite)
       * - Rss (mem_rss_bytes)
         - Resident Set Size of the process running the domain

    Limitations:
      * both used and free reflect the process, but not what is seen inside VM, so it may
        not be useful for user or billing. Will have to review with higher versions of
        Openstack when we move on.

    :param domain: libvirt domain object for extra stats.
    :param dict stats: statistics data retrieved from libvirt.

    :return dict: stats
    """
    items = {}
    try:
        mem = domain.memoryStats()
        items = {
            'mem_use_bytes': 1000 * mem.get('available', 0) if mem.get('available', 0) >= 0 else 0,
            'mem_rss_bytes': 1000 * mem.get('rss', 0) if mem.get('rss', 0) >= 0 else 0,
            'mem_free_bytes': 1000 * mem.get('unused', 0) if mem.get('unused', 0) >= 0 else 0,
            'mem_max_bytes': 1000 * mem.get('actual', 0) if mem.get('actual', 0) >= 0 else 0,
        }
    except Exception:
        items = {
            'mem_use_bytes': 1000 * stats.get('balloon.current', 0),
            'mem_max_bytes': 1000 * stats.get('balloon.maximum', 0),
        }
    return items


def prom_stats(libv_meta, cc):
    """Gather and export prometheus stats."""
    all_stats = []

    try:
        with libv_meta.libvirt_connection() as conn:
            # all_domain_stats = conn.getAllDomainStats(stats=libv_meta.STATS, flags=libv_meta.FLAGS)
            # for domain, stats in all_domain_stats:
            for dom in conn.listAllDomains(flags=libv_meta.LIST_DOMAINS_RUNNING):
                stat_list = []
                try:
                    state = int(dom.state()[0])
                except Exception:
                    state = 0
                try:
                    control_state = int(dom.controlInfo()[0])
                except Exception:
                    control_state = 4
                try:
                    control_time = int(dom.controlInfo()[-1] / 1000)
                except Exception:
                    control_time = -1
                try:
                    instance = dom.name()
                    metadata = libv_meta.get_instance_metadata(instance, dom)
                    try:
                        all_stats.extend(libv_meta.export({
                            'vm_control_time': control_time,
                            'vm_control_state': control_state,
                            'vm_state': state
                        }, instance, metadata=metadata))
                    except Exception:
                        pass
                    if state == libv_meta.DOMAIN_RUNNING and control_time < 300 and control_time >= 0:
                        # Ignore if domain not running or busy/locked for more than 5 min
                        stat_list = conn.domainListGetStats(
                            [dom], stats=libv_meta.STATS, flags=libv_meta.FLAGS)
                except Exception:
                    stat_list = []
                for domain, stats in stat_list:
                    try:
                        all_stats.extend(libv_meta.export(
                            get_cpu_stats(stats), instance, metadata=metadata))
                        all_stats.extend(libv_meta.export(
                            get_net_stats(stats), instance, metadata=metadata))
                        all_stats.extend(libv_meta.export(
                            get_disk_io_stats(stats), instance, metadata=metadata))
                        all_stats.extend(libv_meta.export(get_mem_stats(
                            domain, stats), instance, metadata=metadata))
                        all_stats.extend(libv_meta.export(libv_meta.get_cpu_meta(
                            domain), instance, metadata=metadata))
                    except Exception:
                        pass
    except Exception:
        libv_meta.status = 1  # error

    cc.ALL_STATS = all_stats


def shell(args):
    """Start iPython shell for direct management access."""
    from IPython import embed
    from datetime import datetime as dt  # noqa
    from datetime import timedelta as td  # noqa
    from datetime import time  # noqa
    import logging
    logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)
    libv_meta = LibvirtMetadata()  # noqa
    embed(header="Welcome in iPython shell")


def main(args):
    scheduler = Scheduler()
    libv_meta = LibvirtMetadata()
    try:
        libv_meta.load_libvirt_metadata()
    except Exception:
        pass

    cc = CustomCollector('Libvirt instance stats',
                         helper_name='libvirt', libv_meta=libv_meta)
    REGISTRY.register(cc)
    start_http_server(args.port, addr=args.addr)
    scheduler.log(
        'Exposing metrics at: http://{}:{}/metrics'.format(args.addr, args.port))

    # Every 'wait_time' seconds
    scheduler.add_periodic_task(
        prom_stats, 'second', round=args.wait_time, args=(libv_meta, cc)
    )
    # Every 20 minutes
    scheduler.add_periodic_task(
        libv_meta.load_libvirt_metadata, 'minute', round=20)

    scheduler.run_concurrent(debug=args.debug)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Libvirt textfile exporter')
    parser.set_defaults(func=main)
    subparsers = parser.add_subparsers(
        dest='action', help='Choose an alternative action (optional)')

    parser.add_argument(
        '-p', '--port', dest='port', default=9121, type=int,
        help='Port to expose metrics'
    )
    parser.add_argument(
        '-i', '--ip', dest='addr', default='0.0.0.0',
        help='IP address to expose metrics'
    )
    parser.add_argument(
        '-t', '--wait-time', dest='wait_time', default=2, type=int,
        help='Time to sleep between measures [2-30]'
    )
    parser.add_argument('--debug', dest='debug',
                        action='store_true', help='Debug messages')
    subparsers.add_parser(
        'shell', help='Run iPython shell').set_defaults(func=shell)

    args = parser.parse_args()
    args.func(args)
