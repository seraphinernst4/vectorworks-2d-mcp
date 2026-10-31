from vwmcp import app, install


def test_plugin_dir_macos(tmp_path):
    d = install.plugin_dir("2026", platform="darwin", home=tmp_path)
    assert d == tmp_path / "Library/Application Support/Vectorworks/2026/Plug-ins/vwmcp"


def test_plugin_dir_windows(tmp_path):
    d = install.plugin_dir("2026", platform="win32", appdata=tmp_path)
    assert d == tmp_path / "Nemetschek/Vectorworks/2026/Plug-ins/vwmcp"


def test_install_writes_package_and_reports_version(tmp_path):
    target = tmp_path / "Plug-ins" / "vwmcp"
    msg = install.install_plugin(target)
    assert (target / "vwmcp_bridge.py").read_text() == app.bundled_bridge_source()
    assert (target / "__init__.py").exists()
    assert app.EXPECTED_BRIDGE_VERSION in msg


def test_ensure_plugin_installs_into_every_version(tmp_path):
    assert "No Vectorworks" in install.ensure_plugin(home=tmp_path, platform="darwin")
    base = tmp_path / "Library/Application Support/Vectorworks"
    for v in ("2025", "2026", "2027", "Accounts"):
        (base / v).mkdir(parents=True)
    assert install.installed_versions("darwin", tmp_path) == ["2027", "2026"]
    msg = install.ensure_plugin(home=tmp_path, platform="darwin")
    assert "2027" in msg and "2026" in msg
    for v in ("2026", "2027"):
        target = base / v / "Plug-ins/vwmcp"
        assert (target / "vwmcp_bridge.py").read_text() == app.bundled_bridge_source()
        assert (target / "vs_index.json").read_text() == app.bundled_index_text()
    assert not (base / "2025/Plug-ins").exists()
    assert install.ensure_plugin(home=tmp_path, platform="darwin") is None


def test_ensure_plugin_installs_once_and_only_with_vectorworks(tmp_path):
    assert "not found" in install.ensure_plugin("2026", home=tmp_path, platform="darwin")
    vw = tmp_path / "Library/Application Support/Vectorworks/2026"
    vw.mkdir(parents=True)
    assert "Installed" in install.ensure_plugin("2026", home=tmp_path, platform="darwin")
    target = vw / "Plug-ins/vwmcp"
    assert (target / "vwmcp_bridge.py").read_text() == app.bundled_bridge_source()
    assert install.ensure_plugin("2026", home=tmp_path, platform="darwin") is None  # up to date
    (target / "vwmcp_bridge.py").write_text("old")
    assert "Installed" in install.ensure_plugin("2026", home=tmp_path, platform="darwin")


def test_install_is_idempotent(tmp_path):
    target = tmp_path / "vwmcp"
    install.install_plugin(target)
    install.install_plugin(target)
    assert sorted(p.name for p in target.iterdir()) == ["__init__.py", "vs_index.json", "vwmcp_bridge.py"]
