#!/usr/bin/env python3

# DEBUG=1 python3 /opt/libvirt_exporter/pcimetadata.py

# grep vfio /proc/bus/pci/devices

# pci_device_status{
#     vendorid="10de",
#     deviceid="1df6",
#     domain="0000",
#     bus="0x3b",
#     dev="0x00",
#     func="0x0",
#     driver="vfio-pci",
#     vendor="NVIDIA Corporation",
#     device="GV100GL",
#     model="Tesla V100S PCIe 32GB"
# } 1.0

import os
import glob


DRIVERS = ['vfio-pci']
VENDORS = {
    '10de': 'NVIDIA'
}


def get_pci_dev(dev_fn):
    slot_dev = (((dev_fn) >> 3) & 0x1f)
    return hex(slot_dev) if slot_dev else '0x00'


def get_pci_func(dev_fn):
    return hex((dev_fn) & 0x07)


def get_pci_ids(path='/usr/share/misc/pci.ids'):
    """
    Get PCI IDs from /usr/share/misc/pci.ids

    pci.ids file is part of apt package: pci.ids (might not contain latest GPUs unless upgraded)

    """
    pci_ids_data = open(path).read().split('\n')
    pci_ids = {}
    items = [[item for item in row.replace('\t', '\t  ').split('  ') if item] for row in pci_ids_data[:]]
    vendor = {}
    for item in items:
        if not item or len(item) == 0 or item[0].startswith('#'):
            continue
        elif item[0] == '\t' and item[1] == '\t':
            continue
        elif item[0] == '\t':
            product = dict(
                id=item[1].lower(),
                name=item[2],
            )
            if vendor['id'] in pci_ids:
                pci_ids[vendor['id']]['devices'][product['id']] = product.copy()
            else:
                print("Vendor not in pciids: {vendor}".format(vendor=vendor))
        else:
            vendor['id'] = item[0].lower()
            vendor['name'] = item[1]
            vendor['devices'] = {}
            if vendor['id'] not in pci_ids:
                pci_ids[vendor['id']] = vendor.copy()
            else:
                print("Duplicit vendor ID {vendor_id}".format(vendor_id=vendor['id']))
    return pci_ids


def list_pci_domains(slot):
    """Return ALL domains matching this bus:dev.fn (handles duplicate bus:dev.fn across domains)."""
    pattern = f'/sys/bus/pci/devices/*:{slot}'
    return [
        os.path.basename(m).split(':')[0]
        for m in glob.glob(pattern)
    ]


def get_pci_devices(path="/proc/bus/pci/devices", resolve=False, raw=False, by_vendor=False):
    """
    Get PCI devices
    https://docs.python.org/3/library/string.html#format-specification-mini-language

    Using lspci format: `domain:bus:device.function`

    """
    if resolve:
        pciids = get_pci_ids()
    items = [x.replace(' ', '').split('\t') for x in open(path).read().split('\n')]
    pci_devices = {}
    pci_domains = {}
    for item in items:
        if len(item) > 1 and (item[-1] in DRIVERS or (by_vendor and item[1] and item[1][:4] in VENDORS)):
            vendor_id = item[1][:4]
            product_id = item[1][4:]
            bus_number = item[0][:2]
            dev_number = get_pci_dev(int(item[0][2:], 16))
            fn_number = get_pci_func(int(item[0][2:], 16))
            device = dict(
                bus=format(int(bus_number, 16), '#04x'),  # needs to work for '0a' -> '0x0a'
                dev=dev_number,
                function=fn_number,
                vendor_id=format(int(vendor_id, 16), '#06x'),
                product_id=format(int(product_id, 16), '#06x'),
                # irq=item[2],
                driver=item[-1],
            )
            if resolve:
                if product_id in pciids[vendor_id]["devices"]:
                    product = pciids[vendor_id]["devices"][product_id]["name"]
                    device_name = product.split('[', 1)[0].strip() if '[' in product else product
                    device.update(
                        vendor=pciids[vendor_id]["name"],
                        device=device_name,
                        product=product,
                    )
                else:
                    # Device not found in /usr/share/misc/pci.ids
                    device.update(
                        vendor=pciids[vendor_id]["name"],
                        device=None,
                        product=None,
                    )
            if raw:
                device.update(raw=item)
            slot = '{}:{}.{}'.format(
                device.get('bus')[2:],
                device.get('dev')[2:],
                device.get('function')[2:])

            # Fetch domains based on bus
            tmp_domains = list_pci_domains(slot)
            if os.getenv("DEBUG"):
                print(f"# Domains: {','.join(tmp_domains)}")
            # link one domain per device if bus:dev.fn is not unique
            pci_domains.setdefault(slot, set())
            for pci_domain in tmp_domains:
                if pci_domain not in pci_domains[slot]:
                    pci_domains[slot].add(pci_domain)
                    slot_key = f"{pci_domain}:{slot}"
                    device.update(domain=pci_domain)
                    break
            else:
                # In case domain is not matched a default domain 0 is used.
                slot_key = f"0000:{slot}"

            if os.getenv("DEBUG"):
                print(f"# Device: {slot_key}")
            pci_devices[slot_key] = device
    return pci_devices


if __name__ == "__main__":
    devices = get_pci_devices(resolve=True, by_vendor=True)
    items = []
    for slot, device in devices.items():
        meta = ','.join(['{slot}="{value}"'.format(slot=k, value=v) for k, v in device.items()])
        print("[{slot}] pci_device_status{{{meta}}} {value}".format(slot=slot, meta=meta, value=1.0))
