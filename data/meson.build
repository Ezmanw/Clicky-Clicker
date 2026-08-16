app_conf = configuration_data()
app_conf.set('APP_ID', application_id)
app_conf.set('BINDIR', bindir)
app_conf.set('VERSION', meson.project_version())

# --- Desktop entry --------------------------------------------------------

desktop_file = configure_file(
  input: application_id + '.desktop.in',
  output: application_id + '.desktop',
  configuration: app_conf,
  install: true,
  install_dir: datadir / 'applications',
)

desktop_file_validate = find_program('desktop-file-validate', required: false)
if desktop_file_validate.found()
  test(
    'validate-desktop-file',
    desktop_file_validate,
    args: [desktop_file],
    suite: 'data',
  )
endif

# --- AppStream metadata ---------------------------------------------------

metainfo_file = configure_file(
  input: application_id + '.metainfo.xml.in',
  output: application_id + '.metainfo.xml',
  configuration: app_conf,
  install: true,
  install_dir: datadir / 'metainfo',
)

appstream_util = find_program('appstream-util', required: false)
if appstream_util.found()
  test(
    'validate-metainfo',
    appstream_util,
    args: ['validate-relax', '--nonet', metainfo_file],
    suite: 'data',
  )
endif

# --- GSettings schema -----------------------------------------------------

install_data(
  application_id + '.gschema.xml',
  install_dir: datadir / 'glib-2.0' / 'schemas',
)

compile_schemas = find_program('glib-compile-schemas', required: false)
if compile_schemas.found()
  test(
    'validate-gschema',
    compile_schemas,
    args: ['--strict', '--dry-run', meson.current_source_dir()],
    suite: 'data',
  )
endif

# --- Example presets ------------------------------------------------------
#
# Installed read-only and copied into the user's library on first run, so the
# originals can always be restored by clearing ~/.config/clicky-clicker/macros.

install_subdir(
  'presets',
  install_dir: pkgdatadir,
  strip_directory: false,
)

# --- systemd user service -------------------------------------------------
#
# The daemon is supervised by systemd rather than by anything bespoke: systemd
# already starts it at login, restarts it on failure and captures its log.

systemd_user_unit_dir = get_option('systemduserunitdir')
if systemd_user_unit_dir == ''
  systemd = dependency('systemd', required: false)
  if systemd.found()
    systemd_user_unit_dir = systemd.get_variable(
      pkgconfig: 'systemduserunitdir',
      pkgconfig_define: ['prefix', prefix],
    )
  else
    systemd_user_unit_dir = prefix / 'lib' / 'systemd' / 'user'
  endif
endif

configure_file(
  input: 'clicky-clicker-daemon.service.in',
  output: 'clicky-clicker-daemon.service',
  configuration: app_conf,
  install: true,
  install_dir: systemd_user_unit_dir,
)

# --- udev rule ------------------------------------------------------------
#
# Off by default: it writes outside the prefix and only makes sense for a
# system-wide install performed as root. Enable with -Dinstall-udev-rules=true.

if get_option('install-udev-rules')
  install_data(
    '99-clicky-clicker-uinput.rules',
    install_dir: prefix / 'lib' / 'udev' / 'rules.d',
  )
endif
