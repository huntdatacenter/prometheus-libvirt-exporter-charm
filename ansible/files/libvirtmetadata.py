"""
Libvirt metadata
================

Connection to libvirt and export of metadata.
Includes VM info held by libvirt and statistics.

"""
import re
import uuid
import xml.etree.ElementTree as ET
from contextlib import contextmanager

try:
    import libvirt
except Exception:
    libvirt = None


class LibvirtMetadata:
    """
    Libvirt metadata

    Handling of libvirt data and retrieval of domain/instance metadata.
    Class is intended to be shared between exporters for compute nodes.
    """

    def __init__(self, xmlns='http://openstack.org/xmlns/libvirt/nova/1.0'):
        self.uuidp = re.compile(
            '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.I)
        self.STATS = 0
        self.FLAGS = libvirt.VIR_CONNECT_GET_ALL_DOMAINS_STATS_RUNNING
        self.LIBVIRT_INSTANCES = {}
        self.xmlns = xmlns
        self.status = -1  # uninitialized

    @contextmanager
    def libvirt_connection(self):
        """Yield readonly connection to libvirt."""
        try:
            conn = libvirt.openReadOnly(None)
            try:
                if conn:
                    yield conn
                    self.status = 0  # connected
            finally:
                conn.close()
        except libvirt.libvirtError:
            self.status = 1  # error

    def _load_xml_tree(self, tree):
        """
        Load XML tree into dict.

        :param tree: libvirt XML tree (xml.etree.ElementTree),

        :return dict: parsed data
        """
        data = {}
        if tree.keys():
            data = dict(tree.items())
        if hasattr(tree, 'getchildren') and tree.getchildren():
            for item in tree.getchildren():
                data[item.tag] = self._load_xml_tree(item)
        elif tree.text:
            if data:
                data['value'] = tree.text
            else:
                return tree.text
        return data if data else ''

    def retrieve_domain_metadata(self, domain):
        """
        Retrieve domain metadata from libvirt domain.

        Translates retrieved xml string into python dict.
        """
        data = {}
        try:
            xml_string = domain.metadata(2, self.xmlns)
        except libvirt.libvirtError:
            return data
        try:
            tree = ET.fromstring(xml_string)
            data = self._load_xml_tree(tree)
        except Exception:
            pass
        return data

    def load_instance_metadata(self, domain):
        """
        Load instance metadata data using libvirt domain.

        Returns dict of metadata for domain (domain name, uuid, nova name, project name).
        """
        metadata = {}
        try:
            metadata = {
                'domain': domain.name(),
                'uuid': str(uuid.UUID(bytes=domain.UUID()))
            }
            data = self.retrieve_domain_metadata(domain)
            metadata['name'] = data.get('name', 'unknown')
            metadata['project'] = data.get('owner', {}).get(
                'project', {}).get('value', 'unknown')
        except Exception:
            pass
        return metadata

    def get_libvirt_metadata(self, sync=False):
        """
        Get libvirt metadata for all instances.

        :param bool sync: reload before returning (default: false)
        """
        if sync:
            self.load_libvirt_metadata()
        return self.LIBVIRT_INSTANCES

    def load_libvirt_metadata(self):
        """
        Load metadata and update the store.

        (Re)Loads global store of metadata.
        """
        with self.libvirt_connection() as conn:
            for domain in conn.listAllDomains():
                instance = domain.name()
                self.LIBVIRT_INSTANCES[instance] = self.load_instance_metadata(
                    domain)

    def get_instance_metadata(self, instance, domain=None):
        """Get instance metadata."""
        try:
            if instance in self.LIBVIRT_INSTANCES:
                return self.LIBVIRT_INSTANCES.get(instance)
            else:
                if not domain:
                    with self.libvirt_connection() as conn:
                        domain = conn.lookupByName(instance)
                return self.load_instance_metadata(domain)
        except Exception:
            return {}

    def load_image_metadata(self, metadata, disk):
        source = disk.find('source')
        auth = disk.find('auth')
        driver = disk.find('driver')
        target = disk.find('target')
        pool, volume = ('', '')
        try:
            volume = source.get('name', '')
            pool, volume = volume.split(
                '/') if '/' in volume else ('unknown', volume)
            volume = volume.replace('_disk', '').replace('volume-', '')
        except Exception:
            pass
        try:
            if volume:
                volume = self.uuidp.findall(volume)[0]
        except Exception:
            if not volume:
                volume = source.get('name', 'unknown')
            if not pool:
                pool = 'unknown'
        try:
            hosts = [
                '{}:{}'.format(
                    host.get('name', ''), host.get('port', '')
                ) for host in source.getchildren() if host.get('name') and host.get('port')
            ]
        except Exception:
            hosts = []
        return {
            'domain': metadata.get('domain', 'unknown'),
            'uuid': metadata.get('uuid', 'unknown'),
            'name': metadata.get('name', 'unknown'),
            'project': metadata.get('project', 'unknown'),
            'protocol': source.get('protocol', ''),
            'pool': pool,
            'volume': volume,
            'device': target.get('dev', ''),
            'username': auth.get('username', ''),
            # 'secret': auth.find('secret').get('uuid', ''),
            'format': driver.get('type', ''),
            'path': source.get('name', 'unknown'),
            'hosts': hosts,
        }

    def get_rbd_metadata(self):
        rbd_images = []
        with self.libvirt_connection() as conn:
            for domain in conn.listAllDomains():
                try:
                    metadata = self.load_instance_metadata(domain)
                except Exception:
                    metadata = {}
                try:
                    domain_config = ET.fromstring(domain.XMLDesc())
                    disks = domain_config.findall('.//disk')
                except Exception:
                    disks = []
                for disk in disks:
                    try:
                        image = self.load_image_metadata(metadata, disk)
                        if image['volume'] and image['protocol'] == 'rbd':
                            rbd_images.append(image)
                    except Exception:
                        pass

        return rbd_images

    def get_cpu_meta(self, domain):
        items = {}
        try:
            domain_config = ET.fromstring(domain.XMLDesc())
            f_elems = domain_config.find('.//cpu').findall('.//feature')
        except Exception:
            f_elems = []
        items['variable'] = {}
        for feature in f_elems:
            try:
                required = 1 if feature.get('policy') == 'require' else 0
                items['variable']['feature:{}'.format(feature.get('name'))] = {
                    'vm_cpu_feature': required
                }
            except Exception:
                pass
        return items

    def export(self, stats_items, instance, metadata=None, domain=None, prefix='libv_'):
        stats = []
        if not stats_items or not isinstance(stats_items, dict):
            return stats

        if not metadata or not isinstance(metadata, dict):
            metadata = self.get_instance_metadata(instance, domain=domain)
        if 'domain' not in metadata:
            metadata['domain'] = instance
        if not isinstance(prefix, str):
            prefix = ''

        # Extra variable items and format them (additional metadata)
        if 'variable' in stats_items:
            var_data = stats_items.pop('variable')
            try:
                for metaname, data in var_data.items():
                    splitter = ':' if ':' in metaname else '='
                    submeta = dict([(*x.split(splitter),)
                                    for x in metaname.split(',')])
                    for key, value in metadata.items():
                        submeta[key] = value
                    var_keys = sorted(submeta.keys())
                    var_items = [submeta[x] for x in var_keys]
                    for item, value in data.items():
                        # Formatting stats
                        stats.append(['{}{}'.format(prefix, item),
                                      var_keys, var_items, value])
            except Exception:
                pass

        # Formatting stats
        var_keys = sorted(metadata.keys())
        var_items = [metadata[x] for x in var_keys]
        for item, value in stats_items.items():
            stats.append(['{}{}'.format(prefix, item),
                          var_keys, var_items, value])
        return stats

    def export_prom_stats(self, stats_items, instance, metadata=None, domain=None, prefix='libv_'):
        """Export stats in prometheus format."""
        stats = ''
        if not stats_items or not isinstance(stats_items, dict):
            return stats

        if not metadata or not isinstance(metadata, dict):
            metadata = self.get_instance_metadata(instance, domain=domain)
        if 'domain' not in metadata:
            metadata['domain'] = instance
        if not isinstance(prefix, str):
            prefix = ''
        metadata = ['{}="{}"'.format(key, metadata[key])
                    for key in sorted(metadata.keys())]

        # Extra variable items and format them (additional metadata)
        if 'variable' in stats_items:
            var_data = stats_items.pop('variable')
            try:
                for metaname, data in var_data.items():
                    if ':' in metaname:
                        metanames = metaname.split(',')
                        metaitems = ['{}="{}"'.format(
                            *mn.split(':')) for mn in metanames]
                    else:
                        metaitems = [metaname]
                    metalabel = '{}{}{}'.format(
                        '{', ','.join(metadata + metaitems), '}')
                    for item, value in data.items():
                        # Formatting stats
                        stats += '{}{}{} {}\n'.format(prefix,
                                                      item, metalabel, value)
            except Exception:
                pass

        # Formatting stats
        metalabel = '{}{}{}'.format('{', ','.join(metadata), '}')
        for item, value in stats_items.items():
            stats += '{}{}{} {}\n'.format(prefix, item, metalabel, value)
        return stats
