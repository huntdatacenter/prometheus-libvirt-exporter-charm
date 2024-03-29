# Copyright 2014-2016 Canonical Limited.
#
# This file is part of layer-basic, the reactive base layer for Juju.
#
# charm-helpers is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3 as
# published by the Free Software Foundation.
#
# charm-helpers is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with charm-helpers.  If not, see <http://www.gnu.org/licenses/>.

# This module may only import from the Python standard library.
import os
import subprocess
import sys
import time


'''
execd/preinstall

Read the layer-basic docs for more info on how to use this feature.
https://charmsreactive.readthedocs.io/en/latest/layer-basic.html#exec-d-support
'''


def default_execd_dir():
    return os.path.join(os.environ['JUJU_CHARM_DIR'], 'exec.d')


def execd_module_paths(execd_dir=None):
    """Generate a list of full paths to modules within execd_dir."""
    if not execd_dir:
        execd_dir = default_execd_dir()

    if not os.path.exists(execd_dir):
        return

    for subpath in os.listdir(execd_dir):
        module = os.path.join(execd_dir, subpath)
        if os.path.isdir(module):
            yield module


def execd_submodule_paths(command, execd_dir=None):
    """Generate a list of full paths to the specified command within exec_dir.
    """
    for module_path in execd_module_paths(execd_dir):
        path = os.path.join(module_path, command)
        if os.access(path, os.X_OK) and os.path.isfile(path):
            yield path


def execd_sentinel_path(submodule_path):
    module_path = os.path.dirname(submodule_path)
    execd_path = os.path.dirname(module_path)
    module_name = os.path.basename(module_path)
    submodule_name = os.path.basename(submodule_path)
    return os.path.join(execd_path,
                        '.{}_{}.done'.format(module_name, submodule_name))


def execd_run(command, execd_dir=None, stop_on_error=True, stderr=None):
    """Run command for each module within execd_dir which defines it."""
    if stderr is None:
        stderr = sys.stdout
    for submodule_path in execd_submodule_paths(command, execd_dir):
        # Only run each execd once. We cannot simply run them in the
        # install hook, as potentially storage hooks are run before that.
        # We cannot rely on them being idempotent.
        sentinel = execd_sentinel_path(submodule_path)
        if os.path.exists(sentinel):
            continue

        try:
            subprocess.check_call([submodule_path], stderr=stderr,
                                  universal_newlines=True)
            with open(sentinel, 'w') as f:
                f.write('{} ran successfully {}\n'.format(submodule_path,
                                                          time.ctime()))
                f.write('Removing this file will cause it to be run again\n')
        except subprocess.CalledProcessError as e:
            # Logs get the details. We can't use juju-log, as the
            # output may be substantial and exceed command line
            # length limits.
            print("ERROR ({}) running {}".format(e.returncode, e.cmd),
                  file=stderr)
            print("STDOUT<<EOM", file=stderr)
            print(e.output, file=stderr)
            print("EOM", file=stderr)

            # Unit workload status gets a shorter fail message.
            short_path = os.path.relpath(submodule_path)
            block_msg = "Error ({}) running {}".format(e.returncode,
                                                       short_path)
            try:
                subprocess.check_call(['status-set', 'blocked', block_msg],
                                      universal_newlines=True)
                if stop_on_error:
                    sys.exit(0)  # Leave unit in blocked state.
            except Exception:
                pass  # We care about the exec.d/* failure, not status-set.

            if stop_on_error:
                sys.exit(e.returncode or 1)  # Error state for pre-1.24 Juju


def execd_preinstall(execd_dir=None):
    """Run charm-pre-install for each module within execd_dir."""
    execd_run('charm-pre-install', execd_dir=execd_dir)
