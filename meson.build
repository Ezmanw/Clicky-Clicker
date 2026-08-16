project(
  'clicky-clicker',
  version: '1.0.0',
  license: 'GPL-3.0-or-later',
  meson_version: '>= 0.62.0',
  default_options: ['warning_level=2'],
)

# The application is pure Python, so these are checked rather than linked
# against. Declaring them here means a missing or too-old libadwaita is a
# configure-time error with a clear message, instead of an ImportError or a
# missing-widget crash at run time.
#
# libadwaita 1.5 is the floor because the interface uses AdwDialog,
# AdwAlertDialog, AdwPreferencesDialog and AdwAboutDialog, all introduced there.
dependency('gtk4', version: '>= 4.12')
dependency('libadwaita-1', version: '>= 1.5')

python = import('python')
gnome = import('gnome')

python_installation = python.find_installation('python3', modules: ['gi'])

prefix = get_option('prefix')
bindir = prefix / get_option('bindir')
datadir = prefix / get_option('datadir')
pkgdatadir = datadir / meson.project_name()
moduledir = python_installation.get_install_dir()

application_id = 'io.github.ezmanw.ClickyClicker'

subdir('src')
subdir('data')

# No icon cache update: the application ships no artwork of its own and uses
# stock icon names from the system theme throughout.
gnome.post_install(
  glib_compile_schemas: true,
  update_desktop_database: true,
)

summary(
  {
    'prefix': prefix,
    'bindir': bindir,
    'python modules': moduledir,
    'systemd user units': get_option('systemduserunitdir') == '' ? 'auto' : get_option('systemduserunitdir'),
    'udev rules': get_option('install-udev-rules') ? 'installed' : 'skipped',
  },
  section: 'Directories',
)
