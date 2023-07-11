# vim:set sw=4 ts=8 fileencoding=utf-8:
# SPDX-License-Identifier: MIT
# Copyright © 2023, Serguei E. Leontiev (leo@sai.msu.ru)
#
'''
=======================
cppimport IPython magic
=======================

{CPPIMPORT_DOC}
'''

import contextlib
import hashlib
import logging
import os
import random
import shutil
import sys

from IPython.core import display
from IPython.core.magic import Magics, cell_magic, line_magic, magics_class
from IPython.core.magic_arguments import argument, magic_arguments, parse_argstring
from IPython.paths import get_ipython_cache_dir

import cppimport

_logger = logging.getLogger(__name__)
_ci_log_INFO = logging.INFO + 3
logging.addLevelName(_ci_log_INFO, 'INF')


def _ci_log_info(msg, *args, **kwargs):
    _logger.log(_ci_log_INFO, msg, *args, **kwargs)


class _LowFilter(logging.Filter):
    def __init__(self, level):
        self.level = level

    def filter(self, record):
        return record.levelno < self.level


def _logging_config():
    redh = logging.StreamHandler(sys.stderr)
    redh.setLevel(logging.WARNING)
    ordh = logging.StreamHandler(sys.stdout)
    ordh.addFilter(_LowFilter(logging.WARNING))
    logging.basicConfig(level=logging.WARNING, handlers=[redh, ordh])


class _SetupToolsLevelFilter(logging.Filter):
    def __init__(self, level):
        self.level = level

    def filter(self, record):
        return record.levelno >= self.level


@contextlib.contextmanager
def _set_level(verbosity=None, level=None):
    if level is None:
        level = (logging.WARNING if verbosity == 0 else
                 _ci_log_INFO if verbosity == 1 else
                 logging.INFO if verbosity == 2 else
                 logging.DEBUG)
    f = _SetupToolsLevelFilter(level)  # setuptools changed root level???
    root_log = logging.getLogger()
    old_level = root_log.level
    root_log.setLevel(level)
    root_log.addFilter(f)
    try:
        yield
    finally:
        root_log.removeFilter(f)
        root_log.setLevel(old_level)


@contextlib.contextmanager
def _cflags_append(cflags):
    # TODO: add optional argument cfg to cppimport.imp_from_filepath()
    # TODO: remove this contextmanager
    old_cflags = os.environ.get('CFLAGS')
    os.environ['CFLAGS'] = (cflags if old_cflags is None
                            else (old_cflags + ' ' + cflags))
    try:
        yield
    finally:
        if old_cflags is None:
            del os.environ['CFLAGS']
        else:
            os.environ['CFLAGS'] = old_cflags


