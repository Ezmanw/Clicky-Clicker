# The interpreter is baked into the launchers at configure time so they run
# under the Python that Meson checked for PyGObject, not whatever "python3"
# happens to resolve to first on PATH at run time.
launcher_conf = configuration_data()
launcher_conf.set('PYTHON', python_installation.full_path())
launcher_conf.set('VERSION', meson.project_version())
launcher_conf.set('MODULEDIR', moduledir)
launcher_conf.set('PKGDATADIR', pkgdatadir)

foreach launcher : ['clicky-clicker', 'clicky-clicker-daemon']
  configure_file(
    input: launcher + '.in',
    output: launcher,
    configuration: launcher_conf,
    install: true,
    install_dir: bindir,
    install_mode: 'rwxr-xr-x',
  )
endforeach

install_subdir(
  'clickyclicker',
  install_dir: moduledir,
  exclude_directories: ['__pycache__'],
)
