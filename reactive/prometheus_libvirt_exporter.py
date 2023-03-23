#!/usr/bin/env python3

from extensions.core import hookenv
from extensions.core.hookenv import application_version_set
from extensions.core.hookenv import log
from extensions.core.hookenv import open_port
from extensions.core.hookenv import status_set
from extensions.core.hookenv import unit_private_ip
from charms.reactive import endpoint_from_flag
from charms.reactive import hook
from charms.reactive import when
from charms.reactive import when_not
from charms.reactive.flags import clear_flag
from charms.reactive.flags import set_flag
from extensions.ansible import apply_playbook, install_ansible_support

config = hookenv.config()


@when_not('prometheus-libvirt-exporter.version')
def set_version():
    try:
        with open(file='repo-info') as f:
            for line in f:
                if line.startswith('commit-short'):
                    commit_short = line.split(':')[-1].strip()
                    application_version_set(commit_short)
    except IOError:
        log('Cannot set application version. Missing repo-info.')
    set_flag('prometheus-libvirt-exporter.version')


@when('nova-compute.joined')
@when_not('prometheus-libvirt-exporter.installed')
def install_deps():
    status_set('maintenance', 'installing dependencies')
    install_ansible_support()
    apply_playbook(
        playbook='ansible/playbook.yaml',
        extra_vars=dict(
            exp_port=config.get('port'),
            exp_host=get_ip()[0],
            libvirtservice=config.get('libvirtservice'),
        ))
    status_set('active', 'ready')
    set_flag('prometheus-libvirt-exporter.installed')


# Hooks
@hook('stop')
def stop():
    apply_playbook(
        playbook='ansible/playbook.yaml',
        tags=['uninstall'],
        extra_vars=dict(
            exp_port=config.get('port'),
            exp_host=get_ip()[0],
            libvirtservice=config.get('libvirtservice'),
        ))


@hook('start')
def start():
    install_ansible_support()
    apply_playbook(
        playbook='ansible/playbook.yaml',
        tags=['install'],
        extra_vars=dict(
            exp_port=config.get('port'),
            exp_host=get_ip()[0],
            libvirtservice=config.get('libvirtservice'),
        ))
    status_set('active', 'ready')


@hook('upgrade-charm')
def upgrade_charm():
    clear_flag('prometheus-libvirt-exporter.version')
    apply_playbook(
        playbook='ansible/playbook.yaml',
        extra_vars=dict(
            exp_port=config.get('port'),
            exp_host=get_ip()[0],
            libvirtservice=config.get('libvirtservice'),
        ))
    status_set('active', 'ready')


@hook('update-status')
def update_status():
    try:
        apply_playbook(
            playbook='ansible/playbook.yaml',
            tags=['start'],
            extra_vars=dict(
                exp_port=config.get('port'),
                exp_host=get_ip()[0],
                libvirtservice=config.get('libvirtservice'),
            ))
    except Exception:
        log('Exporter service failed to start. Need libvirt-bin service.')
        status_set('active', 'Service failed to start')
    else:
        status_set('active', 'ready')


@when('prometheus-target.available')
def configure_http(prometheus_target):
    job_name = 'libvirt-exporter'
    log('Register target {}: {}:{}'.format(
        job_name,
        get_ip()[1],
        config.get('port')
    ))
    open_port(config.get('port'))
    prometheus_target.configure(
        private_address=get_ip()[1],
        port=config.get('port')
    )


@when('endpoint.prometheus-manual-job.joined')
def register_prometheus_jobs():
    prometheus = endpoint_from_flag('endpoint.prometheus-manual-job.joined')
    job_name = 'libvirt-exporter'
    target = '{}:{}'.format(
        get_ip()[1],
        config.get('port')
    )
    log('Register manual-job {}: {}'.format(job_name, target))
    open_port(config.get('port'))
    prometheus.register_job(
        job_name=job_name,
        job_data={
            'static_configs': [{
                'targets': [target]
            }]
        })


def get_ip():
    """Get internal IP and relation IP"""
    rel_ip = None
    main_ip = unit_private_ip() if (
        not config.get('host') or (config.get('host') == "none")
    ) else config.get('host')
    if not main_ip or (main_ip == '0.0.0.0'):
        rel_ip = unit_private_ip()
    if not rel_ip:
        rel_ip = main_ip
    return main_ip, rel_ip
