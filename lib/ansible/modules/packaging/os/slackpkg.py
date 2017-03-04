#!/usr/bin/python
# -*- coding: utf-8 -*-

# (c) 2014, Kim Nørgaard
# Written by Kim Nørgaard <jasen@jasen.dk>
# Based on pkgng module written by bleader <bleader@ratonland.org>
# that was based on pkgin module written by Shaun Zinck <shaun.zinck at gmail.com>
# that was based on pacman module written by Afterburn <http://github.com/afterburn>
# that was based on apt module written by Matthew Williams <matthew@flowroute.com>
#
# This module is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This software is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this software.  If not, see <http://www.gnu.org/licenses/>.

import re
import glob
import platform
from distutils.version import LooseVersion, StrictVersion

# import module snippets
from ansible.module_utils.basic import *

ANSIBLE_METADATA = {'status': ['preview'],
                    'supported_by': 'community',
                    'version': '1.0'}

DOCUMENTATION = '''
---
module: slackpkg
short_description: Package manager for Slackware >= 12.2
description:
    - Manage binary packages for Slackware using 'slackpkg' which
      is available in versions after 12.2.
version_added: "2.0"
options:
    name:
        description:
            - name of package to install/remove
        required: true

    state:
        description:
            - state of the package, you can use "installed" as an alias for C(present) and removed as one for c(absent).
        choices: [ 'present', 'absent', 'latest' ]
        required: false
        default: present

    update_cache:
        description:
            - update the package database first
        required: false
        default: false
        choices: [ true, false ]

author: Kim Nørgaard (@KimNorgaard)
requirements: [ "Slackware >= 12.2" ]
'''

EXAMPLES = '''
# Install package foo
- slackpkg:
    name: foo
    state: present

# Remove packages foo and bar
- slackpkg:
    name: foo,bar
    state: absent

# Make sure that it is the most updated package
- slackpkg:
    name: foo
    state: latest
'''


def query_package(name):
    machine = platform.machine()
    packages = glob.glob("/var/log/packages/%s-*-[%s|noarch]*" % (name,
                                                                  machine))
    pattern = re.compile("{}-([^-]+)-({}|noarch)-.*".format(name, machine))
    for package in packages:
        package = package[len("/var/log/packages/"):]
        if pattern.match(package):
            return package
    return False

def query_repository(module, slackpkg_path, name):
    rc, out, err = module.run_command("%s search %s" % (slackpkg_path, name))
    if rc != 0:
        module.fail_json(msg="Could not search repository")
    machine = platform.machine()
    pattern = re.compile(" ({}-([^-]+)-({}|noarch)-[^ ]*)".format(name, machine))
    for line in out.split('\n'):
        matches = re.findall(pattern, line)
        if len(matches) == 1:
            return (matches[0], None)
        elif len(matches) == 2:
            return (matches[0], matches[1])
    module.fail_json(
        msg="failed to locate package {} in repository".format(name))

def remove_packages(module, slackpkg_path, packages):
    remove_c = 0
    # Using a for loop in case of error, we can report the package that failed
    for package in packages:
        # Query the package first, to see if we even need to remove
        installed_package = query_package(package)
        if not installed_package:
            continue

        if not module.check_mode:
            rc, out, err = module.run_command(
                "%s -default_answer=y -batch=on remove %s" % (
                    slackpkg_path, installed_package))

        if not module.check_mode and query_package(package):
            module.fail_json(msg="failed to remove %s: %s" % (package, out))

        remove_c += 1

    if remove_c > 0:

        module.exit_json(changed=True, msg="removed %s package(s)" % remove_c)

    module.exit_json(changed=False, msg="package(s) already absent")


def install_packages(module, slackpkg_path, packages):

    install_c = 0

    for package in packages:
        if query_package(package):
            continue

        if not module.check_mode:
            rc, out, err = module.run_command(
                "%s -default_answer=y -batch=on install %s" % (
                    slackpkg_path,
                    query_repository(module, slackpkg_path, package)[0][0]))

        if not module.check_mode and not query_package(package):
            module.fail_json(msg="failed to install %s: %s" % (package, out),
                             stderr=err)

        install_c += 1

    if install_c > 0:
        module.exit_json(changed=True, msg="installed %s package(s)"
                         % (install_c))

    module.exit_json(changed=False, msg="package(s) already present")


def upgrade_packages(module, slackpkg_path, packages):
    install_c = 0

    for package in packages:
        installed_package = query_package(package)
        current_version, newer_version = query_repository(
            module, slackpkg_path, package)
        if not installed_package and not module.check_mode:
            install_c += 1
            rc, out, err = module.run_command(
                "%s -default_answer=y -batch=on install %s" % (
                    slackpkg_path,
                    current_version[0]))
        elif not module.check_mode and newer_version is not None:
            if LooseVersion(newer_version[1]) < LooseVersion(current_version[1]):
                # don't do anything if a newer version than the one in the
                # repository is installed already
                continue
            install_c += 1
            rc, out, err = module.run_command(
                "%s -default_answer=y -batch=on upgrade %s" % (
                    slackpkg_path, newer_version[0]))
            if query_package(package) != newer_version[0]:
                module.fail_json(
                    msg="failed to upgrade %s: %s" % (package, out),
                    stderr=err,
                    current_version=current_version[0],
                    newer_version=newer_version[0])

        if not module.check_mode and not query_package(package):
            module.fail_json(msg="failed to install %s: %s" % (package, out),
                             stderr=err)

    if install_c > 0:
        module.exit_json(changed=True, msg="updated or installed %s package(s)"
                         % (install_c))

    module.exit_json(changed=False, msg="package(s) already present")


def update_cache(module, slackpkg_path):
    rc, out, err = module.run_command("%s check-updates" % (slackpkg_path))
    for line in out.split('\n'):
        if "News on ChangeLog.txt" in line:
            rc, out, err = module.run_command(
                "%s -batch=on update" % (slackpkg_path))
            if rc != 0:
                module.fail_json(msg="Could not update package cache")
            return
        if "No news is good news" in line:
            return
    module.fail_json(
        msg="Could not check on change lock state",
        out=out,
        err=err)

def main():
    module = AnsibleModule(
        argument_spec=dict(
            state=dict(default="installed", choices=['installed', 'removed', 'absent', 'present', 'latest']),
            name=dict(aliases=["pkg"], required=True, type='list'),
            update_cache=dict(default=False, aliases=["update-cache"],
                              type='bool'),
        ),
        supports_check_mode=True)

    slackpkg_path = module.get_bin_path('slackpkg', True)

    p = module.params

    pkgs = p['name']

    if p["update_cache"]:
        update_cache(module, slackpkg_path)

    if p['state'] == 'latest':
        upgrade_packages(module, slackpkg_path, pkgs)

    elif p['state'] in ['present', 'installed']:
        install_packages(module, slackpkg_path, pkgs)

    elif p["state"] in ['removed', 'absent']:
        remove_packages(module, slackpkg_path, pkgs)

if __name__ == '__main__':
    main()
