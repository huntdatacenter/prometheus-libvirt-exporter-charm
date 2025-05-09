#!/usr/bin/env python3

# grep vfio /proc/bus/pci/devices

# pci_device_status{
#     vendorid="10de",
#     deviceid="1df6",
#     bus="3b",
#     slot="00",
#     func="0",
#     driver="vfio-pci",
#     vendor="NVIDIA Corporation",
#     device="GV100GL",
#     model="Tesla V100S PCIe 32GB"
# } 1.0


DRIVERS = ['vfio-pci']
VENDORS = {
    '10de': 'NVIDIA'
}


def pci_slot(devfn):
    slot = (((devfn) >> 3) & 0x1f)
    return hex(slot) if slot else '0x00'


def pci_func(devfn):
    return hex((devfn) & 0x07)


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


def get_pci_devices(path="/proc/bus/pci/devices", resolve=False, raw=False, by_vendor=False):
    """
    Get PCI devices
    https://docs.python.org/3/library/string.html#format-specification-mini-language

    """
    if resolve:
        pciids = get_pci_ids()
    items = [x.replace(' ', '').split('\t') for x in open(path).read().split('\n')]
    devices = {}
    for item in items:
        if len(item) > 1 and (item[-1] in DRIVERS or (by_vendor and item[1] and item[1][:4] in VENDORS)):
            vendor_id = item[1][:4]
            product_id = item[1][4:]
            device = dict(
                bus=format(int(item[0][:2], 16), '#04x'),  # needs to work for '0a' -> '0x0a'
                slot=pci_slot(int(item[0][2:], 16)),
                function=pci_func(int(item[0][2:], 16)),
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
                device['raw'] = item
            key = '{}:{}.{}'.format(
                device.get('bus')[2:],
                device.get('slot')[2:],
                device.get('function')[2:])
            devices[key] = device
    return devices


if __name__ == "__main__":
    devices = get_pci_devices(resolve=True, by_vendor=True)
    items = []
    for key, device in devices.items():
        meta = ','.join(['{key}="{value}"'.format(key=k, value=v) for k, v in device.items()])
        print("[{key}] pci_device_status{{{meta}}} {value}".format(key=key, meta=meta, value=1.0))