@magics_class
class CppImportMagics(Magics):
    def _cache_init(self):
        '''Create random cache directory.'''

        while True:
            cdir = os.path.join(get_ipython_cache_dir(),
                                'cppimport',
                                '%08x' % random.getrandbits(32))
            try:
                os.makedirs(cdir)
                break
            except (OSError, FileExistsError):
                pass
        self.shell.db['cppimport_cache'] = cdir
        self._lib_dir = cdir

    def _cache_open(self):
        '''Open cache directory on session start'''

        try:
            cdir = self.shell.db['cppimport_cache']
            if os.path.isdir(cdir):
                self._lib_dir = cdir
                return
        except (KeyError, OSError):
            pass
        self._cache_init()

    def _cache_check(self):
        '''Check cache directory.

        If the parallel session executed `_cache_init()`, then the
        current session still continues to use the old directory (the
        one that was considered at the start).
        '''

        if not os.path.isdir(self._lib_dir):
            try:
                os.makedirs(self._lib_dir)
            except (OSError, FileExistsError):
                self._cache_init()
                return

    def _cache_clean(self):
        shutil.rmtree(os.path.join(get_ipython_cache_dir(),
                                   'cppimport'),
                      ignore_errors=True)
        self._cache_init()

    def _parse_argstring_with_config(self, func, line):
        args = parse_argstring(func, line)
        ci_config = self.shell.db.get('cppimport')
        if ci_config is not None:
            sverbosity = args.verbosity
            args = parse_argstring(func,
                                   ci_config + ' ' + line)
            if sverbosity:
                args.verbosity = sverbosity
        return args

    def __init__(self, shell):
        super(CppImportMagics, self).__init__(shell=shell)
        self._cache_open()

    @magic_arguments()
    @argument('-v', '--verbosity', action='count', default=0,
              help='Increase output verbosity.')
    @argument('--clean-cache', action='store_true',
              help='Clean ``cppimport.magic`` build cache.')
    @argument('--defaults', action='store_true',
              help='Delete custom configuration and back to default.')
    @argument('--help', action='store_true',
              help='Print docstring as output cell.')
    @line_magic
    def cppimport_config(self, line):
        args = self._parse_argstring_with_config(self.cppimport_config, line)
        with _set_level(verbosity=args.verbosity):
            if args.help:
                print(self.cppimport_config.__doc__)
            elif args.clean_cache:
                _ci_log_info('Clean cache: %s', self._lib_dir)
                self._cache_clean()
                _logger.debug('New cache: %s', self._lib_dir)
            elif args.defaults:
                try:
                    del self.shell.db['cppimport']
                    _ci_log_info('Deleted custom config. '
                                 'Back to default arguments '
                                 'for %%cppimport')
                except KeyError:
                    _logger.warning('No custom config found '
                                    'for %%cppimport')
            elif not line:
                try:
                    line = self.shell.db['cppimport']
                    _ci_log_info('Current defaults arguments '
                                 'for %%%%cppimport: %s', line)
                except KeyError:
                    _logger.warning('No custom config found '
                                    'for %%cppimport')
            else:
                self.shell.db['cppimport'] = line
                _ci_log_info('New default arguments '
                             'for %%%%cppimport: %s', line)

    @magic_arguments()
    @argument('-v', '--verbosity', action='count', default=0,
              help='Increase output verbosity.')
    @argument('cpp_module', type=str,
              help='Module C/C++ source file name.')
    @argument('--help', action='store_true',
              help='Print docstring as output cell.')
    @cell_magic
    def cppimport(self, line, cell):
        '''Build and import C/C++ module from ``%%ccpimport`` cell

        The content of the cell is written to a file in the
        directory ``$IPYTHONDIR/cppimport/<random>/<hash>/`` using
        a dirname with the hash of the code, flags and configuration
        data. This file is then compiled. The resulting module is
        imported.

        Usage
        =====
        Prepend ``%%cppimport`` to your C++/C code in a cell:

        %%cppimport module.cpp
        // put your code here.
        '''

        args = self._parse_argstring_with_config(self.cppimport, line)
        with _set_level(verbosity=args.verbosity):
            if args.help:
                print(self.cppimport.__doc__)
                return
            code = '\n' + (cell if cell.endswith('\n') else cell + '\n')
            orig_fullname = os.path.splitext(args.cpp_module)[0]
            key = (args.cpp_module, orig_fullname,
                   code, line, self.shell.db.get('cppimport'),
                   self._lib_dir,
                   cppimport.__version__, sys.version_info, sys.executable)
            # TODO: checksum calculate by cppimport.checksum._calc_cur_checksum()
            checksum = hashlib.md5(str(key).encode('utf-8')).hexdigest()

            fullname = '_' + checksum + '_' + orig_fullname

            if (fullname in sys.modules and
               cppimport.settings['force_rebuild']):
                # Symbol ``PyInit_{fullname}`` already defined.
                # For rebuild and load we need another, different.
                while fullname in sys.modules:
                    fullname = ('_'
                                + hashlib.md5(
                                    fullname.encode('utf-8')).hexdigest()
                                + '_'
                                + orig_fullname)
            if fullname in sys.modules:
                module = sys.modules[fullname]
                _logger.warning('The extension %s is already loaded. '
                                'To reload it, use: '
                                '%%cppimport_config --clean-cache',
                                fullname)
            else:
                dir = os.path.join(self._lib_dir, checksum)
                with contextlib.suppress(FileExistsError):
                    os.mkdir(dir)
                filepath = os.path.join(dir, args.cpp_module)
                with open(filepath, 'w') as f:
                    f.write(code)

                # TODO: add optional argument cfg to cppimport.imp_from_filepath()
                # with _cflags_append('-DPyInit_' + orig_fullname +
                #                     '=PyInit_' + fullname):
                #     module = cppimport.imp_from_filepath(filepath, fullname)
                cfgbase = {'extra_compile_args':
                           ['-DPyInit_' + orig_fullname +
                            '=PyInit_' + fullname]}
                module = cppimport.imp_from_filepath(filepath, fullname,
                                                     cfgbase=cfgbase)
                module.__source__ = code

            self.shell.push({orig_fullname: module})
            _ci_log_info('%s', 'C/C++ objects: ' +
                         ' '.join(orig_fullname + '.' + k
                                  for k in module.__dict__.keys()
                                  if not k.startswith('_')))


__doc__ = __doc__.format(CPPIMPORT_DOC=' ' * 8 + CppImportMagics.cppimport.__doc__)


def load_ipython_extension(ip):
    '''Load the extension in IPython.'''
    _logging_config()

    ip.register_magics(CppImportMagics)

    # enable C++ highlight
    patch = '''
        if(typeof IPython === 'undefined') {
            console.log('cppimport/magic.py: TDOO: JupyterLab ' +
                        'syntax highlight - unimplemented.');
        } else {
            IPython.CodeCell.options_default
            .highlight_modes['magic_cpp'] = {'reg':[/^%%cppimport/]};
        }
        '''
    js = display.Javascript(data=patch)
    display.display_javascript(js)
