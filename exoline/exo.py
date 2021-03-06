#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Exoline - Exosite IoT Command Line
https://github.com/exosite/exoline

Usage:
  exo [--help] [options] <command> [<args> ...]

Commands:
{{ command_list }}
Options:
  --host=<host>          OneP host. Default is $EXO_HOST or m2.exosite.com
  --port=<port>          OneP port. Default is $EXO_PORT or 443
  -c --config=<file>     Config file Default is $EXO_CONFIG or ~/.exoline
  --httptimeout=<sec>    HTTP timeout [default: 60] (default for copy is 480)
  --https                Enable HTTPS (deprecated, HTTPS is default)
  --http                 Disable HTTPS
  --useragent=<ua>       Set User-Agent Header for outgoing requests
  --debug                Show debug info (stack traces on exceptions)
  -d --debughttp         Turn on debug level logging in pyonep
  --curl                 Show curl calls for requests. Implies --debughttp
  --discreet             Obfuscate RIDs in stdout and stderr
  -e --clearcache        Invalidate Portals cache after running command
  --portals=<server>     Portals server [default: https://portals.exosite.com]
  -t --vendortoken=<vt>  Vendor token (/admin/home in Portals)
  -n --vendor=<vendor>   Vendor identifier (/admin/managemodels in Portals)
                         (See http://github.com/exosite/exoline#provisioning)
  -h --help              Show this screen
  -v --version           Show version

See 'exo <command> --help' for more information on a specific command.
"""

# Copyright (c) 2015, Exosite, LLC
# All rights reserved
from __future__ import unicode_literals
import sys
import os
import json
if sys.version_info < (3, 0):
    import unicodecsv as csv
else:
    import csv
import platform
import re
from datetime import datetime
from datetime import timedelta
import time
from pprint import pprint
from operator import itemgetter
import logging
from collections import defaultdict
import copy
import difflib
import warnings

import six
from six import StringIO
from six import iteritems
from six import string_types
# python 2.6 support
try:
    from collections import OrderedDict
except ImportError:
    from ordereddict import OrderedDict
import itertools
import math
import glob

from docopt import docopt
from dateutil import parser
from dotenv import Dotenv
import requests
import ruamel.yaml as yaml
import importlib
import humanize
import blessings

from pyonep import onep
from pyonep import provision
import pyonep

try:
    from ..exoline import __version__
    from ..exoline.exocommon import ExoException
    from ..exoline import exocommon
    from ..exoline import serieswriter
except:
    from exoline import __version__
    from exoline.exocommon import ExoException
    from exoline import exocommon
    from exoline import serieswriter

DEFAULT_HOST = 'm2.exosite.com'
DEFAULT_PORT = '80'
DEFAULT_PORT_HTTPS = '443'
DEFAULT_CONFIG = '~/.exoline'
SCRIPT_LIMIT_BYTES = 16 * 1024

PERF_DATA = []

colored_terminal = blessings.Terminal()

cmd_doc = OrderedDict([
    ('read',
        '''Read data from a resource.\n\nUsage:
    exo [options] read <auth> [<rid> ...]

Command options:
    --follow                 continue reading (ignores --end)
    --limit=<limit>          number of data points to read [default: 1]
    --start=<time>
    --end=<time>             start and end times (see details below)
    --tz=<TZ>                Olson TZ name
    --sort=<order>           asc or desc [default: desc]
    --selection=all|autowindow|givenwindow  downsample method [default: all]
    --format=csv|raw         output format [default: csv]
    --timeformat=unix|human|iso8601|excel
                             unix timestamp, human-readable, or spreadsheet-
                             compatible? [default: human]
    --header=name|rid        include a header row
    --chunksize=<size>       [default: 212] break read into requests of
                             length <size>, printing data as it is received.
    {{ helpoption }}

    If <rid> is omitted, reads all datasources and datarules under <auth>.
    All output is in UTC.

    {{ startend }}'''),
    ('write',
        '''Write data at the current time.\n\nUsage:
    exo [options] write <auth> [<rid>] --value=<value>
    exo [options] write <auth> [<rid>] -

The - form takes the value to write from stdin. For example:

    $ echo '42' | exo write 8f21f0189b9acdc82f7ec28dc0c54ccdf8bc5ade myDataport -'''),
    ('record',
        '''Write data at a specified time.\n\nUsage:
    exo [options] record <auth> [<rid>...] [-]
    exo [options] record <auth> [<rid>] (--value=<timestamp,value> ...)
    exo [options] record <auth> [<rid>] --interval=<seconds> ((--value=<value> ...) | -)

    Can take a CSV file on STDIN and record the values to dataports.  The file must have the
    first column to be unix timestamps for each row.  The remaining columns are data to be
    recorded at those timestamps.  Each column is identified by the <rid> arguments.

    The CSV must not have a header row.

    For example:
    $ exo record aCIK dpA dpB dpC - < my.csv
    Will take the CSV file, my.csv, that has four columns. Record that data into the dataports
    with aliases dpA, dpB, and dpC on the shortcut aCIK.


Command options:
    --interval generates timestamps at a regular interval into the past.
    --chunksize=<lines>       [default: 212] break record into requests of length <lines>

    '''),
    ('create',
        '''Create a resource from a json description passed on stdin (with -),
    or using command line shorthand (other variants).\n\nUsage:
    exo [options] create <auth> (--type=client|clone|dataport|datarule|dispatch) -
    exo [options] create <auth> --type=client
    exo [options] create <auth> --type=dataport (--format=float|integer|string)

Command options:
    --name=<name     set a resource name (overwriting the one in stdin if present)
    --alias=<alias>  set an alias
    --ridonly        output the RID by itself on a line
    --cikonly        output the CIK by itself on a line (--type=client only)
    {{ helpoption }}

Details:
    Pass - and a json description object on stdin, or leave it off to use defaults.
    Description is documented here:
    https://github.com/exosite/docs/tree/master/rpc#create-client
    https://github.com/exosite/docs/tree/master/rpc#create-dataport
    https://github.com/exosite/docs/tree/master/rpc#create-datarule

    If - is not present, creates a resource with common defaults.'''),
    ('listing',
        '''List the RIDs of a client's children.\n\nUsage:
    exo [options] listing <auth> [<rid>]

Command options:
    --types=<type1>,...  which resource types to list
                         [default: client,dataport,datarule,dispatch]
    --filters=<f1>,...   criteria for which resources to include
                         [default: owned]
                         activated  resources shared with and activated
                                    by client (<auth>)
                         aliased    resources aliased by client (<auth>)
                         owned      resources owned by client (<auth>)
                         public     public resources
    --tagged=<tag1>,...  resources that have been tagged by any client, and
                         that the client (<auth>) has read access to.
    --plain              show only the child RIDs
    --pretty             pretty print output'''),
#    ('whee',
#        '''Super-fast info tree.\n\nUsage:
#    exo [options] whee <auth>'''),
    ('info',
        '''Get metadata for a resource in json format.\n\nUsage:
    exo [options] info <auth> [<rid>]

Command options:
    --cikonly      print CIK by itself
    --pretty       pretty print output
    --recursive    embed info for any children recursively
    --level=<num>  number of levels to recurse through the client tree
    --include=<key list>
    --exclude=<key list>
                   comma separated list of info keys to include and exclude.
                   Available keys are aliases, basic, counts, description,
                   key, shares, subscribers, tags, usage. If omitted,
                   all available keys are returned.'''),
    ('update',
        '''Update a resource from a json description passed on stdin.\n\nUsage:
    exo [options] update <auth> <rid> -

    For details see https://github.com/exosite/docs/tree/master/rpc#update'''),
    ('map',
        '''Add an alias to a resource.\n\nUsage:
    exo [options] map <auth> <rid> <alias>'''),
    ('unmap',
        '''Remove an alias from a resource.\n\nUsage:
    exo [options] unmap <auth> <alias>'''),
    ('lookup',
        '''Look up a resource's RID based on its alias cik.\n\nUsage:
    exo [options] lookup <auth> [<alias>]
    exo [options] lookup <auth> --owner-of=<rid>
    exo [options] lookup <auth> --share=<code>
    exo [options] lookup <auth> --cik=<cik-to-find>

    If <alias> is omitted, the rid for <auth> is returned. This is equivalent to:
    exo lookup <auth> ""

    The --owner-of variant returns the RID of the immediate parent (owner)
    of <rid>.

    The --share variant returns the RID associated with a share code'''),
    ('drop',
        '''Drop (permanently delete) a resource.\n\nUsage:
    exo [options] drop <auth> [<rid> ...]

Command options:
    --all-children  drop all children of the resource.
    {{ helpoption }}

Warning: if the resource is a client with a serial number
associated with it, the serial number is not released.'''),
    ('flush',
        '''Remove time series data from a resource.\n\nUsage:
    exo [options] flush <auth> [<rid>]

Command options:
    --start=<time>  flush all points newer than <time> (exclusive)
    --end=<time>    flush all points older than <time> (exclusive)

    If --start and --end are both omitted, all points are flushed.'''),
    ('usage',
        '''Display usage of One Platform resources over a time period.\n\nUsage:
    exo [options] usage <auth> [<rid>] --start=<time> [--end=<time>]

    {{ startend }}'''),
    ('tree', '''Display a resource's descendants.\n\nUsage:
    exo [options] tree [--verbose] [--values] <auth>

Command options:
    --level=<num>  depth to traverse, omit or -1 for no limit [default: -1]'''),
    ('twee', '''Display a resource's descendants. Like tree, but more wuvable.\n\nUsage:
    exo [options] twee <auth>

Command options:
    --nocolor      don't use color in output (color is always off in Windows)
    --level=<num>  depth to traverse, omit or -1 for no limit [default: -1]
    --rids         show RIDs instead CIKs below the top level

Example:

    $ exo twee 7893635162b84f78e4475c2d6383645659545344
     Temporary CIK    cl cik: 7893635162b84f78e4475c2d6383645659545344
       ├─  dp.i rid.098f1: 77 (just now)
       └─  dp.s config: {"a":1,"b":2} (21 seconds ago)
    $ exo read 7893635162b84f78e4475c2d6383645659545344 rid.098f1
    2014-09-12 13:48:28-05:00,77
    $ exo read 7893635162b84f78e4475c2d6383645659545344 config --format=raw
    {"a":1,"b":2}
    $ exo info 7893635162b84f78e4475c2d6383645659545344 --include=description --pretty
    {
        "description": {
            "limits": {
                "client": 1,
                "dataport": 10,
                "datarule": 10,
                "disk": "inherit",
                "dispatch": 10,
                "email": 5,
                "email_bucket": "inherit",
                "http": 10,
                "http_bucket": "inherit",
                "share": 5,
                "sms": 0,
                "sms_bucket": 0,
                "xmpp": 10,
                "xmpp_bucket": "inherit"
            },
            "locked": false,
            "meta": "",
            "name": "Temporary CIK",
            "public": false
        }
    }
    '''),
('find', '''Search resource's descendants for matches.\n\nUsage:
    exo find <auth> --match <matches> [--show <shows>]

Command options:
    --show=<shows>           Things to show on match (default: cik)
    --match=<matches>        List of --match x=y,z=w to match on (supported operations: ^ (not), >, <, =)

Example:
    $ exo find $CIK --match "status=activated,model=$CLIENT_MODEL"
    7893635162b84f78e4475c2d6383645659545344
    7893635162b84f78e4475c2d6383645659545341
    7893635162b84f78e4475c2d6383645659545342

    $ exo find $CIK --match "model=$CLIENT_MODEL" --show="status,sn"
    activated   A8-UQN6L7-TUMCN0-PNZMH
    activated   A8-KJGJS3-WRC1RK-S9ECK
    activated   A8-K3CFRF-NP3NH3-2B7UA
    activated   A8-0KP131-C1QFXQ-4HCU4

    $ exo find $CIK --match "status=activated,model=$CLIENT_MODEL" --show='basic'
    {u'status': u'activated', u'type': u'client', u'modified': 1429041332, u'subscribers': 0}
    {u'status': u'activated', u'type': u'client', u'modified': 1430422683, u'subscribers': 0}
    {u'status': u'activated', u'type': u'client', u'modified': 1431013655, u'subscribers': 0}
    {u'status': u'activated', u'type': u'client', u'modified': 1431013616, u'subscribers': 0}


    To use the CIKs that are output from the find command, pipe to xargs
    $ exo find $CIK --match "model=$CLIENT_MODEL" | xargs -I cik sh -c 'printf "cik\t"; exo read cik elapsed_time --time=unix'
    7893635162b84f78e4475c2d6383645659545344    1431203202,4398
    7893635162b84f78e4475c2d6383645659545342    1431203197,4338


    To find all devices that aren't activated:
    $ exo find portal --match "status^activated" --show "name,cik,status"

    $ # they're all activated

    The output from find is tab delimited.

    '''),
    ('script', '''Upload a Lua script\n\nUsage:
    exo [options] script <auth> [<rid>] --file=<script-file>
    exo [options] script <script-file> <auth> ...

    Both forms do the same thing, but --file is the recommended one.
    If <rid> is omitted, the file name part of <script-file> is used
    as both the alias and name of the script. This convention helps
    when working with scripts in Portals, because Portals shows the
    script resource's name but not its alias.

Command options:
    --name=<name>     script name, if different from script filename. The name
                      is used to identify the script, too.
    --recursive       operate on client and any children
    --create          create the script if it doesn't already exist
    --follow       monitor the script's debug log
    --setversion=<vn> set a version number on the script meta'''),
    ('spark', '''Show distribution of intervals between points.\n\nUsage:
    exo [options] spark <auth> [<rid>] --days=<days>

Command options:
    --stddev=<num>  exclude intervals more than num standard deviations from mean
    {{ helpoption }}'''),
    ('copy', '''Make a copy of a client.\n\nUsage:
    exo [options] copy <auth> <destination-cik>

    Copies <auth> and all its non-client children to <destination-cik>.
    Returns CIK of the copy. NOTE: copy excludes all data in dataports.

Command options:
    --cikonly  show unlabeled CIK by itself
    {{ helpoption }}'''),
    ('diff', '''Show differences between two clients.\n\nUsage:
    exo [options] diff <auth> <cik2>

    Displays differences between <auth> and <cik2>, including all non-client
    children. If clients are identical, nothing is output. For best results,
    all children should have unique names.

Command options:
    --full         compare all info, even usage, data counts, etc.
    --no-children  don't compare children
    {{ helpoption }}'''),
    ('ip', '''Get IP address of the server.\n\nUsage:
    exo [options] ip'''),
    ('data', '''Read or write with the HTTP Data API.\n\nUsage:
    exo [options] data <auth> [--write=<alias,value> ...] [--read=<alias> ...]

    If only --write arguments are specified, the call is a write.
    If only --read arguments are specified, the call is a read.
    If both --write and --read arguments are specified, the hybrid
        write/read API is used. Writes are executed before reads.'''),
    ('portals', '''Invalidate the Portals cache for a CIK by telling Portals
    a particular procedure was taken on client identified by <auth>.\n\nUsage:
    exo [options] portals clearcache <auth> [<procedure> ...]

    <procedure> may be any of:
    activate, create, deactivate, drop, map, revoke, share, unmap, update

    If no <procedure> is specified, Exoline tells Portals that all of the
    procedures on the list were performed on the client.

    Warning: drop does not invalidate the cache correctly. Instead, use create.
    '''),
    ('share', '''Generate a code that allows non-owners to access resources\n\nUsage:
    exo [options] share <auth> <rid> [--meta=<string> [--share=<code-to-update>]]

    Pass --meta to associate a metadata string with the share.
    Pass --share to update metadata for an existing share.'''),
    ('revoke', '''Revoke a share code\n\nUsage:
    exo [options] revoke <auth> --share=<code>'''),
    ('activate', '''Activate a share code\n\nUsage:
    exo [options] activate <auth> --share=<code>

If you want to activate a *device*, use the "sn activate"
     command instead'''),
    ('deactivate', '''Deactivate a share code\n\nUsage:
    exo [options] deactivate <auth> --share=<code>'''),
    ('clone', '''Create a clone of a client\n\nUsage:
    exo [options] clone <auth> (--rid=<rid> | --share=<code>)

Command options:
     --noaliases     don't copy aliases
     --nohistorical  don't copy time series data
     --noactivate    don't activate CIK of clone (client only)

     The clone command copies the client resource specified by --rid or --share
     into the client specified by <auth>.

     For example, to clone a portals device, pass the portal CIK as <auth> and
     the device RID as <rid>. The portal CIK can be found in Portals
     https://<yourdomain>.exosite.com/account/portals, where it says Key: <auth>.
     A device's RID can be obtained using exo lookup <device-cik>.

     The clone and copy commands do similar things, but clone uses the RPC's
     create (clone) functionality, which is more full featured.
     https://github.com/exosite/docs/tree/master/rpc#create-clone

     Use the clone command except if you need to copy a device to another portal.''')
    ])

# shared sections of documentation
doc_replace = {
    '{{ startend }}': '''<time> can be a unix timestamp or formatted like any of these:

    2011-10-23T08:00:00-07:00
    10/1/2012
    "2012-10-23 14:01 UTC"
    "2012-10-23 14:01"

    If timezone information is omitted, local timezone is assumed
    If time part is omitted, it assumes 00:00:00.
    To report through the present time, omit --end or pass --end=now''',
    '{{ helpoption }}': '''    -h --help  Show this screen.''',
}

dotpath = os.path.join(os.getcwd(), '.env')
if os.path.exists(dotpath):
    dotenv=Dotenv(dotpath)
    os.environ.update(dotenv)

plugins = []
if platform.system() != 'Windows':
    # load plugins. use timezone because this file may be running
    # as a script in some other location.
    default_plugin_path = os.path.join(os.path.dirname(exocommon.__file__), 'plugins')

    plugin_paths = os.getenv('EXO_PLUGIN_PATH', default_plugin_path).split(':')

    for plugin_path in [i for i in plugin_paths if len(i) > 0]:
        plugin_names = [os.path.basename(f)[:-3]
            for f in glob.glob(plugin_path + "/*.py")
            if not os.path.basename(f).startswith('_')]

        for module_name in plugin_names:
            try:
                plugin = importlib.import_module('plugins.' + module_name)
            except Exception as ex:
                # TODO: only catch the not found exception, for plugin
                # debugging
                #print(ex)
                try:
                    plugin = importlib.import_module('exoline.plugins.' + module_name, package='test')
                except Exception as ex:
                    plugin = importlib.import_module('exoline.plugins.' + module_name)

            # instantiate plugin
            p = plugin.Plugin()
            plugins.append(p)

            # get documentation
            command = p.command()
            if isinstance(command, six.string_types):
                cmd_doc[command] = plugin.__doc__
            else:
                for c in command:
                    cmd_doc[c] = p.doc(c)
else:
    # plugin support for Windows executable build
    try:
        # spec plugin
        try:
            from ..exoline.plugins import spec
        except:
            from exoline.plugins import spec
        p = spec.Plugin()
        plugins.append(p)
        cmd_doc[p.command()] = spec.__doc__

        # transform plugin
        try:
            from ..exoline.plugins import transform
        except:
            from exoline.plugins import transform
        p = transform.Plugin()
        plugins.append(p)
        cmd_doc[p.command()] = transform.__doc__

        # provision plugin
        try:
            from ..exoline.plugins import provision as provisionPlugin
        except:
            from exoline.plugins import provision as provisionPlugin
        p = provisionPlugin.Plugin()
        plugins.append(p)
        for c in p.command():
            cmd_doc[c] = p.doc(c)

        # search plugin
        try:
            from ..exoline.plugins import search
        except:
            from exoline.plugins import search
        p = search.Plugin()
        plugins.append(p)
        cmd_doc[p.command()] = search.__doc__

        # dump plugin
        try:
            from ..exoline.plugins import dump
        except:
            from exoline.plugins import dump
        p = dump.Plugin()
        plugins.append(p)
        cmd_doc[p.command()] = dump.__doc__

        # keys plugin
        try:
            from ..exoline.plugins import keys
        except:
            from exoline.plugins import keys
        p = keys.Plugin()
        plugins.append(p)
        cmd_doc[p.command()] = keys.__doc__

        # switches plugin
        try:
            from ..exoline.plugins import switches
        except:
            from exoline.plugins import switches
        p = switches.Plugin()
        plugins.append(p)
        cmd_doc[p.command()] = switches.__doc__

        # aliases plugin
        try:
            from ..exoline.plugins import aliases
        except:
            from exoline.plugins import aliases
        p = aliases.Plugin()
        plugins.append(p)
        cmd_doc[p.command()] = aliases.__doc__

        # meta plugin
        try:
            from ..exoline.plugins import meta
        except:
            from exoline.plugins import meta
        p = meta.Plugin()
        meta.append(p)
        cmd_doc[p.command()] = meta.__doc__

    except Exception as ex:
        import traceback
        traceback.print_exc()
        pprint(ex)

# perform substitutions on command documentation
for k in cmd_doc:
    # helpoption is appended to any commands that don't already have it
    if '{{ helpoption }}' not in cmd_doc[k]:
        cmd_doc[k] += '\n\nCommand options:\n{{ helpoption }}'
    for r in doc_replace:
        cmd_doc[k] = cmd_doc[k].replace(r, doc_replace[r])

class ExoConfig:
    '''Manages the config file, grouping all realted actions'''
    regex_rid = re.compile("[0-9a-fA-F]{40}")

    def __init__(self, configfile='~/.exoline'):
        # remember the config file requested
        self.askedconfigfile = configfile
        # look in some by-convention locations
        self.configfile = self.realConfigFile(configfile)
        self.loadConfig(self.configfile)

    def realConfigFile(self, configfile):
        '''Find real path for a config file'''
        # Does the file as passed exist?
        cfgf = os.path.expanduser(configfile)

        if os.path.exists(cfgf):
            return cfgf

        # Is it in the exoline folder?
        cfgf = os.path.join('~/.exoline', configfile)
        cfgf = os.path.expanduser(cfgf)
        if os.path.exists(cfgf):
            return cfgf

        # Or is it a dashed file?
        cfgf = '~/.exoline-' + configfile
        cfgf = os.path.expanduser(cfgf)
        if os.path.exists(cfgf):
            return cfgf

        # No such file to load.
        return None

    def loadConfig(self, configfile):
        if configfile is None:
            self.config = {}
        else:
            try:
                with open(configfile) as f:
                    self.config = yaml.load(f, yaml.RoundTripLoader)
            except IOError as ex:
                self.config = {}

    def authparts(self, auth_str, authtype_default):
        '''Returns a tuple of auth type ('token' or 'cik') and the 40
        character token/CIK from an auth string'''
        match = re.match('^(token|cik):(.*)', auth_str)
        if match is not None:
            return match.groups()
        else:
            return authtype_default, auth_str

    def lookup_shortcut(self, auth):
        '''If a CIK has client/resource parts, separate and look those up'''
        # default to CIK type auth, for backward compatibility
        authtype, detypedauth = self.authparts(auth, 'cik')

        if ':c' in detypedauth:
            # break into parts, then lookup each.
            c,g,r = auth.partition(':c')
            auth = { authtype: self._lookup_shortcut(c),
                     'client_id': self._lookup_shortcut(r) }
        elif ':r' in detypedauth:
            c,g,r = auth.partition(':r')
            auth = { authtype: self._lookup_shortcut(c),
                     'resource_id': self._lookup_shortcut(r) }
        else:
            # look it up, then check again for parts.
            auth = self._lookup_shortcut(detypedauth)
            authtype, detypedauth = self.authparts(auth, authtype)
            if ':c' in auth:
                c,g,r = auth.partition(':c')
                auth = { authtype: c,
                         'client_id': r }
            elif ':r' in auth:
                c,g,r = auth.partition(':r')
                auth = { authtype: c,
                         'resource_id': r }
            else:
                auth = { authtype: detypedauth }

        return auth


    def _lookup_shortcut(self, auth):
        '''Look up what was passed for cik in config file
            if it doesn't look like a CIK.'''
        if self.regex_rid.match(auth) is None:
            if 'keys' in self.config:
                if auth in self.config['keys']:
                    return self.config['keys'][auth].strip()
                elif auth.isdigit() and int(auth) in self.config['keys']:
                    return self.config['keys'][int(auth)].strip()
                else:
                    raise ExoException('No CIK shortcut {0}\n{1}'.format(
                        auth, '\n'.join(sorted(map(str, self.config['keys'])))))
            else:
                raise ExoException('Tried a CIK shortcut {0}, but found no keys'.format(auth))
        else:
            return auth

    def mingleArguments(self, args):
        '''This mixes the settings applied from the configfile, the command line and the ENV.
        Command line always overrides ENV which always overrides configfile.
        '''
        # This ONLY works with options that take a parameter.
        toMingle = ['host', 'port', 'httptimeout', 'useragent', 'portals', 'vendortoken', 'vendor']

        # Precedence: ARGV then ENV then CFG

        # Looks for ENV vars and pull them in, unless in ARGV
        for arg in toMingle:
            if args['--'+arg] is None:
                env = os.getenv('EXO_'+arg.upper())
                if env is not None:
                    args['--'+arg] = env

        # Look for CFG vars and pull them in, unless in ARGV
        for arg in toMingle:
            if arg in self.config and args['--'+arg] is None:
                args['--'+arg] = self.config[arg]

        # Copy all ARGV vars to CFG for uniform lookups.
        for arg in toMingle:
            self.config[arg] = args['--'+arg]


exoconfig = ExoConfig(os.getenv('EXO_CONFIG', DEFAULT_CONFIG))
class ExolineOnepV1(onep.OnepV1):
    '''Subclass that re-adds deprecated commands needed for devices created
    in Portals before the commands were deprecated.'''

    def _callJsonRPC(self, auth, callrequests, returnreq=False, notimeout=False):
        '''Time all calls to _callJsonRPC'''
        try:
            ts = time.time()
            procedures = [cr['procedure'] for cr in callrequests]
            r = onep.OnepV1._callJsonRPC(self, auth, callrequests, returnreq, notimeout=notimeout)
        except:
            raise
        finally:
            te = time.time()
            PERF_DATA.append({'cik': auth, 'procedures': procedures, 'seconds': te-ts})
        return r

    def comment(self, auth, rid, visibility, comment, defer=False):
        return self._call('comment', auth, [rid, visibility, comment], defer)


class ExoRPC():
    '''Wrapper for pyonep RPC API.
    Raises exceptions on error and provides some reasonable defaults.'''
    regex_rid = re.compile("[0-9a-fA-F]{40}")
    regex_tweeid = re.compile("rid\.[0-9a-fA-F]{5}")

    class RPCException(Exception):
        def __init__(self, *args):
            try:
                err, conditions = args[0].split(" ", 1)
                if err == "invalid":
                    url = "https://pyonep.readthedocs.org/en/latest/errors/invalid.html"
                elif err == "auth":
                    url = "https://pyonep.readthedocs.org/en/latest/errors/auth.html"
                else:
                    url = "https://pyonep.readthedocs.org/en/latest/errors/general.html"
                self.message = "Error: %s\n\tArguments: %s\n\tFor more information, visit: %s"%(err, conditions, url)
            except:
                self.message = args[0]
        def __str__(self):
            return self.message

    def __init__(self,
                 host=DEFAULT_HOST,
                 port=None,
                 httptimeout=60,
                 https=False,
                 verbose=True,
                 logrequests=False,
                 user_agent=None,
                 curldebug=False):

        if port is None:
            port = DEFAULT_PORT_HTTPS if https else DEFAULT_PORT
        if user_agent is None:
            user_agent = "Exoline {0}".format(__version__)
        self.exo = ExolineOnepV1(
            host=host,
            port=port,
            httptimeout=httptimeout,
            https=https,
            agent=user_agent,
            reuseconnection=True,
            logrequests=logrequests,
            curldebug=curldebug)

    def _raise_for_response(self, isok, response, call=None):
        if not isok:
            if call is None:
                msg = str(response)
            else:
                msg = '{0} ({1})'.format(str(response), str(call))
            raise ExoRPC.RPCException(msg)

    def _raise_for_response_record(self, isok, response):
        '''Undocumented RPC behavior-- if record timestamps are invalid, isok
           is True but response is an array of timestamps and error
           messages.'''
        self._raise_for_response(isok, response)
        if type(response) is list:
            raise ExoRPC.RPCException(', '.join(['{0}: {1}'.format(msg, t) for msg, t in response]))

    def _raise_for_deferred(self, responses):
        r = []
        for call, isok, response in responses:
            self._raise_for_response(isok, response, call=call)
            r.append(response)
        return r

    def mult(self, auth, commands):
        return self._exomult(auth, commands)

    def _check_exomult(self, auth):
        if not (isinstance(auth, six.string_types) or type(auth) is dict):
            raise Exception("_exomult: unexpected type for auth " + str(auth))
        assert(not self.exo.has_deferred(auth))

    def _exomult(self, auth, commands):
        '''Takes a list of onep commands with auth omitted, e.g.:
            [['info', {alias: ""}], ['listing', ['dataport'], {}, {'alias': ''}]'''
        if len(commands) == 0:
            return []
        self._check_exomult(auth)
        for c in commands:
            if type(c) is not list:
                raise Exception("_exomult: found invalid command " + str(c))
            method = getattr(self.exo, c[0])
            method(auth, *c[1:], defer=True)
        assert(self.exo.has_deferred(auth))
        r = self.exo.send_deferred(auth)
        responses = self._raise_for_deferred(r)
        return responses

    def _exomult_with_responses(self, auth, commands):
        '''Like _exomult, but returns full responses and does not raise
           an exception for individual response errors. Call this if errors
           from particular calls are not fatal. General RPC errors still
           raise exceptions, though.'''
        if len(commands) == 0:
            return []
        self._check_exomult(auth)
        for c in commands:
            if type(c) is not list:
                raise Exception("_exomult: found invalid command " + str(c))
            method = getattr(self.exo, c[0])
            method(auth, *c[1:], defer=True)
        r = self.exo.send_deferred(auth)
        results = map(self._undo_pyonep_response_mangling, r)
        return results

    def _undo_pyonep_response_mangling(self, pyonep_response):
        '''pyonep mixes RPC responses up, setting isok to status=='ok'
           and response to either response or the status if status is not 'ok'.
           This undoes that since that's the way pyonep should go.'''
        call, isok, r = pyonep_response
        if isok:
            return {'status': 'ok', 'result': r}
        else:
            return {'status': r}

    def _exobatch(self, auth, commands, batchsize=25):
        '''Performs a set of commands, breaking them into batches of at most batchsize
           to prevent timeout.
             auth - either a cik or an auth dict
             commands - a list of commandset objects like this:
                      {'commands': [['info', rid, options]],
                       'callback': lambda(commandset, result)}
             batchsize - the maximum number of commands/command objects to include
                       in each RPC request.
           Returns a list of responses in the form {'status': !'ok'} on failure or
                {'status': 'ok', 'result': result}
           If any overall failures occur, an exception is raised.'''
        # break calls into chunks to prevent timeout
        def chunks(l, n):
            '''Yield successive n-sized chunks from l.'''
            for i in range(0, len(l), n):
                yield l[i:i+n]
        for commandchunk in chunks(commands, batchsize):
            cmds = []
            for commandset in commandchunk:
                cmds = cmds + commandset['commands']
            #sys.stderr.write('_exomult_with_responses with {0} commands.\n'.format(len(cmds)))
            cmd_responses = self._exomult_with_responses(auth, cmds)
            result_index = 0
            # stitch the flattened result list into command sets
            # and call the command set callbacks
            for i, commandset in enumerate(commandchunk):
                commandset_responses = []
                for cmd in commandset['commands']:
                    commandset_responses.append(cmd_responses[result_index])
                    result_index += 1
                if 'callback' in commandset:
                    commandset['callback'](commandset, commandset_responses)
                yield commandset_responses

    def wait(self, auth, rid, since=None, timeout=None):
        '''Returns timedout, point. If timedout is True,
        point is None'''
        options = {}
        if since is not None:
            options['since'] = since
        if timeout is not None:
            options['timeout'] = timeout
        isok, response = self.exo.wait(
            auth,
            rid,
            options)
        if not isok and response=='expire':
            return True, None
        else:
            self._raise_for_response(isok, response)
            return False, response


    def _readoptions(self, limit, sort, starttime, endtime, selection):
        options ={'limit': limit,
                  'sort': sort,
                  'selection': selection}
        if starttime is not None:
            options['starttime'] = int(starttime)
        if endtime is not None:
            options['endtime'] = int(endtime)
        return options

    def read(self,
             auth,
             rid,
             limit,
             sort='asc',
             starttime=None,
             endtime=None,
             selection='all'):
        options = self._readoptions(limit, sort, starttime, endtime, selection)
        isok, response = self.exo.read(
            auth,
            rid,
            options)
        self._raise_for_response(isok, response)
        return response

    def move(self,
             auth,
             rid,
             destinationrid,
             options={"aliases": True}):
        isok, response = self.exo.move(
            auth,
            rid,
            destinationrid,
            options)
        self._raise_for_response(isok, response)
        return response

    def find(self, auth, matches, shows, verbose=False):
        showcik = False
        if "cik" in shows:
            shows = shows.replace("cik", "key")
        if verbose:
            print("Matching {0} and showing {1}".format(matches, shows))
        matchers = {}
        for matchval in matches.split(","):
            data = re.findall(r"(.*?)([=<>^])(.*)", matchval)
            for d in data:
                if len(d) == 3:
                    matchers[d[0]] = (d[2], d[1])

        shows = [s.strip() for s in shows.split(",")]
        if verbose:
            print("Showing: {0}".format(shows))
            print("Matching: {0}".format(matchers))

        data = self._infotree_fast(auth)

        display_data = []


        def compare(valueA, comp, valueB):
            if verbose:
                print(valueA, comp, valueB)
            if comp == "^":
                return valueA != valueB
            elif comp == ">":
                try:
                    return float(valueA) > float(valueB)
                except:
                    return False
            elif comp == "<":
                try:
                    return float(valueA) < float(valueB)
                except:
                    return False
            elif comp == "=":
                return valueA == valueB
            return False

        def match_node(node, level=0, parents=None):
            if not parents:
                parents = []
            results = {'__matches':[], '__shows':[], "__children":[], "__output":[]}
            if type(node) == type({}):
                for k,v in node.iteritems():
                    #print "\t"*level, k
                    if type(v) == type({}):
                        res = match_node(v, level+1, parents+[k])
                        results['__shows'].extend(res['__shows'])
                        results['__matches'].extend(res['__matches'])
                        results['__children'].extend(res['__children'])
                        results['__output'].extend(res['__output'])

                    if k in shows:
                        #print "Show: ", k, v
                        results['__shows'].append((k,v, level, parents))
                    if k in matchers:
                        value, comparison = matchers.get(k)
                        result = compare(v, comparison, value)
                        if result:
                            if verbose:
                                print("Match: {0} {1}".format(k, v))
                            results['__matches'].append( (k,v,level, parents))
                    if k == "meta":
                        try:
                            jv = json.loads(v)
                            if type(jv) == type({}):
                                res = match_node(jv, level)
                                results['__shows'].extend(res['__shows'])
                                results['__matches'].extend(res['__matches'])
                                results['__children'].extend(res['__children'])
                                results['__output'].extend(res['__output'])

                        except:
                            if verbose:
                                print("Bad meta: {0}".format(v))

                children = node.get('children', [])
                for child in children:
                    res = match_node(child, level+1, [])
                    match_keys = set(r[0] for r in res['__matches'])
                    if all(k in match_keys for k in matchers.keys()):
                        results['__output'].append( res['__shows'] )

            if type(node) == type([]):
                for l in node:
                    res = match_node(l, level+1)
                    results['__shows'].extend(res['__shows'])
                    results['__matches'].extend(res['__matches'])
                    results['__children'].extend(res['__children'])
                    results['__output'].extend(res['__output'])

            if type(node) == type(""):
                pass

            return results

        output = []
        for d in match_node(data)['__output']:
            out = []
            # Loop through so we get the correct order from our input shows
            for show in shows:
                for e in d:
                    if e[0] == show:
                        out.append(str(e[1]))
            output.append("\t".join(out))
        print("\n".join(output))


    def _combinereads(self, reads, sort):
        '''
        >>> exo = ExoRPC()
        >>> exo._combinereads([[[2, 'a'], [1, 'b']]])
        [[2, ['a']], [1, ['b']]]
        >>> exo._combinereads([[[3, 'a'], [2, 'b']], [[3, 77], [1, 78]]])
        [[3, ['a', 77]], [2, ['b', None]], [1, [None, 78]]]
        >>> exo._combinereads([[[5, 'a'], [4, 'b']], [[2, 'd'], [1, 'e']]])
        [[5, ['a', None]], [4, ['b', None]], [2, [None, 'd']], [1, [None, 'e']]]
        >>> exo._combinereads([])
        []
        '''
        if len(reads) == 0:
            return []
        else:
            combined = []

            # indexes into each list indicating the next
            # unprocessed value
            curi = [len(l) - 1 for l in reads]
            #print(reads)

            # loop until we've processed every element
            while curi != [-1] * len(curi):
                # minimum timestamp from unprocessed entries
                timestamp = min([reads[i][ci] for i, ci in enumerate(curi) if ci is not -1],
                        key=itemgetter(0))[0]

                # list of points we haven't processed in each read result
                # (or None, if all have been processed)
                unprocessed = [r[i] if i > -1 else None for i, r in zip(curi, reads)]

                # list of values corresponding to timestamp t
                values = [None if p is None or p[0] != timestamp else p[1]
                        for p in unprocessed]

                #print('curi {}, values {}, unprocessed: {}'.format(curi, values, unprocessed))

                # add to combined results
                combined.append([timestamp, values])

                # update curi based on which values were processed
                for i, v in enumerate(values):
                    if v is not None:
                        curi[i] -= 1

            if sort == 'desc':
                reverse = True
            else:
                reverse = False

            combined.sort(key=itemgetter(0), reverse=reverse)
            return combined

    def readmult(self,
                 auth,
                 rids,
                 limit,
                 sort='asc',
                 starttime=None,
                 endtime=None,
                 selection='all',
                 chunksize=212,
                 progress=lambda count: None):
        '''Generates multiple rids and returns combined timestamped data like this:
               [12314, [1, 77, 'a']
               [12315, [2, 78, None]]
           Where 1, 77, 'a' is the order rids were passed, and None represents
           no data in that dataport for that timestamp.'''
        options = self._readoptions(limit, sort, starttime, endtime, selection)

        count = [0]
        def _read(auth, rids, rid_options):
            '''Returns a list of lists.  Each of the inner lists is the
               set of timestamp, value responses for a RID.'''
            #print("options: ", rid_options)
            responses = self._exomult(auth, [['read', r, o] for r, o in zip(rids, rid_options)])
            count[0] += len(responses)
            progress(count[0])
            return responses

        # Each RID needs to keep track of its stating point for each
        # seperately.  If all of the datapoints requested were not created
        # at the same time,we'll get out of step and skip data.
        ridOptions = []

        for r in rids:
            # Create a copy of the options that will be sent along with
            # each RID
            ridOptions.append(options.copy())

        # Create a list of empty lists to hold the results for each RID
        totals = [[] for i in range(len(rids))]

        # Check if the limit is smaller than the chunksize, if so we can read
        # it in a single slurp.  Otherwise we need to read the results from
        # each RID into memory and then merge them and turncage the resuling
        # list to the limit.
        if limit <= chunksize :
            responses = _read(auth, rids, ridOptions)

            # Add the set of responses to the result
            i = 0
            for r in responses:
                totals[i].extend(r)
                i += 1
        else:
            # Each RID also needs a list to track the number of items remaining
            # to read as one RID may run out of data before another.
            ridMaxLimits = []

            for r in rids:
                # Create another copy of the options told hold the current
                # value of the limit (since it'll be larger than the chunksize)
                ridMaxLimits.append(options.copy())

            if 'sort' in options and options['sort'] == 'desc':
                # descending
                if 'endtime' in options:
                    nextStart = options['endtime']
                else:
                    nextStart = ExoUtilities.parse_ts_tuple(datetime.now().timetuple())

                for o in ridOptions:
                    o['endtime'] = nextStart
                    o['limit'] = chunksize

                done = False
                while not done:
                    # Read the chunk
                    responses = _read(auth, rids, ridOptions)

                    # Get the last time from the responses for each RID and
                    # subtract a second.  This is the start time for the next
                    # chunk.
                    for o, m, r in zip(ridOptions, ridMaxLimits, responses):
                        # Decrement the max to read by the amount we've read
                        m['limit'] = m['limit'] - len(r)
                        if m['limit'] <= 0:
                            done = True
                            break

                        # Set the limit
                        if m['limit'] > chunksize:
                            o['limit'] = chunksize
                        else:
                            o['limit'] = m['limit']

                        if len(r) > 0:
                            # Set the next time to read from
                            o['endtime'] = r[-1][0] - 1

                    # Add the set of responses to the result
                    i = 0
                    for r in responses:
                        totals[i].extend(r)
                        i += 1

                    # Check the length of all of the returned lists, when
                    # they're all zero, we can exit
                    length = []
                    for r in responses:
                        length.append(len(r))

                    if max(length) == 0:
                        break
            else:
                # ascending
                if 'starttime' in options:
                    nextStart = options['starttime']
                else:
                    nextStart = 0

                for o in ridOptions:
                    o['starttime'] = nextStart
                    o['limit'] = chunksize

                done = False
                while not done:
                    # Read the chunk
                    responses = _read(auth, rids, ridOptions)

                    # Get the last time from the responses for each RID and
                    # add a second.  This is the start time for the next
                    # chunk.
                    for o, m, r in zip(ridOptions, ridMaxLimits, responses):
                        # Decrement the max to read by the amount we've read
                        m['limit'] = m['limit'] - len(r)
                        if m['limit'] <= 0:
                            done = True
                            break

                        # Set the limit
                        if m['limit'] > chunksize:
                            o['limit'] = chunksize
                        else:
                            o['limit'] = m['limit']

                        if len(r) > 0:
                            # Set the next time to read from
                            o['starttime'] = r[-1][0] + 1

                    # Add the set of responses to the result
                    i = 0
                    for r in responses:
                        totals[i].extend(r)
                        i += 1

                    # Check the length of all of the returned lists, when
                    # they're all zero, we can exit
                    length = []
                    for r in responses:
                        length.append(len(r))

                    if max(length) == 0:
                        break

        # Combine all of the data read
        res = self._combinereads(totals, options['sort'])

        # Truncate the list to the requested limit and generate the results
        for r in res[:options['limit']]:
            yield r

    def write(self, auth, rid, value):
        isok, response = self.exo.write(auth, rid, value)
        self._raise_for_response(isok, response)

    def record(self, auth, rid, entries):
        isok, response = self.exo.record(auth, rid, entries, {})
        self._raise_for_response_record(isok, response)

    def create(self, auth, type, desc, name=None):
        if name is not None:
            desc['name'] = name
        isok, response = self.exo.create(auth, type, desc)
        self._raise_for_response(isok, response)
        return response

    def update(self, auth, rid, desc):
        isok, response = self.exo.update(auth, rid, desc)
        self._raise_for_response(isok, response)
        return response

    def create_dataport(self, auth, format, name=None):
        '''Create a dataport child of auth with common defaults.
           (retention count duration set to "infinity"). Returns
           RID string of the created dataport.'''
        desc = {"format": format,
                "retention": {
                    "count": "infinity",
                    "duration": "infinity"}
                }
        if name is not None:
            desc['name'] = name
        return self.create(auth, 'dataport', desc)

    def create_client(self, auth, name=None, desc=None):
        '''Create a client child of auth with common defaults.
        ('inherit' set for all limits). Returns RID string
        of the created client.'''
        if desc is None:
            # default description
            desc = {'limits': {'client': 'inherit',
                               'dataport': 'inherit',
                               'datarule': 'inherit',
                               'disk': 'inherit',
                               'dispatch': 'inherit',
                               'email': 'inherit',
                               'email_bucket': 'inherit',
                               'http': 'inherit',
                               'http_bucket': 'inherit',
                               'share': 'inherit',
                               'sms': 'inherit',
                               'sms_bucket': 'inherit',
                               'xmpp': 'inherit',
                               'xmpp_bucket': 'inherit'}
            }
        if name is not None:
            desc['name'] = name
        return self.create(auth, 'client', desc)

    def drop(self, auth, rids):
        for rid in rids:
            self.exo.drop(auth, rid, defer=True)

        if self.exo.has_deferred(auth):
            self._raise_for_deferred(self.exo.send_deferred(auth))

    def map(self, auth, rid, alias):
        '''Creates an alias for rid. '''
        isok, response = self.exo.map(auth, rid, alias)
        self._raise_for_response(isok, response)
        return response

    def unmap(self, auth, alias):
        '''Removes an alias a child of calling client.'''
        isok, response = self.exo.unmap(auth, alias)
        self._raise_for_response(isok, response)
        return response

    def lookup(self, auth, alias):
        isok, response = self.exo.lookup(auth, 'alias', alias)
        self._raise_for_response(isok, response)
        return response

    def lookup_owner(self, auth, rid):
        isok, response = self.exo.lookup(auth, 'owner', rid)
        self._raise_for_response(isok, response)
        return response

    def lookup_shared(self, auth, code):
        isok, response = self.exo.lookup(auth, 'shared', code)
        self._raise_for_response(isok, response)
        return response

    def listing(self, auth, types, options={}, rid=None):
        isok, response = self.exo.listing(auth, types, options=options, resource=rid)
        self._raise_for_response(isok, response)
        return response

    def _listing_with_info(self, auth, types, info_options={}, listing_options={}, read_options=None):
        '''Return a dict mapping types to dicts mapping RID to info for that
        RID. E.g.:
            {'client': {'<rid0>':<info0>, '<rid1>':<info1>},
             'dataport': {'<rid2>':<info2>, '<rid3>':<info3>}}

             info_options and read_options correspond to the options parameters
                 for info and read.
             read_options if set to something other than None, does a read for
                 any datarule or dataport in the listing, passing read_options
                 as options. The result of the read, a list of timestamp value
                 pairs, is placed inside the info dict in a 'read' property.'''

        assert(len(types) > 0)

        listing = self._exomult(auth, [['listing', types, listing_options, {'alias': ''}]])[0]

        # listing is a dictionary mapping types to lists of RIDs, like this:
        # {'client': ['<rid0>', '<rid1>'], 'dataport': ['<rid2>', '<rid3>']}

        # request info for each rid
        # (rids is a flattened version of listing)
        rids = []
        restype = {}
        for typ in types:
            rids += listing[typ]
            for rid in listing[typ]:
                restype[rid] = typ

        info_commands = [['info', rid, info_options] for rid in rids]
        read_commands = []
        readable_rids = [rid for rid in rids if restype[rid] in ['dataport', 'datarule']]
        if read_options is not None:
            # add reads for readable resource types
            read_commands += [['read', rid, read_options] for rid in readable_rids]
        responses = self._exomult(auth, info_commands + read_commands)
        # From the return values make a dict of dicts
        # use ordered dicts in case someone cares about order in the output
        response_index = 0
        listing_with_info = OrderedDict()
        for typ in types:
            type_response = OrderedDict()
            for rid in listing[typ]:
                type_response[rid] = responses[response_index]
                response_index += 1
                if read_options is not None and rid in readable_rids:
                    type_response[rid]['read'] = responses[len(info_commands) + readable_rids.index(rid)]

            listing_with_info[typ] = type_response

        return listing_with_info

    def info(self,
             auth,
             rid={'alias': ''},
             options={},
             cikonly=False,
             recursive=False,
             level=None):
        '''Returns info for RID as a dict.'''
        if cikonly:
            options = {'key': True}
        if recursive:
            rid = None if type(rid) is dict else rid
            response = self._infotree(auth,
                                      rid=rid,
                                      options=options,
                                      level=level)
        else:
            isok, response = self.exo.info(auth, rid, options)
            self._raise_for_response(isok, response)
        if cikonly:
            if not 'key' in response:
                raise ExoException('{0} has no CIK'.format(rid))
            return response['key']
        else:
            return response

    def flush(self, auth, rids, newerthan=None, olderthan=None):
        args=[]
        options = {}
        if newerthan is not None: options['newerthan'] = newerthan
        if olderthan is not None: options['olderthan'] = olderthan
        if len(options) > 0:
            args.append(options)
        cmds = [['flush', rid] + args for rid in rids]
        self._exomult(auth, cmds)

    def usage(self, auth, rid, metrics, start, end):
        for metric in metrics:
            self.exo.usage(auth, rid, metric, start, end, defer=True)
        responses = []
        if self.exo.has_deferred(auth):
            responses = self._raise_for_deferred(self.exo.send_deferred(auth))
        # show report
        maxlen = max([len(m) for m in metrics])
        for i, r in enumerate(responses):
            print("{0}:{1} {2}".format(
                  metrics[i], ' ' * (maxlen - len(metrics[i])), r))

    def share(self, auth, rid, options):
        isok, response = self.exo.share(auth,
                                        rid,
                                        options)
        self._raise_for_response(isok, response)
        return response

    def revoke(self, auth, codetype, code):
        isok, response = self.exo.revoke(auth, codetype, code)
        self._raise_for_response(isok, response)
        return response

    def activate(self, auth, codetype, code):
        isok, response = self.exo.activate(auth, codetype, code)
        self._raise_for_response(isok, response)
        return response

    def deactivate(self, auth, codetype, code):
        isok, response = self.exo.deactivate(auth, codetype, code)
        self._raise_for_response(isok, response)
        return response

    def clone(self, auth, options):
        isok, response = self.exo.create(auth, 'clone', options)
        self._raise_for_response(isok, response)
        return response

    def _print_tree_line(self, line):
        if sys.version_info < (3, 0):
            print(line.encode('utf-8'))
        else:
            print(line)

    def humanize_date(self, time=False):
        '''Get a datetime object or a int() Epoch timestamp and return a
        pretty string like 'an hour ago', 'Yesterday', '3 months ago',
        'just now', etc.'''
        now = datetime.now()
        if type(time) is int:
            diff = now - datetime.fromtimestamp(time)
        elif isinstance(time,datetime):
            diff = now - time
        elif not time:
            diff = now - now
        return humanize.naturaltime(diff)

    def _format_timestamp(self, values):
        '''format tree latest point timestamp

        values is up to two most recent values, e.g.:
            [[<timestamp1>, <value1>], [<timestamp0>, <value0>]]'''
        if values is None:
            return None
        if len(values) == 0:
            return ''
        return self.humanize_date(values[0][0])

    def _format_value_with_previous(self, v, prev, maxlen):
        '''Return a string representing the string v, w/maximum length
           maxlen. If v is longer than maxlen, the return value
           should include something that changed from previous
           value prev.'''
        v = repr(v)
        prev = repr(prev)
        if len(v) <= maxlen:
            return v

        sm = difflib.SequenceMatcher(None, prev, v)
        def get_nonmatching_blocks(mb):
            lasti = 0
            out = []
            for m in mb:
                if m.b - lasti > 0:
                    out.append({'i': lasti, 'size': m.b - lasti})
                lasti = m.b + m.size
            return out

        # get the blocks (index, size) of v that changed from prev
        mb = list(sm.get_matching_blocks())
        nonmatching_blocks = get_nonmatching_blocks(mb)

        # get the biggest non-matching block
        #bnb = nmb.sorted(nonmatching_blocks, key=lambda(b): b['size'])[-1]

        def widen_block(block, s, left=0, right=0):
            '''block is a location in s and size in this form:
               {'i': <index>, 'size': <size>}. Return block b
               such that the b is up to widen_by wider on the
               left and right while keeping it within the bounds
               of s. block must be already a subset of s. '''
            out = copy.copy(block)
            for j in range(left):
                # try to add to left
                if out['i'] > 0:
                    out['i'] -= 1
                    out['size'] += 1
            for j in range(right):
                # try to add to right
                if out['i'] + out['size'] < len(s):
                    out['size'] += 1
            return out

        # number of characters of context to show on either side of a difference
        context = 5
        s = ''

        print(prev)
        print(v)
        print(mb)
        print(nonmatching_blocks)

        startblock = widen_block(nonmatching_blocks[0], v, left=context, right=maxlen)
        s = ''
        if startblock['i'] > 0:
            s += '...'
        s += v[startblock['i']:startblock['i']+startblock['size']]
        return s[:maxlen] + ('...' if startblock['i'] + len(s) < maxlen else '')


    def _format_values(self, values, maxlen=20):
        '''format latest value for output with tree

        values is up to two most recent values, e.g.:
            [[<timestamp1>, <value1>], [<timestamp0>, <value0>]]'''
        if values is None:
            return None
        if len(values) == 0:
            return ''

        v = values[0][1]
        if type(v) is float or type(v) is int:
            return str(v)
        elif type(v) is dict:
            return str(v)
        else:
            latest = v.replace('\n', r'\n').replace('\r', r'\r')
            out = (latest[:maxlen - 3] + '...') if len(latest) > maxlen else latest
            return out
            # this is not better
            #prev = values[1][1] if len(values) > 1 else ''
            #v = values[0][1]
            #return self._format_value_with_previous(v, prev, maxlen)

    def _print_node(self, rid, auth_dict, info, aliases, cli_args, spacer, islast, maxlen=None, values=None):
        twee = cli_args['<command>'] == 'twee'
        typ = info['basic']['type']
        auth_type, auth_str = self.auth_dict_parts(auth_dict)
        if typ == 'client':
            id = auth_type + ': ' + auth_str
            if 'client_id' in auth_dict:
                id = id + ' client_id:' + auth_dict['client_id']
        else:
            id = 'rid: ' + rid
        name = info['description']['name']
        try:
            # Units are a portals only thing
            # u'comments': [[u'public', u'{"unit":"Fahrenheit"}']],']]
            units = json.loads(info['comments'][0][1])['unit']
            if len(units.strip()) == 0:
                units = 'none'
        except:
            units = 'none'

        # Sometimes aliases is a dict, sometimes a list. TODO: Why?
        # Translate it into a list.
        if type(aliases) is dict:
            aliases = aliases.get(rid, [])
        elif aliases is None:
            aliases = []

        opt = OrderedDict()

        def add_opt(o, label, value):
            if o is True or (o in cli_args and cli_args[o] is True):
                opt[label] = value
        try:
            # show portals metadata if present
            # http://developers.exosite.com/display/POR/Developing+for+Portals
            meta = json.loads(info['description']['meta'])
            device = meta['device']
            if device['type'] == 'vendor':
                add_opt(True, 'vendor', device['vendor'])
                add_opt(True, 'model', device['model'])
                add_opt(True, 'sn', device['sn'])
        except:
            pass

        has_alias = aliases is not None and len(aliases) > 0
        if has_alias:
            if type(aliases) is list:
                add_opt(True, 'aliases', json.dumps(aliases))
            else:
                add_opt(True, 'aliases', aliases)
        # show RID for clients with no alias, or if --verbose was passed
        ridopt = False
        if typ == 'client':
            if has_alias:
                ridopt = '--verbose'
            else:
                ridopt = True
        add_opt(ridopt, 'rid', rid)
        add_opt('--verbose', 'unit', units)

        if 'listing_option' in info and info['listing_option'] == 'activated':
            add_opt(True, 'share', True)

        if maxlen == None:
            maxlen = {}
            maxlen['type'] = len(typ)
            maxlen['name'] = len(name)
            maxlen['format'] = 0 if 'format' not in info['description'] else len(info['description']['format'])

        try:
            terminal_width, terminal_height = exocommon.get_terminal_size()
        except:
            # Default to 80 chars
            terminal_width = 80

        val = self._format_values(values, terminal_width)
        timestamp = self._format_timestamp(values)
        add_opt(values is not None, 'value', None if (val is None or timestamp is None) else val + '/' + timestamp)

        if twee:
            # colors, of course
            default = colored_terminal.normal

            if cli_args['--nocolor']:
                SPACER = default
                NAME = default
                TYPE = default
                ID = default
                VALUE = default
                TIMESTAMP = default
                PINK = default
                MODEL = default
                ENDC = default
            else:
                SPACER = default
                NAME = default
                TYPE = colored_terminal.magenta
                ID = colored_terminal.green
                TIMESTAMP = colored_terminal.blue
                VALUE = colored_terminal.yellow
                PINK = colored_terminal.magenta
                MODEL = colored_terminal.cyan
                ENDC = colored_terminal.normal

            # the goal here is to make the line short to provide more room for the value
            # so if there's an alias, just use that, since it's
            # if no alias, then the first ten of the RID and the name
            # if multiple alias, then the first alias

            if typ == 'client':
                if cli_args['--rids']:
                    tweeid = SPACER + ID + id
                else:
                    tweeid = SPACER + ID + id
            else:
                if cli_args['--rids']:
                    tweeid = SPACER + ID + id
                else:
                    if aliases is not None and len(aliases) > 0:
                        tweeid = aliases[0]
                    else:
                        tweeid = 'rid.' + rid[:5]

            displayname = ((name + SPACER + ' ') if len(name) > 0 else ' ')

            displaytype = {'dataport': 'dp', 'client': 'cl', 'datarule': 'dr', 'dispatch': 'ds'}[typ]
            if not cli_args['--nocolor'] and displaytype == "cl":
                NAME = colored_terminal.underline
            if 'format' in info['description']:
                displaytype += '.' + {'binary': 'b', 'string': 's', 'float': 'f', 'integer': 'i'}[info['description']['format']]
            else:
                displaytype = '  ' + displaytype


            displaymodel = ''
            if 'sn' in opt and 'model' in opt:
                displaymodel = ' (' + opt['model'] + '#' + opt['sn'] + ')'


            if val:
                twee_line = "".join([spacer, displayname, ' '*( maxlen['name']+1- len(name)), displaytype, tweeid, (' (share)' if 'listing_option' in info and info['listing_option'] == 'activated' else ''),
                                    ('' if typ == 'client' else ': '), ('' if timestamp is None or len(timestamp) == 0 else ' (' + timestamp + ')'), displaymodel])
                val_size = len(val)
                displayed_chars = len(twee_line)
                allowed_size = terminal_width-displayed_chars
                if val_size > allowed_size:
                    allowed_size -= 3
                    val = val[:allowed_size] + "..."

            self._print_tree_line(
                SPACER + spacer +
                NAME + displayname +
                ' '*(maxlen['name']-len(name)) +
                TYPE + displaytype +
                ' ' +
                ID + tweeid +
                SPACER + (' (share)' if 'listing_option' in info and info['listing_option'] == 'activated' else '') +
                ('' if typ == 'client' else ': ') +
                VALUE + ('' if val is None else val) +
                TIMESTAMP + ('' if timestamp is None or len(timestamp) == 0 else ' (' + timestamp + ')') +
                MODEL + displaymodel +
                ENDC
                )
        else:
            # standard tree
            if 'format' in info['description']:
                fmt = info['description']['format']
                desc = fmt + ' ' * (maxlen['format'] + 1 - len(fmt))
                desc += typ + ' ' * (maxlen['type'] + 1 - len(typ))
                desc += id
            else:
                desc = typ + ' ' * (maxlen['type'] + 1 - len(typ))
                desc += id

            self._print_tree_line('{0}{1}{2} {3} {4}'.format(
                spacer,
                name,
                ' ' * (maxlen['name'] + 1 - len(name)),
                desc,
                '' if len(opt) == 0 else '({0})'.format(', '.join(
                    ['{0}: {1}'.format(k, v) for k, v in iteritems(opt)]))))

    def auth_dict_parts(self, auth_dict):
        '''Return tuple of auth_type ('cik' or 'token'),
           auth_str from an auth dictionary'''
        return ('cik' if 'cik' in auth_dict else 'token',
            auth_dict['cik'] if 'cik' in auth_dict else auth_dict['token'])

    def tree(self, auth, aliases=None, cli_args={}, spacer='', level=0, info_options={}):
        '''Print a tree of entities in OneP'''
        max_level = int(cli_args['--level'])
        # print root node
        isroot = len(spacer) == 0
        if isinstance(auth, six.string_types):
            auth_type = 'cik'
            auth_str = auth
        elif type(auth) is dict:
            auth_type, auth_str = self.auth_dict_parts(auth)
            rid = auth.get('client_id', None)
        else:
            raise ExoException('Unexpected auth type ' + str(type(auth)))
        if isroot:
            # usage and counts are slow, so omit them if we don't need them
            exclude = ['usage', 'counts']
            info_options = self.make_info_options(exclude=exclude)
            rid, info = self._exomult(auth,
                                      [['lookup', 'alias', ''],
                                       ['info', {'alias': ''}, info_options]])
            # info doesn't contain key
            info['key'] = auth_str
            aliases = info['aliases']
            root_aliases = 'see parent'
            self._print_node(rid,
                             auth,
                             info,
                             root_aliases,
                             cli_args,
                             spacer,
                             True)
            if max_level == 0:
                return
            level += 1

        types = ['dataport', 'datarule', 'dispatch', 'client']
        try:
            should_read = '--values' in cli_args and cli_args['--values']

            #_listing_with_info() output looks like this
            # {'client': {'<rid0>':<info0>, '<rid1>':<info1>},
            #  'dataport': {'<rid2>':<info2>}}
            listing = self._listing_with_info(auth,
                types=types,
                info_options=info_options,
                listing_options={'owned': True},
                read_options={'limit': 1} if should_read else None)
            # mark as not shares
            for t in types:
                for info in listing[t].values():
                    info['listing_option'] = 'owned'
            # add annotations for shares
            listing_shares = self._listing_with_info(auth,
                types=types,
                info_options=info_options,
                listing_options={'activated': True},
                read_options={'limit': 1} if should_read else None)
            # mark as shares and add to listing
            for t in types:
                for rid, info in listing_shares[t].items():
                    info['listing_option'] = 'activated'
                    # skip any shares that are in the same listing
                    if rid not in listing[t]:
                        listing[t][rid] = info
        except pyonep.exceptions.OnePlatformException:
            self._print_tree_line(
                spacer +
                "  └─listing for {0} failed. info['basic']['status'] is \
probably not valid.".format(auth_str))
        except ExoRPC.RPCException as ex:
            if str(ex).startswith('locked ('):
                self._print_tree_line(
                    spacer +
                    "  └─{0} is locked".format(json.dumps(auth)))
            else:
                self._print_tree_line(
                    spacer +
                    "  └─RPC error for {0}: {1}".format(json.dumps(auth), ex))
        else:
            # calculate the maximum length of various things for all children,
            # so we can make things line up in the output.
            maxlen = {}
            namelengths = [len(l[1]['description']['name']) for typ in types for l in iteritems(listing[typ])]
            maxlen['name'] = 0 if len(namelengths) == 0 else max(namelengths)

            typelengths = [len(l[1]['basic']['type']) for typ in types for l in iteritems(listing[typ])]
            maxlen['type'] = 0 if len(typelengths) == 0 else max(typelengths)

            formatlengths = [len(l[1]['description']['format'])
                             for typ in types
                             for l in iteritems(listing[typ])
                             if 'format' in l[1]['description']]
            maxlen['format'] = 0 if len(formatlengths) == 0 else max(formatlengths)

            # print everything
            for t_idx, t in enumerate(types):
                typelisting = OrderedDict(sorted(iteritems(listing[t]), key=lambda x: x[1]['description']['name'].lower()))
                islast_nonempty_type = (t_idx == len(types) - 1) or (all(len(listing[typ]) == 0 for typ in types[t_idx + 1:]))
                for rid_idx, rid in enumerate(typelisting):
                    info = typelisting[rid]
                    islastoftype = rid_idx == len(typelisting) - 1
                    islast = islast_nonempty_type and islastoftype
                    if platform.system() != 'Windows':
                        if islast:
                            child_spacer = spacer + '    '
                            own_spacer   = spacer + '  └─'
                        else:
                            child_spacer = spacer + '  │ '
                            own_spacer   = spacer + '  ├─'
                    else:
                        # Windows executable
                        if islast:
                            child_spacer = spacer + '    '
                            own_spacer   = spacer + '  +-'
                        else:
                            child_spacer = spacer + '  | '
                            own_spacer   = spacer + '  +-'

                    if t == 'client':
                        self._print_node(rid, auth, info, aliases, cli_args, own_spacer, islast, maxlen)
                        if max_level == -1 or level < max_level:
                            print((auth_type, auth_str, rid))
                            self.tree({auth_type: auth_str, 'client_id': rid}, info['aliases'], cli_args, child_spacer, level=level + 1, info_options=info_options)
                    else:
                        self._print_node(rid, auth, info, aliases, cli_args, own_spacer, islast, maxlen, values=info['read'] if 'read' in info else None)

    def drop_all_children(self, auth):
        isok, listing = self.exo.listing(
            auth,
            types=['client', 'dataport', 'datarule', 'dispatch'],
            options={},
            resource={'alias': ''})
        self._raise_for_response(isok, listing)
        rids = itertools.chain(*[listing[t] for t in listing.keys()])
        self._exomult(auth, [['drop', rid] for rid in rids])

    def _lookup_rid_by_name(self, auth, name, types=['datarule']):
        '''Look up RID by name. We use name rather than alias to identify
        scripts created in Portals because it only displays names to the
        user, not aliases. Note that if multiple scripts have the same
        name the first one in the listing is returned.'''
        found_rid = None
        listing = self._listing_with_info(auth, types)
        for typ in listing:
            for rid in listing[typ]:
                if listing[typ][rid]['description']['name'] == name:
                    # return first match
                    return rid
        return None

    def _upload_script(self, auth, name, content, rid=None, alias=None, version='0.0.0'):
        '''Upload a lua script, either creating one or updating the existing one'''
        desc = {
            'format': 'string',
            'name': name,
            'preprocess': [],
            'rule': {
                'script': content
            },
            'visibility': 'parent',
            'retention': {
                'count': 'infinity',
                'duration': 'infinity'
            }
        }
        meta = {
            'version': version,
            'uploads': 1,
            'githash': ''
        }
        # if `git rev-parse HEAD` works, include that.
        try:
            githash = os.popen("git rev-parse HEAD").read()
            meta['githash'] = githash
        except:
            pass
        desc['meta'] = json.dumps(meta)

        if rid is None:
            success, rid = self.exo.create(auth, 'datarule', desc)
            if success:
                print("New script RID: {0}".format(rid))
            else:
                raise ExoException("Error creating datarule: {0}".format(rid))
            if alias is None:
                alias = name
            success, rid = self.exo.map(auth, rid, alias)
            if success:
                print("Aliased script to: {0}".format(alias))
            else:
                raise ExoException("Error aliasing script")
        else:
            isok, olddesc = self.exo.info(auth, rid)
            if isok:
                try:
                    oldmetajs = olddesc['description']['meta']
                    oldmeta = json.loads(oldmetajs)
                    uploads = oldmeta['uploads']
                    uploads = uploads + 1
                    meta['uploads'] = uploads
                    desc['meta'] = json.dumps(meta)
                except:
                    pass
                    # if none of that works, go with the default above.

            isok, response = self.exo.update(auth, rid, desc)
            if isok:
                print ("Updated script RID: {0}".format(rid))
            else:
                raise ExoException("Error updating datarule: {0}".format(response))

    def cik_recursive(self, auth, fn):
        '''Run fn on client and all its client children'''
        fn(auth)
        lwi = self._listing_with_info(auth,
                                      ['client'],
                                      info_options={'key': True})
        # {'client': {'<rid0>':<info0>, '<rid1>':<info1>}]
        for rid in lwi['client']:
            self.cik_recursive(lwi['client'][rid]['key'], fn)

    def upload_script_content(self,
                              auths,
                              content,
                              name,
                              recursive=False,
                              create=False,
                              filterfn=lambda script: script,
                              rid=None,
                              version='0.0.0'):
        for auth in auths:
            def up(auth, rid):
                if rid is not None:
                    alias = None
                    if create:
                        # when creating, if <rid> is passed it must be an alias
                        # to use instead of name
                        if type(rid) is not dict:
                            raise ExoException('<rid> must be an alias when passing --create')
                        alias = rid['alias']
                        rid = None
                    self._upload_script(auth, name, content, rid=rid, alias=alias, version=version)
                else:
                    rid = self._lookup_rid_by_name(auth, name)
                    if rid is not None or create:
                        self._upload_script(auth, name, content, rid=rid, version=version)
                    else:
                        # TODO: move this to spec plugin
                        print("Skipping CIK: {0} -- {1} not found".format(auth, name))
                        if not create:
                            print('Pass --create to create it')

            if recursive:
                self.cik_recursive(auth, lambda auth: up(auth, rid))
            else:
                up(auth, rid)


    def upload_script(self,
                      auths,
                      filename,
                      name=None,
                      recursive=False,
                      create=False,
                      filterfn=lambda script: script,
                      rid=None,
                      follow=False,
                      version='0.0.0'):
        try:
            f = open(filename)
        except IOError:
            raise ExoException('Error opening file {0}.'.format(filename))
        else:
            with f:
                content = filterfn(f.read())
                if len(content) > SCRIPT_LIMIT_BYTES:
                    sys.stderr.write(
                        'WARNING: script is {0} bytes over the size limit of {1} bytes.\n'.format(
                            len(content) - SCRIPT_LIMIT_BYTES, SCRIPT_LIMIT_BYTES))
                if name is None:
                    # if no name is specified, use the file name as a name
                    name = os.path.basename(filename)
                def upl():
                    self.upload_script_content(
                        auths,
                        content,
                        name=name,
                        recursive=recursive,
                        create=create,
                        filterfn=filterfn,
                        rid=rid,
                        version=version)
                if follow:
                    if len(auths) > 1:
                        raise Exception('following more than one CIK is not supported')
                    lines = followSeries(
                        self,
                        auths[0],
                        {'alias': name},
                        timeout_milliseconds=3000,
                        printFirst=True)
                    options = {'format': 'human'}
                    writer = serieswriter.SeriesWriter(['timestamp', 'log'], options)
                    last_modified = 0
                    last_activity = 0
                    last_status = ''
                    uploaded = False
                    nocolor = platform.system() == 'Windows'
                    def ifcolor(c):
                        return colored_terminal.normal if nocolor else c
                    class colors:
                        SPACER = ifcolor(colored_terminal.normal)
                        NAME = ifcolor(colored_terminal.normal)
                        TYPE = ifcolor(colored_terminal.magenta)
                        ID = ifcolor(colored_terminal.green)
                        TIMESTAMP = ifcolor(colored_terminal.blue)
                        VALUE = ifcolor(colored_terminal.yellow)
                        PINK = ifcolor(colored_terminal.magenta)
                        MODEL = ifcolor(colored_terminal.cyan)
                        ENDC = ifcolor(colored_terminal.normal)
                        GRAY = ifcolor(colored_terminal.gray)
                        GREEN = ifcolor(colored_terminal.green)
                        RED = ifcolor(colored_terminal.red)

                    def status_color(status):
                        return colors.RED if status == 'error' else colors.GREEN
                    # loop forever
                    for timestamp, vals in lines:
                        towrite = []
                        info = self._exomult(auths[0], [
                            ['info', {'alias': name}, {'basic': True, 'description': True}]])[0]
                        code = info['description']['rule']['script']
                        if timestamp is not None and vals is not None:
                            # received a point

                            # break up lines
                            if uploaded:
                                lines = vals[0].split('\n')
                                for line in lines:
                                    # Parse lua errors and show the line with the error
                                    # [string "..."]:6: global namespace is reserved
                                    match = re.match('\[string ".*\.\.\."\]:(\d+): (.*)', line)
                                    if match is None:
                                        towrite.append([timestamp, [line], 'debug'])
                                    else:
                                        err_line = int(match.groups()[0])
                                        code_lines = code.splitlines()

                                        code_excerpt = ''
                                        # previous line
                                        if err_line > 1:
                                            code_excerpt += (' ' * 11 + str(err_line - 1) + ' ' + code_lines[err_line - 2] + '\n')
                                        # line with the error
                                        code_excerpt += ' ' * 11 + str(err_line) + ' ' + code_lines[err_line - 1] + '\n'
                                        # next line
                                        if err_line < len(code_lines):
                                            code_excerpt += (' ' * 11 + str(err_line + 1) + ' ' + code_lines[err_line])

                                        err_msg = match.groups()[1]
                                        towrite.append([timestamp, [colors.RED + 'ERROR: ' + err_msg + colors.ENDC + ' (line ' + str(err_line) + ')\n' + code_excerpt], 'debug'])

                        modified = info['basic']['modified']
                        if modified != last_modified:
                            if uploaded:
                                towrite.append([modified, [colors.PINK + 'script modified' + colors.ENDC], '00 modified'])
                            last_modified = modified
                        '''# sort by timestamp to keep the code simple
                        activities = sorted(info['basic']['activity'], key=lambda x: x[0])
                        for act_ts, act_list in activities:
                            if act_ts > last_activity:
                                if uploaded:
                                    msg = ', '.join(reversed([status_color(s) + s + colors.ENDC for s in act_list])) + colors.ENDC
                                    towrite.append(
                                        [act_ts, [msg], '01 activity'])
                                last_activity = act_ts'''
                        status = info['basic']['status']
                        if status != last_status:
                            # this doesn't have a timestamp, so use the highest timestamp
                            towrite.append([
                                None,
                                ['[' + colors.GRAY + '.' * 8 + colors.ENDC + '] ' +
                                 status_color(status) + status + colors.ENDC],
                                '02 status'])
                            last_status = status

                        # upload *after* getting info for the first time,
                        # for more consistent output
                        if not uploaded:
                            # warn if script is unchanged
                            if code == content:
                                sys.stderr.write(colors.PINK + 'WARNING' + colors.ENDC + ': script code matches what is on the server, so script will NOT be restarted\n')
                            upl()
                            uploaded = True

                        # sort by timestamp, then tag
                        # (sorting by tag puts modified before status, which is more common)
                        towrite = sorted(towrite, key=lambda x: (x[0], x[2]))
                        for ts, vals, tag in towrite:
                            if ts is not None:
                                writer.write(ts, vals)
                            else:
                                print(vals[0])


                        #c = exocommon.getch()
                        #print('char: ' + c)
                else:
                    upl()

    def lookup_rid(self, auth, cik_to_find):
        isok, listing = self.exo.listing(auth, types=['client'], options={}, resource={'alias': ''})
        self._raise_for_response(isok, listing)

        for rid in listing['client']:
            self.exo.info(auth, rid, {'key': True}, defer=True)

        if self.exo.has_deferred(auth):
            responses = self.exo.send_deferred(auth)
            for idx, r in enumerate(responses):
                call, isok, response = r
                self._raise_for_response(isok, response)

                if response['key'] == cik_to_find:
                    return listing['client'][idx]

    def record_backdate(self, auth, rid, interval_seconds, values):
        '''Record a list of values and record them as if they happened in
        the past interval_seconds apart. For example, if values
            ['a', 'b', 'c']
        are passed in with interval 10, they're recorded as
            [[0, 'c'], [-10, 'b'], [-20, 'a']].
        interval_seconds must be positive.'''
        timestamp = -interval_seconds

        tvalues = []
        values.reverse()
        for v in values:
            tvalues.append([timestamp, v])
            timestamp -= interval_seconds
        return self.record(auth, rid, tvalues)


    def _create_from_infotree(self, parentcik, infotree):
        '''Create a copy of infotree under parentcik'''
        info_to_copy = infotree['info']
        typ = info_to_copy['basic']['type']
        rid = self.create(parentcik, typ, info_to_copy['description'])
        if 'comments' in info_to_copy and len(info_to_copy['comments']) > 0:
            commands = [['comment', rid, c[0], c[1]] for c in info_to_copy['comments']]
            self._exomult(parentcik, commands)
        if typ == 'client':
            # look up new CIK
            cik = self.info(parentcik, rid)['key']
            children = infotree['info']['children']
            aliases_to_create = {}
            for child in children:
                newrid, _ = self._create_from_infotree(cik, child)
                if child['rid'] in infotree['info']['aliases']:
                    aliases_to_create[newrid] = infotree['info']['aliases'][child['rid']]

            # add aliases in one request
            self._exomult(
                cik,
                list(itertools.chain(*[[['map', r, alias]
                                     for alias in aliases_to_create[r]]
                                     for r in aliases_to_create])))
            return rid, cik
        else:
            return rid, None

    def _counttypes(self, infotree, counts=defaultdict(int)):
        '''Return a dictionary with the count of each type of resource in the
        tree. For example, {'client': 2, 'dataport': 1, 'dispatch':1}'''
        info = infotree['info']
        counts[info['basic']['type']] += 1
        if 'children' in info:
            for child in info['children']:
                counts = self._counttypes(child, counts=counts)
        return counts

    def copy(self, cik, destcik, infotree=None):
        '''Make a copy of cik and its non-client children to destcik and
        return the cik of the copy.'''


        # read in the whole client to copy at once
        if infotree is None:
            def check_for_unsupported(rid, info):
                desc = info['description']
                if 'subscribe' in desc and desc['subscribe'] is not None and len(desc['subscribe']) > 0:
                    raise ExoException('''Copy does not yet support resources that use the "subscribe" feature, as RID {0} in the source client does.\nIf you're just copying a device into the same portal consider using the clone command.'''.format(rid));
                return rid
            destcik = exoconfig.lookup_shortcut(destcik)
            infotree = self._infotree(cik, options={}, nodeidfn=check_for_unsupported)

        # check counts
        counts = self._counttypes(infotree)
        destinfo = self.info(destcik, options={'description': True, 'counts': True})

        noroom = ''
        for typ in counts:
            destlimit = destinfo['description']['limits'][typ]
            destcount = destinfo['counts'][typ]
            needs = counts[typ]
            # TODO: need a way to check if limit is set to 'inherit'
            if type(destlimit) is int and destlimit - destcount < needs:
                noroom = noroom + 'Thing to copy has {0} {1}{4}, parent has limit of {3} (and is using {2}).\n'.format(
                    needs, typ, destcount, destlimit, 's' if needs > 1 else '')

        if len(noroom) > 0:
            raise ExoException('Copy would violate parent limits:\n{0}'.format(noroom))

        cprid, cpcik = self._create_from_infotree(destcik, infotree)

        return cprid, cpcik

    def _remove(self, dct, keypaths):
        '''Remove keypaths from dictionary.
        >>> ex = ExoRPC()
        >>> ex._remove({'a': {'b': {'c': 1}}}, [['a', 'b', 'c']])
        {'a': {'b': {}}}
        >>> ex._remove({'a': {'b': {'q': 1}}}, [['a', 'b', 'c']])
        {'a': {'b': {'q': 1}}}
        >>> ex._remove({}, [['a'], ['b'], ['c']])
        {}
        >>> ex._remove({'q': 'a'}, [['a'], ['b']])
        {'q': 'a'}
        '''
        for kp in keypaths:
            x = dct
            for i, k in enumerate(kp):
                if k in x:
                    if i == len(kp) - 1:
                        del x[k]
                    else:
                        x = x[k]
                else:
                    break
        return dct

    def _differences(self, dict1, dict2):
        differ = difflib.Differ()

        s1 = json.dumps(dict1, indent=2, sort_keys=True).splitlines(1)
        s2 = json.dumps(dict2, indent=2, sort_keys=True).splitlines(1)

        return list(differ.compare(s1, s2))

    #def _infotree(self,
    #              auth,
    #              rid=None,
    #              restype='client',
    #              resinfo=None,
    #              nodeidfn=lambda rid,
    #              info: rid,
    #              options={},
    #              level=None,
    #              raiseExceptions=True,
    #              errorfn=lambda auth, msg: None):

    def _infotree_fast(self,
                       auth,
                       nodeidfn=lambda rid, info: rid,
                       options={},
                       level=None,
                       listing_options={},
                       visit=lambda tree, level, parentRID: None):
        '''Faster version of _infotree that uses the new listing and breadth
           first traversal to reduce the number of RPC calls.'''
        rootnode = {'tree': {'type': 'client'}, 'par': None}
        #if rid is not None:
        #    # nodeidfn here?
        #    rootnode['tree']['rid'] = rid
        level = 0
        gen = [rootnode]
        nextgen = []
        def callback(commandset, result):
            # add the commandset results to the node
            node = commandset['node']
            tree = node['tree']
            info_idx = 0
            listing_idx = 1
            lookup_idx = 2

            # set node info
            if result[info_idx]['status'] != 'ok':
                tree['info'] = {'error': result[info_idx]}
            else:
                tree['info'] = result[info_idx]['result']

            # lookup is only done for the root node when rid is not known
            if len(result) == lookup_idx + 1:
                # this would not be OK
                assert(result[lookup_idx]['status'] == 'ok')
                tree['rid'] = result[lookup_idx]['result']

            tree['rid'] = nodeidfn(tree['rid'], tree['info'])

            # set node listing
            if tree['type'] == 'client':
                if result[listing_idx]['status'] != 'ok':
                    tree['children'] = {'error': result[listing_idx]}
                else:
                    children = []
                    r = result[listing_idx]['result']
                    for typ in r.keys():
                        for rid in r[typ]:
                            children.append({'rid': rid, 'type': typ})
                    tree['children'] = children

        def commandset(node):
            rid = node['tree']['rid'] if 'rid' in node['tree'] else {'alias': ''}
            types = ['client', 'dataport', 'datarule', 'dispatch']
            commands = [
                ['info', rid, options]
            ]
            if node['tree']['type'] == 'client':
                commands.append(['listing', types, listing_options, rid])
            if 'rid' not in node['tree']:
                commands.append(['lookup', 'aliased', ''])
            return {'node': node,
                    'commands': commands,
                    'callback': callback}

        while len(gen) > 0:
            # set up commandsets with callbacks that modify the nodes in gen
            commands = map(commandset, gen)

            # get info, listing, etc. for each node at this level
            results = self._exobatch(auth, commands)
            results = list(results)

            # now the nodes are populated, so build up the next generation
            for node in gen:
                visit(node['tree'], level, node['par']);
                if 'children' in node['tree']:
                    for child_tree in node['tree']['children']:
                        nextgen.append({'tree': child_tree, 'par': node['tree']['rid']})

            gen = nextgen
            nextgen = []
            level += 1

        return rootnode['tree']

    def _infotree(self,
                  auth,
                  rid=None,
                  restype='client',
                  resinfo=None,
                  nodeidfn=lambda rid,
                  info: rid,
                  options={},
                  level=None,
                  raiseExceptions=True,
                  errorfn=lambda auth, msg: None):
        '''Get all info for a cik and its children in a nested dict.
        The basic unit is {'rid': '<rid>', 'info': <info-with-children>},
        where <info-with-children> is just the info object for that node
        with the addition of 'children' key, which is a dict containing
        more nodes. Here's an example return value:

           {'rid': '<rid 0>', 'info': {'description': ...,
                        'basic': ....,
                        ...
                        'children: [{'rid': '<rid 1>', 'info': {'description': ...,
                                                'basic': ...
                                                'children': [{'rid': '<rid 2>', 'info': {'description': ...,
                                                                         'basic': ...,
                                                                         'children: [] } } },
                                    {'rid': '<rid 3>', 'info': {'description': ...,
                                                'basic': ...
                                                'children': {} } }] } }

           As it's building this nested dict, it calls nodeidfn with the rid and info
           (w/o children) for each node.
        '''
        try:
            # handle passing cik for auth
            if isinstance(auth, string_types):
                auth = {'cik': auth}
            types = ['dataport', 'datarule', 'dispatch', 'client']
            listing = {}
            norid = rid is None
            if norid:
                rid, resinfo = self._exomult(auth, [
                    ['lookup', 'aliased', ''],
                    ['info', {'alias': ''}, options]])
            else:
                if resinfo is None:
                    resinfo = self._exomult(auth, [['info', rid, options]])[0]

            myid = nodeidfn(rid, resinfo)

            if level is not None and level <= 0:
                return {'rid': myid, 'info': resinfo}

            if restype == 'client':
                if not norid:
                    # key is only available to owner (not the resource itself)
                    auth = {
                        'cik': auth['cik'],
                        'client_id': rid
                    }
                try:
                    listing = self._exomult(auth, [['listing', types, {}, {'alias': ''}]])[0]
                except ExoRPC.RPCException as e:
                    listing = dict([(t, []) for t in types])
                    errorfn(auth, str(e))
                rids = [rid for rid in list(itertools.chain.from_iterable([listing[t] for t in types]))]
                # break info calls into chunks to prevent timeout
                chunksize = 20
                def chunks(l, n):
                    '''Yield successive n-sized chunks from l.'''
                    for i in range(0, len(l), n):
                        yield l[i:i+n]
                infos = []
                for ridchunk in chunks(rids, chunksize):
                    infos += self._exomult(auth, [['info', rid, options] for rid in ridchunk])
            else:
                listing = []

            resinfo['children'] = []
            infoIndex = 0
            for typ in types:
                if typ in listing:
                    ridlist = listing[typ]
                    for childrid in ridlist:
                        tr = self._infotree(auth,
                                            rid=childrid,
                                            restype=typ,
                                            resinfo=infos[infoIndex],
                                            nodeidfn=nodeidfn,
                                            options=options,
                                            level=None if level is None else level-1,
                                            raiseExceptions=raiseExceptions,
                                            errorfn=errorfn)
                        infoIndex += 1
                        resinfo['children'].append(tr)
            resinfo['children'].sort(key=lambda x: x['rid'] if 'rid' in x else '')

            return {'rid': myid, 'info': resinfo}
        except Exception as ex:
            if raiseExceptions:
                six.reraise(Exception, ex)
            else:
                return {'exception': ex, 'auth': auth, 'rid': rid}

    def _difffilter(self, difflines):
        d = difflines

        # replace differing rid children lines with a single <<rid>>
        ridline = '^[+-](.*").*\.[a-f0-9]{40}(".*)\n'
        d = re.sub(ridline * 2, r' \1<<RID>>\2\n', d, flags=re.MULTILINE)

        # replace differing rid alias lines with a single <<rid>> placeholder
        a = '(.*")[a-f0-9]{40}("\: \[)\n'
        plusa = '^\+' + a
        minusa = '^\-' + a
        d = re.sub(plusa + minusa, r' \1<<RID>>\2\n', d, flags=re.MULTILINE)
        d = re.sub(minusa + plusa, r' \1<<RID>>\2\n', d, flags=re.MULTILINE)

        # replace differing cik lines with a single <<auth>> placeholder
        a = '(.*"key"\: ")[a-f0-9]{40}(",.*)\n'
        plusa = '^\+' + a
        minusa = '^\-' + a
        d = re.sub(plusa + minusa, r' \1<<auth>>\2\n', d, flags=re.MULTILINE)
        d = re.sub(minusa + plusa, r' \1<<auth>>\2\n', d, flags=re.MULTILINE)

        return d

    def diff(self, cik1, cik2, full=False, nochildren=False):
        '''Show differences between two ciks.'''

        cik2 = exoconfig.lookup_shortcut(cik2)

        # list of info "keypaths" to not include in comparison
        # only the last item in the list is removed. E.g. for a
        # keypath of ['counts', 'disk'], only the 'disk' key is
        # ignored.
        ignore = [['usage'],
                  ['counts', 'disk'],
                  ['counts', 'email'],
                  ['counts', 'http'],
                  ['counts', 'share'],
                  ['counts', 'sms'],
                  ['counts', 'xmpp'],
                  ['basic', 'status'],
                  ['basic', 'modified'],
                  ['basic', 'activity'],
                  ['data']]

        if nochildren:
            info1 = self.info(cik1)
            info1 = self._remove(info1, ignore)
            info2 = self.info(cik2)
            info2 = self._remove(info2, ignore)
        else:
            def name_prepend(rid, info):
                if not full:
                    self._remove(info, ignore)
                # prepend the name so that node names tend to sort (and so
                # compare well)
                return info['description']['name'] + '.' + rid
            info1 = self._infotree(cik1, nodeidfn=name_prepend, options={})
            info2 = self._infotree(cik2, nodeidfn=name_prepend, options={})

        if info1 == info2:
            return None
        else:
            differences = self._differences(info1, info2)
            differences = ''.join(differences)

            if not full:
                # pass through a filter that removes
                # differences that we don't care about
                # (e.g. different RIDs)
                differences = self._difffilter(differences)

                if all([line[0] == ' ' for line in differences.split('\n')]):
                    return None

            return differences

    def make_info_options(self, include=[], exclude=[]):
        '''Create options for the info command based on included
        and excluded keys.'''
        options = {}
        # TODO: this is a workaround. The RPC API returns empty list if any
        # keys are set to false. So, the workaround is to include all keys
        # except for the excluded ones. This has the undesirable
        # side-effect of producing "<key>": null in the results, so it would be
        # better for this to be done in the API.
        #
        #for key in exclude:
        #    options[key] = False

        if len(exclude) > 0:
            options.update(dict([(k, True) for k in ['aliases',
                                                        'basic',
                                                        'counts',
                                                        'description',
                                                        'key',
                                                        'shares',
                                                        'subscribers',
                                                        'tags',
                                                        'usage']
                                    if k not in exclude]))
        else:
            for key in include:
                options[key] = True

        return options

class ExoData():
    '''Implements the Data Interface API
    https://github.com/exosite/docs/tree/master/data'''

    def __init__(self, url='http://m2.exosite.com'):
        self.url = url

    def raise_for_status(self, r):
        try:
            r.raise_for_status()
        except Exception as ex:
            raise ExoException(str(ex))

    def read(self, cik, aliases):
        headers = {'X-Exosite-CIK': cik,
                   'Accept': 'application/x-www-form-urlencoded; charset=utf-8'}
        url = self.url + '/onep:v1/stack/alias?' + '&'.join(aliases)
        r = requests.get(url, headers=headers)
        self.raise_for_status(r)
        return r.text

    def write(self, cik, alias_values):
        headers = {'X-Exosite-CIK': cik,
                   'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8'}
        url = self.url + '/onep:v1/stack/alias'
        r = requests.post(url, headers=headers, data=alias_values)
        self.raise_for_status(r)
        return r.text

    def writeread(self, cik, alias_values, aliases):
        headers = {'X-Exosite-CIK': cik,
                   'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8',
                   'Accept': 'application/x-www-form-urlencoded; charset=utf-8'}
        url = self.url + '/onep:v1/stack/alias?' + '&'.join(aliases)
        r = requests.post(url, headers=headers, data=alias_values)
        self.raise_for_status(r)
        return r.text

    def ip(self):
        r = requests.get(self.url + '/ip')
        r.raise_for_status()
        return r.text


class ExoPortals():
    '''Provides access to the Portals APIs'''

    # list of procedures that may be included in invalidation data
    writeprocs = ['activate',
                  'create',
                  'deactivate',
                  'drop',
                  'map',
                  'revoke',
                  'share',
                  'unmap',
                  'update']

    def __init__(self, portalsserver='https://portals.exosite.com'):
        self.portalsserver = portalsserver

    def invalidate(self, data):
        # This API is documented here:
        # https://i.exosite.com/display/DEVPORTALS/Portals+Cache+Invalidation+API
        data = json.dumps(data)
        #print('invalidating with ' + data)
        try:
            response = requests.post(self.portalsserver + '/api/portals/v1/cache',
                                     data=data)
        except Exception as ex:
            raise ExoException('Failed to connect to ' + self.portalsserver)
        try:
            response.raise_for_status
        except Exception as ex:
            raise ExoException('Bad status from Portals cache invalidate API call: ' + ex)


class ExoUtilities():

    @classmethod
    def parse_ts(cls, s):
        return None if s is None else ExoUtilities.parse_ts_tuple(parser.parse(s).timetuple())

    @classmethod
    def parse_ts_tuple(cls, t):
        return int(time.mktime(t))

    @classmethod
    def get_cik(cls, auth, allow_only_cik=True):
        '''Get the 40 character CIK from auth dict, or raise
           an error that CIK type auth is required.'''
        if 'cik' not in auth:
            raise ExoException('This operation requires a CIK')
        if allow_only_cik and len(auth.keys()) > 1:
            raise ExoException('This operation does not support client_id and resource_id access')
        return auth['cik']

    @classmethod
    def get_startend(cls, args):
        '''Get start and end timestamps based on standard arguments'''
        start = args.get('--start', None)
        end = args.get('--end', None)
        def is_ts(s):
            return s is not None and re.match('^-?[0-9]+$', s) is not None
        if is_ts(start):
            start = int(start)
            if start < 0:
                start = ExoUtilities.parse_ts_tuple((datetime.now() + timedelta(seconds=start)).timetuple())
        else:
            start = ExoUtilities.parse_ts(start)
        if end == 'now':
            end = None
        elif is_ts(end):
            end = int(end)
            if end < 0:
                end = ExoUtilities.parse_ts_tuple((datetime.now() + timedelta(seconds=end)).timetuple())
        else:
            end = ExoUtilities.parse_ts(end)
        return start, end

    @classmethod
    def format_time(cls, sec):
        '''Formats a time interval for human consumption'''
        intervals = [[60 * 60 * 24, 'd'],
                     [60 * 60, 'h'],
                     [60, 'm']]
        text = ""
        for s, label in intervals:
            if sec >= s and sec // s > 0:
                text = "{0} {1}{2}".format(text, sec // s, label)
                sec -= s * (sec // s)
        if sec > 0:
            text += " {0}s".format(sec)
        return text.strip()

    @classmethod
    def handleSystemExit(cls, ex):
        # Handle SystemExit per
        # https://docs.python.org/2/library/exceptions.html#exceptions.SystemExit
        if ex.code is None:
            return 0
        elif isinstance(ex.code, six.string_types):
            sys.stderr.write(ex.code + '\n')
            return 1
        elif type(ex.code is int):
            return ex.code
        else:
            sys.stderr.write('Unexpected exitcode: {0}\n'.format(ex.code))
            return 1


def spark(numbers, empty_val=None):
    """Generate a text based sparkline graph from a list of numbers (ints or
    floats).

    When value is empty_val, show no bar.

    https://github.com/1stvamp/py-sparkblocks

    Based on:
      https://github.com/holman/spark
    and:
      http://www.datadrivenconsulting.com/2010/06/twitter-sparkline-generator/
    """

    out = []

    min_value = min(numbers)
    max_value = max(numbers)
    value_scale = max_value - min_value

    for number in numbers:
        if number == empty_val:
            out.append(" ")
        else:
            if (number - min_value) != 0 and value_scale != 0:
                scaled_value = (number - min_value) / value_scale
            else:
                scaled_value = 0
            num = math.floor(min([6, (scaled_value * 7)]))

            # Hack because 9604 and 9608 aren't vertically aligned the same as
            # other block elements
            if num == 3:
                if (scaled_value * 7) < 3.5:
                    num = 2
                else:
                    num = 4
            elif num == 7:
                num = 6

            if six.PY3:
                unichrfn = chr
            else:
                unichrfn = unichr
            out.append(unichrfn(int(9601 + num)))

    return ''.join(out)

def meanstdv(l):
    '''Calculate mean and standard deviation'''
    n, mean, std = len(l), 0, 0
    mean = sum(l) / float(len(l))
    std = math.sqrt(sum([(x - mean)**2 for x in l]) / (len(l) - 1))
    return mean, std


def show_intervals(er, auth, rid, start, end, limit, numstd=None):
    # show a distribution of intervals between data
    data = er.read(auth,
                   rid,
                   limit,
                   sort='desc',
                   starttime=start,
                   endtime=end)

    if len(data) == 0:
        return
    intervals = [data[i - 1][0] - data[i][0] for i in range(1, len(data))]
    intervals = sorted(intervals)

    if numstd is not None:
        # only include data within numstd standard deviations
        # of the mean
        mean, std = meanstdv(intervals)
        intervals = [x for x in intervals
                    if mean - numstd * std <= x
                    and x <= mean + numstd * std]
        if len(intervals) == 0:
            return
    num_bins = 60
    min_t, max_t = min(intervals), max(intervals)
    bin_size = float(max_t - min_t) / num_bins * 1.0
    bins = []
    for i in range(num_bins):
        bin_min = min_t + i * bin_size
        bin_max = min_t + (i + 1) * bin_size
        if i != 0:
            critfn = lambda x: bin_min < x and x <= bin_max
        else:
            critfn = lambda x: bin_min <= x and x <= bin_max
        #bins.append((bin_min, bin_max, float(
        #    sum(map(critfn, intervals)))))
        if six.PY3:
            mapfn = map
        else:
            mapfn = itertools.imap
        bins.append(float(sum(mapfn(critfn, intervals))))

    print(spark(bins, empty_val=0))

    min_label = ExoUtilities.format_time(min_t)
    max_label = ExoUtilities.format_time(max_t)
    sys.stdout.write(min_label)
    sys.stdout.write(' ' * (num_bins - len(min_label) - len(max_label)))
    sys.stdout.write(max_label + '\n')


# return a generator that reads rid forever and yields either:
# A. timestamp, value pair (on data)
# B. None, None (on timeout)
def followSeries(er, auth, rid, timeout_milliseconds, printFirst=True):
    # do an initial read
    results = er.readmult(
        auth,
        [rid],
        limit=1,
        selection='all',
        sort='desc')

    # --follow doesn't want the result to be an iterator
    results = list(results)
    last_t, last_v = 0, None
    if len(results) > 0 and printFirst:
        last_t, last_v = results[0]
        yield(last_t, last_v)

    while True:
        timedout, point = er.wait(
            auth,
            rid,
            since=last_t + 1,
            timeout=timeout_milliseconds)
        if not timedout:
            last_t, last_v = point
            yield(last_t, [last_v])

            # flush output for piping this output to other programs
            sys.stdout.flush()
        else:
            yield(None, None)


def read_cmd(er, auth, rids, args):
    '''Read command'''
    if len(rids) == 0:
        # if only a CIK was passed, include all dataports and datarules
        # by default.
        listing = er.listing(auth, ['dataport', 'datarule'], options={}, rid={'alias': ''})
        rids = listing['dataport'] + listing['datarule']
        aliases = er.info(auth, options={'aliases': True})['aliases']
        # look up aliases for column headers
        cmdline_rids = [aliases[rid][0] if rid in aliases else rid for rid in rids]

        # in this case default to showing headers
        headertype = 'rid'
    else:
        cmdline_rids = args['<rid>']
        headertype = args['--header']

    limit = args['--limit']
    limit = 1 if limit is None else int(limit)

    # time range
    start, end = ExoUtilities.get_startend(args)

    timeformat = args['--timeformat']
    if headertype == 'name':
        # look up names of rids
        infos = er._exomult(auth,
                            [['info', r, {'description': True}] for r in rids])
        headers = ['timestamp'] + [i['description']['name'] for i in infos]
    else:
        # use whatever headers were passed at the command line (RIDs or
        # aliases)
        headers = ['timestamp'] + [str(r) for r in cmdline_rids]

    fmt = args['--format']
    tz = args['--tz']

    options = {
        'format': fmt,
        'timeformat': timeformat,
        'tz': tz
    }

    lw = serieswriter.SeriesWriter(headers, options)
    if headertype is not None:
        # write headers
        lw.write_headers()

    timeout_milliseconds = 3000
    if args['--follow']:
        if len(rids) > 1:
            raise ExoException('--follow does not support reading from multiple rids')

        lines = followSeries(
            er,
            auth,
            rids[0],
            timeout_milliseconds=timeout_milliseconds,
            printFirst=True)
        # goes forever
        for ts, v in lines:
            if ts is not None and v is not None:
                lw.write(ts, v)
    else:
        chunksize = int(args['--chunksize'])
        result = er.readmult(auth,
                             rids,
                             sort=args['--sort'],
                             starttime=start,
                             endtime=end,
                             limit=limit,
                             selection=args['--selection'],
                             chunksize=chunksize)
        for t, v in result:
            lw.write(t, v)


def plain_print(arg):
    print(arg)


def pretty_print(arg):
    print(json.dumps(arg, sort_keys=True, indent=4, separators=(',', ': ')))


def handle_args(cmd, args):
    use_https = False if args['--http'] is True else True

    # command-specific http timeout defaults
    if args['--httptimeout'] == '60':
        if args['<command>'] == 'copy':
            args['--httptimeout'] == '480'

    port = args['--port']
    if port is None:
        port = DEFAULT_PORT_HTTPS if use_https else DEFAULT_PORT

    er = ExoRPC(
        host=args['--host'],
        port=port,
        https=use_https,
        httptimeout=args['--httptimeout'],
        logrequests=args['--clearcache'],
        user_agent=args['--useragent'],
        curldebug=args['--curl'])

    pop = provision.Provision(
        host=args['--host'],
        manage_by_cik=False,
        port=port,
        verbose=True,
        httptimeout=args['--httptimeout'],
        https=use_https,
        raise_api_exceptions=True,
        curldebug=args['--curl'])

    if cmd in ['ip', 'data']:
        if args['--https'] is True or args['--port'] is not None or args['--debughttp'] is True or args['--curl'] is True:
            # TODO: support these
            raise ExoException('--https, --port, --debughttp, and --curl are not supported for ip and data commands.')
        ed = ExoData(url='http://' + args['--host'])

    if cmd in ['portals'] or args['--clearcache']:
        portals = ExoPortals(args['--portals'])

    if '<auth>' in args and args['<auth>'] is not None:
        auth = args['<auth>']
        if type(auth) is list:
            auth = [exoconfig.lookup_shortcut(a) for a in auth]
        else:
            auth = exoconfig.lookup_shortcut(auth)
    else:
        # for data ip command
        auth = None
    def rid_or_alias(rid, auth=None):
        '''Translate what was passed for <rid> to an alias object if
           it doesn't look like a RID.'''
        if er.regex_rid.match(rid) is None:
            if er.regex_tweeid.match(rid) is None:
                return {'alias': rid}
            else:
                # look up full RID based on short version
                tweetype, ridfrag = rid.split('.')
                listing = er.listing(auth, ['client', 'dataport', 'datarule', 'dispatch'], options={}, rid={'alias': ''})
                candidates = []
                for typ in listing:
                    for fullrid in listing[typ]:
                        if fullrid.startswith(ridfrag):
                            candidates.append(fullrid)
                if len(candidates) == 1:
                    return candidates[0]
                elif len(candidates) > 1:
                    raise ExoException('More than one RID starts with ' + ridfrag + '. Better use the full RID.')
                else:
                    raise ExoException('No RID found that starts with ' + ridfrag + '. Is it an immediate child of ' + auth + '?')
        else:
            return rid

    rids = []
    if '<rid>' in args:
        if type(args['<rid>']) is list:
            for rid in args['<rid>']:
                rids.append(rid_or_alias(rid, auth))
        else:
            if args['<rid>'] is None:
                rids.append({"alias": ""})
            else:
                rids.append(rid_or_alias(args['<rid>'], auth))

    if args.get('--pretty', False):
        pr = pretty_print
    else:
        pr = plain_print

    try:
        if cmd == 'read':
            read_cmd(er, auth, rids, args)
        elif cmd == 'write':
            if args['-']:
                val = sys.stdin.read()
                # remove extra newline
                if val[-1] == '\n':
                    val = val[:-1]
                er.write(auth, rids[0], val)
            else:
                er.write(auth, rids[0], args['--value'])
        elif cmd == 'record':
            interval = args['--interval']
            if interval is None:
                # split timestamp, value
                if not args['--value']:
                    headers = ['timestamp'] + [x for x in range(0,len(rids))]
                    if sys.version_info < (3, 0):
                        dr = csv.DictReader(sys.stdin, headers, encoding='utf-8')
                    else:
                        dr = csv.DictReader(sys.stdin, headers)
                    rows = list(dr)
                    chunkcnt=0
                    entries=[[] for x in range(0,len(rids))]
                    for row in rows:
                        s = row['timestamp']
                        if s is not None and re.match('^[-+]?[0-9]+$', s) is not None:
                            ts = int(s)
                        else:
                            ts = ExoUtilities.parse_ts(s)
                        for column in range(0,len(rids)):
                            value = row[column]
                            # TODO: How to deal with an empty cell should be a cmdline option.
                            # skip it, or record a default number or empty string?
                            if value is not None:
                                entries[column].append([ts, value])
                        chunkcnt += 1
                        if chunkcnt > int(args['--chunksize']):
                            for idx in range(0,len(rids)):
                                er.record(auth, rids[idx], entries[idx])
                            chunkcnt = 0
                            entries=[[] for x in range(0,len(rids))]

                    for idx in range(0,len(rids)):
                        if len(entries[idx]) > 0:
                            er.record(auth, rids[idx], entries[idx])

                else:
                    entries = []
                    has_errors = False
                    tvalues = args['--value']
                    reentry = re.compile('(-?\d+),(.*)')
                    for tv in tvalues:
                        match = reentry.match(tv)
                        if match is None:
                            try:
                                t, v = tv.split(',')
                                if t is not None and re.match('^[-+]?[0-9]+$', t) is not None:
                                    ts = int(t)
                                else:
                                    ts = ExoUtilities.parse_ts(t)
                                entries.append([ts, v])
                            except Exception:
                                sys.stderr.write(
                                    'Line not in <timestamp>,<value> format: {0}'.format(tv))
                                has_errors = True
                        else:
                            g = match.groups()
                            s = g[0]
                            if s is not None and re.match('^[-+]?[0-9]+$', s) is not None:
                                ts = int(s)
                            else:
                                ts = ExoUtilities.parse_ts(s)
                            entries.append([ts, g[1]])

                    if has_errors or len(entries) == 0:
                        raise ExoException("Problems with input.")
                    else:
                        er.record(auth, rids[0], entries)
            else:
                if args['-']:
                    values = [v.strip() for v in sys.stdin.readlines()]
                else:
                    values = args['--value']
                interval = int(interval)
                if interval <= 0:
                    raise ExoException("--interval must be positive")
                er.record_backdate(auth, rids[0], interval, values)
        elif cmd == 'create':
            typ = args['--type']
            ridonly = args['--ridonly']
            cikonly = args['--cikonly']
            if ridonly and cikonly:
                raise ExoException('--ridonly and --cikonly are mutually exclusive')
            if args['-']:
                s = sys.stdin.read()
                try:
                    desc = json.loads(s)
                except Exception as ex:
                    raise ExoException(ex)
                rid = er.create(auth,
                                type=typ,
                                desc=desc,
                                name=args['--name'])
            elif typ == 'client':
                rid = er.create_client(auth,
                                    name=args['--name'])
            elif typ == 'dataport':
                rid = er.create_dataport(auth,
                                        args['--format'],
                                        name=args['--name'])
            else:
                raise ExoException('No defaults for {0}.'.format(args['--type']))
            if ridonly:
                pr(rid)
            elif cikonly:
                print(er.info(auth, rid, cikonly=True))
            else:
                pr('rid: {0}'.format(rid))
                if typ == 'client':
                    # for convenience, look up the cik
                    print('cik: {0}'.format(er.info(auth, rid, cikonly=True)))
            if args['--alias'] is not None:
                er.map(auth, rid, args['--alias'])
                if not ridonly:
                    print("alias: {0}".format(args['--alias']))
        elif cmd == 'update':
            s = sys.stdin.read()
            try:
                desc = json.loads(s)
            except Exception as ex:
                raise ExoException(ex)
            pr(er.update(auth, rids[0], desc=desc))
        elif cmd == 'map':
            er.map(auth, rids[0], args['<alias>'])
        elif cmd == 'unmap':
            er.unmap(auth, args['<alias>'])
        elif cmd == 'lookup':
            # look up by cik or alias
            cik_to_find = args['--cik']
            owner_of = args['--owner-of']
            share = args['--share']
            if cik_to_find is not None:
                cik_to_find = exoconfig.lookup_shortcut(cik_to_find)
                rid = er.lookup_rid(auth, cik_to_find)
                if rid is not None:
                    pr(rid)
            elif owner_of is not None:
                rid = er.lookup_owner(auth, owner_of)
                if rid is not None:
                    pr(rid)
            elif share is not None:
                rid = er.lookup_shared(auth, share)
                if rid is not None:
                    pr(rid)
            else:
                alias = args['<alias>']
                if alias is None:
                    alias = ""
                pr(er.lookup(auth, alias))
        elif cmd == 'drop':
            if args['--all-children']:
                er.drop_all_children(auth)
            else:
                if len(rids) == 0:
                    raise ExoException("<rid> is required")
                er.drop(auth, rids)
        elif cmd == 'listing':
            types = args['--types'].split(',')

            options = {}
            tags = args['--tagged']
            if tags is not None:
                options['tagged'] = tags.split(',')
            filters = args['--filters']
            if filters is not None:
                for f in filters.split(','):
                    options[f] = True
                listing = er.listing(auth, types, options=options, rid=rids[0])
            if args['--plain']:
                for t in types:
                    for rid in listing[t]:
                        print(rid)
            else:
                pr(json.dumps(listing))
        elif cmd == 'whee':
            tree = er._infotree_fast(auth, options={'basic': True})
            pr(json.dumps(tree))
        elif cmd == 'info':
            include = args['--include']
            include = [] if include is None else [key.strip()
                for key in include.split(',')]
            exclude = args['--exclude']
            exclude = [] if exclude is None else [key.strip()
                for key in exclude.split(',')]

            options = er.make_info_options(include, exclude)
            level = args['--level']
            level = None if level is None or args['--recursive'] is False else int(level)
            info = er.info(auth,
                        rids[0],
                        options=options,
                        cikonly=args['--cikonly'],
                        recursive=args['--recursive'],
                        level=level)
            if args['--pretty']:
                pr(info)
            else:
                if args['--cikonly']:
                    pr(info)
                else:
                    # output json
                    pr(json.dumps(info))
        elif cmd == 'flush':
            start, end = ExoUtilities.get_startend(args)
            er.flush(auth, rids, newerthan=start, olderthan=end)
        elif cmd == 'usage':
            allmetrics = ['client',
                        'dataport',
                        'datarule',
                        'dispatch',
                        'email',
                        'http',
                        'sms',
                        'xmpp']

            start, end = ExoUtilities.get_startend(args)
            er.usage(auth, rids[0], allmetrics, start, end)
        # special commands
        elif cmd == 'tree':
            er.tree(auth, cli_args=args)
        elif cmd == 'find':
            shows = args['--show'] if args['--show'] else "cik"
            er.find(auth, args['--match'], shows)
        elif cmd == 'twee':
            args['--values'] = True
            if platform.system() == 'Windows':
                args['--nocolor'] = True
            er.tree(auth, cli_args=args)
        elif cmd == 'script':
            # auth is a list of auths
            if args['--file']:
                filename = args['--file']
            else:
                filename = args['<script-file>']
            rid = None if args['<rid>'] is None else rids[0]
            svers = None if not '--setversion' in args else args['--setversion']
            er.upload_script(auth,
                filename,
                name=args['--name'],
                recursive=args['--recursive'],
                create=args['--create'],
                rid=rid,
                follow=args['--follow'],
                version=svers)

        elif cmd == 'spark':
            days = int(args['--days'])
            end = ExoUtilities.parse_ts_tuple(datetime.now().timetuple())
            start = ExoUtilities.parse_ts_tuple((datetime.now() - timedelta(days=days)).timetuple())
            numstd = args['--stddev']
            numstd = int(numstd) if numstd is not None else None
            show_intervals(er, auth, rids[0], start, end, limit=1000000, numstd=numstd)
        elif cmd == 'copy':
            destcik = args['<destination-cik>']
            newrid, newcik = er.copy(auth, destcik)
            if args['--cikonly']:
                pr(newcik)
            else:
                pr('cik: ' + newcik)
        elif cmd == 'diff':
            if sys.version_info < (2, 7):
                raise ExoException('diff command requires Python 2.7 or above')

            diffs = er.diff(auth,
                            args['<cik2>'],
                            full=args['--full'],
                            nochildren=args['--no-children'])
            if diffs is not None:
                print(diffs)
        elif cmd == 'ip':
            pr(ed.ip())
        elif cmd == 'data':
            reads = args['--read']
            writes = args['--write']
            cik = ExoUtilities.get_cik(auth)
            def get_alias_values(writes):
                # TODO: support values with commas
                alias_values = []
                re_assign = re.compile('(.*),(.*)')
                for w in writes:
                    if w.count(',') > 1:
                        raise ExoException('Values with commas are not supported.')
                    m = re_assign.match(w)
                    if m is None or len(m.groups()) != 2:
                        raise ExoException("Bad alias assignment format")
                    alias_values.append(m.groups())
                return alias_values

            if len(reads) > 0 and len(writes) > 0:
                alias_values = get_alias_values(writes)
                print(ed.writeread(cik, alias_values, reads))
            elif len(reads) > 0:
                print(ed.read(cik, reads))
            elif len(writes) > 0:
                alias_values = get_alias_values(writes)
                ed.write(cik, alias_values)
        elif cmd == 'portals':

            procedures = args['<procedure>']
            if len(procedures) == 0:
                procedures = ExoPortals.writeprocs
            else:
                unknownprocs = []
                for p in procedures:
                    if p not in ExoPortals.writeprocs:
                        unknownprocs.append(p)
                if len(unknownprocs) > 0:
                    raise ExoException(
                        'Unknown procedure(s) {0}'.format(','.join(unknownprocs)))
            if not isinstance(auth, six.string_types):
                raise ExoException("provision command requires cik for auth")
            data = {'auth': {'cik': auth},
                    'calls':[{'procedure': p, 'arguments': [], 'id': i}
                             for i, p in enumerate(procedures)]}
            portals.invalidate(data)
        elif cmd == 'share':
            options = {}
            share = args['--share']
            if share is not None:
                options['share'] = share
            meta = args['--meta']
            if meta is not None:
                options['meta'] = meta
            pr(er.share(auth,
                        rids[0],
                        options))
        elif cmd == 'revoke':
            if args['--share'] is not None:
                typ = 'share'
                code = args['--share']
            else:
                typ = 'client'
                code = args['--client']
            pr(er.revoke(auth, typ, code))
        elif cmd == 'activate':
            if args['--share'] is not None:
                typ = 'share'
                code = args['--share']
            else:
                typ = 'client'
                code = args['--client']
            er.activate(auth, typ, code)
        elif cmd == 'deactivate':
            if args['--share'] is not None:
                typ = 'share'
                code = args['--share']
            else:
                typ = 'client'
                code = args['--client']
            er.deactivate(auth, typ, code)
        elif cmd == 'clone':
            options = {}
            if args['--share'] is not None:
                options['code'] = args['--share']
            if args['--rid'] is not None:
                rid_to_clone = args['--rid']
                if er.regex_rid.match(rid_to_clone) is None:
                    # try to look up RID for an alias
                    alias = rid_to_clone
                    rid_to_clone = er.lookup(auth, alias)
                options['rid'] = rid_to_clone

            options['noaliases'] = args['--noaliases']
            options['nohistorical'] = args['--nohistorical']

            rid = er.clone(auth, options)
            pr('rid: {0}'.format(rid))
            info = er.info(auth, rid, {'basic': True, 'key': True})
            typ = info['basic']['type']
            copycik = info['key']
            if typ == 'client':
                if not args['--noactivate']:
                    er.activate(auth, 'client', copycik)
                # for convenience, look up the cik
                pr('cik: {0}'.format(copycik))
        else:
            # search plugins
            handled = False
            exitcode = 1
            for plugin in plugins:
                if cmd in plugin.command():
                    options = {
                            'auth': auth,
                            'rids': rids,
                            'rpc': er,
                            'provision': pop,
                            'exception': ExoException,
                            'provision-exception': pyonep.exceptions.ProvisionException,
                            'utils': ExoUtilities,
                            'config': exoconfig
                            }
                    try:
                        options['data'] = ed
                    except NameError:
                        # no problem
                        pass

                    if cmd == "switches":
                        options['doc'] = cmd_doc
                    exitcode = plugin.run(cmd, args, options)
                    handled = True
                    break
            if not handled:
                raise ExoException("Command not handled")
            return exitcode
    finally:
        if args['--clearcache']:
            for req in er.exo.loggedrequests():
                procs = [c['procedure'] for c in req['calls']]
                # if operation will invalidate the Portals cache...
                if len([p for p in procs if p in ExoPortals.writeprocs]) > 0:
                    portals.invalidate(req)


class DiscreetFilter(object):
    '''Filter stdin/stdout to hide anything that looks like
       an RID'''
    def __init__(self, out):
        self.out = out
        # match the two halves of an RID/CIK
        self.ridre = re.compile('([a-fA-F0-9]{20})([a-fA-F0-9]{20})')

    def write(self, message):
        # hide the second half
        if sys.version_info < (3, 0):
            message = message.decode('utf-8')
        s = self.ridre.sub('\g<1>01234567890123456789', message)
        if sys.version_info < (3, 0):
            s = s.encode('utf-8')
        self.out.write(s)

    def flush(self):
        self.out.flush()

def cmd(argv=None, stdin=None, stdout=None, stderr=None):
    '''Wrap the command line interface. Globally redirects args
    and io so that the application can be tested externally.'''

    # globally redirect args and io
    if argv is not None:
        sys.argv = argv
    if stdin is not None:
        sys.stdin = stdin
    if stderr is not None:
        sys.stderr = stderr
    if stdout is not None:
        sys.stdout = stdout

    # add the first line of the detailed documentation to
    # the exo --help output. Some lines span newlines.
    max_cmd_length = max(len(cmd) for cmd in cmd_doc)
    command_list = ''
    for cmd in cmd_doc:
        lines = cmd_doc[cmd].split('\n\n')[0].split('\n')
        command_list += '  ' + cmd + ' ' * (max_cmd_length - len(cmd)) + '  ' + lines[0] + '\n'
        for line in lines[1:]:
            command_list += ' ' * max_cmd_length + line + '\n'
    doc = __doc__.replace('{{ command_list }}', command_list)

    try:
        args = docopt(
            doc,
            version="Exosite Command Line {0}".format(__version__),
            options_first=True)
    except SystemExit as ex:
        return ExoUtilities.handleSystemExit(ex)


    global exoconfig
    if args['--config'] is None:
        args['--config'] = os.environ.get('EXO_CONFIG', '~/.exoline')
    exoconfig = ExoConfig(args['--config'])

    # get command args
    cmd = args['<command>']
    argv = [cmd] + args['<args>']
    if cmd in cmd_doc:
        # if doc expects yet another command, pass options_first=True
        options_first = True if re.search(
            '^Commands:$',
            cmd_doc[cmd],
            flags=re.MULTILINE) else False
        try:
            args_cmd = docopt(cmd_doc[cmd], argv=argv, options_first=options_first)
        except SystemExit as ex:
            return ExoUtilities.handleSystemExit(ex)
    else:
        alphabet = 'abcdefghijklmnopqrstuvwxyz'
        def edits(word):
            # courtesy of:
            # http://norvig.com/spell-correct.html
            splits     = [(word[:i], word[i:]) for i in range(len(word) + 1)]
            deletes    = [a + b[1:] for a, b in splits if b]
            transposes = [a + b[1] + b[0] + b[2:] for a, b in splits if len(b)>1]
            replaces   = [a + c + b[1:] for a, b in splits for c in alphabet if b]
            inserts    = [a + c + b     for a, b in splits for c in alphabet]
            return set(deletes + transposes + replaces + inserts)

        e = edits(cmd)
        # make a list of valid commands the user could have meant
        alts = [w for w in cmd_doc.keys() if w in e]
        alt_msg = ''
        if 0 < len(alts) and len(alts) < 4:
            alt_msg = 'Did you mean {0}? '.format(' or '.join(alts))
        print('Unknown command {0}. {1}Try "exo --help"'.format(cmd, alt_msg))
        return 1
    # merge command-specific arguments into general arguments
    args.update(args_cmd)
    # turn on stdout/stderr filtering
    if args['--discreet']:
        sys.stdout = DiscreetFilter(sys.stdout)
        sys.stderr = DiscreetFilter(sys.stderr)

    # configure logging
    logging.basicConfig(stream=sys.stderr)
    logging.getLogger("pyonep.onep").setLevel(logging.ERROR)
    if args['--debughttp'] or args['--curl']:
        logging.getLogger("pyonep.onep").setLevel(logging.DEBUG)
        logging.getLogger("pyonep.provision").setLevel(logging.DEBUG)

    # substitute environment variables
    if args['--host'] is None:
        args['--host'] = os.environ.get('EXO_HOST', DEFAULT_HOST)
    if args['--port'] is None:
        args['--port'] = os.environ.get('EXO_PORT', None)

    exoconfig.mingleArguments(args)
    try:
        exitcode = handle_args(cmd, args)
        if exitcode is None:
            return 0
        else:
            return exitcode
    except ExoException as ex:
        # command line tool threw an exception on purpose
        sys.stderr.write("Command line error: {0}\r\n".format(ex))
        return 1
    except ExoRPC.RPCException as ex:
        # pyonep library call signaled an error in return values
        sys.stderr.write("One Platform error: {0}\r\n".format(ex))
        return 1
    except pyonep.exceptions.ProvisionException as ex:
        # if the body of the provision response is something other
        # than a repeat of the status and reason, show it
        showBody = str(ex).strip() != "HTTP/1.1 {0} {1}".format(
            ex.response.status(),
            ex.response.reason())
        sys.stderr.write(
            "One Platform provisioning exception: {0}{1}\r\n".format(
                ex,
                ' (' + str(ex.response.body).strip() + ')' if showBody else ''))
        return 1
    except pyonep.exceptions.OnePlatformException as ex:
        # pyonep library call threw an exception on purpose
        sys.stderr.write("One Platform exception: {0}\r\n".format(ex))
        return 1
    except pyonep.exceptions.JsonRPCRequestException as ex:
        sys.stderr.write("JSON RPC Request Exception: {0}\r\n".format(ex))
        return 1
    except pyonep.exceptions.JsonRPCResponseException as ex:
        sys.stderr.write("JSON RPC Response Exception: {0}\r\n".format(ex))
        return 1
    except KeyboardInterrupt:
        if args['--debug']:
            raise

    return 0


class CmdResult():
    def __init__(self, exitcode, stdout, stderr):
        self.exitcode = exitcode
        self.stdout = stdout
        self.stderr = stderr


def run(argv, stdin=None):
    '''Runs an exoline command, translating stdin from
    string and stdout to string. Returns a CmdResult.'''
    old = {'stdin': sys.stdin, 'stdout': sys.stdout, 'stderr': sys.stderr}
    try:
        if stdin is None:
            stdin = sys.stdin
        elif isinstance(stdin, six.string_types):
            sio = StringIO()
            if six.PY3:
                sio.write(stdin)
            else:
                sio.write(stdin.encode('utf-8'))
            sio.seek(0)
            stdin = sio
        stdout = StringIO()
        stderr = StringIO()
        exitcode = cmd(argv=argv, stdin=stdin, stdout=stdout, stderr=stderr)
        stdout.seek(0)
        stdout = stdout.read().strip()  # strip to get rid of leading newline
        stderr.seek(0)
        stderr = stderr.read().strip()
    finally:
        # restore stdout, stderr, stdin
        sys.stdin = old['stdin']
        sys.stdout = old['stdout']
        sys.stderr = old['stderr']
    return CmdResult(exitcode, stdout, stderr)


if __name__ == '__main__':
    sys.exit(cmd(sys.argv))

#  vim: set ai et sw=4 ts=4 :
