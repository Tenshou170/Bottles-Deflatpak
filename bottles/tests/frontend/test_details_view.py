import os
# ruff: noqa: E402

from types import SimpleNamespace

import pytest
from gi.repository import Gio

bottles_resource = Gio.Resource.load(
    os.environ.get("BOTTLES_TEST_RESOURCE", "build/bottles.gresource")
)
Gio.resources_register(bottles_resource)

from bottles.backend.models.config import BottleConfig
from bottles.frontend.views.details import DetailsView
from bottles.frontend.views.eagle import EagleView


def _make_view(transition_running, showing_details):
    unloaded = []
    view = SimpleNamespace(unload_view=lambda *_args: unloaded.append(True))
    other_page = object()
    view.window = SimpleNamespace(
        main_leaf=SimpleNamespace(
            get_child_transition_running=lambda: transition_running,
            get_visible_child=lambda: view if showing_details else other_page,
        )
    )
    return view, unloaded


@pytest.mark.parametrize(
    ("transition_running", "showing_details", "expects_unload"),
    (
        (False, False, True),
        (False, True, False),
        (True, False, False),
        (True, True, False),
    ),
)
def test_views_survive_until_the_leaflet_settles_elsewhere(
    transition_running, showing_details, expects_unload
):
    view, unloaded = _make_view(transition_running, showing_details)

    DetailsView._DetailsView__on_main_leaf_changed(view)

    assert bool(unloaded) is expects_unload


def test_unload_view_empties_the_page_stack():
    from gi.repository import Adw, Gtk

    stack = Gtk.Stack()
    stack.add_named(Adw.Bin(), "preferences")
    stack.add_named(Adw.Bin(), "dependencies")
    view = SimpleNamespace(stack_bottle=stack)

    DetailsView.unload_view(view)

    assert stack.get_first_child() is None


def test_build_pages_avoids_duplicate_stack_entries(monkeypatch):
    from gi.repository import Adw, Gtk, GLib
    from bottles.backend.models.config import BottleConfig

    stack = Gtk.Stack()
    pref = Adw.Bin()
    deps = Adw.Bin()
    view = SimpleNamespace(
        config=BottleConfig(),
        view_bottle=Adw.Bin(),
        default_view=Gtk.Box(),
        view_preferences=pref,
        view_dependencies=deps,
        view_registry_rules=Adw.Bin(),
        view_versioning=Adw.Bin(),
        view_installers=Adw.Bin(),
        view_taskmanager=Adw.Bin(),
        view_eagle=Adw.Bin(),
        stack_bottle=stack,
        set_actions=lambda widget: None,
    )
    view.view_bottle.actions = Gtk.Box()

    # First build
    DetailsView.build_pages(view)
    context = GLib.MainContext.default()
    while context.pending():
        context.iteration(False)

    assert stack.get_child_by_name("preferences") == pref
    assert stack.get_child_by_name("dependencies") == deps

    # Second build (should safely not duplicate)
    DetailsView.build_pages(view)
    while context.pending():
        context.iteration(False)

    assert stack.get_child_by_name("preferences") == pref
    assert stack.get_child_by_name("dependencies") == deps


def test_eagle_follows_the_selected_bottle():
    eagle = EagleView(SimpleNamespace(), BottleConfig())
    previous_manager = eagle.manager
    unused = SimpleNamespace(
        set_config=lambda **kwargs: None, update=lambda **kwargs: None
    )
    view = SimpleNamespace(
        view_bottle=unused,
        view_preferences=unused,
        view_taskmanager=unused,
        view_installers=unused,
        view_registry_rules=unused,
        view_versioning=unused,
        view_eagle=eagle,
    )
    for config in (
        BottleConfig(Name="First", Runner="soda-11.0-34"),
        BottleConfig(Name="Second", Environment="Steam", RunnerPath="/runner"),
    ):
        DetailsView.set_config(view, config, rebuild_pages=False)
        assert eagle.config is config
        assert eagle.manager.config is config
        assert eagle.manager is not previous_manager
        previous_manager = eagle.manager
